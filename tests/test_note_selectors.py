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

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import Locator as PlaywrightLocator  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from src.models import Article
from src.note import (  # noqa: E402
    LinkButtonObservationStop,
    LinkButtonOutOfViewportError,
    NotePoster,
    NotePosterError,
    ProductLink,
    ProductLinkBlockOutOfViewportError,
    ProductLinkValidationError,
    TagValidationError,
    UrlApplyObservationStop,
    UrlInputDisappearedObservationStop,
    UrlInputObservationStop,
    _bounding_box_within_viewport,
    _normalize_whitespace,
    build_body_with_hashtags,
    build_product_links_trailer,
    normalize_tags,
    parse_product_links,
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


# -- 本文入力欄の誤検出防止・read-back検証(実機の重大な不具合への対処) ------
#
# GitHub Actions Content Pipeline #16で、パイプライン自体は"success"で
# 終了したにもかかわらず、実際のnote下書きの本文が完全に空になるという
# 重大な不具合が発生した。原因は、本文editorの候補セレクタが全滅した際の
# 「画面上に見えている最初のcontenteditableを無条件に使う」という位置
# ベースのフォールバックが、本文editorではない別の要素(タイトル入力欄)を
# 誤って掴んでしまったこと。以下はその再発防止(誤検出の禁止・read-back
# 検証)を確認するテスト。


def test_normalize_whitespace_removes_all_whitespace_including_newlines():
    assert _normalize_whitespace("a\n\n\nb c\td\n") == "abcd"


def test_same_element_true_for_identical_locator(page):
    page.set_content('<div id="x" contenteditable="true"></div>')
    poster = _bare_poster()
    loc = page.locator("#x")

    assert poster._same_element(page, loc, loc) is True


def test_same_element_false_for_different_elements(page):
    page.set_content(
        '<div id="a" contenteditable="true"></div>'
        '<div id="b" contenteditable="true"></div>'
    )
    poster = _bare_poster()

    assert poster._same_element(page, page.locator("#a"), page.locator("#b")) is False


def test_fill_title_returns_the_resolved_locator(page):
    page.set_content('<textarea placeholder="タイトル"></textarea>')
    poster = _bare_poster()

    locator = poster._fill_title(page, "テストタイトル")

    assert locator.input_value() == "テストタイトル"


def test_fill_body_raises_when_no_body_editor_candidate_matches(page):
    """本文editorであることに根拠のある候補(role=textbox name=本文、
    class名にbody/editorを含むcontenteditable)がいずれも一致しない場合、
    以前は位置ベースの最終手段(画面上の最初のcontenteditable)に
    フォールバックしていたが、この挙動が実機での本文消失事故の原因と
    なったため撤去した。候補が全滅した場合は入力せずNotePosterErrorで
    中断する(呼び出し側でneeds_reviewに倒れる)。
    """
    page.set_content('<div id="unrelated" contenteditable="true"></div>')
    poster = _bare_poster()
    title_locator = page.locator("#unrelated")

    with pytest.raises(NotePosterError):
        poster._fill_body(page, "本文テキスト", title_locator=title_locator)


def test_fill_body_raises_when_resolved_element_is_same_as_title(page):
    """本文用の候補セレクタが、たまたまタイトル欄自身にも一致してしまう
    (本文editorを正しく特定できていない)状況を再現する。
    """
    page.set_content(
        '<div class="body-editor"><div id="title-and-body" contenteditable="true">'
        "</div></div>"
    )
    poster = _bare_poster()
    title_locator = page.locator("#title-and-body")

    with pytest.raises(NotePosterError, match="タイトル入力欄と同一"):
        poster._fill_body(page, "本文テキスト", title_locator=title_locator)


def test_fill_body_succeeds_when_body_editor_is_distinct_from_title(page):
    page.set_content(
        '<div id="title" contenteditable="true"></div>'
        '<div class="body-editor"><div id="body" contenteditable="true"></div></div>'
    )
    poster = _bare_poster()
    title_locator = page.locator("#title")

    body_locator = poster._fill_body(page, "本文テキスト", title_locator=title_locator)

    assert body_locator.get_attribute("id") == "body"
    assert "本文テキスト" in body_locator.inner_text()


def test_fill_body_source_has_no_positional_fallback_candidates():
    """本文editorであることを保証できない位置ベースのフォールバック
    (「最初の/2番目のcontenteditable」)が復活していないことを確認する
    回帰テスト。
    """
    import inspect

    source = inspect.getsource(NotePoster._fill_body)
    assert "nth(1)" not in source
    assert "最終手段" not in source


def test_assert_body_matches_passes_when_locator_contains_expected_text(page):
    page.set_content('<div contenteditable="true" class="editor"></div>')
    poster = _bare_poster()
    editor = page.locator(".editor")
    body = "本文の内容です。"
    expected = build_body_with_hashtags(body, ["テスト", "自動投稿"])
    poster._set_multiline_text(page, editor, expected)

    poster._assert_body_matches(
        page, editor, expected, "#テスト #自動投稿", stage="保存前"
    )  # 例外が出なければOK


def test_assert_body_matches_ignores_newline_representation_differences(page):
    # 実際のcontenteditableは複数行の内容を<div>等で表現することがあり、
    # inner_text()の改行の量が入力時の想定と厳密には一致しないことがある。
    # 空白文字の表現差では不一致と判定しないことを確認する。
    page.set_content(
        '<div contenteditable="true" class="editor">'
        "<div>本文</div><div>2行目</div>"
        "</div>"
    )
    poster = _bare_poster()
    editor = page.locator(".editor")
    expected = "本文\n\n\n2行目"  # 実際の改行量とは異なる期待値

    poster._assert_body_matches(page, editor, expected, "", stage="保存前")  # 例外が出なければOK


def test_assert_body_matches_raises_when_content_does_not_match(page):
    page.set_content('<div contenteditable="true" class="editor"></div>')
    poster = _bare_poster()
    editor = page.locator(".editor")
    poster._set_multiline_text(page, editor, "違う内容")

    with pytest.raises(NotePosterError, match="一致しませんでした"):
        poster._assert_body_matches(page, editor, "期待していた本文", "", stage="保存前")


def test_assert_body_matches_error_reports_head_tail_hashtag_details(page):
    page.set_content('<div contenteditable="true" class="editor"></div>')
    poster = _bare_poster()
    editor = page.locator(".editor")
    # 先頭は入力されているが、末尾のタグ行が欠けている状況を再現する。
    poster._set_multiline_text(page, editor, "期待した本文の先頭部分だけです")

    expected_body = "期待した本文の先頭部分だけです" + ("\n" * 5) + "#テスト"
    with pytest.raises(NotePosterError) as exc_info:
        poster._assert_body_matches(page, editor, expected_body, "#テスト", stage="保存前")

    message = str(exc_info.value)
    assert "先頭一致=True" in message
    assert "末尾一致=False" in message
    assert "タグ行一致=False" in message


def test_run_step_returns_actions_return_value(page):
    poster = _bare_poster()

    result = poster._run_step(page, "テストステップ", lambda: 42)

    assert result == 42


def test_full_body_flow_detects_content_loss_after_save_without_ever_publishing(page):
    """本文editorの特定→入力→read-back→保存→保存後read-back、という
    一連の流れを模したページに対するテスト。保存によって本文が失われる
    状況(実機で観測された不具合の一種)を再現し、保存後のread-back検証で
    正しく検知できること、また一連の流れの中で「投稿する」ボタンが
    一度もクリックされないことを確認する。
    """
    page.set_content(
        """
        <textarea placeholder="タイトル"></textarea>
        <div class="body-editor">
          <div id="body" contenteditable="true"></div>
        </div>
        <button id="save">下書き保存</button>
        <button id="post">投稿する</button>
        <script>
          window.__posted = false;
          document.getElementById('post').addEventListener('click', () => {
            window.__posted = true;
          });
          // 保存を押すと本文が失われる状況を再現する。
          document.getElementById('save').addEventListener('click', () => {
            document.getElementById('body').innerText = '';
          });
        </script>
        """
    )
    poster = _bare_poster()

    title_locator = poster._fill_title(page, "タイトル")
    body_text = "本文" + ("\n" * 5) + "#テスト"
    body_locator = poster._fill_body(page, body_text, title_locator=title_locator)

    # 保存前のread-backは成功するはず。
    poster._assert_body_matches(page, body_locator, body_text, "#テスト", stage="保存前")

    page.get_by_role("button", name="下書き保存").click()

    # 保存後のread-backでは、内容が失われたことを検知して例外になるはず。
    with pytest.raises(NotePosterError, match="一致しませんでした"):
        poster._assert_body_matches(page, body_locator, body_text, "#テスト", stage="保存後")

    # この一連の流れで「投稿する」は一度もクリックされていない。
    assert page.evaluate("window.__posted") is False


# -- 実機DOM構造(Content Pipeline #18で判明)に基づく回帰テスト -------------
#
# 実機の失敗時HTMLダンプから、本文editorの実際のDOM構造が判明した。
# タイトルは <textarea placeholder="記事タイトル">、本文はProseMirror製の
# contenteditableで、class="ProseMirror note-common-styles__textnote-body"
# role="textbox" aria-multiline="true" data-placeholder="..." を持つ。
# 以下はこの実機DOM構造に対する回帰テスト。

_REAL_EDITOR_HTML = """
<textarea placeholder="記事タイトル"></textarea>
<div
  contenteditable="true"
  translate="no"
  class="ProseMirror note-common-styles__textnote-body"
  role="textbox"
  aria-multiline="true"
  data-placeholder="たのしかった旅行について、書いてみませんか？">
</div>
"""


def test_fill_body_finds_real_prosemirror_body_editor(page):
    page.set_content(_REAL_EDITOR_HTML)
    poster = _bare_poster()

    title_locator = poster._fill_title(page, "自動投稿テスト")
    body_locator = poster._fill_body(page, "本文テキスト", title_locator=title_locator)

    assert poster._same_element(page, title_locator, body_locator) is False
    assert "ProseMirror" in (body_locator.get_attribute("class") or "")
    assert "本文テキスト" in body_locator.inner_text()


def test_fill_title_and_fill_body_target_distinct_real_dom_elements(page):
    page.set_content(_REAL_EDITOR_HTML)
    poster = _bare_poster()

    title_locator = poster._fill_title(page, "自動投稿テスト")
    body_locator = poster._fill_body(page, "本文テキスト", title_locator=title_locator)

    # タイトルはtextarea、本文はProseMirrorのcontenteditableへ入力されて
    # いる(どちらか一方に両方の文字列が混ざっていないことを確認する)。
    assert page.locator("textarea").input_value() == "自動投稿テスト"
    assert "自動投稿テスト" not in body_locator.inner_text()


def test_full_body_flow_with_real_dom_structure_verifies_readback_before_and_after_save(page):
    """実機DOM構造(ProseMirror)を模したページで、本文入力→保存前read-back→
    下書き保存→保存後read-backの一連の流れが正しく機能することを確認する。
    """
    page.set_content(
        _REAL_EDITOR_HTML
        + """
        <button id="save">下書き保存</button>
        <button id="post">投稿する</button>
        <script>
          window.__posted = false;
          document.getElementById('post').addEventListener('click', () => {
            window.__posted = true;
          });
        </script>
        """
    )
    poster = _bare_poster()

    title_locator = poster._fill_title(page, "自動投稿テスト")
    body_text = "本文" + ("\n" * 5) + "#テスト"
    body_locator = poster._fill_body(page, body_text, title_locator=title_locator)

    poster._assert_body_matches(page, body_locator, body_text, "#テスト", stage="保存前")

    page.get_by_role("button", name="下書き保存").click()

    # このダミーページでは保存によって本文が失われないため、保存後の
    # read-backも成功するはず。
    poster._assert_body_matches(page, body_locator, body_text, "#テスト", stage="保存後")

    assert page.evaluate("window.__posted") is False


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


# -- 商品導線(本文末尾のテキストリンク)のロジック(ブラウザ不要) -------------
#
# 実機テストで、本文中にECサイトの生URLを置いたところnoteが自動的に
# 商品カードへ変換し、read-back検証が(正しく)不一致を検出して安全停止した。
# 対策として、ECの生URLを本文の文字列としては一切登場させず、「→ 商品を
# 見る」という固定文言だけにインラインリンクを設定する方式に変更した。
# 以下はその組み立て・解析ロジック(parse_product_links /
# build_product_links_trailer / build_body_with_hashtagsへの統合)の検証。


def test_parse_product_links_empty_string_returns_empty_list():
    assert parse_product_links("") == []
    assert parse_product_links("   ") == []


def test_parse_product_links_empty_array_returns_empty_list():
    assert parse_product_links("[]") == []


def test_parse_product_links_parses_single_entry():
    raw = '[{"label": "TOY JAM 瀬戸内レモン", "url": "https://you-ichi.jp/?pid=192116331"}]'

    result = parse_product_links(raw)

    assert result == [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331")
    ]


def test_parse_product_links_parses_multiple_entries_preserving_order():
    raw = (
        '[{"label": "商品A", "url": "https://example.com/a"}, '
        '{"label": "商品B", "url": "https://example.com/b"}]'
    )

    result = parse_product_links(raw)

    assert result == [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]


def test_parse_product_links_trims_surrounding_whitespace_only():
    raw = '[{"label": "  商品A  ", "url": "  https://example.com/a  "}]'

    result = parse_product_links(raw)

    assert result == [ProductLink(label="商品A", url="https://example.com/a")]


def test_parse_product_links_rejects_malformed_json():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links("{not valid json")


def test_parse_product_links_rejects_non_array_top_level():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('{"label": "商品A", "url": "https://example.com/a"}')


def test_parse_product_links_rejects_non_object_element():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('["not-an-object"]')


def test_parse_product_links_rejects_missing_label():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('[{"url": "https://example.com/a"}]')


def test_parse_product_links_rejects_missing_url():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('[{"label": "商品A"}]')


def test_parse_product_links_rejects_empty_label():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('[{"label": "   ", "url": "https://example.com/a"}]')


def test_parse_product_links_rejects_url_without_scheme():
    # 自動で "https://" を補完する等の推測修正は行わず、安全に停止する。
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('[{"label": "商品A", "url": "example.com/a"}]')


def test_parse_product_links_rejects_unsupported_scheme():
    with pytest.raises(ProductLinkValidationError):
        parse_product_links('[{"label": "商品A", "url": "ftp://example.com/a"}]')


def test_build_product_links_trailer_single_entry():
    trailer = build_product_links_trailer(
        [ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331")]
    )

    assert trailer == (
        "この記事に出てきた商品" + ("\n" * 5) + "TOY JAM 瀬戸内レモン" + "\n" + "→ 商品を見る"
    )


def test_build_product_links_trailer_multiple_entries():
    trailer = build_product_links_trailer(
        [
            ProductLink(label="商品A", url="https://example.com/a"),
            ProductLink(label="商品B", url="https://example.com/b"),
        ]
    )

    assert trailer == (
        "この記事に出てきた商品"
        + ("\n" * 5)
        + "商品A"
        + "\n"
        + "→ 商品を見る"
        + ("\n" * 5)
        + "商品B"
        + "\n"
        + "→ 商品を見る"
    )


def test_build_product_links_trailer_heading_is_generic_not_article_specific():
    # ARTICLE-001向けの「この記事に出てきたジャム」のような特定記事専用の
    # 文言をハードコードしていないことを確認する(汎用設計の要件)。
    trailer = build_product_links_trailer(
        [ProductLink(label="任意の商品", url="https://example.com/x")]
    )

    assert "ジャム" not in trailer
    assert "この記事に出てきた商品" in trailer


def test_build_body_with_hashtags_appends_product_links_trailer_without_tags():
    body = "本文"
    links = [ProductLink(label="商品A", url="https://example.com/a")]

    result = build_body_with_hashtags(body, [], links)

    assert result == body + ("\n" * 5) + build_product_links_trailer(links)


def test_build_body_with_hashtags_orders_body_then_links_then_tags():
    body = "本文"
    links = [ProductLink(label="商品A", url="https://example.com/a")]

    result = build_body_with_hashtags(body, ["テスト"], links)

    assert result == (
        body
        + ("\n" * 5)
        + build_product_links_trailer(links)
        + ("\n" * 5)
        + "#テスト"
    )


def test_build_body_with_hashtags_never_includes_raw_url_text():
    # ECの生URLは、この関数の戻り値のどこにも文字列として含まれてはいけない
    # (URLはhref属性としてのみ、_apply_product_links()が別途設定する)。
    body = "本文"
    links = [
        ProductLink(label="商品A", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(label="商品B", url="https://you-ichi.jp/?pid=191552342"),
    ]

    result = build_body_with_hashtags(body, ["テスト"], links)

    for link in links:
        assert link.url not in result


def test_build_body_with_hashtags_with_no_links_and_no_tags_is_unchanged():
    body = "タグも商品導線も無い記事の本文です。"

    assert build_body_with_hashtags(body, [], []) == body
    assert build_body_with_hashtags(body, [], None) == body


def test_article_from_record_reads_product_links_and_defaults_to_empty():
    without_column = Article.from_record(2, {"id": "a1"})
    assert without_column.product_links == ""

    raw = '[{"label": "商品A", "url": "https://example.com/a"}]'
    with_column = Article.from_record(2, {"id": "a1", "product_links": raw})
    assert with_column.product_links == raw


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


# -- 商品導線リンクの設定・検証(実機Artifact確認済みのツールバーボタン方式、
#    観測専用実装) -------------------------------------------------------
#
# 実機のGitHub Actions実行(TEST-004)で、note公式の「エディタのガイド」を
# パースした結果、リンク挿入はツールバーの「ボタン」一覧には存在するが、
# 「キーボードショートカット」一覧には存在しないことが判明した
# (Control+K/Meta+Kは実機で無反応だった)。そのためショートカット方式は
# 撤去し、追加で取得した実機Artifact(04/05/06のHTMLダンプ)で確認できた
# 以下のフローティング編集ツールバーのDOM構造を使う方式に切り替えた。
#
#   <div data-active="true" role="toolbar" id="desktop-toolbar">
#     ...
#     <button aria-label="リンク">...</button>
#     ...
#   </div>
#
# ただし、このリンクボタンをクリックした後に実際にどのようなURL入力UIが
# 出現するかはまだ実機で観測できていないため、今回は「クリックした直後に
# 意図的に安全停止する」観測専用の実装になっている
# (_set_link_on_text_occurrence / LinkButtonObservationStop)。
#
# 商品名(label)と「→ 商品を見る」のHTML構造は、実機のGitHub Actions実行
# (TEST-004)で、noteのエディタが別々の<p>要素にはせず、同一の<p>要素内に
# <br>を挟んで描画すること(<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>)が
# 判明したため、以下のフィクスチャはすべてこの実機DOM構造を再現している。

_LINK_TOOLBAR_HTML = """
<div class="editor" contenteditable="true">
  <p>本文</p>
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
</div>
<div data-active="true" role="toolbar" id="desktop-toolbar">
  <button aria-label="AIアシスタント">AI</button>
  <button aria-label="太字">B</button>
  <button aria-label="リンク">リンク</button>
  <button aria-label="引用">引用</button>
</div>
<script>
  window.__urlInputKeys = [];
  document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
    .addEventListener('click', (e) => {
      e.target.setAttribute('data-clicked', 'true');
      // 実機で確認できたURL入力UI(TEST-004の追加観測)を、実機と同じく
      // active toolbar(#desktop-toolbar)の内部に再現する。
      const toolbar = document.getElementById('desktop-toolbar');
      const textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      textarea.addEventListener('keydown', (ev) => {
        window.__urlInputKeys.push(ev.key);
      });
      const applyButton = document.createElement('button');
      applyButton.setAttribute('data-name', 'Button');
      applyButton.setAttribute('type', 'button');
      applyButton.id = ':r16:';
      const applySpan = document.createElement('span');
      applySpan.textContent = '適用';
      applyButton.appendChild(applySpan);
      applyButton.addEventListener('click', (ev) => {
        ev.target.closest('button').setAttribute('data-clicked', 'true');
        // 実機Artifact(TEST-004、「適用」クリック後の観測)で確認できた
        // 完成後のDOM(<a href="..."><span class="highlight">→ 商品を見る
        // </span></a>)をローカルで再現する。「適用」クリック後はURL入力欄
        // と「適用」ボタン自体もDOMから消え、通常の選択ツールバーへ戻る。
        const url = textarea.value;
        const targetP = Array.from(
          document.querySelectorAll('.editor p')
        ).find((el) => el.textContent.includes('瀬戸内レモン'));
        const textNode = targetP && Array.from(targetP.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE
            && node.textContent.trim() === '→ 商品を見る'
        );
        if (textNode) {
          const a = document.createElement('a');
          a.setAttribute('href', url);
          a.setAttribute('target', '_blank');
          a.setAttribute('rel', 'noopener');
          const span = document.createElement('span');
          span.className = 'highlight';
          span.textContent = '→ 商品を見る';
          a.appendChild(span);
          textNode.replaceWith(a);
        }
        textarea.remove();
        applyButton.remove();
        cancelButton.remove();
      });
      const cancelButton = document.createElement('button');
      cancelButton.setAttribute('aria-label', 'URLの入力をやめる');
      toolbar.appendChild(textarea);
      toolbar.appendChild(applyButton);
      toolbar.appendChild(cancelButton);
    });
</script>
"""

_URL_INPUT_SELECTOR_FOR_TESTS = 'textarea[placeholder="https://"][inputmode="text"][name="alt"]'
_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS = 'div[role="toolbar"][data-active="true"]'


def test_apply_product_links_clicks_toolbar_button_and_inputs_url_then_applies_link(page):
    # リンクボタンをクリックした先に出現するURL入力欄まで到達し、URLを
    # 入力してread-backが一致することを確認したうえで、ツールバー内の
    # 「適用」ボタンをクリックし、実際に<a>要素が対象ブロック内へ反映
    # されるまで待ってから正常終了することを確認する(完成実装・第6段階、
    # 2026年8月29日)。URLの確定操作のうちEnter/Tab/フォーカス解除/他要素
    # クリックは一切行わない(「適用」ボタンのクリックだけが、実機で
    # 確認できた確定操作の一部として実装されている)。
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]

    poster._apply_product_links(page, body_locator, links)  # 例外が出なければOK

    link_button = page.locator('#desktop-toolbar button[aria-label="リンク"]')
    assert link_button.get_attribute("data-clicked") == "true"

    anchor = body_locator.locator("a")
    assert anchor.count() == 1
    assert anchor.inner_text().strip() == "→ 商品を見る"
    assert anchor.get_attribute("href") == links[0].url
    # 「適用」クリック後、実機と同様にURL入力欄・「適用」ボタンはDOMから
    # 消えていること。
    assert page.locator(_URL_INPUT_SELECTOR_FOR_TESTS).count() == 0
    assert (
        page.locator("#desktop-toolbar").get_by_role("button", name="適用", exact=True).count()
        == 0
    )
    # Enter/Tabを送信していないことを確認する(fill()はキーイベントを
    # 発生させないため、keydownを記録するwindow.__urlInputKeysは空のまま
    # のはずである)。
    sent_keys = page.evaluate("() => window.__urlInputKeys")
    assert sent_keys == []
    assert "Enter" not in sent_keys
    assert "Tab" not in sent_keys


def test_apply_product_links_logs_url_input_stage_diagnostics_through_healthy_flow(page, caplog):
    # URL入力欄が消失せず正常に進む場合、_log_url_input_diagnostics()による
    # 各段階(A〜E, G)のログが1回の呼び出しの中ですべて記録され、消失検知
    # 段階(F)のログは出力されないことを確認する(2026年8月29日、実機で
    # 同一commitが成功/失敗の両方を示した後に追加した診断強化)。
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]

    with caplog.at_level("INFO"):
        poster._apply_product_links(page, body_locator, links)  # 例外が出なければOK

    for stage in (
        "A_URL入力直前",
        "D_fill完了直後",
        "E_read-back直前",
        "G_read-back成功後",
    ):
        assert f"診断[{stage}]" in caplog.text
    assert "診断[F_textarea消失検知]" not in caplog.text


def test_apply_product_links_is_noop_when_no_links():
    poster = _bare_poster()
    # ページを用意せずとも、product_links が空なら何もせず正常終了するはず。
    poster._apply_product_links(
        page=None, body_locator=None, product_links=[]
    )  # 例外が出なければOK


def test_apply_product_links_raises_when_label_block_not_found(page):
    # product_linksに商品Bが含まれているのに、本文中に商品Bを含むブロックが
    # 存在しない場合、どのブロックがどのリンクに対応するか一意に定まらない
    # ため安全停止する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]

    with pytest.raises(NotePosterError):
        poster._apply_product_links(page, body_locator, links)


# -- _find_product_link_block(商品名を含むブロックの一意特定、TEST-004対応) --
#
# 実機DOM(TEST-004)で、商品名(label)と「→ 商品を見る」が別々の<p>では
# なく同一の<p>内に<br>を挟んで存在することが判明したため、「商品名の
# 直後の兄弟要素」ではなく「商品名と『→ 商品を見る』の両方を含む
# ブロック」として一意に特定する方式に変更した。


def test_find_product_link_block_finds_the_correct_block_for_each_label(page):
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "<p>商品B<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block_a = poster._find_product_link_block(
        page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
    )
    block_b = poster._find_product_link_block(
        page, body_locator, ProductLink(label="商品B", url="https://example.com/b")
    )

    assert block_a.inner_text().strip() == "商品A\n→ 商品を見る"
    assert block_b.inner_text().strip() == "商品B\n→ 商品を見る"
    assert poster._same_element(page, block_a, block_b) is False


def test_find_product_link_block_ignores_label_only_occurrence_elsewhere_in_body(page):
    # 商品名だけの行が商品導線と無関係な場所(通常の本文)にたまたま出現
    # しても、直後に「→ 商品を見る」が続かないため候補にはせず、本来の
    # 商品導線ブロックだけを一意に特定できることを確認する(2026年8月29日、
    # ARTICLE-001の実機実行を踏まえた隣接2行判定への修正)。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>商品A</p>"
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
    )

    assert block.inner_text().strip() == "商品A\n→ 商品を見る"


def test_find_product_link_block_raises_when_adjacent_pair_appears_in_multiple_blocks(page):
    # 商品名の直後に「→ 商品を見る」が続く、構造上有効な商品導線ブロックが
    # 同じ商品名で複数件あった場合(本物の曖昧さ)は、これまで通り推測せず
    # 中断する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_find_product_link_block_raises_when_block_has_no_link_text(page):
    # 商品名を含むブロックはあるが、その中に「→ 商品を見る」が無い
    # (構造が想定と異なる)場合は推測せず中断する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_find_product_link_block_raises_when_link_text_appears_twice_in_block(page):
    # 同一ブロック内に「→ 商品を見る」が複数回出現する場合も、どちらを
    # 対象にすべきか一意に定まらないため中断する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_find_product_link_block_raises_when_block_has_unexpected_extra_line(page):
    # ブロック内の行構成が [label, リンク対象] のちょうど2行と異なる場合
    # (余計な行がある等)、構造が想定と異なるため推測せず中断する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る<br>おまけ</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_find_product_link_block_raises_when_no_block_contains_the_label(page):
    # 商品名を含むブロックがそもそも本文editor内に1つも無い場合。
    page.set_content('<div class="editor" contenteditable="true"></div>')
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
        )


# -- ARTICLE-001の実機実行で判明した、商品名が別商品名のprefixになっている ---
# ケースの回帰テスト(2026年8月29日)。「TOY JAM 瀬戸内レモン」と「TOY JAM
# 瀬戸内レモン月桂樹」のように、一方の商品名がもう一方の商品名の先頭部分と
# 完全に一致する場合でも、行単位の完全一致判定により誤って両方にマッチしない
# ことを確認する。


def test_find_product_link_block_does_not_match_when_label_is_a_prefix_of_another_products_name(
    page,
):
    # ARTICLE-001相当のケース: 短い商品名が、別の商品(長い商品名)の
    # ブロックの1行目の先頭部分と一致していても、行単位の完全一致でなければ
    # そのブロックを誤って候補にしないことを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "<p>TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )

    assert block.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"


def test_find_product_link_block_resolves_each_label_to_its_own_block_when_one_is_a_prefix(
    page,
):
    # ARTICLE-001相当のケース: 2商品それぞれについて_find_product_link_
    # block()を呼んだとき、それぞれ正しい(取り違えていない)ブロックが
    # 返ることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "<p>TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block_short = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )
    block_long = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    )

    assert block_short.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"
    assert block_long.inner_text().strip() == "TOY JAM 瀬戸内レモン月桂樹\n→ 商品を見る"
    assert poster._same_element(page, block_short, block_long) is False


def test_find_product_link_block_ignores_label_mentioned_in_ordinary_prose_article001(page):
    # ARTICLE-001相当のケース(2026年8月29日、隣接2行判定への修正):
    # 本文中のふつうの文章側に商品名だけの行がたまたま出現しても、
    # 直後に「→ 商品を見る」が続かないため商品導線ブロックとしては拾わず、
    # 本来の商品導線ブロック(商品名の直後に「→ 商品を見る」が続く)だけを
    # 商品ごとに正しく一意特定できることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>今、その感覚につながっているマーマレードが2つある。</p>"
        "<p>TOY JAM 瀬戸内レモン</p>"
        "<p>について考えている。</p>"
        "<p>この記事に出てきたジャム</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "<p>TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block_short = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )
    block_long = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    )

    assert block_short.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"
    assert block_long.inner_text().strip() == "TOY JAM 瀬戸内レモン月桂樹\n→ 商品を見る"
    assert poster._same_element(page, block_short, block_long) is False


def test_find_product_link_block_logs_candidate_index_and_lines_on_success(page, caplog):
    # 一意に特定できた場合も、候補ブロックのindexと正規化済みlinesを
    # 診断ログへ記録することを確認する(次回の実機実行で失敗しても1回で
    # 原因を切り分けられるようにするため)。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with caplog.at_level("INFO"):
        poster._find_product_link_block(
            page,
            body_locator,
            ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        )

    assert "商品導線ブロック候補" in caplog.text
    assert "'block_index': 1" in caplog.text
    assert "'lines': ['TOY JAM 瀬戸内レモン', '→ 商品を見る']" in caplog.text


def test_find_product_link_block_logs_all_candidates_when_ambiguous(page, caplog):
    # 候補が複数件で安全停止する場合も、それぞれの候補のindexとlinesが
    # 診断ログに記録されることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "<p>商品A<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with caplog.at_level("INFO"):
        with pytest.raises(NotePosterError):
            poster._find_product_link_block(
                page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
            )

    assert "'block_index': 1" in caplog.text
    assert "'block_index': 2" in caplog.text


def test_find_product_link_block_does_not_log_when_no_candidate_found(page, caplog):
    # 候補が0件の場合は「候補ブロック」について記録すべき情報が無いため、
    # 候補ログ自体は出力されないことを確認する(0件であることは通常の
    # NotePosterErrorのメッセージで報告される)。
    page.set_content('<div class="editor" contenteditable="true"></div>')
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with caplog.at_level("INFO"):
        with pytest.raises(NotePosterError):
            poster._find_product_link_block(
                page, body_locator, ProductLink(label="商品A", url="https://example.com/a")
            )

    assert "商品導線ブロック候補" not in caplog.text


# -- direct child(:scope > p)へのスコープ限定(ARTICLE-001の実機Artifact直接 --
# 解析を踏まえた修正、2026年8月29日)。本文editor配下には、ネストした
# (direct childではない)descendant要素として同じ[label, "→ 商品を見る"]の
# パターンが偶然出現しうる(実機で確認済み)ため、`body_locator.locator("p")`
# (descendantまで拾う)ではなく`body_locator.locator(":scope > p")`
# (direct childのみ)に限定して候補探索する。


def test_find_product_link_block_ignores_nested_descendant_p_not_a_direct_child(page):
    # ARTICLE-001相当のケース: 本文editorの直接の子ではない、ネストした
    # (別階層の)要素の中にも同じ[label, "→ 商品を見る"]のパターンが
    # 存在する場合でも、direct childの商品導線ブロック1件だけが候補になる
    # ことを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<div class="hidden-mirror">'
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"  # direct childではない(ネスト)
        "</div>"
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"  # 本物のdirect child
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )

    assert block.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"
    # direct childのpであることを確認する(ネストしたものではない)。
    assert page.evaluate(
        "(el) => el.parentElement.classList.contains('editor')", block.element_handle()
    )


def test_find_product_link_block_raises_when_only_a_nested_descendant_matches(page):
    # direct childには有効な商品導線が無く、descendant(ネストした要素)にしか
    # 一致するpが無い場合は、0件として安全停止することを確認する
    # (descendantまで拾ってしまう旧実装への回帰防止)。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<div class="hidden-mirror">'
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
        "<p>この記事に出てきた商品</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page,
            body_locator,
            ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a"),
        )


def test_find_product_link_block_diagnostic_log_uses_direct_child_indices_only(page, caplog):
    # 診断ログに記録されるblock_indexは、direct child(:scope > p)として
    # 評価した際のインデックスであり、descendant(ネストしたp)は候補にも
    # ログにも含まれないことを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<div class="hidden-mirror">'
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with caplog.at_level("INFO"):
        poster._find_product_link_block(
            page,
            body_locator,
            ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        )

    # direct childのp列挙(:scope > p)では [この記事に出てきた商品, 商品導線]
    # の2件のみが対象になり、商品導線はそのうちindex=1である。
    assert "'block_index': 1" in caplog.text
    # ログの内容が1件の候補だけを含み、ネストしたdescendantの分は含まれて
    # いない(=候補が1件だけなので複数件のあいまいエラーにもならない)こと。
    assert "'block_index': 2" not in caplog.text


def test_find_product_link_block_source_scopes_to_direct_children_only():
    """商品導線ブロックの探索が、descendantまで拾う通常のCSSセレクタ
    (`body_locator.locator("p")`)ではなく、`:scope > p`によるdirect
    childへの明示的なスコープ限定であることをソースから確認する回帰
    テスト(2026年8月29日、ARTICLE-001の実機Artifact直接解析を踏まえた
    修正)。docstringの説明文中にも`locator("p")`という字面が登場する
    ため、docstringを文字列置換で除去するのではなく、実際に`blocks`へ
    代入している行を正規表現で直接取り出して判定する。
    """
    import inspect
    import re

    source = inspect.getsource(NotePoster._find_product_link_block)
    match = re.search(r'blocks = body_locator\.locator\(([^)]*)\)', source)
    assert match is not None, "blocks = body_locator.locator(...) の行が見つかりません"
    assert match.group(1) == '":scope > p"'


# -- ブロック全文の完全一致への厳密化(ARTICLE-001の実機再実行Artifactを ------
# 踏まえた再修正、2026年8月29日)。direct child(:scope > p)への限定後も、
# 記事本文ほぼ全体を含む巨大なdirect-child <p>の**途中**に商品導線と同じ
# [label, "→ 商品を見る"]の隣接2行がたまたま含まれていたため、「ブロック内
# のどこかに隣接2行が存在すれば候補」という判定では巨大なpも候補になって
# しまっていた。候補条件を「ブロックのlines全体がちょうど[label, "→ 商品を
# 見る"]の2行だけで構成されているか」に厳密化した。


def test_find_product_link_block_ignores_huge_block_that_merely_contains_the_pair_midway(page):
    # ARTICLE-001相当のケース: 記事本文ほぼ全体を含む巨大なdirect-child <p>
    # の途中に[label, CTA]の隣接2行が含まれていても、そのブロック全体は
    # [label, CTA]の2行だけでは構成されていないため候補にせず、商品導線
    # 専用の別ブロックだけを一意に特定できることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>記事本文の書き出しです。<br>"
        "TOY JAM 瀬戸内レモン<br>"
        "→ 商品を見る<br>"
        "締めの文章です。</p>"
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )

    assert block.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"


def test_find_product_link_block_raises_when_only_the_huge_block_contains_the_pair(page):
    # 巨大なdirect-child <p>の途中にしか[label, CTA]が存在せず、商品導線
    # 専用のブロックが存在しない場合は、候補0件として安全停止することを
    # 確認する(巨大なpを誤って候補にしない=巨大pを商品導線として採用
    # しないことの確認)。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>記事本文の書き出しです。<br>"
        "TOY JAM 瀬戸内レモン<br>"
        "→ 商品を見る<br>"
        "締めの文章です。</p>"
        "<p>この記事に出てきた商品</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._find_product_link_block(
            page,
            body_locator,
            ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a"),
        )


def test_find_product_link_block_tolerates_blank_lines_around_the_dedicated_block(page):
    # 商品導線専用ブロックの前後に空行(<br><br>による空行)があっても、
    # if line.strip()による空行除外を通じて正常に1件として判定できる
    # ことを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p><br>TOY JAM 瀬戸内レモン<br>→ 商品を見る<br></p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )

    assert block.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"


def test_find_product_link_block_resolves_article001_full_structure_with_huge_block_and_two_products(
    page,
):
    # ARTICLE-001相当の完全な構造の回帰テスト: 巨大なdirect-child <p>内に
    # 記事本文があり、その途中に瀬戸内レモン・月桂樹の両方の[label, CTA]が
    # 含まれている。さらに別途、両商品それぞれの専用direct-child <p>が
    # 存在する。このとき、瀬戸内レモン・月桂樹それぞれについて、専用の
    # ブロックだけを1件ずつ正しく特定できることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>記事本文の書き出しです。<br>"
        "今日はジャムの話をします。<br>"
        "TOY JAM 瀬戸内レモン<br>"
        "→ 商品を見る<br>"
        "続きの文章です。<br>"
        "TOY JAM 瀬戸内レモン月桂樹<br>"
        "→ 商品を見る<br>"
        "締めの文章です。</p>"
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "<p>TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block_short = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )
    block_long = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    )

    assert block_short.inner_text().strip() == "TOY JAM 瀬戸内レモン\n→ 商品を見る"
    assert block_long.inner_text().strip() == "TOY JAM 瀬戸内レモン月桂樹\n→ 商品を見る"
    assert poster._same_element(page, block_short, block_long) is False


def test_assert_links_match_resolves_article001_full_structure_end_to_end(page):
    # 上記のARTICLE-001相当の完全な構造で、_assert_links_match()による
    # end-to-endの検証(href・アンカーテキストの照合)も正しく行えることを
    # 確認する。
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    ]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>記事本文の書き出しです。<br>"
        "今日はジャムの話をします。<br>"
        "TOY JAM 瀬戸内レモン<br>"
        "→ 商品を見る<br>"
        "続きの文章です。<br>"
        "TOY JAM 瀬戸内レモン月桂樹<br>"
        "→ 商品を見る<br>"
        "締めの文章です。</p>"
        "<p>この記事に出てきた商品</p>"
        '<p>TOY JAM 瀬戸内レモン<br><a href="https://you-ichi.jp/?pid=192116331">'
        "→ 商品を見る</a></p>"
        '<p>TOY JAM 瀬戸内レモン月桂樹<br><a href="https://you-ichi.jp/?pid=191552342">'
        "→ 商品を見る</a></p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_find_product_link_block_matches_exact_label_with_surrounding_whitespace(page):
    # ブロック側のテキストに前後の空白が含まれていても(strip()で吸収)、
    # 完全一致判定できることを確認する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>  TOY JAM 瀬戸内レモン  <br>  → 商品を見る  </p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    block = poster._find_product_link_block(
        page,
        body_locator,
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    )

    assert block is not None


def test_assert_links_match_resolves_article001_style_prefix_labels_to_correct_urls(page):
    # ARTICLE-001相当のケース: 2商品(片方がもう片方の商品名のprefix)の
    # それぞれについて、_assert_links_match()がリンク先を取り違えずに
    # 検証できることを確認する(_find_product_link_blockの修正が実際の
    # 検証フローでも機能することのend-to-end確認)。
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    ]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p>TOY JAM 瀬戸内レモン<br><a href="https://you-ichi.jp/?pid=192116331">'
        "→ 商品を見る</a></p>"
        '<p>TOY JAM 瀬戸内レモン月桂樹<br><a href="https://you-ichi.jp/?pid=191552342">'
        "→ 商品を見る</a></p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_assert_links_match_detects_swapped_hrefs_with_article001_style_prefix_labels(page):
    # ARTICLE-001相当のケースで、万一hrefが取り違えられていた場合は
    # 従来通り不一致として検出できることを確認する。
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    ]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p>TOY JAM 瀬戸内レモン<br><a href="https://you-ichi.jp/?pid=191552342">'
        "→ 商品を見る</a></p>"
        '<p>TOY JAM 瀬戸内レモン月桂樹<br><a href="https://you-ichi.jp/?pid=192116331">'
        "→ 商品を見る</a></p>"
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError, match="不一致"):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_find_product_link_block_source_uses_whole_block_exact_match_not_substring():
    """商品名の候補判定が、「ブロック内のどこかに隣接2行が存在するか」
    ではなく、「ブロック全体(正規化済みlines)が[label, CTA]の2行だけで
    構成されているか」という全文完全一致であることをソースから確認する
    回帰テスト(2026年8月29日、ARTICLE-001の実機再実行Artifactを踏まえた
    再修正)。docstringの説明文中にも同種の字面が登場するため、docstring
    を文字列置換で除去するのではなく、実際の候補判定行を正規表現で
    直接取り出して判定する。
    """
    import inspect
    import re

    source = inspect.getsource(NotePoster._find_product_link_block)
    match = re.search(r"if (lines == expected_lines):", source)
    assert match is not None, "候補判定が `lines == expected_lines` になっていません"

    # 「ブロック内のどこかに隣接2行が存在すれば候補」という判定
    # (`any(...)`によるループ探索)には戻していないことを確認する。
    assert "for j in range(len(lines) - 1)" not in source
    assert ".startswith(" not in source
    assert "label in text" not in source


# -- _select_product_link_text_in_block(ブロック内の対象テキストだけを選択) --


def test_select_product_link_text_in_block_selects_only_the_link_text(page):
    page.set_content(
        '<div class="editor" contenteditable="true"><p>商品A<br>→ 商品を見る</p></div>'
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first

    poster._select_product_link_text_in_block(page, block)

    selected = page.evaluate("() => window.getSelection().toString()")
    assert selected.strip() == "→ 商品を見る"


def test_select_product_link_text_in_block_raises_when_no_matching_text_node(page):
    # 「→ 商品を見る」がブロックの直接の子テキストノードとして存在しない
    # (別の文言しかない)場合は推測で選択せず中断する。
    page.set_content(
        '<div class="editor" contenteditable="true"><p>商品A<br>違う文言</p></div>'
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first

    with pytest.raises(NotePosterError):
        poster._select_product_link_text_in_block(page, block)


def test_select_product_link_text_in_block_raises_when_link_text_appears_twice(page):
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>商品A<br>→ 商品を見る<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first

    with pytest.raises(NotePosterError):
        poster._select_product_link_text_in_block(page, block)


# -- _find_active_link_toolbar_button(実機Artifactで確認したツールバー構造) --
#
# TEST-004の追加観測(実機のGitHub Actions実行で取得したHTMLダンプ04/05/06)
# で、noteのフローティング編集ツールバーは
#   <div data-active="true" role="toolbar" id="desktop-toolbar">
# という単一のDOM要素として存在し、その内部に
#   <button aria-label="リンク">
# がちょうど1件だけ存在することが判明した(class名等の推測ベースの属性には
# 依存しない)。以下はこの実機DOM構造をローカルの疑似ページで再現したテスト。
#
# さらにTEST-004の実行ログとHTMLダンプを突き合わせた結果、「ツールバーが
# 0件」という安全停止は、セレクタの誤りではなく「テキスト選択からツール
# バーが実際にDOMへ出現しdata-active="true"になるまでの短い非同期の遅延」
# が原因だったと判明した。以下のテストでは、この遅延をJSのsetTimeout()で
# 再現し、_find_active_link_toolbar_button()が固定sleep()を使わずに
# 出現を待てること、待っても出現しなければ安全停止することの両方を確認する。
# 待機を伴わない安全停止系のテストでは、テスト全体の実行時間を抑えるため
# 短いtimeout_ms(数百ms)を明示的に渡している(本番呼び出し側は常に
# デフォルト値 _LINK_TOOLBAR_APPEAR_TIMEOUT_MS を使う)。


def test_find_active_link_toolbar_button_finds_the_button(page):
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()

    button = poster._find_active_link_toolbar_button(page)

    assert button.get_attribute("aria-label") == "リンク"


def test_find_active_link_toolbar_button_succeeds_when_toolbar_appears_after_a_short_delay(page):
    # 選択直後は0件でも、短時間後にツールバー(とその内部のリンクボタン)が
    # DOMへ出現した場合は、固定sleepを使わずに検出できることを確認する。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <script>
          setTimeout(() => {
            const div = document.createElement('div');
            div.setAttribute('data-active', 'true');
            div.setAttribute('role', 'toolbar');
            div.id = 'desktop-toolbar';
            const btn = document.createElement('button');
            btn.setAttribute('aria-label', 'リンク');
            div.appendChild(btn);
            document.body.appendChild(div);
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()

    button = poster._find_active_link_toolbar_button(page, timeout_ms=2000)

    assert button.get_attribute("aria-label") == "リンク"


def test_find_active_link_toolbar_button_raises_when_toolbar_never_appears(page):
    # timeoutまでツールバーが1件も出現しなければ、これまで通り推測せず
    # needs_reviewへ安全停止する。テストを高速に保つため短いtimeoutを渡す。
    page.set_content('<div class="editor" contenteditable="true"><p>本文</p></div>')
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=200)


def test_find_active_link_toolbar_button_raises_when_multiple_toolbars_appear(page):
    # 出現を待っている最中にlocatorが2件以上に一致すると、Playwrightの
    # strict modeにより例外(TimeoutErrorとは別のError)が送出される。
    # _wait_for_locator_to_appear()はこれも「一意に特定できない」ケースと
    # して扱い、.first()等で1件を選んで先に進んだりしない。呼び出し側は
    # 改めてcount()を取り直し、複数件なら安全停止することを確認する
    # (出現後の一意性の再検証)。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <script>
          setTimeout(() => {
            for (const id of ['desktop-toolbar', 'mobile-toolbar']) {
              const div = document.createElement('div');
              div.setAttribute('data-active', 'true');
              div.setAttribute('role', 'toolbar');
              div.id = id;
              const btn = document.createElement('button');
              btn.setAttribute('aria-label', 'リンク');
              div.appendChild(btn);
              document.body.appendChild(div);
            }
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=2000)


def test_wait_for_locator_to_appear_returns_false_on_strict_mode_violation(page):
    # _wait_for_locator_to_appear() 単体でも、locatorが最初から複数件に
    # 一致する場合にstrict mode違反を安全に吸収してFalseを返す
    # (例外を外へ漏らさず、.first()等で位置ベースに1件を選ばない)ことを
    # 直接確認する。
    page.set_content(
        """
        <button aria-label="リンク">1</button>
        <button aria-label="リンク">2</button>
        """
    )
    poster = _bare_poster()
    duplicated = page.locator('button[aria-label="リンク"]')

    result = poster._wait_for_locator_to_appear(duplicated, timeout_ms=500)

    assert result is False
    # strict mode違反後もcount()自体は正常に実際の件数を返す。
    assert duplicated.count() == 2


def test_wait_for_locator_to_appear_succeeds_without_first_or_nth(page):
    page.set_content('<button aria-label="リンク">1</button>')
    poster = _bare_poster()
    unique = page.locator('button[aria-label="リンク"]')

    result = poster._wait_for_locator_to_appear(unique, timeout_ms=500)

    assert result is True


def test_find_active_link_toolbar_button_ignores_inactive_toolbar(page):
    # data-active="false"のツールバー(非表示中)は対象にしない。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <div data-active="false" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=200)


def test_find_active_link_toolbar_button_ignores_link_labeled_button_outside_toolbar(page):
    # ツールバーの外にある同名のaria-label要素を誤って拾わないことを確認する
    # (ページ全体ではなくactive toolbar内だけをスコープにしていることの確認)。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <button aria-label="リンク">ツールバー外のリンクボタン</button>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="太字">B</button>
        </div>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=200)


def test_find_active_link_toolbar_button_raises_when_button_duplicated_inside_toolbar(page):
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク">リンク1</button>
          <button aria-label="リンク">リンク2</button>
        </div>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=200)


def test_find_active_link_toolbar_button_waits_when_only_the_button_appears_late(page):
    # ツールバー自体は最初から存在するが、内部のリンクボタンだけが遅れて
    # 出現するケース(ツールバー特定後、ボタン側の出現待ちが正しく働くこと
    # の確認)。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar"></div>
        <script>
          setTimeout(() => {
            const btn = document.createElement('button');
            btn.setAttribute('aria-label', 'リンク');
            document.getElementById('desktop-toolbar').appendChild(btn);
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()

    button = poster._find_active_link_toolbar_button(page, timeout_ms=2000)

    assert button.get_attribute("aria-label") == "リンク"


def test_find_active_link_toolbar_button_raises_when_button_never_appears(page):
    # ツールバーは存在するが、リンクボタンがtimeoutまで一度も出現しない
    # 場合は安全停止する。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>本文</p></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="太字">B</button>
        </div>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_active_link_toolbar_button(page, timeout_ms=200)


def test_find_active_link_toolbar_button_does_not_use_fixed_sleep():
    """固定sleepを使わず、Playwrightの自動待機に委ねていることを確認する。

    note.pyがtimeモジュール自体をimportしていないことを確認する
    (importしていなければ time.sleep() はそもそも呼びようがない)。
    _find_active_link_toolbar_button / _wait_for_locator_to_appear の
    ソース中に実際の関数呼び出しとしての "sleep(" が無いことも確認する
    (docstring中の説明文言はここでは対象にしない)。
    """
    import inspect
    import re

    from src import note as note_module

    assert not hasattr(note_module, "time")

    for func in (
        NotePoster._find_active_link_toolbar_button,
        NotePoster._wait_for_locator_to_appear,
    ):
        source = inspect.getsource(func)
        # docstring本文(説明文中の "time.sleep()" という言及)を除いた、
        # 実際のコード部分だけを検査対象にする。
        code_only = source.replace(func.__doc__ or "", "")
        assert re.search(r"\bsleep\s*\(", code_only) is None


def test_wait_for_locator_functions_have_no_first_or_nth_in_code():
    """待機処理(_wait_for_locator_to_appear / _find_active_link_toolbar_
    button)のコード部分に、位置ベースの要素選択(.first / .nth())が
    一切使われていないことを確認する。

    待機目的であっても .first()/.nth() は使わない、という安全要件の
    ための回帰テスト。当初の実装は _wait_for_locator_to_appear() 内で
    `locator.first.wait_for(...)` を使っており、この要件に反していた
    (指摘を受けてlocator自体を待機対象にする実装へ修正した)。
    docstring中の説明文言("`.first`や`.nth()`のような…"等)は対象外とし、
    実際のコード部分だけを検査する。
    """
    import inspect
    import re

    for func in (
        NotePoster._wait_for_locator_to_appear,
        NotePoster._find_active_link_toolbar_button,
    ):
        source = inspect.getsource(func)
        code_only = source.replace(func.__doc__ or "", "")
        assert ".first" not in code_only, f"{func.__name__} に .first が含まれています"
        assert re.search(r"\.nth\s*\(", code_only) is None, (
            f"{func.__name__} に .nth( が含まれています"
        )


# -- _set_link_on_text_occurrence(観測専用実装: クリック後は必ず安全停止) -----


def test_set_link_on_text_occurrence_raises_when_block_has_no_matching_text_node(page):
    # ブロック内に「→ 商品を見る」と完全一致する直接の子テキストノードが
    # 無い(構造の想定違い)場合は安全停止する。ツールバーへは到達しない。
    page.set_content('<div class="editor" contenteditable="true"><p>違う文言</p></div>')
    poster = _bare_poster()
    block = page.locator(".editor p").first

    with pytest.raises(NotePosterError):
        poster._set_link_on_text_occurrence(
            page, block, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_set_link_on_text_occurrence_inputs_url_and_clicks_apply_then_applies_link(
    page,
):
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    poster._set_link_on_text_occurrence(page, block, link)  # 例外が出なければOK

    link_button = page.locator('#desktop-toolbar button[aria-label="リンク"]')
    assert link_button.get_attribute("data-clicked") == "true"
    # 「適用」クリック後、実機と同様にURL入力欄・「適用」ボタンはDOMから
    # 消えていること。
    assert page.locator(_URL_INPUT_SELECTOR_FOR_TESTS).count() == 0
    assert (
        page.locator("#desktop-toolbar").get_by_role("button", name="適用", exact=True).count()
        == 0
    )
    anchor = page.locator(".editor a")
    assert anchor.count() == 1
    assert anchor.inner_text().strip() == "→ 商品を見る"
    assert anchor.get_attribute("href") == link.url


# -- _ensure_product_link_block_in_viewport(pre-selection scroll、ARTICLE-001 --
# の実機実行を踏まえた修正、2026年8月29日)
#
# フローティング編集ツールバーはposition: fixedであり、出現した後に
# scroll_into_view_if_needed()しても画面上の位置は変化しない
# (LinkButtonOutOfViewportErrorのdocstring・実機Artifactで確認済み)。
# そこで「浮動ツールバーを後から直す」のではなく、「浮動ツールバーの元に
# なる選択(selection)を、最初から画面内で作る」設計に変更した。
# _select_product_link_text_in_block()でテキストを選択する前に、商品導線
# ブロック自体をscroll_into_view_if_needed()し、viewport内に収まっている
# ことを確認する。


def test_ensure_product_link_block_in_viewport_scrolls_block_into_view(page):
    # 画面下方にある商品ブロックを、selection前にscrollしてviewport内へ
    # 入れられることを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div style="height: 3000px;"></div>
        <div class="editor" contenteditable="true">
          <p>この記事に出てきた商品</p>
          <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
        </div>
        """
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    box_before = block.bounding_box()
    assert box_before["y"] > 600  # scroll前はまだviewport外

    poster._ensure_product_link_block_in_viewport(page, block, link)  # 例外が出なければOK

    box_after = block.bounding_box()
    assert 0 <= box_after["y"]
    assert box_after["y"] + box_after["height"] <= 600


def test_ensure_product_link_block_in_viewport_succeeds_when_already_in_view(page):
    # ブロックが最初からviewport内にある(短文記事、TEST-004相当)場合も、
    # 余計な問題を起こさず正常終了することを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    poster._ensure_product_link_block_in_viewport(page, block, link)  # 例外が出なければOK


def test_ensure_product_link_block_in_viewport_raises_when_still_out_of_viewport_after_scroll(
    page,
):
    # position: fixedでviewportの高さを超えるtopを持つブロックは、
    # window単位のスクロールでは絶対にviewport内へ入らない。選択処理へは
    # 進まず安全停止することを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div class="editor" contenteditable="true">
          <p>この記事に出てきた商品</p>
          <p style="position: fixed; top: 900px; left: 10px;">
            TOY JAM 瀬戸内レモン<br>→ 商品を見る
          </p>
        </div>
        """
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    with pytest.raises(ProductLinkBlockOutOfViewportError):
        poster._ensure_product_link_block_in_viewport(page, block, link, timeout_ms=300)


def test_ensure_product_link_block_in_viewport_raises_when_bounding_box_is_none(page):
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p style="display:none;">TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>'
        "</div>"
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    with pytest.raises(ProductLinkBlockOutOfViewportError):
        poster._ensure_product_link_block_in_viewport(page, block, link, timeout_ms=300)


def test_ensure_product_link_block_in_viewport_saves_failure_artifact(page, caplog):
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div class="editor" contenteditable="true">
          <p>この記事に出てきた商品</p>
          <p style="position: fixed; top: 900px; left: 10px;">
            TOY JAM 瀬戸内レモン<br>→ 商品を見る
          </p>
        </div>
        """
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    with caplog.at_level("WARNING"):
        with pytest.raises(ProductLinkBlockOutOfViewportError):
            poster._ensure_product_link_block_in_viewport(page, block, link, timeout_ms=300)

    assert "商品導線ブロックpre-selection viewport確認" in caplog.text


def test_set_link_on_text_occurrence_runs_pre_selection_scroll_before_text_selection(
    page, monkeypatch
):
    # pre-selection scroll(_ensure_product_link_block_in_viewport)が
    # _select_product_link_text_in_block()より前に実行されることを、
    # 選択が実行される時点で既にブロックがviewport内に収まっていることを
    # 確認することで検証する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div style="height: 3000px;"></div>
        <div class="editor" contenteditable="true">
          <p>この記事に出てきた商品</p>
          <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
        </div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    observed: dict = {}
    original = NotePoster._select_product_link_text_in_block

    def spy(self, page_arg, block_arg):
        observed["box_at_selection_time"] = block_arg.bounding_box()
        return original(self, page_arg, block_arg)

    monkeypatch.setattr(NotePoster, "_select_product_link_text_in_block", spy)

    # この簡易fixtureにはURL入力欄が無いため、リンクボタンクリック以降の
    # どこかでNotePosterError(のサブクラス)が発生して止まるが、
    # _select_product_link_text_in_block()自体は必ず呼ばれるため、
    # そこでのbounding_boxを検証すれば順序の確認としては十分。
    with pytest.raises(NotePosterError):
        poster._set_link_on_text_occurrence(page, block, link)

    assert "box_at_selection_time" in observed
    box = observed["box_at_selection_time"]
    assert box is not None
    assert 0 <= box["y"]
    assert box["y"] + box["height"] <= 600


_LONG_ARTICLE001_STYLE_HTML = """
<div style="height: 3000px;">spacer</div>
<div class="editor" contenteditable="true">
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
</div>
<div data-active="false" role="toolbar" id="desktop-toolbar"
     style="position: fixed; display: none;">
  <button aria-label="リンク">リンク</button>
</div>
<script>
  window.__urlInputKeys = [];
  // 実機のnoteと同様、フローティング編集ツールバーの位置は「選択範囲の
  // 現在のviewport相対位置」から計算される(position: fixedで、selection
  // が変わるたびに再配置される)ことをローカルで再現する。事前にブロック
  // 自体をviewport内へscrollしてから選択すれば、ここで計算されるtopは
  // viewport内に収まる。逆に、scrollせずに選択すればtopはviewport外の
  // 大きな値になる。
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    if (!sel || sel.toString().trim() !== '→ 商品を見る') return;
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    const toolbar = document.getElementById('desktop-toolbar');
    toolbar.style.top = Math.max(0, rect.top - 50) + 'px';
    toolbar.style.left = Math.max(0, rect.left) + 'px';
    toolbar.style.display = 'block';
    toolbar.setAttribute('data-active', 'true');
  });
  document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
    .addEventListener('click', (e) => {
      e.target.setAttribute('data-clicked', 'true');
      const toolbar = document.getElementById('desktop-toolbar');
      const textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      textarea.addEventListener('keydown', (ev) => {
        window.__urlInputKeys.push(ev.key);
      });
      const applyButton = document.createElement('button');
      applyButton.setAttribute('data-name', 'Button');
      applyButton.setAttribute('type', 'button');
      const applySpan = document.createElement('span');
      applySpan.textContent = '適用';
      applyButton.appendChild(applySpan);
      applyButton.addEventListener('click', (ev) => {
        ev.target.closest('button').setAttribute('data-clicked', 'true');
        const url = textarea.value;
        const targetP = Array.from(
          document.querySelectorAll('.editor p')
        ).find((el) => el.textContent.includes('瀬戸内レモン'));
        const textNode = targetP && Array.from(targetP.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE
            && node.textContent.trim() === '→ 商品を見る'
        );
        if (textNode) {
          const a = document.createElement('a');
          a.setAttribute('href', url);
          a.setAttribute('target', '_blank');
          a.setAttribute('rel', 'noopener');
          const span = document.createElement('span');
          span.className = 'highlight';
          span.textContent = '→ 商品を見る';
          a.appendChild(span);
          textNode.replaceWith(a);
        }
        textarea.remove();
        applyButton.remove();
        cancelButton.remove();
      });
      const cancelButton = document.createElement('button');
      cancelButton.setAttribute('aria-label', 'URLの入力をやめる');
      toolbar.appendChild(textarea);
      toolbar.appendChild(applyButton);
      toolbar.appendChild(cancelButton);
    });
</script>
"""


def test_apply_product_links_succeeds_for_long_article001_style_page_with_block_far_below(
    page,
):
    # ARTICLE-001相当の回帰テスト: 商品導線ブロックが文書のかなり下に
    # あり、フローティング編集ツールバーの位置が選択範囲のviewport相対
    # 位置から動的に計算される(position: fixed)長文記事でも、
    # pre-selection scrollにより選択・ツールバー出現・リンクボタンの
    # クリック・URL入力・「適用」・<a>要素の反映まで、実際にviewport内で
    # 完了できることを確認する。
    page.set_viewport_size({"width": 1280, "height": 800})
    page.set_content(_LONG_ARTICLE001_STYLE_HTML)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]

    poster._apply_product_links(page, body_locator, links)  # 例外が出なければOK

    anchor = body_locator.locator("a")
    assert anchor.count() == 1
    assert anchor.inner_text().strip() == "→ 商品を見る"
    assert anchor.get_attribute("href") == links[0].url

    # 実際にリンクボタン・「適用」ボタンのクリックがviewport内で行われた
    # (安全停止せず完了した)ことの確認。
    link_button = page.locator('#desktop-toolbar button[aria-label="リンク"]')
    assert link_button.get_attribute("data-clicked") == "true"
    # fill()はキーイベントを発生させないため、keydownを記録する
    # window.__urlInputKeysは空のままのはずである。
    sent_keys = page.evaluate("() => window.__urlInputKeys")
    assert sent_keys == []


# -- _bounding_box_within_viewport(viewport境界判定の純粋関数) -------------
#
# _ensure_link_button_in_viewport() から座標計算部分だけを切り出した純粋
# 関数。実際のブラウザ描画に依存せず、境界値を含めて判定の正しさを直接
# 確認できる。


def test_bounding_box_within_viewport_accepts_box_touching_the_edges():
    viewport = {"width": 1280, "height": 800}
    # 右端・下端がちょうどviewportの右端・下端と一致する場合は「収まって
    # いる」とみなす。
    assert _bounding_box_within_viewport(
        {"x": 0, "y": 0, "width": 1280, "height": 800}, viewport
    )
    assert _bounding_box_within_viewport(
        {"x": 1270, "y": 790, "width": 10, "height": 10}, viewport
    )


def test_bounding_box_within_viewport_rejects_box_outside_each_edge():
    viewport = {"width": 1280, "height": 800}
    assert not _bounding_box_within_viewport(
        {"x": -1, "y": 0, "width": 10, "height": 10}, viewport
    )
    assert not _bounding_box_within_viewport(
        {"x": 0, "y": -1, "width": 10, "height": 10}, viewport
    )
    assert not _bounding_box_within_viewport(
        {"x": 1271, "y": 0, "width": 10, "height": 10}, viewport
    )
    assert not _bounding_box_within_viewport(
        {"x": 0, "y": 791, "width": 10, "height": 10}, viewport
    )


# -- _ensure_link_button_in_viewport(クリック前のviewport確認・安全停止) --
#
# 実機のGitHub Actions実行(TEST-004)で、リンクボタン自体は一意に特定
# できていたにもかかわらず、click()が「element is outside of the
# viewport」を繰り返して既定の30秒タイムアウトで失敗する事象が発生した。
# 以下はこの状況をローカルの疑似ページで再現し、force=True・JavaScript
# clickのような迂回手段を使わずに安全停止できることを確認するテスト。


def test_ensure_link_button_in_viewport_succeeds_after_scrolling_into_view(page):
    # ページ読み込み直後はviewport外(はるか下)にあるボタンでも、
    # scroll_into_view_if_needed()によって実際にviewport内へ入れば
    # 例外を送出せず先に進めることを確認する(通常の書類順の要素なので、
    # 本物のスクロールで到達可能なケース)。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div style="height: 2000px;"></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク" style="width: 40px; height: 40px;">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    poster._ensure_link_button_in_viewport(page, link_button)  # 例外が出なければOK

    box = link_button.bounding_box()
    assert box is not None
    assert box["y"] + box["height"] <= 600


def test_ensure_link_button_in_viewport_raises_when_still_out_of_viewport_after_scroll(page):
    # position: fixedでviewportの高さを超えるtopを持つ要素は、window単位の
    # スクロールでは絶対にviewport内へ入らない(実機で観測されたのと同じ
    # 状況を再現)。scroll_into_view_if_needed()を試みても解決しないため、
    # 推測でクリックせず安全停止することを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div data-active="true" role="toolbar" id="desktop-toolbar"
             style="position: fixed; top: 900px; left: 10px;">
          <button aria-label="リンク">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    with pytest.raises(LinkButtonOutOfViewportError):
        poster._ensure_link_button_in_viewport(page, link_button, timeout_ms=500)


def test_ensure_link_button_in_viewport_raises_when_bounding_box_is_none(page):
    # bounding_box()がNone(非表示等で取得できない)の場合も、推測せず
    # 安全停止することを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク" style="display:none;">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    with pytest.raises(LinkButtonOutOfViewportError):
        poster._ensure_link_button_in_viewport(page, link_button, timeout_ms=300)


# -- position: fixed祖先の検知(ARTICLE-001の実機再実行を踏まえた診断強化、 --
# 2026年8月29日)。scroll_into_view_if_needed()後もbounding_box().yがほぼ
# 変化しなかった実機事象の原因を、次回の実機実行で1回で切り分けられる
# ようにするための診断強化。


def test_bounding_box_within_viewport_is_a_pure_function_without_scroll_position():
    """`_bounding_box_within_viewport()`が`box`と`viewport`だけを引数に
    取り、`window.scrollY`のような追加のスクロール位置情報を一切必要と
    しない(=box自体が既にviewport相対座標であることを前提とした純粋
    関数である)ことをシグネチャから確認する回帰テスト。scroll位置を別途
    加減算する実装に変更されていないことの確認(2026年8月29日、
    ARTICLE-001の実機実行を踏まえた確認)。
    """
    import inspect

    params = list(inspect.signature(_bounding_box_within_viewport).parameters)
    assert params == ["box", "viewport"]


def test_ensure_link_button_in_viewport_bounding_box_is_viewport_relative_after_real_scroll(
    page,
):
    # Playwrightのbounding_box()は要素のgetBoundingClientRect()相当の
    # viewport相対座標(documentページ全体に対する絶対座標ではない)で
    # あることを、実際にブラウザをスクロールさせて確認する(ARTICLE-001の
    # 実機実行で座標系の解釈に疑義が出たための確認)。scroll後、
    # window.scrollYを別途加減算しなくても、bounding_box()の値をそのまま
    # _bounding_box_within_viewport()に渡せば正しく判定できる。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div style="height: 2000px;"></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク" style="width: 40px; height: 40px;">リンク</button>
        </div>
        """
    )
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    box_before = link_button.bounding_box()
    assert box_before["y"] > 600  # scroll前はまだviewport外

    page.evaluate("() => window.scrollTo(0, 1600)")
    scroll_y = page.evaluate("() => window.scrollY")
    box_after = link_button.bounding_box()

    assert scroll_y > 0
    # bounding_box()は既にviewport相対なので、scroll_yを加減算しなくても
    # そのままviewport内判定に使える。
    assert 0 <= box_after["y"] <= 600
    assert _bounding_box_within_viewport(box_after, {"width": 800, "height": 600})


def test_ensure_link_button_in_viewport_error_mentions_fixed_position_ancestor(page):
    # position: fixedの祖先が原因でscroll_into_view_if_needed()が効果を
    # 持たない場合、エラーメッセージにその旨が含まれ、次回の実機実行で
    # 「スクロールでは解決できない」ことを即座に判断できることを確認する。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div data-active="true" role="toolbar" id="desktop-toolbar"
             style="position: fixed; top: 900px; left: 10px;">
          <button aria-label="リンク">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    with pytest.raises(LinkButtonOutOfViewportError) as exc_info:
        poster._ensure_link_button_in_viewport(page, link_button, timeout_ms=500)

    assert "position: fixed" in str(exc_info.value)


def test_ensure_link_button_in_viewport_logs_fixed_position_ancestor(page, caplog):
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div data-active="true" role="toolbar" id="desktop-toolbar"
             style="position: fixed; top: 900px; left: 10px;">
          <button aria-label="リンク">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    with caplog.at_level("INFO"):
        with pytest.raises(LinkButtonOutOfViewportError):
            poster._ensure_link_button_in_viewport(page, link_button, timeout_ms=500)

    assert "position_fixed祖先" in caplog.text
    assert "'fixed': True" in caplog.text


def test_ensure_link_button_in_viewport_does_not_mention_fixed_when_element_is_simply_too_tall(
    page,
):
    # position: fixedが原因ではない(単に要素自体がviewportより高さが
    # 大きく、スクロールしても全体は収まりきらない)ケースでは、誤って
    # position: fixedの言及をしないことを確認する(scroll containerが
    # windowではない場合に誤って安全判定しないことの確認と対をなす、
    # 「原因ではないものを誤って原因扱いしない」確認)。
    page.set_viewport_size({"width": 800, "height": 600})
    page.set_content(
        """
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク" style="width: 40px; height: 900px;">リンク</button>
        </div>
        """
    )
    poster = _bare_poster()
    link_button = page.locator(f'{_ACTIVE_TOOLBAR_SELECTOR_FOR_TESTS} button[aria-label="リンク"]')

    with pytest.raises(LinkButtonOutOfViewportError) as exc_info:
        poster._ensure_link_button_in_viewport(page, link_button, timeout_ms=500)

    assert "position: fixed" not in str(exc_info.value)


def test_ensure_link_button_in_viewport_fixed_probe_is_read_only():
    """position: fixed祖先の検知(`_FIXED_ANCESTOR_PROBE_JS`)が、
    `getComputedStyle()`による読み取りのみであり、クリック・フォーカス・
    値の変更等の操作を一切行わないことをソースから確認する回帰テスト。
    """
    from src import note as note_module

    probe_js = note_module._FIXED_ANCESTOR_PROBE_JS
    assert "getComputedStyle" in probe_js
    assert ".click(" not in probe_js
    assert ".focus(" not in probe_js
    assert ".blur(" not in probe_js


def test_ensure_link_button_in_viewport_does_not_use_force_or_javascript_click():
    """クリック前のviewport確認処理が、force=Trueによるactionability
    check迂回や、JavaScript経由の直接クリック(el.click()等)を一切
    使っていないことをソースから確認する回帰テスト。
    """
    import inspect

    for func in (
        NotePoster._ensure_link_button_in_viewport,
        NotePoster._ensure_product_link_block_in_viewport,
        NotePoster._set_link_on_text_occurrence,
        NotePoster._log_url_input_diagnostics,
        NotePoster._wait_for_product_link_applied,
    ):
        source = inspect.getsource(func)
        # docstring本文(説明文中の "force=True" 等の言及)を除いた、実際の
        # コード部分だけを検査対象にする。
        code_only = source.replace(func.__doc__ or "", "")
        assert "force=True" not in code_only
        assert "force = True" not in code_only
        assert ".click()\"" not in code_only
        assert "el.click(" not in code_only
        assert "el => el.click" not in code_only


def test_link_button_click_timeout_is_much_shorter_than_playwright_default():
    from src import note as note_module

    # Playwright既定の30秒(30000ms)より十分短い上限になっていることを
    # 確認する(既定のまま長時間の再試行に頼らない設計であることの確認)。
    assert 0 < note_module._LINK_BUTTON_CLICK_TIMEOUT_MS < 30000


def test_set_link_on_text_occurrence_source_uses_short_click_timeout():
    import inspect

    source = inspect.getsource(NotePoster._set_link_on_text_occurrence)
    assert "link_button.click(timeout=_LINK_BUTTON_CLICK_TIMEOUT_MS)" in source


def test_viewport_size_is_still_1280x800():
    """今回はviewportサイズを変更していないことを確認する回帰テスト
    (1280x800のまま)。
    """
    import inspect

    from src.note import NotePoster as _NP

    source = inspect.getsource(_NP.__enter__)
    assert 'viewport={"width": 1280, "height": 800}' in source


# -- _find_url_input_textarea(実機Artifactで確認したURL入力欄構造) --------
#
# TEST-004の追加観測で、リンクボタンをクリックした直後に
#   <textarea inputmode="text" name="alt" placeholder="https://"></textarea>
# というURL入力欄が出現することが判明した。以下はこの実機DOM構造を
# ローカルの疑似ページで再現したテスト。

_URL_INPUT_HTML = """
<textarea inputmode="text" name="alt" placeholder="https://"></textarea>
<button aria-label="URLの入力をやめる"></button>
"""


def test_find_url_input_textarea_selector_matches_the_confirmed_real_dom():
    # 実機確認済みの3属性(placeholder/inputmode/name)すべてを満たす
    # セレクタになっていることを確認する。
    from src import note as note_module

    assert note_module._URL_INPUT_SELECTOR == _URL_INPUT_SELECTOR_FOR_TESTS


def test_find_url_input_textarea_finds_the_textarea(page):
    page.set_content(_URL_INPUT_HTML)
    poster = _bare_poster()

    url_input = poster._find_url_input_textarea(page)

    assert url_input.get_attribute("name") == "alt"
    assert url_input.get_attribute("placeholder") == "https://"
    assert url_input.get_attribute("inputmode") == "text"


def test_find_url_input_textarea_succeeds_when_it_appears_after_a_short_delay(page):
    # クリック直後は0件でも、短時間後にURL入力欄がDOMへ出現した場合は、
    # 固定sleepを使わずに検出できることを確認する。
    page.set_content(
        """
        <script>
          setTimeout(() => {
            const textarea = document.createElement('textarea');
            textarea.setAttribute('inputmode', 'text');
            textarea.setAttribute('name', 'alt');
            textarea.setAttribute('placeholder', 'https://');
            document.body.appendChild(textarea);
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()

    url_input = poster._find_url_input_textarea(page, timeout_ms=2000)

    assert url_input.get_attribute("placeholder") == "https://"


def test_find_url_input_textarea_raises_when_it_never_appears(page):
    page.set_content("<div>本文</div>")
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_input_textarea(page, timeout_ms=200)


def test_find_url_input_textarea_raises_when_multiple_appear(page):
    page.set_content(
        """
        <textarea inputmode="text" name="alt" placeholder="https://"></textarea>
        <textarea inputmode="text" name="alt" placeholder="https://"></textarea>
        """
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_input_textarea(page, timeout_ms=200)


# -- _find_url_apply_button(実機Artifactで確認した「適用」ボタン構造) ------
#
# TEST-004の追加観測で、「適用」ボタンはURL入力欄と同じactive toolbar
# (role="toolbar" data-active="true")の内部に存在することが判明した。
#   <button data-name="Button" type="button" id=":r16:"><span>適用</span></button>
# idはReactのuseId等が生成する動的な値の可能性が高いためセレクタには使わず、
# ページ全体からの「適用」文字列検索でもなく、確認済みのactive toolbarを
# スコープとしてget_by_role("button", name="適用", exact=True)で特定する。


def _toolbar_with_url_input_and_apply_button_html(*, apply_button_html: str) -> str:
    return (
        '<div data-active="true" role="toolbar" id="desktop-toolbar">'
        '<textarea inputmode="text" name="alt" placeholder="https://"></textarea>'
        f"{apply_button_html}"
        '<button aria-label="URLの入力をやめる"></button>'
        "</div>"
    )


_TOOLBAR_WITH_APPLY_BUTTON_HTML = _toolbar_with_url_input_and_apply_button_html(
    apply_button_html=(
        '<button data-name="Button" type="button" id=":r16:"><span>適用</span></button>'
    )
)


def test_find_url_apply_button_finds_the_button_scoped_to_the_toolbar(page):
    page.set_content(_TOOLBAR_WITH_APPLY_BUTTON_HTML)
    poster = _bare_poster()

    apply_button = poster._find_url_apply_button(page)

    assert apply_button.inner_text().strip() == "適用"


def test_find_url_apply_button_raises_when_no_active_toolbar(page):
    page.set_content("<div>本文</div>")
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_apply_button(page, timeout_ms=200)


def test_find_url_apply_button_raises_when_url_input_missing_from_toolbar(page):
    # ツールバーはあるがURL入力欄が見当たらない(構造の想定違い)場合は、
    # 「適用」ボタンを探しに行かず安全停止する。
    page.set_content(
        '<div data-active="true" role="toolbar" id="desktop-toolbar">'
        '<button data-name="Button" type="button"><span>適用</span></button>'
        "</div>"
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_apply_button(page, timeout_ms=200)


def test_find_url_apply_button_raises_when_button_missing(page):
    page.set_content(
        '<div data-active="true" role="toolbar" id="desktop-toolbar">'
        '<textarea inputmode="text" name="alt" placeholder="https://"></textarea>'
        "</div>"
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_apply_button(page, timeout_ms=200)


def test_find_url_apply_button_raises_when_button_duplicated_in_toolbar(page):
    page.set_content(
        _toolbar_with_url_input_and_apply_button_html(
            apply_button_html=(
                '<button><span>適用</span></button><button><span>適用</span></button>'
            )
        )
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_apply_button(page, timeout_ms=200)


def test_find_url_apply_button_ignores_apply_labeled_button_outside_toolbar(page):
    # ツールバーの外にある同名の「適用」ボタンを誤って拾わないことを確認
    # する(ページ全体の文字列検索ではなく、toolbarというスコープで一意に
    # 特定していることの確認)。
    page.set_content(
        '<button><span>適用</span></button>'
        + _toolbar_with_url_input_and_apply_button_html(apply_button_html="")
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._find_url_apply_button(page, timeout_ms=200)


def test_find_url_apply_button_succeeds_when_it_appears_after_a_short_delay(page):
    page.set_content(
        '<div data-active="true" role="toolbar" id="desktop-toolbar">'
        '<textarea inputmode="text" name="alt" placeholder="https://"></textarea>'
        "</div>"
        """
        <script>
          setTimeout(() => {
            const btn = document.createElement('button');
            const span = document.createElement('span');
            span.textContent = '適用';
            btn.appendChild(span);
            document.getElementById('desktop-toolbar').appendChild(btn);
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()

    apply_button = poster._find_url_apply_button(page, timeout_ms=2000)

    assert apply_button.inner_text().strip() == "適用"


def test_find_url_apply_button_does_not_use_dynamic_id_or_first_or_nth():
    """「適用」ボタンの特定に、動的なid(:r16:等)や.first()/.nth()の
    ような位置ベースの絞り込みを使っていないことをソースから確認する
    回帰テスト。
    """
    import inspect

    source = inspect.getsource(NotePoster._find_url_apply_button)
    code_only = source.replace(NotePoster._find_url_apply_button.__doc__ or "", "")
    assert ":r16:" not in code_only
    assert "get_by_role" in code_only
    assert ".first" not in code_only
    assert ".nth(" not in code_only


# -- _wait_for_product_link_applied(「適用」クリック後の<a>反映待ち) --------


def test_wait_for_product_link_applied_succeeds_when_anchor_already_present(page):
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<p>商品A<br><a href="https://example.com/a">→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first
    link = ProductLink(label="商品A", url="https://example.com/a")

    poster._wait_for_product_link_applied(page, block, link, timeout_ms=300)  # 例外が出なければOK


def test_wait_for_product_link_applied_succeeds_when_anchor_appears_after_a_short_delay(page):
    page.set_content(
        """
        <div class="editor" contenteditable="true">
          <p id="target">商品A<br>→ 商品を見る</p>
        </div>
        <script>
          setTimeout(() => {
            const p = document.getElementById('target');
            const textNode = Array.from(p.childNodes).find(
              (node) => node.nodeType === Node.TEXT_NODE
                && node.textContent.trim() === '→ 商品を見る'
            );
            const a = document.createElement('a');
            a.href = 'https://example.com/a';
            a.textContent = '→ 商品を見る';
            textNode.replaceWith(a);
          }, 300);
        </script>
        """
    )
    poster = _bare_poster()
    block = page.locator("#target")
    link = ProductLink(label="商品A", url="https://example.com/a")

    poster._wait_for_product_link_applied(page, block, link, timeout_ms=2000)  # 例外が出なければOK


def test_wait_for_product_link_applied_raises_when_anchor_never_appears(page):
    # 「適用」をクリックしても対象ブロック内にリンクが反映されない
    # (0件のまま)場合は、推測で先へ進まずneeds_reviewへ安全停止する。
    page.set_content(
        '<div class="editor" contenteditable="true"><p>商品A<br>→ 商品を見る</p></div>'
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first
    link = ProductLink(label="商品A", url="https://example.com/a")

    with pytest.raises(NotePosterError):
        poster._wait_for_product_link_applied(page, block, link, timeout_ms=200)


def test_wait_for_product_link_applied_raises_when_anchor_appears_twice(page):
    # 対象ブロック内に「→ 商品を見る」というアクセシブルネームを持つリンクが
    # 2件出現した場合(strict mode違反)も、位置ベースで1件を選ばず安全停止する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<p>商品A<br>'
        '<a href="https://example.com/a">→ 商品を見る</a>'
        '<a href="https://example.com/a">→ 商品を見る</a>'
        "</p></div>"
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first
    link = ProductLink(label="商品A", url="https://example.com/a")

    with pytest.raises(NotePosterError):
        poster._wait_for_product_link_applied(page, block, link, timeout_ms=200)


def test_wait_for_product_link_applied_saves_failure_artifact_when_anchor_missing(page, caplog):
    page.set_content(
        '<div class="editor" contenteditable="true"><p>商品A<br>→ 商品を見る</p></div>'
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first
    link = ProductLink(label="商品A", url="https://example.com/a")

    with caplog.at_level("WARNING"):
        with pytest.raises(NotePosterError):
            poster._wait_for_product_link_applied(page, block, link, timeout_ms=200)

    assert "商品導線URL適用後のリンク未反映" in caplog.text


_LINK_TOOLBAR_HTML_WITH_MISBEHAVING_URL_INPUT = """
<div class="editor" contenteditable="true">
  <p>本文</p>
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
</div>
<div data-active="true" role="toolbar" id="desktop-toolbar">
  <button aria-label="リンク">リンク</button>
</div>
<script>
  document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
    .addEventListener('click', () => {
      const textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      // read-back不一致を再現するため、入力値を強制的に書き換える。
      textarea.addEventListener('input', () => {
        textarea.value = 'https://example.com/UNEXPECTED';
      });
      document.body.appendChild(textarea);
    });
</script>
"""


def test_set_link_on_text_occurrence_raises_when_url_readback_mismatches(page):
    # 入力したURLとread-backした値が一致しない場合は、観測専用停止
    # (UrlInputObservationStop)ではなく通常のNotePosterErrorで安全停止
    # することを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_MISBEHAVING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/expected")

    with pytest.raises(NotePosterError) as exc_info:
        poster._set_link_on_text_occurrence(page, block, link)

    assert not isinstance(exc_info.value, UrlInputObservationStop)


def test_set_link_on_text_occurrence_logs_diagnostics_on_readback_mismatch(page, caplog):
    # read-back不一致の場合も、_capture_failure() 経由で診断サマリの
    # ログが出力されている(=Artifact保存処理が呼ばれている)ことを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_MISBEHAVING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/expected")

    with caplog.at_level("WARNING"):
        with pytest.raises(NotePosterError):
            poster._set_link_on_text_occurrence(page, block, link)

    assert "商品導線URL入力read-back不一致" in caplog.text


_DISAPPEARING_URL_INPUT_TARGET_URL = "https://example.com/vanish"

_LINK_TOOLBAR_HTML_WITH_DISAPPEARING_URL_INPUT = """
<div class="editor" contenteditable="true">
  <p>本文</p>
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
</div>
<div data-active="true" role="toolbar" id="desktop-toolbar">
  <button aria-label="リンク">リンク</button>
</div>
<script>
  document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
    .addEventListener('click', () => {
      const toolbar = document.getElementById('desktop-toolbar');
      const textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      // 実機(TEST-004)で観測された「URL入力完了後にURL入力欄自体が
      // DOMから消失し、active toolbarのdata-activeもfalseに戻る」事象を
      // ローカルで再現する。
      textarea.addEventListener('input', () => {
        if (textarea.value === '__TARGET_URL__') {
          textarea.remove();
          toolbar.setAttribute('data-active', 'false');
        }
      });
      toolbar.appendChild(textarea);
    });
</script>
""".replace("__TARGET_URL__", _DISAPPEARING_URL_INPUT_TARGET_URL)


def test_set_link_on_text_occurrence_raises_dedicated_stop_when_url_input_disappears(page):
    # press_sequentially()完了直後にcount()を再確認し、URL入力欄が消失して
    # いた場合はread-backを試みず、専用のUrlInputDisappearedObservationStop
    # で安全停止することを確認する(2026年8月29日、実機で同一commitが
    # 成功/失敗の両方を示した後に追加)。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_DISAPPEARING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(
        label="TOY JAM 瀬戸内レモン", url=_DISAPPEARING_URL_INPUT_TARGET_URL
    )

    with pytest.raises(UrlInputDisappearedObservationStop):
        poster._set_link_on_text_occurrence(page, block, link)


def test_set_link_on_text_occurrence_disappearance_stop_is_distinct_error_type(page):
    # 消失検知(UrlInputDisappearedObservationStop)は、read-back不一致の
    # 通常のNotePosterErrorや、観測専用停止のUrlInputObservationStop・
    # UrlApplyObservationStopとは異なる、専用の例外型であることを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_DISAPPEARING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(
        label="TOY JAM 瀬戸内レモン", url=_DISAPPEARING_URL_INPUT_TARGET_URL
    )

    with pytest.raises(UrlInputDisappearedObservationStop) as exc_info:
        poster._set_link_on_text_occurrence(page, block, link)

    assert type(exc_info.value) is UrlInputDisappearedObservationStop
    assert not isinstance(exc_info.value, UrlInputObservationStop)
    assert not isinstance(exc_info.value, UrlApplyObservationStop)
    # 消失検知も、呼び出し側(main.py)がneeds_reviewへ倒す既存の共通
    # 例外処理に乗る、NotePosterErrorのサブクラスであること。
    assert isinstance(exc_info.value, NotePosterError)


def test_set_link_on_text_occurrence_saves_failure_artifact_when_url_input_disappears(
    page, caplog
):
    # URL入力欄の消失を検知した場合も、_capture_failure()経由でArtifact
    # (診断サマリログ)が保存されていることを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_DISAPPEARING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(
        label="TOY JAM 瀬戸内レモン", url=_DISAPPEARING_URL_INPUT_TARGET_URL
    )

    with caplog.at_level("WARNING"):
        with pytest.raises(UrlInputDisappearedObservationStop):
            poster._set_link_on_text_occurrence(page, block, link)

    assert "商品導線URL入力後textarea消失" in caplog.text


def test_set_link_on_text_occurrence_logs_stage_f_only_when_url_input_disappears(
    page, caplog
):
    # 消失検知段階(F)のログは、実際に消失したときだけ出力され、その前段の
    # A〜Dのログは(消失前なので)出力されていることを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_DISAPPEARING_URL_INPUT)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(
        label="TOY JAM 瀬戸内レモン", url=_DISAPPEARING_URL_INPUT_TARGET_URL
    )

    with caplog.at_level("INFO"):
        with pytest.raises(UrlInputDisappearedObservationStop):
            poster._set_link_on_text_occurrence(page, block, link)

    for stage in (
        "A_URL入力直前",
        "D_fill完了直後",
        "F_textarea消失検知",
    ):
        assert f"診断[{stage}]" in caplog.text
    # 消失後なのでread-back直前(E)・成功後(G)には到達していない。
    assert "診断[E_read-back直前]" not in caplog.text
    assert "診断[G_read-back成功後]" not in caplog.text


def test_set_link_on_text_occurrence_distinguishes_readback_exception_from_disappearance(
    page, monkeypatch
):
    # count()の再確認では1件だったにもかかわらず、input_value()自体の
    # 呼び出し中に例外が発生した場合は、消失検知(事前チェックで検知する
    # UrlInputDisappearedObservationStop)とは異なる、通常のNotePosterError
    # として例外内容を含めて報告されることを確認する(read-backした値が
    # `None`だったという曖昧な扱いに握りつぶされないことの確認)。
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]

    def _boom(self, *args, **kwargs):
        raise PlaywrightError("boom: simulated mid-call failure during input_value()")

    monkeypatch.setattr(PlaywrightLocator, "input_value", _boom, raising=True)

    with pytest.raises(NotePosterError) as exc_info:
        poster._apply_product_links(page, body_locator, links)

    assert not isinstance(exc_info.value, UrlInputDisappearedObservationStop)
    assert not isinstance(exc_info.value, UrlApplyObservationStop)
    assert not isinstance(exc_info.value, UrlInputObservationStop)
    assert "boom" in str(exc_info.value)
    assert "直前のcount()の再確認では1件だった" in str(exc_info.value)


def test_log_url_input_diagnostics_never_raises_when_elements_are_absent(page):
    # 診断用ログ関数自体は、URL入力欄やactive toolbarが存在しない状態でも
    # 例外を送出せず、取得できる範囲だけを記録することを確認する。
    page.set_content("<div>本文だけ</div>")
    poster = _bare_poster()
    url_input = page.locator(_URL_INPUT_SELECTOR_FOR_TESTS)

    poster._log_url_input_diagnostics(page, url_input, stage="テスト")  # 例外が出なければOK


def test_log_url_input_diagnostics_does_not_steal_focus(page):
    # 診断用ログ関数が、フォーカス済みの要素からフォーカスを奪わない
    # (click/focus/blurのいずれも行わない)ことを実際のDOM上で確認する。
    page.set_content(
        _LINK_TOOLBAR_HTML.replace(
            "</div>\n<script>",
            '</div>\n<input id="already-focused">\n<script>',
        )
    )
    page.locator("#already-focused").click()
    assert page.evaluate("() => document.activeElement.id") == "already-focused"

    poster = _bare_poster()
    url_input = page.locator(_URL_INPUT_SELECTOR_FOR_TESTS)
    poster._log_url_input_diagnostics(page, url_input, stage="フォーカス確認")

    assert page.evaluate("() => document.activeElement.id") == "already-focused"


# -- URL入力欄の値を「追記」ではなく「完全置換」する(ARTICLE-001の2商品 -----
# 連続設定を踏まえた修正、2026年8月29日)
#
# 実機のGitHub Actions実行(ARTICLE-001)で、1商品目のURL入力・「適用」・
# <a>生成までは成功したが、2商品目のURL入力欄のread-backが
# 「(1商品目のURL)(2商品目のURL)」という連結された値になっており、
# NotePosterError(read-back不一致)でneeds_reviewへ安全停止した。原因は
# URL入力欄が1商品目クリック後もクリアされておらず(noteのSPA側がURL入力欄
# のstateを商品間で再利用しているためと考えられる)、press_sequentially()
# (現在のカーソル位置に1文字ずつ追加するだけで、既存値を選択・削除しない)
# を使っていたことだった。fill()(要素へフォーカスしたうえで値を完全に
# 置換する)へ変更した。


def test_fill_replaces_stale_value_on_realistic_url_input_textarea(page):
    # fill()が、noteの実DOM(textarea[placeholder="https://"]
    # [inputmode="text"][name="alt"])と同じ属性を持つ<textarea>に対して、
    # 既存値を完全に置換する(追記にならない)ことを直接確認する
    # (推測でfill()へ変更するのではなく、実DOMと同じ属性の要素で動作を
    # 確認するため)。
    page.set_content(
        '<textarea inputmode="text" name="alt" placeholder="https://"></textarea>'
    )
    url_input = page.locator(_URL_INPUT_SELECTOR_FOR_TESTS)

    url_input.fill("https://you-ichi.jp/?pid=192116331")
    assert url_input.input_value() == "https://you-ichi.jp/?pid=192116331"

    url_input.fill("https://you-ichi.jp/?pid=191552342")
    actual = url_input.input_value()

    assert actual == "https://you-ichi.jp/?pid=191552342"
    assert actual != (
        "https://you-ichi.jp/?pid=192116331https://you-ichi.jp/?pid=191552342"
    )


_LINK_TOOLBAR_HTML_WITH_STALE_URL_VALUE = """
<div class="editor" contenteditable="true">
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>
</div>
<div data-active="true" role="toolbar" id="desktop-toolbar">
  <button aria-label="リンク">リンク</button>
</div>
<script>
  document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
    .addEventListener('click', () => {
      const toolbar = document.getElementById('desktop-toolbar');
      const textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      // 実機で観測された「1商品目クリック後もURL入力欄に前の商品のURLが
      // 残ったまま」という状態をローカルで再現する。
      textarea.value = 'https://you-ichi.jp/?pid=192116331';
      const applyButton = document.createElement('button');
      const applySpan = document.createElement('span');
      applySpan.textContent = '適用';
      applyButton.appendChild(applySpan);
      applyButton.addEventListener('click', (ev) => {
        ev.target.closest('button').setAttribute('data-clicked', 'true');
        const url = textarea.value;
        const targetP = Array.from(
          document.querySelectorAll('.editor p')
        ).find((el) => el.textContent.includes('瀬戸内レモン月桂樹'));
        const textNode = targetP && Array.from(targetP.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE
            && node.textContent.trim() === '→ 商品を見る'
        );
        if (textNode) {
          const a = document.createElement('a');
          a.setAttribute('href', url);
          a.textContent = '→ 商品を見る';
          textNode.replaceWith(a);
        }
        textarea.remove();
        applyButton.remove();
      });
      toolbar.appendChild(textarea);
      toolbar.appendChild(applyButton);
    });
</script>
"""


def test_set_link_on_text_occurrence_replaces_stale_leftover_url_value(page):
    # ARTICLE-001相当のケース: URL入力欄に前の商品のURLが残った状態から
    # 始まっても、fill()による完全置換によって、read-backおよび最終的な
    # hrefが今回の商品のURLと完全一致し、連結された値にならないことを
    # 確認する。
    page.set_content(_LINK_TOOLBAR_HTML_WITH_STALE_URL_VALUE)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(1)
    link = ProductLink(
        label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
    )

    poster._set_link_on_text_occurrence(page, block, link)  # 例外が出なければOK

    anchor = page.locator(".editor a")
    assert anchor.count() == 1
    assert anchor.get_attribute("href") == link.url
    assert anchor.get_attribute("href") != (
        "https://you-ichi.jp/?pid=192116331" + link.url
    )


_LINK_TOOLBAR_HTML_REUSED_STATE_TWO_PRODUCTS = """
<div class="editor" contenteditable="true">
  <p>この記事に出てきた商品</p>
  <p data-product-id="1">TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
  <p data-product-id="2">TOY JAM 瀬戸内レモン月桂樹<br>→ 商品を見る</p>
</div>
<div data-active="true" role="toolbar" id="desktop-toolbar">
  <button aria-label="リンク">リンク</button>
</div>
<script>
  const toolbar = document.getElementById('desktop-toolbar');
  toolbar.querySelector('button[aria-label="リンク"]').addEventListener('click', () => {
    const sel = window.getSelection();
    const range = sel.getRangeAt(0);
    const targetP = range.startContainer.parentElement.closest('p');

    // 実機と同様に、URL入力欄・「適用」ボタンのDOM要素そのものを商品間で
    // 使い回す(既存要素があれば再利用する)。textareaの値は明示的に
    // クリアしない(「前の商品のURLが残ったまま」を再現するため)。
    let textarea = toolbar.querySelector('textarea');
    if (!textarea) {
      textarea = document.createElement('textarea');
      textarea.setAttribute('inputmode', 'text');
      textarea.setAttribute('name', 'alt');
      textarea.setAttribute('placeholder', 'https://');
      toolbar.appendChild(textarea);
    }
    textarea.dataset.targetProductId = targetP.dataset.productId;

    let applyButton = toolbar.querySelector('button[data-role="apply-btn"]');
    if (!applyButton) {
      applyButton = document.createElement('button');
      applyButton.setAttribute('data-role', 'apply-btn');
      const span = document.createElement('span');
      span.textContent = '適用';
      applyButton.appendChild(span);
      toolbar.appendChild(applyButton);
      applyButton.addEventListener('click', () => {
        const url = textarea.value;
        const productId = textarea.dataset.targetProductId;
        const p = document.querySelector('[data-product-id="' + productId + '"]');
        const textNode = p && Array.from(p.childNodes).find(
          (node) => node.nodeType === Node.TEXT_NODE
            && node.textContent.trim() === '→ 商品を見る'
        );
        if (textNode) {
          const a = document.createElement('a');
          a.setAttribute('href', url);
          a.textContent = '→ 商品を見る';
          textNode.replaceWith(a);
        }
      });
    }
  });
</script>
"""


def test_apply_product_links_sets_correct_url_for_each_of_two_consecutive_products(page):
    # ARTICLE-001相当の回帰テスト: URL入力欄・「適用」ボタンのDOM要素が
    # 商品間で使い回され(実機と同様)、1商品目クリック後もURL入力欄の値が
    # クリアされない状態でも、2商品を連続処理したときにそれぞれ正しい
    # (取り違えず、連結もされない)hrefが設定されることを確認する。
    page.set_content(_LINK_TOOLBAR_HTML_REUSED_STATE_TWO_PRODUCTS)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    ]

    poster._apply_product_links(page, body_locator, links)  # 例外が出なければOK

    anchor_1 = page.locator('.editor p[data-product-id="1"] a')
    anchor_2 = page.locator('.editor p[data-product-id="2"] a')
    assert anchor_1.count() == 1
    assert anchor_2.count() == 1
    assert anchor_1.get_attribute("href") == links[0].url
    assert anchor_2.get_attribute("href") == links[1].url
    # 2商品目のhrefが、1商品目URLと2商品目URLの連結値になっていないこと
    # を明示的に確認する。
    assert anchor_2.get_attribute("href") != (links[0].url + links[1].url)


def test_set_link_on_text_occurrence_source_uses_fill_not_press_sequentially_for_url():
    # ARTICLE-001の実機実行(2商品連続設定)で、press_sequentially()による
    # 追記方式が原因でURL入力欄に前の商品のURLが残留・連結する事象が発生
    # したため、fill()による完全置換へ変更したことをソースから確認する
    # 回帰テスト(2026年8月29日)。docstringの説明文中にも
    # press_sequentially()という字面が経緯の説明として登場するため、
    # docstringを除いた実際のコード行だけを対象にする。
    import inspect

    source = inspect.getsource(NotePoster._set_link_on_text_occurrence)
    doc = NotePoster._set_link_on_text_occurrence.__doc__ or ""
    code_only = source.replace(doc, "")
    assert "url_input.fill(link.url)" in code_only
    assert "url_input.press_sequentially(" not in code_only


def test_set_link_on_text_occurrence_applies_publish_action_guard_before_click(page):
    # クリック対象のボタンの表示テキストに公開系キーワードが含まれている
    # (想定外の誤検出)場合は、_assert_not_publish_action()でクリック前に
    # 安全停止する。観測専用停止(LinkButtonObservationStop)とは区別される
    # べき、通常の安全装置によるNotePosterErrorであることを確認する。
    page.set_content(
        """
        <div class="editor" contenteditable="true"><p>商品A<br>→ 商品を見る</p></div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク">投稿する</button>
        </div>
        """
    )
    poster = _bare_poster()
    block = page.locator(".editor p").first

    with pytest.raises(NotePosterError) as exc_info:
        poster._set_link_on_text_occurrence(
            page, block, ProductLink(label="商品A", url="https://example.com/a")
        )

    assert not isinstance(exc_info.value, LinkButtonObservationStop)


def test_set_link_on_text_occurrence_source_has_no_positional_fallback_candidates():
    """リンク設定関連の候補セレクタに、位置ベースの無条件フォールバック
    (「最初の/2番目の」等)が使われていないことを確認する。
    """
    import inspect

    for func in (
        NotePoster._set_link_on_text_occurrence,
        NotePoster._find_active_link_toolbar_button,
        NotePoster._find_url_input_textarea,
        NotePoster._find_url_apply_button,
        NotePoster._find_product_link_block,
        NotePoster._select_product_link_text_in_block,
        NotePoster._wait_for_product_link_applied,
    ):
        source = inspect.getsource(func)
        assert "最終手段" not in source


def test_set_link_on_text_occurrence_source_never_confirms_the_url():
    """URL入力後の確定操作のうち、Enter/Tab送信・意図的なフォーカス解除・
    「URLの入力をやめる」ボタンのクリックはまだ実装していないことを
    ソースから確認する回帰テスト。「適用」ボタンのクリックは実機で確認
    できた確定操作の一部としてすでに実装済みのため、対象外とする。
    """
    import inspect

    source = inspect.getsource(NotePoster._set_link_on_text_occurrence)
    code_only = source.replace(NotePoster._set_link_on_text_occurrence.__doc__ or "", "")
    assert 'press("Enter")' not in code_only
    assert 'press("Tab")' not in code_only
    assert "URLの入力をやめる" not in code_only
    # クリックしているのはlink_button・apply_buttonの2箇所だけであることを
    # 確認する(他の要素をクリックしていないことの確認)。URL入力欄は
    # fill()で値を設定するため、明示的なclick()は行っていない。
    assert code_only.count(".click(") == 2


def _product_trailer_html(entries: list[tuple[str, str | None]]) -> str:
    """(label, href_or_None) のリストから商品導線部分のHTMLを組み立てる。

    実機DOM(TEST-004)に合わせ、商品名と「→ 商品を見る」を同一の<p>要素内に
    <br>を挟んで並べる。hrefがNoneの場合は「→ 商品を見る」をリンクされて
    いないプレーンテキストのままにする(「リンクが設定されていない」ケースの
    再現用)。
    """
    parts = []
    for label, href in entries:
        link_html = f'<a href="{href}">→ 商品を見る</a>' if href is not None else "→ 商品を見る"
        parts.append(f"<p>{label}<br>{link_html}</p>")
    return (
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>" + "".join(parts) + "</div>"
    )


def test_assert_links_match_passes_when_all_links_correct(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/a")]))
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_assert_links_match_passes_when_no_links_expected():
    poster = _bare_poster()
    poster._assert_links_match(
        page=None, body_locator=None, product_links=[], stage="保存前"
    )  # 例外が出なければOK


def test_assert_links_match_raises_on_href_mismatch(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/WRONG")]))
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError, match="不一致"):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_raises_when_link_missing(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", None)]))
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_raises_when_label_is_included_in_link_range(page):
    # 商品名までリンク範囲に含まれてしまった場合(1つの<a>が商品名と
    # 「→ 商品を見る」の両方を包んでいる)、アンカーのテキストが
    # 「→ 商品を見る」と完全一致しなくなるため失敗として検出する。
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p><a href="https://example.com/a">商品A<br>→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_raises_when_label_has_its_own_separate_link(page):
    # 商品名自体にも別のリンクが付いてしまった場合(ブロック内の<a>要素が
    # 2件になる)も失敗として検出する。
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p><a href="https://example.com/a">商品A</a><br>'
        '<a href="https://example.com/a">→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_raises_when_occurrence_count_mismatches(page):
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/a")]))
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_passes_with_multiple_products(page):
    # 複数商品のケース。それぞれのブロックが商品名で正しく特定され、
    # 対応するhrefだけが一致していることを確認する。
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]
    page.set_content(
        _product_trailer_html(
            [
                ("商品A", "https://example.com/a"),
                ("商品B", "https://example.com/b"),
            ]
        )
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_assert_links_match_raises_when_hrefs_are_swapped_between_products(page):
    # 複数商品で、それぞれのブロックには正しくリンクが付いているものの、
    # href同士が入れ替わってしまっている(取り違え)場合を検出できることを
    # 確認する。
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]
    page.set_content(
        _product_trailer_html(
            [
                ("商品A", "https://example.com/b"),  # 本来は商品Aのurlのはず
                ("商品B", "https://example.com/a"),  # 本来は商品Bのurlのはず
            ]
        )
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    with pytest.raises(NotePosterError, match="不一致"):
        poster._assert_links_match(page, body_locator, links, stage="保存前")


def test_assert_links_match_ignores_unrelated_links_elsewhere_in_body(page):
    # 本文中に将来ふつうの参考リンク等が入る可能性があるため、本文editor内の
    # <a>要素の総数を数える検証は行わない設計であることを確認する。
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<p>本文中に<a href="https://reference.example.com">参考リンク</a>があります</p>'
        "<p>この記事に出てきた商品</p>"
        '<p>商品A<br><a href="https://example.com/a">→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_assert_links_match_passes_with_real_dom_span_wrapped_anchor_text(page):
    # 実機Artifact(TEST-004、「適用」クリック後の観測)で確認できた実際の
    # DOM構造(<a href="..."><span class="highlight">→ 商品を見る</span></a>、
    # <a>にtarget="_blank" rel="noopener"付き)に対しても、既存の
    # _assert_links_match()がコード変更なしでそのまま正しく判定できる
    # ことを確認する(2026年8月29日、完成実装ラウンドでの実機DOM監査)。
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p class="paragraph">TOY JAM 瀬戸内レモン<br>'
        '<a href="https://you-ichi.jp/?pid=192116331" target="_blank" rel="noopener">'
        '<span class="highlight">→ 商品を見る</span></a></p>'
        "</div>"
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")

    poster._assert_links_match(page, body_locator, links, stage="保存前")  # 例外が出なければOK


def test_apply_product_links_stops_before_second_link_when_first_link_never_applies(page):
    # 複数のproduct_linksのうち1件でもリンク反映を確認できなければ、
    # 後続の商品の処理には進まず例外を送出することを確認する(呼び出し元の
    # create_draft()では_run_stepがNotePosterErrorをそのまま再送出するため、
    # 下書き保存(_save_draft)へは進まない)。
    page.set_content(
        """
        <div class="editor" contenteditable="true">
          <p>この記事に出てきた商品</p>
          <p>商品A<br>→ 商品を見る</p>
          <p>商品B<br>→ 商品を見る</p>
        </div>
        <div data-active="true" role="toolbar" id="desktop-toolbar">
          <button aria-label="リンク">リンク</button>
        </div>
        <script>
          document.querySelector('#desktop-toolbar button[aria-label="リンク"]')
            .addEventListener('click', () => {
              const toolbar = document.getElementById('desktop-toolbar');
              const textarea = document.createElement('textarea');
              textarea.setAttribute('inputmode', 'text');
              textarea.setAttribute('name', 'alt');
              textarea.setAttribute('placeholder', 'https://');
              const applyButton = document.createElement('button');
              const span = document.createElement('span');
              span.textContent = '適用';
              applyButton.appendChild(span);
              // 意図的に、「適用」クリックしても<a>要素を生成しない
              // (=リンクが反映されないケースを再現する)。
              applyButton.addEventListener('click', () => {
                textarea.remove();
                applyButton.remove();
              });
              toolbar.appendChild(textarea);
              toolbar.appendChild(applyButton);
            });
        </script>
        """
    )
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]

    with pytest.raises(NotePosterError):
        poster._apply_product_links(page, body_locator, links)

    # 商品Bのブロックには一切触れていない(商品Aの反映確認で安全停止した)
    # ことを確認する。
    assert page.locator(".editor a").count() == 0


# -- 商品リンク自動設定をproductionでは呼ばない運用への変更(ARTICLE-001の -----
# 実機実行を踏まえた運用方針の変更、2026年8月29日)
#
# note.com側の商品リンク設定UI(フローティング編集ツールバー・URL入力欄・
# 「適用」ボタン)の実機での不安定さ(URL入力欄の消失、値の残留・連結、
# position: fixedによるviewport外配置、2商品目の<a>反映timeoutなど)を
# 受けて、create_draft()はproduct_linksが指定されていても
# _apply_product_links()・_assert_links_match()を呼ばない運用に変更した。
# 商品リンク自動設定関連のメソッド・例外クラス自体は削除せず、将来
# note.com側のUIが安定した場合の再検証に備えて残している。


def test_create_draft_source_does_not_call_product_link_automation():
    """create_draft()のソースに、_apply_product_links()・
    _assert_links_match()の呼び出しが含まれていないことを確認する回帰
    テスト(2026年8月29日、商品リンク自動設定をproductionでは呼ばない
    運用への変更)。docstringの説明文中にはこれらのメソッド名が経緯の
    説明として登場するため、docstringを除いた実際のコード行だけを対象に
    する。
    """
    import inspect

    source = inspect.getsource(NotePoster.create_draft)
    doc = NotePoster.create_draft.__doc__ or ""
    code_only = source.replace(doc, "")
    assert "self._apply_product_links(" not in code_only
    assert "self._assert_links_match(" not in code_only


def test_create_draft_source_still_saves_draft_and_verifies_body_readback():
    """create_draft()が、商品リンク自動設定を呼ばなくなった後も、
    タイトル・本文の入力、下書き保存、保存前後の本文read-back確認は
    引き続き行っていることをソースから確認する回帰テスト。
    """
    import inspect

    source = inspect.getsource(NotePoster.create_draft)
    doc = NotePoster.create_draft.__doc__ or ""
    code_only = source.replace(doc, "")
    assert "self._fill_title(" in code_only
    assert "self._fill_body(" in code_only
    assert "self._save_draft(" in code_only
    assert code_only.count("self._assert_body_matches(") == 2


def test_product_link_automation_methods_still_exist_for_future_reenablement():
    """商品リンク自動設定関連のメソッド・例外クラスが、production run
    pathから呼ばれなくなった後も削除されずに残っていることを確認する
    回帰テスト(将来note.com側のUIが安定した場合の再検証に備えるため)。
    """
    for method_name in (
        "_apply_product_links",
        "_assert_links_match",
        "_set_link_on_text_occurrence",
        "_find_product_link_block",
        "_select_product_link_text_in_block",
        "_wait_for_product_link_applied",
        "_ensure_product_link_block_in_viewport",
        "_find_active_link_toolbar_button",
        "_find_url_input_textarea",
        "_find_url_apply_button",
        "_log_url_input_diagnostics",
    ):
        assert hasattr(NotePoster, method_name), f"{method_name} が削除されています"

    from src import note as note_module

    for class_name in (
        "LinkButtonObservationStop",
        "UrlInputObservationStop",
        "UrlApplyObservationStop",
        "UrlInputDisappearedObservationStop",
        "LinkButtonOutOfViewportError",
        "ProductLinkBlockOutOfViewportError",
    ):
        assert hasattr(note_module, class_name), f"{class_name} が削除されています"


def test_build_product_links_trailer_still_produces_plain_text_without_urls(page):
    # 商品リンク自動設定を呼ばなくなった後も、本文には商品名・「→ 商品を
    # 見る」がプレーンテキストとして残ること(ECの生URLは含まれない)を
    # 確認する。
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(
            label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"
        ),
    ]
    body = build_body_with_hashtags("本文です。", [], links)

    assert "TOY JAM 瀬戸内レモン" in body
    assert "TOY JAM 瀬戸内レモン月桂樹" in body
    assert body.count("→ 商品を見る") == 2
    assert "https://" not in body
    assert "you-ichi.jp" not in body


def test_assert_body_matches_detects_card_like_extra_content_in_product_trailer(page):
    # 実機で発生した不具合(本文中の生URLがnoteによって商品カードへ自動
    # 変換され、本文read-backで想定外の追加テキストが検出された)の再現。
    # 商品導線方式に切り替えた後も、この検知能力自体は弱めていないことを
    # 確認する回帰テスト。
    page.set_content('<div contenteditable="true" class="editor"></div>')
    poster = _bare_poster()
    editor = page.locator(".editor")
    expected = build_body_with_hashtags(
        "本文", [], [ProductLink(label="商品A", url="https://example.com/a")]
    )
    # 商品カード化により、本来無いはずの追加テキストが混入した状況を再現する。
    corrupted = expected + "商品画像 価格 購入する"
    poster._set_multiline_text(page, editor, corrupted)

    with pytest.raises(NotePosterError, match="一致しませんでした"):
        poster._assert_body_matches(page, editor, expected, "", stage="保存前")
