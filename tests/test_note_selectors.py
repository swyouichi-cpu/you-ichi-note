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

from src.models import Article
from src.note import (  # noqa: E402
    LinkButtonObservationStop,
    LinkButtonOutOfViewportError,
    NotePoster,
    NotePosterError,
    ProductLink,
    ProductLinkValidationError,
    TagValidationError,
    UrlApplyObservationStop,
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


def test_apply_product_links_clicks_toolbar_button_and_inputs_url_then_stops_for_observation(page):
    # リンクボタンをクリックした先に出現するURL入力欄まで到達し、URLを
    # 入力してread-backが一致することを確認したうえで、ツールバー内の
    # 「適用」ボタンをクリックし、意図的にUrlApplyObservationStopで安全
    # 停止する(観測専用実装・第4段階)。URLの確定操作のうちEnter/Tab/
    # フォーカス解除/他要素クリックは一切行わない(「適用」ボタンの
    # クリックだけが、実機で確認できた確定操作の一部として実装されている)。
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    body_locator = page.locator(".editor")
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
    ]

    with pytest.raises(UrlApplyObservationStop):
        poster._apply_product_links(page, body_locator, links)

    link_button = page.locator('#desktop-toolbar button[aria-label="リンク"]')
    assert link_button.get_attribute("data-clicked") == "true"

    url_input = page.locator(_URL_INPUT_SELECTOR_FOR_TESTS)
    assert url_input.input_value() == links[0].url

    apply_button = page.locator("#desktop-toolbar").get_by_role(
        "button", name="適用", exact=True
    )
    assert apply_button.get_attribute("data-clicked") == "true"
    # <a href>の生成確認・_assert_links_match()・下書き保存にはまだ
    # 進んでいないため、<a>要素はまだ作られていない。
    assert body_locator.locator("a").count() == 0
    # Enter/Tabを送信していないことを確認する(press_sequentially由来の
    # 文字キー以外が送られていないこと)。
    sent_keys = page.evaluate("() => window.__urlInputKeys")
    assert "Enter" not in sent_keys
    assert "Tab" not in sent_keys
    # 診断処理(_capture_failure)自身が意図しないフォーカス操作を行わない
    # ことも確認する(直前にクリックした「適用」ボタンがactiveElementの
    # ままであること)。
    assert page.evaluate("() => document.activeElement.tagName") == "BUTTON"


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


def test_find_product_link_block_raises_when_label_appears_in_multiple_blocks(page):
    # 商品名が本文中に偶然複数回出現する場合、どのブロックが商品導線かを
    # 安全に一意特定できないため中断する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>商品A</p>"
        "<p>この記事に出てきた商品</p>"
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


def test_set_link_on_text_occurrence_inputs_url_and_clicks_apply_then_raises_apply_observation_stop(
    page,
):
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    block = page.locator(".editor p").nth(2)
    link = ProductLink(label="TOY JAM 瀬戸内レモン", url="https://example.com/a")

    with pytest.raises(UrlApplyObservationStop):
        poster._set_link_on_text_occurrence(page, block, link)

    link_button = page.locator('#desktop-toolbar button[aria-label="リンク"]')
    assert link_button.get_attribute("data-clicked") == "true"
    url_input = page.locator(_URL_INPUT_SELECTOR_FOR_TESTS)
    assert url_input.input_value() == link.url
    apply_button = page.locator("#desktop-toolbar").get_by_role(
        "button", name="適用", exact=True
    )
    assert apply_button.get_attribute("data-clicked") == "true"
    # <a href>の生成確認・_assert_links_match()・下書き保存にはまだ
    # 進んでいないため、<a>要素はまだ作られていない。
    assert page.locator(".editor a").count() == 0


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


def test_ensure_link_button_in_viewport_does_not_use_force_or_javascript_click():
    """クリック前のviewport確認処理が、force=Trueによるactionability
    check迂回や、JavaScript経由の直接クリック(el.click()等)を一切
    使っていないことをソースから確認する回帰テスト。
    """
    import inspect

    for func in (
        NotePoster._ensure_link_button_in_viewport,
        NotePoster._set_link_on_text_occurrence,
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
    # クリックしているのはlink_button・url_input・apply_buttonの3箇所だけ
    # であることを確認する(他の要素をクリックしていないことの確認)。
    assert code_only.count(".click(") == 3


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
