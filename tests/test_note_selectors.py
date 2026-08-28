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
    poster._cors_notes = []
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


# 「本文は画面上に表示されているのに、noteの内部の文字数カウンタが
# 「0 文字」のまま反映されない」という実機で確認された不具合の再現・検証用。
_COUNTER_HTML = """
<div contenteditable="true" class="editor"></div>
<div id="counter">0 文字</div>
<script>
  document.querySelector('.editor').addEventListener('input', (e) => {
    const len = e.target.innerText.replace(/\\n/g, '').length;
    document.getElementById('counter').textContent = len + ' 文字';
  });
</script>
"""


def test_assert_body_registered_passes_when_counter_updates(page):
    page.set_content(_COUNTER_HTML)
    poster = _bare_poster()
    editor = page.locator(".editor")

    # press_sequentially() は実際のキー入力に近いイベントを発生させるため、
    # ページ側のinputリスナー(=noteの内部状態更新を模したもの)が反応する。
    poster._set_multiline_text(page, editor, "テスト本文です")

    poster._assert_body_registered(page)  # 例外が出なければOK


def test_assert_body_registered_raises_when_counter_stays_zero(page):
    page.set_content(_COUNTER_HTML)
    poster = _bare_poster()

    # inputイベントを発生させずにDOMだけ書き換える(内部状態が更新されない
    # 不具合を模した状態)。画面上は文字が見えても文字数カウンタは0のまま。
    page.evaluate('document.querySelector(".editor").innerText = "テスト本文です"')

    with pytest.raises(NotePosterError, match="0 文字"):
        poster._assert_body_registered(page)


def test_assert_not_publish_action_blocks_publish_labeled_button(page):
    # 「投稿する」は実機確認済みの、実際に記事を公開してしまうボタンの文言。
    page.set_content("<button>投稿する</button>")
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._assert_not_publish_action(page.get_by_role("button"))


def test_assert_not_publish_action_allows_proceed_to_publish_settings_button(page):
    # 「公開に進む」は公開設定パネルを開くだけの画面遷移であり、
    # それ自体は公開しないことを実機で確認済み(投稿するボタンは別)。
    page.set_content("<button>公開に進む</button>")
    poster = _bare_poster()

    poster._assert_not_publish_action(page.get_by_role("button"))  # 例外が出なければOK


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
    assert "APIパスへのレスポンスとCORSヘッダ" in text


def test_diagnostics_text_reports_cors_headers_for_api_responses(page):
    poster = _bare_poster()
    poster._cors_notes = [
        "403 https://note.com/api/v1/text_notes "
        "access-control-allow-origin=(なし) access-control-allow-credentials=(なし)"
    ]
    page.set_content("<div>ダミー</div>")

    text = poster._diagnostics_text(page, step_name="テストステップ")

    assert "403 https://note.com/api/v1/text_notes" in text
    assert "access-control-allow-origin=(なし)" in text


# -- 「公開設定パネル」経由のタグ入力(実機確認済みのUI構造を模したページ) --
#
# 実際のnoteのHTML構造そのものではなく、ユーザーが実機で確認した要素
# (「公開に進む」「ハッシュタグ」見出し・入力欄・「キャンセル」・「投稿する」)
# を最小限のダミーページとして再現し、_fill_tagsのロジック(開く→入力→
# チップ確定確認→キャンセル→再度開いて保持確認)そのものを検証する。
_PUBLISH_SETTINGS_HTML_TEMPLATE = """
<button id="proceed">公開に進む</button>
<div id="panel" style="display:none">
  <h2>ハッシュタグ</h2>
  <input id="tag-input" placeholder="ハッシュタグを追加する" />
  <div id="chips"></div>
  <button id="cancel">キャンセル</button>
  <button id="post">投稿する</button>
</div>
<script>
  document.getElementById('proceed').addEventListener('click', () => {{
    document.getElementById('panel').style.display = 'block';
  }});
  document.getElementById('tag-input').addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') {{
      const span = document.createElement('span');
      span.textContent = '#' + e.target.value;
      document.getElementById('chips').appendChild(span);
      e.target.value = '';
    }}
  }});
  document.getElementById('cancel').addEventListener('click', () => {{
    document.getElementById('panel').style.display = 'none';
    {clear_chips_js}
  }});
</script>
"""


def test_fill_tags_succeeds_when_hashtags_persist_after_cancel(page):
    page.set_content(_PUBLISH_SETTINGS_HTML_TEMPLATE.format(clear_chips_js=""))
    poster = _bare_poster()

    poster._fill_tags(page, ["テスト", "サンプル"])

    chips_text = page.locator("#chips").inner_text()
    assert "#テスト" in chips_text
    assert "#サンプル" in chips_text


def test_fill_tags_raises_when_hashtags_are_discarded_by_cancel(page):
    # 「キャンセル」でタグが失われてしまうケース(未確認だった懸念)を再現。
    page.set_content(
        _PUBLISH_SETTINGS_HTML_TEMPLATE.format(
            clear_chips_js="document.getElementById('chips').innerHTML = '';"
        )
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError, match="見当たりませんでした"):
        poster._fill_tags(page, ["テスト"])


def test_fill_tags_never_clicks_the_post_button(page):
    """安全設計の確認: タグ入力の一連の流れで「投稿する」ボタンは一度も押さない。"""
    page.set_content(_PUBLISH_SETTINGS_HTML_TEMPLATE.format(clear_chips_js=""))
    page.evaluate(
        "document.getElementById('post').addEventListener('click', "
        "() => { window.__posted = true; })"
    )
    poster = _bare_poster()

    poster._fill_tags(page, ["テスト"])

    assert page.evaluate("window.__posted === true") is False
