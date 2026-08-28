"""note.com への下書き作成を Playwright で自動操作する。

★重要な注意(このファイルを読む/直す人へ)★
note.com は公式APIを提供しておらず、ここに書かれているセレクタ(画面上の
どのボタン・入力欄を操作するかの指定)は、note側のUI変更で壊れうる前提で
運用してください。1つの指定方法だけに依存せず、複数の候補を順番に試す
「フォールバック方式」にしているのはそのためです。

このファイルを書いている環境(Claudeの実行環境)は、ネットワーク制限により
note.com へ直接アクセスして画面を確認することができません。そのため、
セレクタが実際に正しいかどうかは GitHub Actions 上での実行結果でしか
確認できません。失敗した場合は、後述の「失敗時の診断データ」
(スクリーンショット・HTMLダンプ)を頼りに次の修正を行う設計にしています。

★安全設計(絶対に守ること)★
このファイルには「公開する」ボタンを押すコードを一切含めない。
実装できるのは「下書き保存」までであり、公開操作を自動化する
コードパスを追加する場合は、必ず別途ユーザーの明示的な合意を得ること。
念のため、保存ボタンをクリックする直前にボタンの文言を確認し、
「公開」系の単語が含まれていたら処理を中断する安全装置も入れている。

★ログイン方式★
メールアドレス・パスワードを直接入力させる方式は、note側のreCAPTCHA
導入により機能しない可能性が高いため採用しない。代わりに、
ユーザーが scripts/note_login_bootstrap.py をローカルで実行して
事前に取得した「ログイン済みセッション情報(storage_state)」を
そのまま使い回す方式にしている。storage_stateの中身・Cookie・トークンは
このファイルのどのログ出力にも含めない。

★失敗時の診断データ★
NOTE_DEBUG_SCREENSHOT_DIR が設定されている場合、各ステップの成功時に加え、
失敗した瞬間にもスクリーンショット・page.content()のHTMLダンプ・
テキスト診断ファイル(_diag.txt)を保存する。_diag.txtには以下を含める。
  - 失敗時点の page.url() / document.title / document.readyState
  - JSコンソールに出力されたログ・エラー(ブラウザ内のJS実行状況を見るため)
  - 読み込みに失敗したリクエスト(requestfailed)
  - HTTPステータスが2xx/3xx以外だったレスポンスの一覧
これらはいずれもDOM・ネットワークの状態を見るための情報であり、
Cookie・セッション情報・GitHub Secretsの中身は一切含まれない
(レスポンスのヘッダ・ボディそのものは記録しない。URL・メソッド・
ステータスコードのみを記録し、URLのクエリ文字列も念のため除去する)。
ただし記事本文などあなたのコンテンツそのものは画面/ページ内テキストとして
写り得るため、共有前に中身を確認すること。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from src.config import NoteConfig
from src.logger import get_logger
from src.models import Article

logger = get_logger()

NOTE_NEW_NOTE_URL = "https://note.com/notes/new"

# 保存ボタンをクリックする直前、ボタンの文言にこれらの単語が含まれていたら
# 「公開」系のボタンだと判断して中断する(誤ってセレクタが公開ボタンに
# マッチしてしまった場合の保険)。
_FORBIDDEN_PUBLISH_KEYWORDS = ["公開に進む", "公開する", "予約投稿", "投稿する", "Publish"]

# デバッグ用にスクリーンショット/HTMLダンプを保存したい場合、環境変数で有効化する。
_SCREENSHOT_DIR = os.environ.get("NOTE_DEBUG_SCREENSHOT_DIR", "").strip()

_DEFAULT_CANDIDATE_TIMEOUT_MS = 4000

# 診断データが際限なく増えないよう、記録件数の上限を設ける。
_MAX_DIAG_ENTRIES = 300


def _strip_query(url: str) -> str:
    """URLからクエリ文字列を除去する(トークン等が紛れ込む可能性への念のための対策)。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class NotePosterError(RuntimeError):
    """note操作中の失敗。呼び出し側でneeds_review/errorに振り分けるための例外。"""


class NotePoster:
    def __init__(self, config: NoteConfig | None = None):
        self._config = config or NoteConfig.load()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._step_count = 0
        # ページの読み込み状況を診断するための記録(秘密情報は含めない)。
        self._console_messages: list[str] = []
        self._page_errors: list[str] = []
        self._failed_requests: list[str] = []
        self._responses: list[tuple[str, int]] = []

    def __enter__(self) -> "NotePoster":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        try:
            storage_state = json.loads(self._config.storage_state_json)
        except json.JSONDecodeError as exc:
            raise NotePosterError(
                "NOTE_STORAGE_STATE がJSONとして読み取れません。"
                "scripts/note_login_bootstrap.py で取得したファイルの中身を"
                "そのまま設定しているか確認してください。"
            ) from exc
        self._context = self._browser.new_context(storage_state=storage_state)
        return self

    def __exit__(self, *_exc_info) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    # -- 診断データ(秘密情報は含まない) --------------------------------------

    def _attach_diagnostics(self, page: Page) -> None:
        """ページのJS実行状況・ネットワーク状況を記録するリスナーを登録する。

        「読み込みが完了しない」原因(JSエラー、APIの401/403、リクエスト失敗など)
        を後から追えるようにするための仕組み。ここで記録するのはURL・メソッド・
        ステータスコード・コンソールのテキストのみで、Cookie・認証ヘッダ・
        レスポンス本文は一切記録しない。
        """

        def _on_console(msg) -> None:
            if len(self._console_messages) < _MAX_DIAG_ENTRIES:
                text = msg.text[:500] if msg.text else ""
                self._console_messages.append(f"[{msg.type}] {text}")

        def _on_pageerror(exc) -> None:
            if len(self._page_errors) < _MAX_DIAG_ENTRIES:
                self._page_errors.append(str(exc)[:500])

        def _on_requestfailed(request) -> None:
            if len(self._failed_requests) < _MAX_DIAG_ENTRIES:
                failure = request.failure or "(不明)"
                self._failed_requests.append(
                    f"{request.method} {_strip_query(request.url)} -> {failure}"
                )

        def _on_response(response) -> None:
            if len(self._responses) < _MAX_DIAG_ENTRIES:
                self._responses.append((_strip_query(response.url), response.status))

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)
        page.on("requestfailed", _on_requestfailed)
        page.on("response", _on_response)

    def _diagnostics_text(self, page: Page, step_name: str) -> str:
        """失敗時点の状況をまとめたテキストを組み立てる(秘密情報を含まない)。"""
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            url = "(取得失敗)"
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = "(取得失敗)"
        try:
            ready_state = page.evaluate("document.readyState")
        except Exception:  # noqa: BLE001
            ready_state = "(取得失敗)"
        try:
            body_snippet = page.evaluate(
                "(document.body && document.body.innerText || '').trim().slice(0, 300)"
            )
        except Exception:  # noqa: BLE001
            body_snippet = "(取得失敗)"

        error_responses = [
            f"{status} {url_}" for url_, status in self._responses if status >= 400
        ]
        status_counts: dict[int, int] = {}
        for _url, status in self._responses:
            status_counts[status] = status_counts.get(status, 0) + 1

        lines = [
            f"failed_step: {step_name}",
            f"page.url(): {url}",
            f"document.title: {title}",
            f"document.readyState: {ready_state}",
            "",
            f"body innerText(先頭300文字): {body_snippet!r}",
            "",
            f"console messages ({len(self._console_messages)}件、末尾20件を表示):",
            *[f"  {m}" for m in self._console_messages[-20:]],
            "",
            f"pageerror ({len(self._page_errors)}件):",
            *[f"  {m}" for m in self._page_errors],
            "",
            f"requestfailed ({len(self._failed_requests)}件):",
            *[f"  {m}" for m in self._failed_requests],
            "",
            f"response status件数: {status_counts}",
            f"4xx/5xxのレスポンス ({len(error_responses)}件):",
            *[f"  {m}" for m in error_responses],
        ]
        return "\n".join(lines)

    def _debug_dir(self) -> Path | None:
        if not _SCREENSHOT_DIR:
            return None
        out_dir = Path(_SCREENSHOT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _screenshot(self, page: Page, label: str) -> None:
        out_dir = self._debug_dir()
        if out_dir is None:
            return
        self._step_count += 1
        path = out_dir / f"{self._step_count:02d}_{label}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info("スクリーンショット保存: %s", path)

    def _dump_html(self, page: Page, label: str) -> None:
        """失敗時にDOM構造を保存する(Cookie/セッション情報は含まれない)。"""
        out_dir = self._debug_dir()
        if out_dir is None:
            return
        path = out_dir / f"{self._step_count:02d}_{label}.html"
        path.write_text(page.content(), encoding="utf-8")
        logger.info("HTMLダンプ保存: %s", path)

    def _capture_failure(self, page: Page, step_name: str) -> None:
        self._screenshot(page, f"FAILED_{step_name}")
        self._dump_html(page, f"FAILED_{step_name}")

        diag_text = self._diagnostics_text(page, step_name)
        out_dir = self._debug_dir()
        if out_dir is not None:
            diag_path = out_dir / f"{self._step_count:02d}_FAILED_{step_name}_diag.txt"
            diag_path.write_text(diag_text, encoding="utf-8")
            logger.info("診断テキスト保存: %s", diag_path)

        # ログには全文ではなく要約だけを出す(GitHub Actionsのログが
        # 肥大化しすぎないようにするため。詳細はArtifactのdiag.txtを見る)。
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            url = "(取得失敗)"
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = "(取得失敗)"
        error_response_count = sum(1 for _u, status in self._responses if status >= 400)
        logger.warning(
            "診断サマリ [%s]: url=%s title=%r console=%d件 pageerror=%d件 "
            "requestfailed=%d件 4xx/5xx応答=%d件",
            step_name,
            url,
            title,
            len(self._console_messages),
            len(self._page_errors),
            len(self._failed_requests),
            error_response_count,
        )

    # -- 複数候補セレクタから最初に見つかったものを使う仕組み -------------------

    def _resolve_locator(
        self,
        page: Page,
        candidates: list[tuple[str, Locator]],
        step_name: str,
        timeout_ms: int = _DEFAULT_CANDIDATE_TIMEOUT_MS,
    ) -> Locator:
        """候補セレクタを順番に試し、最初に画面上に現れたものを返す。

        note.comのUI変更で1つのセレクタが壊れても、他の候補で拾えるようにする
        ためのフォールバック機構。全滅した場合は診断データを残してから
        NotePosterError を送出する。
        """
        tried: list[str] = []
        for label, locator in candidates:
            tried.append(label)
            try:
                locator.first.wait_for(state="visible", timeout=timeout_ms)
                logger.info("  [%s] 候補 '%s' で要素を検出", step_name, label)
                return locator.first
            except PlaywrightTimeoutError:
                continue

        self._capture_failure(page, step_name)
        raise NotePosterError(
            f"{step_name}: 試した候補セレクタ({', '.join(tried)})のいずれにも"
            f"一致する表示中の要素が見つかりませんでした。noteの画面構成が"
            f"変わった可能性があります。"
            + (
                f" 診断データを {_SCREENSHOT_DIR} に保存しました。"
                if _SCREENSHOT_DIR
                else " NOTE_DEBUG_SCREENSHOT_DIRを設定して再実行すると、"
                "失敗時のスクリーンショットとHTMLが確認できます。"
            )
        )

    def _assert_logged_in(self, page: Page) -> None:
        """ログイン済みか確認する。ログイン画面に飛ばされていたらセッション切れ。"""
        if "login" in page.url:
            self._capture_failure(page, "login_check")
            raise NotePosterError(
                "noteのログインセッションが無効になっている可能性があります"
                "(ログイン画面にリダイレクトされました)。"
                "scripts/note_login_bootstrap.py を再実行し、"
                "NOTE_STORAGE_STATE を更新してください。"
            )

    def _wait_for_editor_mounted(self, page: Page, timeout_ms: int = 15000) -> None:
        """SPAの画面がローディング状態のまま止まっていないかを確認する。

        タイトル欄などの個別セレクタを探す前に、まず「アプリ自体が
        何かしら描画されているか」を広めの条件でチェックする。ここで
        失敗した場合は、個別のセレクタが変わったのではなく、
        JSの実行やAPI呼び出し自体が失敗している可能性が高いと判断できる。
        """
        try:
            page.wait_for_function(
                """
                () => {
                  // ローディングスピナーそのものもDOM上は「子要素」として存在するため、
                  // 単に子要素の有無だけでは判定しない。実際に入力可能なフォーム要素、
                  // または一定量の可視テキストが現れたかどうかで判定する。
                  const hasFormFields =
                    document.querySelectorAll('textarea, [contenteditable="true"]').length > 0;
                  const hasVisibleText =
                    (document.body && document.body.innerText || '').trim().length > 50;
                  return hasFormFields || hasVisibleText;
                }
                """,
                timeout=timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            self._capture_failure(page, "エディタ読み込み確認")
            raise NotePosterError(
                "note編集画面のアプリ本体が読み込まれた形跡が確認できませんでした"
                "(ローディング状態のまま止まっている可能性があります)。"
                "セレクタの問題ではなく、JSの実行やAPI呼び出し自体が失敗している"
                "可能性があります。診断データ(コンソールログ・失敗したリクエスト・"
                "HTTPステータス)を確認してください。"
            ) from exc

    def _run_step(self, page: Page, step_name: str, action) -> None:
        """1ステップを実行し、どこで失敗しても診断データを残してから
        NotePosterError として送出し直す(呼び出し側での原因特定を助けるため)。
        """
        try:
            action()
        except NotePosterError:
            raise  # _resolve_locator側で既に診断データを残しているのでそのまま
        except PlaywrightTimeoutError as exc:
            self._capture_failure(page, step_name)
            raise NotePosterError(
                f"{step_name} でタイムアウトしました: {exc}"
                + (f" 診断データを {_SCREENSHOT_DIR} に保存しました。" if _SCREENSHOT_DIR else "")
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 想定外の失敗も必ず診断データを残す
            self._capture_failure(page, step_name)
            raise NotePosterError(f"{step_name} で予期しないエラー: {exc}") from exc

    def create_draft(self, article: Article) -> str:
        """記事を入力し、下書き保存する。戻り値は下書き編集画面のURL。

        絶対に公開ボタンは押さない。成功したら "draft_created" をログに残す。
        """
        assert self._context is not None, "with文の中で使ってください"
        page = self._context.new_page()
        self._attach_diagnostics(page)

        logger.info("noteの新規作成画面へアクセス")
        self._run_step(
            page, "画面アクセス", lambda: page.goto(NOTE_NEW_NOTE_URL, wait_until="networkidle")
        )
        logger.info("画面アクセス直後: url=%s title=%r", page.url, page.title())
        self._screenshot(page, "01_opened_new_note")
        self._assert_logged_in(page)

        logger.info("エディタの読み込み完了を確認")
        self._run_step(page, "エディタ読み込み確認", lambda: self._wait_for_editor_mounted(page))
        logger.info("エディタ読み込み確認OK: url=%s title=%r", page.url, page.title())
        # SPAはJS側で非同期にログイン状態を確認してから /login へ遷移することが
        # あるため、goto直後だけでなくここでも再確認する。
        self._assert_logged_in(page)

        logger.info("タイトルを入力")
        self._run_step(page, "タイトル入力", lambda: self._fill_title(page, article.title))
        self._screenshot(page, "02_title_filled")

        logger.info("本文を入力")
        self._run_step(page, "本文入力", lambda: self._fill_body(page, article.body))
        self._screenshot(page, "03_body_filled")

        tags = article.tag_list()
        if tags:
            logger.info("タグを入力(%d件)", len(tags))
            self._run_step(page, "タグ入力", lambda: self._fill_tags(page, tags))
            self._screenshot(page, "04_tags_filled")

        logger.info("下書き保存")
        self._run_step(page, "下書き保存", lambda: self._save_draft(page))
        self._screenshot(page, "05_saved_draft")

        note_url = page.url
        page.close()
        logger.info("draft_created id=%s note_url=%s", article.id, note_url)
        return note_url

    # -- 入力補助 -------------------------------------------------------------

    def _set_single_line_text(self, locator: Locator, text: str) -> None:
        """タイトルなど1行の入力欄に安全にテキストを入れる。

        fill()は要素の種類(textarea/contenteditable)によって効かない場合が
        あるため、クリック→全選択→削除→入力、で統一する。
        """
        locator.click()
        locator.press("Control+A")
        locator.press("Backspace")
        locator.press_sequentially(text, delay=10)

    def _set_multiline_text(self, page: Page, locator: Locator, text: str) -> None:
        """本文などリッチテキストエディタ(contenteditable)へ複数行を入力する。

        1文字ずつ全体をタイプすると長文で時間がかかりすぎるため、
        行単位で insert_text し、行の区切りだけ Enter キーで表現する。
        """
        locator.click()
        locator.press("Control+A")
        locator.press("Backspace")
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                page.keyboard.insert_text(line)
            if i < len(lines) - 1:
                page.keyboard.press("Enter")

    def _assert_not_publish_action(self, locator: Locator) -> None:
        """クリック対象が誤って「公開」系のボタンになっていないか最終確認する。"""
        try:
            text = (locator.inner_text() or "").strip()
        except PlaywrightTimeoutError:
            text = ""
        if any(keyword in text for keyword in _FORBIDDEN_PUBLISH_KEYWORDS):
            raise NotePosterError(
                f"安全装置により処理を中断しました: クリック対象のボタンの文言"
                f"('{text}')に公開系のキーワードが含まれています。セレクタが"
                f"意図しないボタンに一致している可能性があります。"
            )

    # -- 各ステップの実装(note.comのUI変更に備えて複数候補を用意) -------------

    def _fill_title(self, page: Page, title: str) -> None:
        candidates = [
            ("role=textbox name=タイトル", page.get_by_role("textbox", name="タイトル")),
            ("placeholder=タイトル(完全一致)", page.get_by_placeholder("タイトル", exact=True)),
            ("placeholder*=タイトル(部分一致)", page.get_by_placeholder(re.compile("タイトル"))),
            (
                "css textarea[placeholder*=タイトル]",
                page.locator('textarea[placeholder*="タイトル"]'),
            ),
            (
                "css [class*=title] 系のtextarea/contenteditable",
                page.locator(
                    '[class*="title" i] textarea, [class*="Title" i] textarea, '
                    '[class*="title" i] [contenteditable="true"], '
                    '[class*="Title" i] [contenteditable="true"]'
                ),
            ),
            (
                "最終手段: 編集領域内の最初のtextarea",
                page.locator("textarea").first,
            ),
        ]
        locator = self._resolve_locator(page, candidates, step_name="タイトル入力欄")
        self._set_single_line_text(locator, title)

    def _fill_body(self, page: Page, body: str) -> None:
        candidates = [
            ("role=textbox name=本文", page.get_by_role("textbox", name=re.compile("本文"))),
            (
                "css [class*=body] 系のcontenteditable",
                page.locator(
                    '[class*="body" i] [contenteditable="true"], '
                    '[class*="Body" i] [contenteditable="true"], '
                    '[class*="editor" i] [contenteditable="true"]'
                ),
            ),
            (
                "最終手段: 2番目のcontenteditable(1番目はタイトルの可能性)",
                page.locator('[contenteditable="true"]').nth(1),
            ),
            (
                "最終手段: 最初のcontenteditable",
                page.locator('[contenteditable="true"]').first,
            ),
        ]
        locator = self._resolve_locator(page, candidates, step_name="本文入力欄")
        self._set_multiline_text(page, locator, body)

    def _fill_tags(self, page: Page, tags: list[str]) -> None:
        candidates = [
            ("role=textbox name=タグ", page.get_by_role("textbox", name=re.compile("タグ"))),
            ("placeholder*=タグ", page.get_by_placeholder(re.compile("タグ"))),
            ("css input[placeholder*=タグ]", page.locator('input[placeholder*="タグ"]')),
            (
                "css [class*=tag] 系のinput",
                page.locator('[class*="tag" i] input, [class*="Tag" i] input'),
            ),
        ]
        tag_input = self._resolve_locator(page, candidates, step_name="タグ入力欄")
        for tag in tags:
            tag_input.click()
            tag_input.press_sequentially(tag, delay=10)
            page.keyboard.press("Enter")

    def _save_draft(self, page: Page) -> None:
        candidates = [
            ("role=button name=下書き保存", page.get_by_role("button", name="下書き保存")),
            ("role=button name=下書きを保存", page.get_by_role("button", name="下書きを保存")),
            ("text=下書き保存", page.get_by_text("下書き保存", exact=False)),
            (
                "css button:has-text(下書き保存)",
                page.locator('button:has-text("下書き保存")'),
            ),
        ]
        save_button = self._resolve_locator(page, candidates, step_name="下書き保存ボタン")
        self._assert_not_publish_action(save_button)
        save_button.click()
        page.wait_for_load_state("networkidle")
