"""SheetsClientのロジックのテスト。実際のGoogle APIへは接続しない。

gspreadのWorksheetの代わりに、必要なメソッドだけを持つ
軽量なフェイクオブジェクトを使う。
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.models import Article, Status
from src.sheets import SheetsClient, _is_publish_at_eligible


class FakeWorksheet:
    def __init__(self, header: list[str], rows: list[list[str]]):
        self.header = header
        self.rows = rows  # 2行目以降のデータ(headerと同じ列順)
        self.updates: list[tuple[int, int, str]] = []

    def row_values(self, row_number: int) -> list[str]:
        assert row_number == 1
        return self.header

    def get_all_records(self) -> list[dict]:
        return [dict(zip(self.header, row)) for row in self.rows]

    def update_cell(self, row: int, col: int, value: str) -> None:
        self.updates.append((row, col, value))


HEADER = [
    "id", "title", "body", "tags", "category", "source_theme",
    "content_type", "status", "publish_at", "note_url", "craft_url",
    "error_message", "created_at", "updated_at",
]


def make_client_with_rows(rows: list[list[str]]) -> tuple[SheetsClient, FakeWorksheet]:
    client = SheetsClient.__new__(SheetsClient)  # connect()を経由せず直接組み立てる
    client._config = None
    fake_ws = FakeWorksheet(HEADER, rows)
    client._worksheet = fake_ws
    client._header = HEADER
    return client, fake_ws


def row(id_="a1", status="ready", content_type="free", note_url="", publish_at=""):
    return [
        id_, "タイトル", "本文", "タグ1,タグ2", "思考", "テーマ",
        content_type, status, publish_at, note_url, "", "", "", "",
    ]


def test_get_next_target_article_filters_ready_free_without_note_url():
    client, _ = make_client_with_rows([
        row(id_="a1", status="draft_created"),
        row(id_="a2", status="ready", content_type="paid"),
        row(id_="a3", status="ready", content_type="free", note_url="https://note.com/x"),
        row(id_="a4", status="ready", content_type="free"),
    ])

    target = client.get_next_target_article()

    assert target is not None
    assert target.id == "a4"


def test_get_next_target_article_returns_none_when_no_match():
    client, _ = make_client_with_rows([
        row(id_="a1", status="draft_created"),
        row(id_="a2", status="processing"),
    ])

    assert client.get_next_target_article() is None


def test_get_next_target_article_skips_future_publish_at():
    client, _ = make_client_with_rows([
        row(id_="a1", status="ready", content_type="free", publish_at="2999-01-01T00:00:00+00:00"),
        row(id_="a2", status="ready", content_type="free", publish_at=""),
    ])

    target = client.get_next_target_article()

    assert target is not None
    assert target.id == "a2"


def test_get_next_target_article_includes_past_publish_at():
    client, _ = make_client_with_rows([
        row(id_="a1", status="ready", content_type="free", publish_at="2000-01-01T00:00:00+00:00"),
    ])

    target = client.get_next_target_article()

    assert target is not None
    assert target.id == "a1"


def test_is_publish_at_eligible_empty_is_eligible():
    article = Article.from_record(2, {"id": "a1", "publish_at": ""})
    assert _is_publish_at_eligible(article, datetime(2026, 1, 1, tzinfo=timezone.utc)) is True


def test_is_publish_at_eligible_unparseable_is_not_eligible():
    article = Article.from_record(2, {"id": "a1", "publish_at": "not-a-date"})
    assert _is_publish_at_eligible(article, datetime(2026, 1, 1, tzinfo=timezone.utc)) is False


def test_is_publish_at_eligible_naive_datetime_treated_as_utc():
    article = Article.from_record(2, {"id": "a1", "publish_at": "2000-01-01T00:00:00"})
    assert _is_publish_at_eligible(article, datetime(2026, 1, 1, tzinfo=timezone.utc)) is True


def test_find_stale_processing_articles():
    client, _ = make_client_with_rows([
        row(id_="a1", status="processing"),
        row(id_="a2", status="ready"),
        row(id_="a3", status="processing"),
    ])

    stale = client.find_stale_processing_articles()

    assert {a.id for a in stale} == {"a1", "a3"}


def test_find_inconsistent_ready_with_note_url():
    client, _ = make_client_with_rows([
        row(id_="a1", status="ready", note_url="https://note.com/x"),
        row(id_="a2", status="ready", note_url=""),
        row(id_="a3", status="processing", note_url="https://note.com/y"),
    ])

    inconsistent = client.find_inconsistent_ready_with_note_url()

    assert {a.id for a in inconsistent} == {"a1"}


def test_update_fields_writes_correct_columns_and_updated_at():
    client, fake_ws = make_client_with_rows([row(id_="a1", status="ready")])
    article = client.list_articles()[0]

    client.update_fields(article, status=Status.PROCESSING.value)

    status_col = HEADER.index("status") + 1
    updated_at_col = HEADER.index("updated_at") + 1
    written_cols = {col for (_row, col, _value) in fake_ws.updates}
    assert status_col in written_cols
    assert updated_at_col in written_cols
    status_value = next(v for (_r, c, v) in fake_ws.updates if c == status_col)
    assert status_value == Status.PROCESSING.value
