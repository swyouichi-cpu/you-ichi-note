"""note.py のフォールバック機構(_resolve_locator等)を、実際のnote.comではなく
ローカルの静的HTMLに対して検証するテスト。

note.comへは(この開発環境からもGitHub Actionsからも)アクセスしないため、
「本当にnoteの画面で動くか」はここでは保証できない。ここで保証するのは、
「複数の候補セレクタから正しく要素を見つけ、textarea/contenteditable
どちらにも安全にテキストを入力できる」という仕組み自体の正しさ。
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

from src.note import NotePoster, NotePosterError  # noqa: E402


@pytest.fixture(scope="module")
def page():
    import os

    # このサンドボックス環境ではブラウザが/opt/pw-browsers配下に固定パスで
    # 用意されている場合がある(playwrightパッケージのバージョンとブラウザの
    # リビジョンが一致しないことがあるため)。実行環境(GitHub Actions等)では
    # `playwright install chromium` で入れた通常のブラウザがそのまま使われる。
    fixed_path = "/opt/pw-browsers/chromium"
    launch_kwargs = {"executable_path": fixed_path} if os.path.exists(fixed_path) else {}

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(**launch_kwargs)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwrightのブラウザが利用できないためスキップします: {exc}")
    p = browser.new_page()
    yield p
    browser.close()
    pw.stop()


def _bare_poster() -> NotePoster:
    """__init__(NOTE_STORAGE_STATEの読み込み)を経由せず、内部メソッドだけ使う。"""
    poster = NotePoster.__new__(NotePoster)
    poster._step_count = 0
    poster._console_messages = []
    poster._page_errors = []
    poster._failed_requests = []
    poster._responses = []
    return poster


def test_resolve_locator_finds_title_via_placeholder_fallback(page):
    page.set_content('<textarea placeholder="タイトル"></textarea>')
    poster = _bare_poster()

    locator = poster._resolve_locator(
        page,
        [
            ("role=textbox name=タイトル(存在しない候補)", page.get_by_role("textbox", name="タイトル")),
            ("placeholder=タイトル", page.get_by_placeholder("タイトル", exact=True)),
        ],
        step_name="タイトル入力欄",
    )
    poster._set_single_line_text(locator, "テストタイトル")

    assert page.locator("textarea").input_value() == "テストタイトル"


def test_resolve_locator_raises_when_nothing_matches(page):
    page.set_content("<div>関係ない要素</div>")
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._resolve_locator(
            page,
            [("存在しない候補", page.get_by_placeholder("タイトル"))],
            step_name="タイトル入力欄",
            timeout_ms=300,
        )


def test_set_multiline_text_on_contenteditable(page):
    page.set_content('<div contenteditable="true" class="editor"></div>')
    poster = _bare_poster()
    editor = page.locator(".editor")

    poster._set_multiline_text(page, editor, "1行目\n2行目\n3行目")

    text = editor.inner_text()
    assert "1行目" in text
    assert "2行目" in text
    assert "3行目" in text


def test_assert_not_publish_action_blocks_publish_labeled_button(page):
    page.set_content("<button>公開に進む</button>")
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._assert_not_publish_action(page.get_by_role("button"))


def test_assert_not_publish_action_allows_draft_save_button(page):
    page.set_content("<button>下書き保存</button>")
    poster = _bare_poster()

    poster._assert_not_publish_action(page.get_by_role("button"))  # 例外が出なければOK


def test_wait_for_editor_mounted_succeeds_when_form_fields_present(page):
    page.set_content('<div id="__next"><textarea></textarea></div>')
    poster = _bare_poster()

    poster._wait_for_editor_mounted(page, timeout_ms=1000)  # 例外が出なければOK


def test_wait_for_editor_mounted_raises_when_stuck_on_loading_spinner(page):
    # ローディングスピナーだけが表示され続けている(今回GitHub Actionsで
    # 実際に発生した状況)を模したページ。
    page.set_content('<div id="__next"><div class="spinner"></div></div>')
    poster = _bare_poster()

    with pytest.raises(NotePosterError, match="読み込まれた形跡が確認できません"):
        poster._wait_for_editor_mounted(page, timeout_ms=300)


def test_diagnostics_text_includes_url_title_and_readystate(page):
    page.set_content("<title>診断テスト</title><div>本文サンプル</div>")
    poster = _bare_poster()

    text = poster._diagnostics_text(page, step_name="テストステップ")

    assert "failed_step: テストステップ" in text
    assert "page.url()" in text
    assert "document.readyState" in text
    assert "本文サンプル" in text
