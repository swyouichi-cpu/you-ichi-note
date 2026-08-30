"""StatusManagerの遷移ロジックのテスト(Google Sheetsへは一切アクセスしない)。"""
from __future__ import annotations

import pytest

from src.models import Article, Status
from src.status_manager import (
    DoubleProcessingGuard,
    DraftCreationVerificationError,
    ManualLinkSetupVerificationError,
    StatusManager,
)


class FakeSheetsClient:
    """SheetsClientの代わりに使う、メモリ上だけで動くテスト用の実装。"""

    def __init__(self, articles: list[Article]):
        self._articles = {a.id: a for a in articles}

    def list_articles(self) -> list[Article]:
        return list(self._articles.values())

    def find_stale_processing_articles(self) -> list[Article]:
        return [a for a in self._articles.values() if a.status == Status.PROCESSING]

    def find_inconsistent_ready_with_note_url(self) -> list[Article]:
        return [
            a
            for a in self._articles.values()
            if a.status == Status.READY and a.note_url.strip()
        ]

    def update_fields(self, article: Article, **fields: str) -> None:
        current = self._articles[article.id]
        for key, value in fields.items():
            setattr(current, key, value)


class FlakyStatusSheetsClient(FakeSheetsClient):
    """statusフィールドの書き込みが最初の1回だけ反映されない状況を再現する。

    実機テスト(GitHub Actions Content Pipeline #16)で、note_urlと
    updated_atは反映されたのにstatusだけreadyのまま残るという不整合が
    観測された。1回だけ意図的に無視することで、mark_draft_created()の
    read-back検証がこれを検知できることと、その後のneeds_reviewへの
    書き込みは正常に反映される(=検知後の自動復旧メッセージはきちんと
    残せる)ことの両方をテストできるようにする。
    """

    def __init__(self, articles: list[Article]):
        super().__init__(articles)
        self._status_write_failures_remaining = 1

    def update_fields(self, article: Article, **fields: str) -> None:
        current = self._articles[article.id]
        for key, value in fields.items():
            if key == "status" and self._status_write_failures_remaining > 0:
                self._status_write_failures_remaining -= 1
                continue  # statusの書き込みを1回だけ無視する
            setattr(current, key, value)


def make_article(**overrides) -> Article:
    base = dict(
        row_number=2,
        id="a1",
        title="テスト記事",
        body="本文",
        tags="タグ1,タグ2",
        category="思考",
        source_theme="テーマ",
        content_type="free",
        status=Status.READY.value,
    )
    base.update(overrides)
    return Article(**base)


def test_reconcile_stale_processing_without_note_url_becomes_needs_review():
    article = make_article(status=Status.PROCESSING.value, note_url="")
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    stale = manager.reconcile_stale_processing()

    assert len(stale) == 1
    updated = sheets.list_articles()[0]
    assert updated.status == Status.NEEDS_REVIEW.value
    assert "note_url が未記録" in updated.error_message


def test_reconcile_stale_processing_with_note_url_still_becomes_needs_review():
    """note_urlが記録済み(=下書き作成は成功していたかもしれない)でも、
    自動でdraftへは進めない(二重投稿防止を自動復旧より優先するため)。"""
    article = make_article(status=Status.PROCESSING.value, note_url="https://note.com/x/n/abc")
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    stale = manager.reconcile_stale_processing()

    assert len(stale) == 1
    updated = sheets.list_articles()[0]
    assert updated.status == Status.NEEDS_REVIEW.value
    assert "重複下書きを避けるため" in updated.error_message


def test_reconcile_leaves_ready_and_draft_created_rows_untouched():
    ready = make_article(id="a1", status=Status.READY.value)
    draft_created = make_article(id="a2", status=Status.DRAFT_CREATED.value)
    sheets = FakeSheetsClient([ready, draft_created])
    manager = StatusManager(sheets)

    stale = manager.reconcile_stale_processing()

    assert stale == []
    statuses = {a.id: a.status for a in sheets.list_articles()}
    assert statuses == {"a1": Status.READY.value, "a2": Status.DRAFT_CREATED.value}


def test_claim_article_transitions_ready_to_processing():
    article = make_article(status=Status.READY.value)
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.claim_article(article)

    assert sheets.list_articles()[0].status == Status.PROCESSING.value


def test_claim_article_raises_if_already_claimed_by_someone_else():
    """取得した直後に、別プロセスが既にprocessingへ変えていた場合は
    二重処理を避けるためエラーにする。"""
    article = make_article(status=Status.READY.value)
    sheets = FakeSheetsClient([article])
    # claim直前に他プロセスがprocessingへ変更したことをシミュレート
    sheets.update_fields(article, status=Status.PROCESSING.value)
    manager = StatusManager(sheets)

    with pytest.raises(DoubleProcessingGuard):
        manager.claim_article(article)


def test_mark_draft_created_sets_urls_and_clears_error():
    article = make_article(status=Status.PROCESSING.value, error_message="前回のエラー")
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.mark_draft_created(article, note_url="https://note.com/x/n/abc", craft_url="")

    updated = sheets.list_articles()[0]
    assert updated.status == Status.DRAFT_CREATED.value
    assert updated.note_url == "https://note.com/x/n/abc"
    assert updated.error_message == ""


def test_mark_draft_created_raises_when_status_write_is_not_reflected():
    """実機で観測された不整合(note_urlは書けたがstatusだけreadyのまま)の再現。

    書き込みAPI自体が例外を出さなくても、read-back検証で不一致を検知し、
    needs_reviewへ倒したうえでDraftCreationVerificationErrorを送出する
    (呼び出し側main.pyが「正常成功」として扱わないようにするため)。
    """
    article = make_article(status=Status.PROCESSING.value)
    sheets = FlakyStatusSheetsClient([article])
    manager = StatusManager(sheets)

    with pytest.raises(DraftCreationVerificationError):
        manager.mark_draft_created(article, note_url="https://note.com/x/n/abc", craft_url="")

    updated = sheets.list_articles()[0]
    # note_url自体は書き込めている(実機と同じ状況)が、statusはneeds_reviewへ倒れる。
    assert updated.status == Status.NEEDS_REVIEW.value
    assert updated.note_url == "https://note.com/x/n/abc"
    assert "read-back" in updated.error_message or "反映" in updated.error_message


def test_reconcile_inconsistent_ready_with_note_url_moves_to_needs_review():
    inconsistent = make_article(
        id="a1", status=Status.READY.value, note_url="https://note.com/x/n/abc"
    )
    clean_ready = make_article(id="a2", status=Status.READY.value, note_url="")
    sheets = FakeSheetsClient([inconsistent, clean_ready])
    manager = StatusManager(sheets)

    result = manager.reconcile_inconsistent_ready_with_note_url()

    assert {a.id for a in result} == {"a1"}
    updated = {a.id: a for a in sheets.list_articles()}
    assert updated["a1"].status == Status.NEEDS_REVIEW.value
    assert updated["a1"].note_url == "https://note.com/x/n/abc"
    assert updated["a2"].status == Status.READY.value  # 正常なreadyは変更しない


def test_mark_needs_review_with_note_url_sets_status_note_url_and_message():
    # 商品リンク手動設定待ち(2026年8月29日、ARTICLE-001の実機実行を
    # 踏まえた運用方針の変更): 下書き作成自体は成功したが商品リンクの
    # 自動設定は行っていない記事を、note_urlを保持したままneeds_review
    # へ倒せることを確認する。
    article = make_article(status=Status.PROCESSING.value)
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.mark_needs_review_with_note_url(
        article,
        note_url="https://note.com/x/n/abc",
        message="商品リンクの手動設定が必要です。",
    )

    updated = sheets.list_articles()[0]
    assert updated.status == Status.NEEDS_REVIEW.value
    assert updated.note_url == "https://note.com/x/n/abc"
    assert updated.error_message == "商品リンクの手動設定が必要です。"


def test_mark_needs_review_with_note_url_is_not_picked_up_as_ready_inconsistency():
    # needs_review + note_url は、readyのまま残る不整合の検知
    # (reconcile_inconsistent_ready_with_note_url)とは衝突しない
    # (statusがreadyではないため対象外になる)ことを確認する。
    article = make_article(status=Status.PROCESSING.value)
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.mark_needs_review_with_note_url(
        article, note_url="https://note.com/x/n/abc", message="商品リンクの手動設定が必要です。"
    )

    inconsistent = manager.reconcile_inconsistent_ready_with_note_url()
    assert inconsistent == []
    updated = sheets.list_articles()[0]
    assert updated.status == Status.NEEDS_REVIEW.value  # 変更されていない
    assert updated.note_url == "https://note.com/x/n/abc"  # 変更されていない


def test_mark_needs_review_with_note_url_raises_when_status_write_is_not_reflected():
    # 実機で観測された不整合(mark_draft_created()と同種)の再現:
    # 書き込みAPI自体が例外を出さなくても、read-back検証で不一致を検知し、
    # ManualLinkSetupVerificationErrorを送出する(呼び出し側main.pyが
    # 「正常成功」として扱わないようにするため)。
    article = make_article(status=Status.PROCESSING.value)
    sheets = FlakyStatusSheetsClient([article])
    manager = StatusManager(sheets)

    with pytest.raises(ManualLinkSetupVerificationError):
        manager.mark_needs_review_with_note_url(
            article,
            note_url="https://note.com/x/n/abc",
            message="商品リンクの手動設定が必要です。",
        )

    updated = sheets.list_articles()[0]
    # フォールバックのmark_needs_review()により、statusは最終的に
    # needs_reviewへ倒れる(mark_draft_createdと同じ設計)。
    assert updated.status == Status.NEEDS_REVIEW.value
    assert "read-back" in updated.error_message or "反映" in updated.error_message


def test_mark_error_records_stage_in_message():
    article = make_article(status=Status.PROCESSING.value)
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.mark_error(article, stage="note_login", message="ログインに失敗しました")

    updated = sheets.list_articles()[0]
    assert updated.status == Status.ERROR.value
    assert updated.error_message == "[note_login] ログインに失敗しました"
