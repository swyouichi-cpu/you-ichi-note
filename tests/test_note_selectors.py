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
    NotePoster,
    NotePosterError,
    ProductLink,
    ProductLinkValidationError,
    TagValidationError,
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


# -- 商品導線リンクの設定・検証(実機で確認されたnoteの選択ツールバー方式) --
#
# 人間が実機で、本文中の「→ 商品を見る」だけを選択するとフローティング
# ツールバーが表示され、リンク(鎖アイコン)からURLを設定すると商品カードへ
# 変換されずインラインリンクになることを確認した。以下はそのUI操作を
# ローカルの疑似ページで再現したテスト。ツールバー自体の正確なDOM構造は
# 実機のHTMLダンプではまだ確認できていないため、ここでの疑似UIは
# 「role=button name=リンク」「input[type=url]」という、_apply_product_links
# が実際に試す候補の1つを模したものであり、実機と完全に一致する保証はない。

_LINK_TOOLBAR_HTML = """
<div class="editor" contenteditable="true">
  <p>本文</p>
  <p>この記事に出てきた商品</p>
  <p>TOY JAM 瀬戸内レモン</p>
  <p>→ 商品を見る</p>
  <p>TOY JAM 瀬戸内レモン月桂樹</p>
  <p>→ 商品を見る</p>
</div>
<div id="toolbar" style="display:none">
  <button id="link-btn">リンク</button>
</div>
<input type="url" id="url-input" style="display:none">
<script>
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    const toolbar = document.getElementById('toolbar');
    if (sel && !sel.isCollapsed && sel.toString().trim().length > 0) {
      toolbar.style.display = 'block';
    } else {
      toolbar.style.display = 'none';
    }
  });
  document.getElementById('link-btn').addEventListener('click', () => {
    window.__savedRange = window.getSelection().getRangeAt(0).cloneRange();
    document.getElementById('url-input').style.display = 'block';
  });
  document.getElementById('url-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const range = window.__savedRange;
      const a = document.createElement('a');
      a.href = document.getElementById('url-input').value;
      a.textContent = range.toString();
      range.deleteContents();
      range.insertNode(a);
      document.getElementById('url-input').style.display = 'none';
      document.getElementById('toolbar').style.display = 'none';
      document.getElementById('url-input').value = '';
    }
  });
</script>
"""


def test_apply_product_links_sets_correct_href_on_each_occurrence(page):
    page.set_content(_LINK_TOOLBAR_HTML)
    poster = _bare_poster()
    links = [
        ProductLink(label="TOY JAM 瀬戸内レモン", url="https://you-ichi.jp/?pid=192116331"),
        ProductLink(label="TOY JAM 瀬戸内レモン月桂樹", url="https://you-ichi.jp/?pid=191552342"),
    ]

    poster._apply_product_links(page, links)

    # get_by_text(exact=True)は完全一致テキストを持つ最も内側の要素を返す。
    # リンク設定後は<p><a>→ 商品を見る</a></p>のようにa要素の方が内側になる
    # ため、a要素自身が返る(_assert_links_matchと同じ理由)。
    occurrences = page.get_by_text("→ 商品を見る", exact=True)
    assert occurrences.nth(0).evaluate("el => el.tagName") == "A"
    assert occurrences.nth(0).get_attribute("href") == links[0].url
    assert occurrences.nth(1).evaluate("el => el.tagName") == "A"
    assert occurrences.nth(1).get_attribute("href") == links[1].url
    # 商品名自体はリンクされていない。
    label_element = page.get_by_text("TOY JAM 瀬戸内レモン", exact=True).first
    assert label_element.evaluate("el => el.tagName") != "A"
    assert label_element.locator("a").count() == 0


def test_apply_product_links_is_noop_when_no_links():
    poster = _bare_poster()
    # ページを用意せずとも、product_links が空なら何もせず正常終了するはず。
    poster._apply_product_links(page=None, product_links=[])  # 例外が出なければOK


def test_apply_product_links_raises_when_occurrence_count_mismatches(page):
    # 「→ 商品を見る」が本文中に1件しか無いのに、2件のproduct_linksを
    # 渡した場合、どちらがどちらに対応するか一意に定まらないため安全停止する。
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A</p>"
        "<p>→ 商品を見る</p>"
        "</div>"
    )
    poster = _bare_poster()
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]

    with pytest.raises(NotePosterError):
        poster._apply_product_links(page, links)


def test_set_link_on_text_occurrence_raises_when_toolbar_not_found(page):
    # リンク設定UI(ツールバー・URL入力欄)が全く存在しないページでは、
    # 位置ベースの推測に頼らずNotePosterErrorで安全停止する。
    page.set_content('<div contenteditable="true"><p>→ 商品を見る</p></div>')
    poster = _bare_poster()
    target = page.get_by_text("→ 商品を見る", exact=True).first

    with pytest.raises(NotePosterError):
        poster._set_link_on_text_occurrence(
            page, target, ProductLink(label="商品A", url="https://example.com/a")
        )


def test_set_link_on_text_occurrence_source_has_no_positional_fallback_candidates():
    """リンク設定ボタン・URL入力欄の候補セレクタに、位置ベースの無条件
    フォールバック(「最初の/2番目の」等)が使われていないことを確認する。
    """
    import inspect

    source = inspect.getsource(NotePoster._set_link_on_text_occurrence)
    assert "最終手段" not in source


def _product_trailer_html(entries: list[tuple[str, str | None]]) -> str:
    """(label, href_or_None) のリストから商品導線部分のHTMLを組み立てる。

    hrefがNoneの場合はリンクされていないプレーンテキストのままにする
    (「リンクが設定されていない」ケースの再現用)。
    """
    parts = []
    for label, href in entries:
        link_html = f'<a href="{href}">→ 商品を見る</a>' if href is not None else "→ 商品を見る"
        parts.append(f"<p>{label}</p><p>{link_html}</p>")
    return (
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>" + "".join(parts) + "</div>"
    )


def test_assert_links_match_passes_when_all_links_correct(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/a")]))
    poster = _bare_poster()

    poster._assert_links_match(page, links, stage="保存前")  # 例外が出なければOK


def test_assert_links_match_passes_when_no_links_expected():
    poster = _bare_poster()
    poster._assert_links_match(page=None, product_links=[], stage="保存前")  # 例外が出なければOK


def test_assert_links_match_raises_on_href_mismatch(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/WRONG")]))
    poster = _bare_poster()

    with pytest.raises(NotePosterError, match="不一致"):
        poster._assert_links_match(page, links, stage="保存前")


def test_assert_links_match_raises_when_link_missing(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(_product_trailer_html([("商品A", None)]))
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, links, stage="保存前")


def test_assert_links_match_raises_when_label_itself_is_linked(page):
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        "<p>この記事に出てきた商品</p>"
        '<p><a href="https://example.com/a">商品A</a></p>'
        '<p><a href="https://example.com/a">→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, links, stage="保存前")


def test_assert_links_match_raises_when_occurrence_count_mismatches(page):
    links = [
        ProductLink(label="商品A", url="https://example.com/a"),
        ProductLink(label="商品B", url="https://example.com/b"),
    ]
    page.set_content(_product_trailer_html([("商品A", "https://example.com/a")]))
    poster = _bare_poster()

    with pytest.raises(NotePosterError):
        poster._assert_links_match(page, links, stage="保存前")


def test_assert_links_match_ignores_unrelated_links_elsewhere_in_body(page):
    # 本文中に将来ふつうの参考リンク等が入る可能性があるため、本文editor内の
    # <a>要素の総数を数える検証は行わない設計であることを確認する。
    links = [ProductLink(label="商品A", url="https://example.com/a")]
    page.set_content(
        '<div class="editor" contenteditable="true">'
        '<p>本文中に<a href="https://reference.example.com">参考リンク</a>があります</p>'
        "<p>この記事に出てきた商品</p>"
        "<p>商品A</p>"
        '<p><a href="https://example.com/a">→ 商品を見る</a></p>'
        "</div>"
    )
    poster = _bare_poster()

    poster._assert_links_match(page, links, stage="保存前")  # 例外が出なければOK


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
