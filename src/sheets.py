"""Google Sheets (投稿キュー) との読み書きを担当する。

認証は「サービスアカウント」方式。あなたのGoogleパスワードは一切使わず、
ロボット専用のGoogleアカウント(の鍵)経由でシートにアクセスする。
事前に、対象スプレッドシートをそのサービスアカウントのメールアドレスと
共有(編集者権限)しておく必要がある。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from src.config import Config
from src.logger import get_logger
from src.models import Article, ContentType, Status

logger = get_logger()

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def now_iso() -> str:
    """UTC基準のISO8601文字列。GitHub ActionsはUTCで動くため統一する。

    スプレッドシート上の日時はUTCで記録される点に注意(JSTではない)。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_publish_at_eligible(article: Article, now: datetime) -> bool:
    """publish_at が空、または現在時刻(UTC)以前であれば対象とする。

    パース不能な値は安全側に倒し、対象外(Falseを返す)にする
    (誤って未来の予定を早く処理してしまうことを避ける)。
    """
    raw = article.publish_at.strip()
    if not raw:
        return True
    try:
        publish_at = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning(
            "publish_at をパースできませんでした: id=%s publish_at=%r。対象外とします。",
            article.id,
            raw,
        )
        return False
    if publish_at.tzinfo is None:
        publish_at = publish_at.replace(tzinfo=timezone.utc)
    return publish_at <= now


class SheetsError(RuntimeError):
    pass


class SheetsClient:
    def __init__(self, config: Config):
        self._config = config
        self._worksheet: gspread.Worksheet | None = None
        self._header: list[str] = []

    def connect(self) -> None:
        try:
            info = json.loads(self._config.google_service_account_json)
        except json.JSONDecodeError as exc:
            raise SheetsError(
                "GOOGLE_SERVICE_ACCOUNT_JSON がJSONとして読み取れません。"
                "Google Cloudで発行したサービスアカウント鍵ファイルの中身を"
                "そのまま設定しているか確認してください。"
            ) from exc

        credentials = Credentials.from_service_account_info(info, scopes=_SCOPES)
        client = gspread.authorize(credentials)

        try:
            spreadsheet = client.open_by_key(self._config.spreadsheet_id)
        except gspread.exceptions.APIError as exc:
            raise SheetsError(
                "スプレッドシートを開けませんでした。SPREADSHEET_ID が正しいか、"
                "サービスアカウントのメールアドレスをそのシートに共有(編集者)して"
                "いるか確認してください。"
            ) from exc

        try:
            self._worksheet = spreadsheet.worksheet(self._config.sheet_name)
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetsError(
                f"シート '{self._config.sheet_name}' が見つかりません。"
                "SHEET_NAME(タブ名)を確認してください。"
            ) from exc

        self._header = self._worksheet.row_values(1)
        missing = [c for c in ("id", "status", "content_type") if c not in self._header]
        if missing:
            raise SheetsError(
                f"必須の列がヘッダー行にありません: {missing}。"
                "1行目に列名が正しく入っているか確認してください。"
            )
        logger.info("Sheets接続成功")

    def _require_worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:
            raise SheetsError("Sheetsに接続していません。connect()を先に呼んでください。")
        return self._worksheet

    def _col_index(self, column_name: str) -> int:
        """列名からGoogle Sheets上の列番号(1始まり)を返す。"""
        try:
            return self._header.index(column_name) + 1
        except ValueError as exc:
            raise SheetsError(f"列 '{column_name}' がヘッダー行に存在しません。") from exc

    def list_articles(self) -> list[Article]:
        """全行を読み込み、Articleのリストとして返す(1行目=ヘッダーを除く)。"""
        ws = self._require_worksheet()
        records = ws.get_all_records()  # ヘッダー行を自動的にキーとして使う
        return [
            Article.from_record(row_number=i + 2, record=record)
            for i, record in enumerate(records)
        ]

    def get_next_target_article(self) -> Article | None:
        """自動処理の対象となる次の1件を返す(なければNone)。

        条件: status == ready かつ content_type == free かつ note_url が空、
        かつ publish_at が空、または現在時刻(UTC)以前であること。
        1回の実行で最大1件のみを対象にする設計。
        """
        now = datetime.now(timezone.utc)
        for article in self.list_articles():
            if (
                article.status == Status.READY
                and article.content_type == ContentType.FREE
                and not article.note_url.strip()
                and _is_publish_at_eligible(article, now)
            ):
                return article
        return None

    def find_stale_processing_articles(self) -> list[Article]:
        """前回以前の実行で processing のまま残ってしまった行を返す。

        このメソッドは「今回の実行中にこのプロセスがprocessingへ変更した行」
        は含まない(呼び出しタイミングは常に、今回のprocessing変更より前)。
        """
        return [a for a in self.list_articles() if a.status == Status.PROCESSING]

    def find_inconsistent_ready_with_note_url(self) -> list[Article]:
        """status=ready なのに note_url が既に入っている不整合な行を返す。

        本来 ready は「まだnote下書きを作成していない」状態のはずだが、
        note下書き作成自体は成功して note_url の書き込みは反映された
        ものの、続く status を draft_created へ更新する書き込みだけが
        何らかの理由で反映されなかった場合にこの状態になりうる
        (実機で実際に観測された不整合)。get_next_target_article() は
        note_url が空の行しか対象にしないため、この状態の行は新規の
        note下書きが誤って重複作成されることはないが、それ以上自動処理も
        進まないまま放置され続けてしまう。この不整合を検出し、reconcile
        処理の一環としてneeds_reviewへ倒すために使う。
        """
        return [
            a
            for a in self.list_articles()
            if a.status == Status.READY and a.note_url.strip()
        ]

    def update_fields(self, article: Article, **fields: str) -> None:
        """指定した列だけを更新する(article.row_numberの行)。updated_atは自動更新。

        1フィールド=1回のAPI呼び出し(update_cell)で書き込む。実機で
        「note_url と updated_at は反映されたのに status だけ反映され
        なかった」という不整合が観測されたため、原因調査の手がかりとして
        書き込み直前に列名・列番号・値をログに残す(値は記事本文などの
        秘密情報ではなく、Sheetsの業務データそのものであるため記録して
        問題ない)。呼び出し側(StatusManager)で書き込み後のread-back
        検証を行う設計にしているため、ここでは書き込みリクエストの送信
        までを担当する。
        """
        ws = self._require_worksheet()
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        for column_name, value in fields.items():
            col = self._col_index(column_name)
            logger.info(
                "Sheets書き込み: id=%s row=%d column=%s(col_index=%d) value=%r",
                article.id,
                article.row_number,
                column_name,
                col,
                value,
            )
            ws.update_cell(article.row_number, col, value)
