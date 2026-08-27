"""記事のstatus遷移を一元管理する。

設計方針(ユーザー確認済み):
  - 基本の流れ: ready -> processing -> draft -> published
  - 明確な失敗:   processing -> error
  - 成否が不明:   processing -> needs_review

  最重要ルール:「二重投稿防止」を「自動復旧」より優先する。
  そのため、前回の実行が processing のまま終わってしまった行を見つけても、
  一定時間の経過だけを理由に自動で ready へ戻すことは絶対にしない。
  安全側に倒し、常に needs_review にして人間の確認を挟む。
"""
from __future__ import annotations

from src.logger import get_logger
from src.models import Article, Status
from src.sheets import SheetsClient

logger = get_logger()


class DoubleProcessingGuard(RuntimeError):
    """claim_article() 呼び出し時点で対象記事がすでに ready でなかった場合に送出。"""


class StatusManager:
    def __init__(self, sheets: SheetsClient):
        self._sheets = sheets

    def reconcile_stale_processing(self) -> list[Article]:
        """実行開始時に必ず呼ぶ。前回以前からprocessingのまま残っている行を
        すべて needs_review に変更し、その一覧を返す。

        note_url の有無で人間向けメッセージを変える(どこまで進んでいたかの
        手がかりを残すため)が、いずれの場合も自動では ready/draft に進めない。
        """
        stale = self._sheets.find_stale_processing_articles()
        for article in stale:
            if article.note_url.strip():
                message = (
                    "前回の実行が完了せず processing のまま検出されました。"
                    "note_url が記録済みのため、note下書きの作成自体は成功していた"
                    "可能性があります。重複下書きを避けるため自動では復旧しません。"
                    "note側で実際に下書きが存在するか確認し、問題なければ status を "
                    "draft に、下書きが存在しない場合は note_url を空にしたうえで "
                    "status を ready に手動で変更してください。"
                )
            else:
                message = (
                    "前回の実行が完了せず processing のまま検出されました。"
                    "note_url が未記録のため、note下書き作成の前後で中断した"
                    "可能性があります。note側に下書きが残っていないか確認したうえで、"
                    "問題なければ status を ready に手動で変更してください。"
                )
            logger.warning("要確認: id=%s を needs_review にします(processing残留を検出)", article.id)
            self._sheets.update_fields(
                article,
                status=Status.NEEDS_REVIEW.value,
                error_message=message,
            )
        return stale

    def claim_article(self, article: Article) -> None:
        """ready -> processing。取得直前に最新状態を再確認し、二重取得を防ぐ。"""
        latest = self._latest(article)
        if latest is None or latest.status != Status.READY:
            raise DoubleProcessingGuard(
                f"id={article.id} は ready ではなくなっていたため処理を中止しました "
                f"(現在の status={latest.status if latest else '(削除された?)'})。"
            )
        logger.info("processingへ変更: id=%s", article.id)
        self._sheets.update_fields(article, status=Status.PROCESSING.value)

    def mark_draft(self, article: Article, note_url: str, craft_url: str = "") -> None:
        logger.info("draftへ変更: id=%s", article.id)
        self._sheets.update_fields(
            article,
            status=Status.DRAFT.value,
            note_url=note_url,
            craft_url=craft_url,
            error_message="",
        )

    def mark_error(self, article: Article, stage: str, message: str) -> None:
        logger.error("errorへ変更: id=%s stage=%s", article.id, stage)
        self._sheets.update_fields(
            article,
            status=Status.ERROR.value,
            error_message=f"[{stage}] {message}",
        )

    def mark_needs_review(self, article: Article, stage: str, message: str) -> None:
        logger.warning("needs_reviewへ変更: id=%s stage=%s", article.id, stage)
        self._sheets.update_fields(
            article,
            status=Status.NEEDS_REVIEW.value,
            error_message=f"[{stage}] {message}",
        )

    def _latest(self, article: Article) -> Article | None:
        for a in self._sheets.list_articles():
            if a.id == article.id:
                return a
        return None
