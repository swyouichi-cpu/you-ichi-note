"""note.py のフォールバック機構(_resolve_locator等)を、実際のnote.comではなく
ローカルの静的HTMLに対して検証するテスト。

note.comへは(この開発環境からもGitHub Actionsからも)アクセスしないため、
「本当にnoteの画面で動くか」はここでは保証できない。ここで保証するのは、
「複数の候補セレクタから正しく要素を見つけ、textarea/contenteditable
どちらにも安全にテキストを入力できる」という仕組み自体の正しさと、
タグ正規化・本文末尾ハッシュタグ組み立てのロジックの正しさ。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

from src.note import (  # noqa: E402
    NotePoster,
    NotePosterError,
    TagValidationError,
    build_body_with_hashtags,
    normalize_tags,
)

_NOTE_SOURCE_PATH = Path(__file__).resolve().parent.parent / "src" / "note.py"


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


@pytest.mark.parametrize("label", ["投稿する", "公開する", "予約投稿", "公開に進む"])
def test_assert_not_publish_action_blocks_all_forbidden_publish_labels(page, label):
    # 本文末尾ハッシュタグ方式への移行後、「公開に進む」ボタンへは一切
    # 遷移しなくなった。誤ってこのボタンがクリック対象になってしまった
    # 場合の保険として、安全装置は「公開に進む」も含めてブロックする。
    page.set_content(f"<button>{label}</button>")
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


# -- 自動保存完了待ち(下書き保存の直前に呼ぶ) -------------------------------


def test_wait_for_autosave_idle_returns_immediately_when_not_saving(page):
    page.set_content("<div>下書き保存</div>")  # 「保存中」という文言は含まない
    poster = _bare_poster()

    poster._wait_for_autosave_idle(page, timeout_ms=1000)  # 例外が出なければOK


def test_wait_for_autosave_idle_waits_until_indicator_disappears(page):
    # 「保存中」が最初は表示されているが、少し経つと消える(=保存完了)ケース。
    page.set_content(
        """
        <span id="saving">保存中</span>
        <script>
          setTimeout(() => { document.getElementById('saving').remove(); }, 200);
        </script>
        """
    )
    poster = _bare_poster()

    poster._wait_for_autosave_idle(page, timeout_ms=3000)  # 例外が出なければOK


def test_wait_for_autosave_idle_raises_when_stuck_saving(page):
    page.set_content('<span id="saving">保存中</span>')  # 消えないまま
    poster = _bare_poster()

    with pytest.raises(NotePosterError, match="保存中"):
        poster._wait_for_autosave_idle(page, timeout_ms=300)


# -- タグ正規化・本文末尾ハッシュタグ組み立て(ブラウザ不要の純粋なロジック) --
#
# 公開設定パネル経由のタグ入力は、note公式の仕様として「キャンセル」で
# 破棄されることが実機検証とnote公式ヘルプの両方で確認されたため撤去した。
# 代わりに、note公式ヘルプが案内する「本文中に #タグ名 と直接書く」方式に
# 統一している。以下はその組み立てロジック(normalize_tags /
# build_body_with_hashtags)の検証。


def test_build_body_with_hashtags_appends_two_tags():
    body = "これは本文です。"

    result = build_body_with_hashtags(body, ["テスト", "自動投稿"])

    assert result == body + ("\n" * 5) + "#テスト #自動投稿"


def test_build_body_with_hashtags_appends_three_or_more_tags():
    body = "本文"

    result = build_body_with_hashtags(body, ["タグ1", "タグ2", "タグ3"])

    assert result == body + ("\n" * 5) + "#タグ1 #タグ2 #タグ3"


def test_build_body_with_hashtags_inserts_exactly_five_line_breaks():
    body = "本文"

    result = build_body_with_hashtags(body, ["テスト"])

    separator = result[len(body) : len(result) - len("#テスト")]
    assert separator == "\n\n\n\n\n"
    assert separator.count("\n") == 5


def test_build_body_with_hashtags_returns_body_unchanged_when_no_tags():
    body = "タグが1つも無い記事の本文です。改行\nも含む。"

    assert build_body_with_hashtags(body, []) == body


def test_normalize_tags_strips_leading_hash():
    assert normalize_tags(["#テスト", "#自動投稿"]) == ["テスト", "自動投稿"]


def test_normalize_tags_trims_surrounding_whitespace():
    assert normalize_tags(["  テスト  ", "\t自動投稿\n"]) == ["テスト", "自動投稿"]


def test_normalize_tags_excludes_empty_tags():
    assert normalize_tags(["テスト", "", "   ", "#"]) == ["テスト"]


def test_normalize_tags_excludes_duplicate_tags():
    assert normalize_tags(["テスト", "テスト", "#テスト"]) == ["テスト"]


def test_normalize_tags_rejects_ambiguous_internal_whitespace_without_altering():
    # 「広島 レモン」のようにタグ内部に空白があるケースは、本文末尾の
    # タグ行が半角スペース区切りのため、どこまでが1つのタグかを安全に
    # 判定できない。自動で「広島レモン」のように空白を詰めて「直す」ことは
    # 絶対にせず、TagValidationErrorを送出してneeds_reviewに倒す。
    with pytest.raises(TagValidationError):
        normalize_tags(["広島 レモン"])


def test_normalize_tags_does_not_collapse_internal_whitespace_even_on_failure():
    # 上のテストで「広島 レモン」が拒否されることを確認しているが、
    # 万一実装が例外を出す前に値を書き換えていないかも明示的に確認する
    # (内部の空白を勝手に詰めた文字列を返してしまわないこと)。
    with pytest.raises(TagValidationError) as exc_info:
        normalize_tags(["広島 レモン"])

    assert "広島 レモン" in str(exc_info.value)
    assert "広島レモン" not in str(exc_info.value)


def test_normalize_tags_ignores_body_content_entirely():
    # 本文中に同じ単語が出現していても、タグの正規化・重複判定には
    # 一切影響しない(normalize_tagsは本文を引数に取らず参照もしない)。
    # 過去に発生した「地の文への部分一致による誤判定」バグの再発防止。
    tags = normalize_tags(["自動投稿"])
    body_with_same_word = "これは自動投稿のテスト記事です。自動投稿という語が複数回登場します。"

    result = build_body_with_hashtags(body_with_same_word, tags)

    assert result == body_with_same_word + ("\n" * 5) + "#自動投稿"


def test_removed_publish_settings_methods_no_longer_exist():
    """公開設定パネル方式の撤去を確認する回帰テスト(誤った復活の防止)。"""
    removed_method_names = [
        "_fill_tags",
        "_open_publish_settings",
        "_close_publish_settings",
        "_hashtag_input_candidates",
        "_hashtag_chip_candidate",
        "_list_visible_hashtag_chips",
        "_enter_hashtags",
        "_assert_hashtags_present",
        "_classify_post_click_state",
        "_visible_dialog_locator",
        "_extract_dialog_text",
    ]
    for name in removed_method_names:
        assert not hasattr(NotePoster, name), f"{name} が復活しています"


def test_publish_related_labels_are_never_used_as_click_selectors():
    """投稿する/公開する/予約投稿/公開に進むが、クリック対象のセレクタ
    (get_by_role/get_by_text/get_by_placeholder/locator)としてnote.py中の
    どこにも使われていないことを確認する。

    _FORBIDDEN_PUBLISH_KEYWORDS のリスト定義そのものにこれらの語が
    含まれるのは意図通り(危険な語を検知するための安全装置)なので、
    その行だけは除外して確認する。
    """
    lines = _NOTE_SOURCE_PATH.read_text(encoding="utf-8").splitlines()
    forbidden_labels = ["投稿する", "公開する", "予約投稿", "公開に進む"]
    selector_call_names = ("get_by_role", "get_by_text", "get_by_placeholder", "locator(")

    for i, line in enumerate(lines, start=1):
        if "_FORBIDDEN_PUBLISH_KEYWORDS" in line:
            continue
        for label in forbidden_labels:
            if label in line and any(call in line for call in selector_call_names):
                pytest.fail(
                    f"note.py:{i} で公開系の文言 '{label}' がセレクタとして"
                    f"使われている可能性があります: {line!r}"
                )
