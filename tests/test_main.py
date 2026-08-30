"""main.py の cmd_run() ルーティングロジックのソース確認テスト。

cmd_run() は実際の Google Sheets 接続・Playwright によるブラウザ操作を
伴うため(_connect() が本物の SheetsClient を作り、NotePoster() が本物の
ブラウザを起動する)、note.py / status_manager.py の既存テストと同じ設計
方針で、フルの実行(モック化)によるテストは行わない。ここでは、
product_links の有無に応じた分岐が意図した通りソース上に存在すること
だけを、ソースコードの検査によって確認する(2026年8月29日、ARTICLE-001の
実機実行を踏まえ、product_links が指定されている記事は商品リンクの自動
設定を行わずnote_urlを保持したままneeds_reviewへ倒す運用へ変更したこと
の回帰テスト)。
"""
from __future__ import annotations

import inspect

from src.main import cmd_run


def test_cmd_run_source_routes_product_links_to_manual_setup_needs_review():
    # product_linksが指定されている記事は、mark_draft_created()ではなく
    # mark_needs_review_with_note_url()へルーティングされることを確認する。
    source = inspect.getsource(cmd_run)
    assert "from src.note import NotePoster, parse_product_links" in source
    assert "if product_links:" in source
    assert "manager.mark_needs_review_with_note_url(" in source


def test_cmd_run_source_still_calls_mark_draft_created_for_articles_without_product_links():
    # product_linksが無い記事は、これまで通りmark_draft_created()へ
    # ルーティングされることを確認する(従来の挙動を維持)。
    source = inspect.getsource(cmd_run)
    assert "manager.mark_draft_created(" in source


def test_cmd_run_source_manual_setup_branch_returns_before_mark_draft_created():
    # product_linksブランチ(mark_needs_review_with_note_url)が、
    # mark_draft_created()より前のコードパスにあり、`return 0`で
    # 抜けることを確認する(=product_linksが空の場合だけ
    # mark_draft_created()に到達する、という早期returnの構造の確認)。
    source = inspect.getsource(cmd_run)
    manual_setup_idx = source.index("manager.mark_needs_review_with_note_url(")
    draft_created_idx = source.index("manager.mark_draft_created(")
    assert manual_setup_idx < draft_created_idx

    between = source[manual_setup_idx:draft_created_idx]
    assert "return 0" in between


def test_cmd_run_source_reinterprets_product_links_after_create_draft_succeeds():
    # create_draft()が例外なく戻った後にparse_product_links()を再度呼んで
    # いること(create_draft()の成功結果を前提にルーティングを決めている
    # こと)を確認する。
    source = inspect.getsource(cmd_run)
    create_draft_idx = source.index("poster.create_draft(article)")
    parse_idx = source.index("parse_product_links(article.product_links)")
    assert create_draft_idx < parse_idx


def test_cmd_run_does_not_call_product_link_automation_directly():
    # cmd_run()自体は_apply_product_links()等を直接呼ばない
    # (note操作はcreate_draft()経由でのみ行う、という既存の抽象化を
    # 維持していることの確認)。
    source = inspect.getsource(cmd_run)
    assert "poster._apply_product_links(" not in source
    assert "poster._assert_links_match(" not in source


def test_cmd_run_source_never_calls_publish_related_operations():
    # cmd_run()に公開系操作(投稿する・公開する・予約投稿・公開に進む・
    # publish API)が一切追加されていないことを確認する。
    source = inspect.getsource(cmd_run)
    for keyword in ("投稿する", "公開する", "予約投稿", "公開に進む", "publish"):
        assert keyword not in source
