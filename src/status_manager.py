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


class ManualLinkSetupVerificationError(RuntimeError):
    """商品リンク手動設定待ち(needs_review + note_url)書き込み後の
    read-back検証に失敗した場合に送出する(2026年8月29日)。

    mark_draft_created()と同じ設計で、書き込みAPI呼び出し自体がエラーを
    出さなかったことは「成功」の証拠にならないため、書き込み後に必ず
    Sheetsを読み戻して確認する。一致しなかった場合はこの例外を送出し、
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

    def mark_needs_review_with_note_url(
        self, article: Article, note_url: str, message: str
    ) -> None:
        """商品リンク(product_links)が指定されている記事について、
        note下書き作成自体は成功した(note_urlを取得できた)ものの、商品
        リンクの設定は人間が行う必要があることを示す状態
        (needs_review + note_url)へ変更する(2026年8月29日、ARTICLE-001の
        実機実行を踏まえた運用方針の変更)。

        note.com側の商品リンク設定UIの実機での不安定さ(URL入力欄の消失、
        値の残留・連結、position: fixedによるviewport外配置など)を受けて
        product_linksが指定された記事は自動でのリンク設定を行わない運用に
        変更した。下書き作成(タイトル・本文・下書き保存)自体は成功して
        いるため、既存のstatus体系(ready/processing/draft_created/
        published/error/needs_review)は変更せず、`needs_review`に
        `note_url`を保持したまま倒すことで「下書き作成済み・人間が商品
        リンクを手動設定する必要がある」ことを表現する。

        get_next_target_article()はstatus==readyの行しか対象にしないため、
        この状態(status=needs_review)の行が自動処理で再取得され、新しい
        note下書きが重複作成されることはない。呼び出し時点で行の状態は
        claim_article()により既にstatus=processingになっているため、
        万一この書き込みが完全に反映されずstatus=processingのまま残った
        場合も、既存のreconcile_stale_processing()が次回実行の最初に
        検出してneeds_reviewへ倒す(このとき万一note_urlだけは反映されて
        いた場合の案内メッセージも既に用意されている)。万一
        status=readyまで巻き戻った状態でnote_urlだけ入った場合も、既存の
        reconcile_inconsistent_ready_with_note_url()が検出する。
        いずれの経路でも新しいnote下書きが重複作成されることはない
        (多重の安全網)。

        mark_draft_created()と同じく、書き込みAPIがエラーを出さなかった
        だけでは成功とみなさず、書き込み直後にSheetsを読み戻して
        status/note_urlが実際に反映されているかを確認する。一致しなかった
        場合は(mark_draft_created()と同様に)通常のmark_needs_review()で
        もう一度書き込みを試みたうえで、ManualLinkSetupVerificationError
        を送出し、呼び出し側で「正常成功」の完了ログを出させないように
        する。
        """
        logger.info(
            "needs_review(商品リンク手動設定待ち)へ変更: id=%s note_url=%s",
            article.id,
            note_url,
        )
        self._sheets.update_fields(
            article,
            status=Status.NEEDS_REVIEW.value,
            note_url=note_url,
            error_message=message,
        )

        latest = self._latest(article)
        if (
            latest is None
            or latest.status != Status.NEEDS_REVIEW
            or latest.note_url.strip() != note_url.strip()
        ):
            actual_status = latest.status if latest else "(行が見つかりません)"
            actual_note_url = latest.note_url if latest else ""
            verify_message = (
                "商品リンク手動設定待ち(needs_review + note_url)への書き込みを"
                f"実行しましたが、Sheetsを読み戻したところ status="
                f"{actual_status!r} note_url={actual_note_url!r} でした"
                f"(期待値: status=needs_review, note_url={note_url!r})。"
                "書き込みAPI自体はエラーを出しませんでしたが、実際には反映され"
                "ていない可能性があります。"
            )
            logger.error(
                "needs_review(商品リンク手動設定待ち)書き込みのread-back検証に"
                "失敗: id=%s %s",
                article.id,
                verify_message,
            )
            self.mark_needs_review(
                article, stage="sheets_write_verification", message=verify_message
            )
            raise ManualLinkSetupVerificationError(verify_message)

        logger.info(
            "needs_review(商品リンク手動設定待ち)書き込みのread-back検証に成功: id=%s",
            article.id,
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
