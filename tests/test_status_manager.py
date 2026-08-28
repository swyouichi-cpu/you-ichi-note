"""StatusManagerの遷移ロジックのテスト(Google Sheetsへは一切アクセスしない)。"""
from __future__ import annotations

import pytest

from src.models import Article, Status
from src.status_manager import DoubleProcessingGuard, StatusManager


class FakeSheetsClient:
    """SheetsClientの代わりに使う、メモリ上だけで動くテスト用の実装。"""

    def __init__(self, articles: list[Article]):
        self._articles = {a.id: a for a in articles}

    def list_articles(self) -> list[Article]:
        return list(self._articles.values())

    def find_stale_processing_articles(self) -> list[Article]:
        return [a for a in self._articles.values() if a.status == Status.PROCESSING]

    def update_fields(self, article: Article, **fields: str) -> None:
        current = self._articles[article.id]
        for key, value in fields.items():
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


def test_mark_error_records_stage_in_message():
    article = make_article(status=Status.PROCESSING.value)
    sheets = FakeSheetsClient([article])
    manager = StatusManager(sheets)

    manager.mark_error(article, stage="note_login", message="ログインに失敗しました")

    updated = sheets.list_articles()[0]
    assert updated.status == Status.ERROR.value
    assert updated.error_message == "[note_login] ログインに失敗しました"
