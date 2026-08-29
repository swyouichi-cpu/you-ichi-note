"""Google Sheets 1行 = 1記事 を表すデータモデル。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    """記事の処理状態。

    正常系: READY -> PROCESSING -> DRAFT_CREATED -> PUBLISHED
    失敗系(原因が明確):      PROCESSING -> ERROR
    失敗系(成否が不明):      PROCESSING -> NEEDS_REVIEW

    NEEDS_REVIEW は人間が note / Craft の実際の状態を確認し、
    手動で READY か DRAFT_CREATED に書き換えるまで、自動処理の対象にしない。
    """

    READY = "ready"
    PROCESSING = "processing"
    DRAFT_CREATED = "draft_created"
    PUBLISHED = "published"
    ERROR = "error"
    NEEDS_REVIEW = "needs_review"


class ContentType(StrEnum):
    FREE = "free"
    PAID = "paid"


# Google Sheets のヘッダー行と1:1対応する列名。
# この順序はヘッダー行の並びを規定するものではなく、
# 実際の列位置は SheetsClient がヘッダー名で解決する。
COLUMNS = [
    "id",
    "title",
    "body",
    "tags",
    "category",
    "source_theme",
    "content_type",
    "status",
    "publish_at",
    "note_url",
    "craft_url",
    "error_message",
    "created_at",
    "updated_at",
    "product_links",
]


@dataclass
class Article:
    """スプレッドシートの1行分の記事データ。"""

    row_number: int  # シート上の実際の行番号(1始まり、ヘッダー行を含む) - 更新時に使う
    id: str
    title: str
    body: str
    tags: str
    category: str
    source_theme: str
    content_type: str
    status: str
    publish_at: str = ""
    note_url: str = ""
    craft_url: str = ""
    error_message: str = ""
    created_at: str = ""
    updated_at: str = ""
    product_links: str = ""  # JSON配列の生文字列。詳細はsrc/note.pyのparse_product_links()を参照

    @classmethod
    def from_record(cls, row_number: int, record: dict) -> "Article":
        return cls(
            row_number=row_number,
            id=str(record.get("id", "")).strip(),
            title=str(record.get("title", "")),
            body=str(record.get("body", "")),
            tags=str(record.get("tags", "")),
            category=str(record.get("category", "")),
            source_theme=str(record.get("source_theme", "")),
            content_type=str(record.get("content_type", "")).strip().lower(),
            status=str(record.get("status", "")).strip().lower(),
            publish_at=str(record.get("publish_at", "")),
            note_url=str(record.get("note_url", "")),
            craft_url=str(record.get("craft_url", "")),
            error_message=str(record.get("error_message", "")),
            created_at=str(record.get("created_at", "")),
            updated_at=str(record.get("updated_at", "")),
            product_links=str(record.get("product_links", "")),
        )

    def tag_list(self) -> list[str]:
        """'タグ1, タグ2,タグ3' のようなカンマ区切り文字列をリストにする。"""
        return [t.strip() for t in self.tags.split(",") if t.strip()]
