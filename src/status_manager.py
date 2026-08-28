"""記事のstatus遷移を一元管理する。

設計方針(ユーザー確認済み):
  - 基本の流れ: ready -> processing -> draft_created -> published
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


class DraftCreationVerificationError(RuntimeError):
    """draft_created書き込み後のread-back検証に失敗した場合に送出。

    実機テストで、mark_draft_created()の書き込みAPI呼び出し自体は
    例外を出さずに完了したにもかかわらず、実際にはSheets上のstatusが
    draft_createdへ更新されていない(readyのまま残る)という不整合が
    観測された。書き込みAPIがエラーを出さなかったことは「成功」の
    証拠にならないため、書き込み後に必ずSheetsを読み戻して確認する。
    一致しなかった場合はneeds_reviewへ倒したうえでこの例外を送出し、
    呼び出し側(main.py)が「正常成功」として扱わないようにする。
    """


class StatusManager:
    def __init__(self, sheets: SheetsClient):
        self._sheets = sheets

    def reconcile_stale_processing(self) -> list[Article]:
        """実行開始時に必ず呼ぶ。前回以前からprocessingのまま残っている行を
        すべて needs_review に変更し、その一覧を返す。

        note_url の有無で人間向けメッセージを変える(どこまで進んでいたかの
        手がかりを残すため)が、いずれの場合も自動では ready/draft_created に進めない。
        """
        stale = self._sheets.find_stale_processing_articles()
        for article in stale:
            if article.note_url.strip():
                message = (
                    "前回の実行が完了せず processing のまま検出されました。"
                    "note_url が記録済みのため、note下書きの作成自体は成功していた"
                    "可能性があります。重複下書きを避けるため自動では復旧しません。"
                    "note側で実際に下書きが存在するか確認し、問題なければ status を "
                    "draft_created に、下書きが存在しない場合は note_url を空にしたうえで "
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

    def reconcile_inconsistent_ready_with_note_url(self) -> list[Article]:
        """実行開始時に必ず呼ぶ。status=readyなのにnote_urlが既に入っている
        不整合な行をすべてneeds_reviewに変更し、その一覧を返す。

        get_next_target_article()はnote_urlが空の行しか対象にしないため、
        この状態の行から新しいnote下書きが重複作成されることはないが、
        自動処理からも人間の確認対象からも外れたまま永久に放置されて
        しまう。安全側に倒し、人間の確認を求める。
        """
        inconsistent = self._sheets.find_inconsistent_ready_with_note_url()
        for article in inconsistent:
            message = (
                "status が ready のまま、note_url に値が入っている不整合な"
                "状態を検出しました。本来ready状態ではnote_urlは空のはずです。"
                "note下書き作成自体は成功した(note_urlの書き込みは反映された)"
                "ものの、続くstatusをdraft_createdへ更新する書き込みが何らかの"
                "理由で反映されなかった可能性があります。新しいnote下書きを"
                "重複作成しないよう自動では復旧しません。note側で実際に下書きが"
                "存在するか確認し、問題なければ status を draft_created に、"
                "下書きが存在しない場合は note_url を空にしたうえで status は "
                "ready のままにしてください。"
            )
            logger.warning(
                "要確認: id=%s を needs_review にします(ready+note_url不整合を検出)",
                article.id,
            )
            self._sheets.update_fields(
                article,
                status=Status.NEEDS_REVIEW.value,
                error_message=message,
            )
        return inconsistent

    def mark_draft_created(self, article: Article, note_url: str, craft_url: str = "") -> None:
        """draft_createdへ変更する。書き込みAPIがエラーを出さなかっただけでは
        成功とみなさず、書き込み直後にSheetsを読み戻してstatus/note_urlが
        実際に反映されているかを確認する(read-back検証)。

        実機テストで、この書き込み自体は例外なく完了したにもかかわらず、
        実際にはstatusがdraft_createdへ更新されずreadyのまま残るという
        不整合が観測されたため、この検証を追加した。一致しなかった場合は
        needs_reviewへ倒したうえでDraftCreationVerificationErrorを送出し、
        呼び出し側で「正常成功」の完了ログを出させないようにする。
        """
        logger.info("draft_createdへ変更: id=%s note_url=%s", article.id, note_url)
        self._sheets.update_fields(
            article,
            status=Status.DRAFT_CREATED.value,
            note_url=note_url,
            craft_url=craft_url,
            error_message="",
        )

        latest = self._latest(article)
        if (
            latest is None
            or latest.status != Status.DRAFT_CREATED
            or latest.note_url.strip() != note_url.strip()
        ):
            actual_status = latest.status if latest else "(行が見つかりません)"
            actual_note_url = latest.note_url if latest else ""
            message = (
                "draft_createdへの書き込みを実行しましたが、Sheetsを読み戻した"
                f"ところ status={actual_status!r} note_url={actual_note_url!r} "
                f"でした(期待値: status=draft_created, note_url={note_url!r})。"
                "書き込みAPI自体はエラーを出しませんでしたが、実際には反映され"
                "ていない可能性があります。Sheets側のデータ入力規則(プルダウン等)"
                "やApps Scriptのトリガーがstatus列の値を制限・上書きしていないか"
                "確認してください。安全のためneeds_reviewとして扱います。"
            )
            logger.error(
                "draft_created書き込みのread-back検証に失敗: id=%s %s",
                article.id,
                message,
            )
            self.mark_needs_review(article, stage="sheets_write_verification", message=message)
            raise DraftCreationVerificationError(message)

        logger.info("draft_created書き込みのread-back検証に成功: id=%s", article.id)

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
