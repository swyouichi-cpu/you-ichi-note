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
このファイルには、記事を実際に公開/投稿/予約投稿してしまうボタン
(noteの実機確認により文言は「投稿する」)を押すコードを一切含めない。
実装できるのは「下書き保存」までであり、公開操作を自動化するコードパスを
追加する場合は、必ず別途ユーザーの明示的な合意を得ること。
念のため、危険な可能性のあるボタンをクリックする直前には必ず
_assert_not_publish_action() でボタンの文言を確認し、_FORBIDDEN_PUBLISH_
KEYWORDS に含まれる単語(「投稿する」「公開する」「予約投稿」等)が
含まれていたら処理を中断する安全装置を入れている。

タグ(ハッシュタグ)はnoteの現在のエディタでは本文編集画面には無く、
「公開に進む」ボタンの先にある「公開設定」パネルの中にしかない
(ユーザーが実機で確認済み)。「公開に進む」はこのパネルを開くだけの
画面遷移であり、それ自体は公開しない。パネル内の右上にある「投稿する」
ボタンを押して初めて公開される。このパネルの左上「キャンセル」で
公開せずに編集画面へ戻れることも確認済みだが、「キャンセルでタグが
保持されるか」は事前に確認できなかったため、_fill_tags() は
キャンセル後にもう一度パネルを開き直してタグが実際に残っているかを
実行時に確認し、確認できない場合は成功したと見なさずに中断する
(_assert_hashtags_present)。「投稿する」ボタンへのセレクタや
クリック処理はコード中のどこにも存在しない。

「公開に進む」は、本文の自動保存(「保存中」表示)が完了する前に押すと、
公開設定パネルへ遷移せずダイアログが出ることが実機で確認された。
そのため _open_publish_settings() は、クリックの前に必ず
_wait_for_autosave_idle() で「保存中」の表示が消えるのを待つ(固定sleepでは
なく、実際に表示が消えたことをポーリングで確認する)。クリックした後も
結果を決め打ちせず、_classify_post_click_state() で
「パネルへ遷移した」「(クリック前には無かった)ダイアログが新たに出た」
「編集画面のまま変化なし」の3状態を判定し、パネル遷移以外は診断データを
残したうえで原因が分かるメッセージ付きで中断する。

note.comのエディタには「AIと構成づくりや推敲を一緒に進められます」という
AIアシスタントの案内ツールチップが常時 role="dialog" として表示されており、
これを「公開に進む」クリックが引き起こしたエラーダイアログと誤判定して
しまう不具合が実機テストで発生した。そのため dialog判定は、クリック直前に
既に表示されていたダイアログの有無を記録しておき、クリック後に「それまで
無かったダイアログが新たに現れたか」だけを見るようにしている
(_visible_dialog_locator / dialog_was_visible_before)。

★タグ保持確認の偽陽性について(既知の問題への対処)★
実機テストで、「テスト」「自動投稿」というタグを入力・確定・キャンセル後、
もう一度公開設定パネルを開いたところ、入力していないはずの無関係な単語
(記事本文由来と見られるもの)がタグとして残っており、逆に「自動投稿」が
消えている、という不具合が見つかった。原因は、タグの確認処理が「#」無しの
素の単語にも部分一致する候補を持っていたため、本文・タイトル中に同じ単語
(例:「自動投稿テスト」というタイトル中の「自動投稿」)が出現すると、
実際にはタグチップが無くても地の文への一致で「確認できた」と誤判定して
いたこと。そのため _hashtag_chip_candidate() は「#タグ名」への一致のみを
判定基準にし(#無しの素の単語への一致は候補から除外)、
_assert_hashtags_present() は全タグを確認したうえで欠けているものを
まとめて報告し、実際に画面上に見えている#付き文字列の一覧
(_list_visible_hashtag_chips)もエラーメッセージに含めるようにした。
また _enter_hashtags() は、各タグをチップとして確定させた直後に
_wait_for_autosave_idle() でnote側の内部状態・自動保存が反映されるのを
待ってから次のタグの入力へ進むようにし、前のタグの保存が完了する前に
次を入力してしまう競合を避けている。

★本文入力の内部状態反映について(既知の問題への対処)★
以前は本文の入力に keyboard.insert_text() を行単位でまとめて流し込む
実装を使っていた。画面上はテキストが表示され document.title にも
反映されるため一見成功しているように見えたが、実機テストで「公開に進む」
を押した際に「タイトル、本文を入力してください」という検証ダイアログが
表示され、実際にはnote側の文字数カウンタ(「0 文字」表示)が更新されて
おらず、内部状態には反映されていなかったことが判明した。insert_textは
1回のinputイベントとしてまとめてテキストを差し込むため、noteのリッチ
テキストエディタが実際のキー入力イベント列を前提に内部状態を更新して
いる場合に検知されないと考えられる。そのため press_sequentially()
(1文字ずつ実際のキー入力に近いイベントを発生させる)に変更し、
本文入力の直後に文字数カウンタが「0 文字」のままでないかを確認する
_assert_body_registered() を追加している。

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
  - 読み込みに失敗したリクエスト(requestfailed)。resource_type・Originヘッダ・
    Cookieヘッダが「付いていたかどうか」(値そのものは含まない)を含む
  - HTTPステータスが2xx/3xx以外だったレスポンスの一覧
  - APIパス(/api/を含むURL)へのレスポンスについて、
    access-control-allow-origin / access-control-allow-credentials
    ヘッダの値(CORS許可設定そのものであり、認証情報ではない)
これらはいずれもDOM・ネットワークの状態を見るための情報であり、
Cookie・セッション情報・GitHub Secretsの中身は一切含まれない
(レスポンスのヘッダ・ボディそのものは記録しない。URL・メソッド・
ステータスコードのみを記録し、URLのクエリ文字列も念のため除去する)。
ただし記事本文などあなたのコンテンツそのものは画面/ページ内テキストとして
写り得るため、共有前に中身を確認すること。

★ブラウザcontextの設定★
GitHub Actions(クラウドIP・ヘッドレス)からのアクセスがnote側のbot対策等に
引っかかっていないかを切り分けるため、一般的なデスクトップChromeに近い
User-Agent・locale(ja-JP)・timezone(Asia/Tokyo)・viewportを明示的に設定して
いる。これは特定の人物・組織を装うものではなく、一般的なブラウザ環境を
再現するテスト目的の設定。
"""
from __future__ import annotations

import json
import os
import re
import time
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

# クリック直前、ボタンの文言にこれらの単語が含まれていたら「実際に公開/投稿
# してしまう」ボタンだと判断して中断する(誤ってセレクタが該当ボタンに
# マッチしてしまった場合の保険)。
#
# 「公開に進む」はユーザーが実機で確認済みの通り、公開設定パネルを開くだけの
# 画面遷移であり、それ自体は公開しない(パネル内の「投稿する」を押すまでは
# 公開されない)。そのためこのリストには含めない。実際に含めているのは、
# 押した瞬間に記事が公開/予約される可能性がある文言のみ。
_FORBIDDEN_PUBLISH_KEYWORDS = ["投稿する", "公開する", "予約投稿", "Publish"]

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
        self._cors_notes: list[str] = []

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
        # GitHub Actions(ヘッドレス・クラウドIP)からのアクセスがnote側の
        # bot対策等に引っかかっていないかを切り分けるため、実在のデスクトップ
        # Chromeに近いUser-Agent/locale/timezone/viewportを明示的に設定する。
        # これは特定の人物・組織を装うものではなく、一般的なブラウザ環境を
        # 再現するテスト目的の設定。
        self._context = self._browser.new_context(
            storage_state=storage_state,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 800},
        )
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
                try:
                    headers = request.headers
                except Exception:  # noqa: BLE001
                    headers = {}
                # Cookieが「送信されようとしたか」を確認するため、値ではなく
                # ヘッダの有無だけを記録する(値そのものは絶対に記録しない)。
                cookie_present = "cookie" in headers
                origin = headers.get("origin", "(なし)")
                self._failed_requests.append(
                    f"{request.method} {_strip_query(request.url)} "
                    f"resource_type={request.resource_type} origin={origin} "
                    f"cookie_header_present={cookie_present} -> {failure}"
                )

        def _on_response(response) -> None:
            if len(self._responses) < _MAX_DIAG_ENTRIES:
                self._responses.append((_strip_query(response.url), response.status))
            # CORS関連ヘッダの有無だけを別途記録する(APIパスのみ、件数上限あり)。
            # ヘッダの値そのものは記録するが、これは通信可否の設定値であり
            # 秘密情報ではない(Cookie等の認証情報は含まれない)。
            if "/api/" in response.url and len(self._cors_notes) < _MAX_DIAG_ENTRIES:
                try:
                    headers = response.headers
                except Exception:  # noqa: BLE001
                    headers = {}
                allow_origin = headers.get("access-control-allow-origin", "(なし)")
                allow_credentials = headers.get("access-control-allow-credentials", "(なし)")
                self._cors_notes.append(
                    f"{response.status} {_strip_query(response.url)} "
                    f"access-control-allow-origin={allow_origin} "
                    f"access-control-allow-credentials={allow_credentials}"
                )

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
            "",
            f"APIパスへのレスポンスとCORSヘッダ ({len(self._cors_notes)}件):",
            *[f"  {m}" for m in self._cors_notes],
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
            "requestfailed=%d件 4xx/5xx応答=%d件 API応答=%d件",
            step_name,
            url,
            title,
            len(self._console_messages),
            len(self._page_errors),
            len(self._failed_requests),
            error_response_count,
            len(self._cors_notes),
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

        logger.info("本文がnote側に反映されたか確認")
        self._run_step(page, "本文反映確認", lambda: self._assert_body_registered(page))

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

        以前は keyboard.insert_text() を行単位でまとめて流し込む実装だった。
        画面上はテキストが表示され、document.titleにも反映されるため
        一見成功しているように見えたが、実際にはnote側の文字数カウンタが
        「0 文字」のままになり、内部状態には反映されていなかった
        (「公開に進む」を押した際に「タイトル、本文を入力してください」という
        検証ダイアログが出てしまう原因になっていた)。

        insert_textは1つのinput イベントとしてまとめてテキストを差し込むため、
        note側のリッチテキストエディタが本来のキー入力イベント列を前提に
        内部状態を更新している場合、正しく検知されない可能性がある。
        press_sequentially() は1文字ずつ実際のキー入力に近いイベント
        (keydown/keypress/input/keyup)を発生させ、"\\n" は自動的にEnterキー
        として扱われるため、この方式に切り替えた。長文では時間がかかるが、
        正しさを優先する。
        """
        locator.click()
        locator.press("Control+A")
        locator.press("Backspace")
        locator.press_sequentially(text)

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

    def _assert_body_registered(self, page: Page) -> None:
        """本文がnote側の内部状態(文字数カウンタ)にも反映されたことを確認する。

        画面上は文字が表示されていても、note側の内部状態(文字数カウンタ等)に
        反映されていないことがある(過去に「0 文字」のまま止まっていた実績あり)。
        「公開に進む」へ進んでから検証ダイアログで気づくと原因の切り分けが
        難しくなるため、本文入力の直後にこの時点で検知する。
        """
        try:
            page.get_by_text("0 文字", exact=True).wait_for(state="visible", timeout=2000)
            still_zero = True
        except PlaywrightTimeoutError:
            still_zero = False
        if still_zero:
            raise NotePosterError(
                "本文を入力しましたが、文字数カウンタが「0 文字」のままです。"
                "画面上は本文が表示されていても、noteエディタの内部状態に"
                "反映されていない可能性があります(過去に発生した既知の問題)。"
            )

    def _fill_tags(self, page: Page, tags: list[str]) -> None:
        """ハッシュタグ(タグ)を設定する。

        ユーザーが実機で確認した結果、noteの現在のエディタではタグ入力欄は
        本文編集画面には無く、「公開に進む」ボタンの先にある「公開設定」
        パネル(ハッシュタグ / 記事タイプ / 記事の追加 / クーポン / 詳細設定)
        の中にしかない。このパネルの右上には実際に投稿してしまう
        「投稿する」ボタンがあるが、パネルを開くだけでは公開されず、
        左上の「キャンセル」で編集画面に戻れることも確認済み。

        ただし「キャンセルで戻ったときにタグが保持されるか」は未確認だった
        ため、ここでは以下の手順で「保持されていることを実行時に確認して
        から先に進む」設計にする。保持が確認できない場合は下書き保存を
        試みず、原因不明のまま処理を進めないようにする。

          1. 「公開に進む」を押してパネルを開く(投稿するボタンには触れない)
          2. ハッシュタグを1件ずつ入力し、その都度チップとして確定した
             ことを画面上で確認する
          3. 「キャンセル」で編集画面へ戻る
          4. もう一度「公開に進む」を押してパネルを開き直し、
             先ほど入力したタグがすべてチップとして残っているか確認する
          5. (確認できたら)「キャンセル」でパネルを閉じ、編集画面へ戻る
        """
        self._open_publish_settings(page)
        self._enter_hashtags(page, tags)
        self._close_publish_settings(page)

        logger.info("タグがキャンセル後も保持されているか再確認")
        self._open_publish_settings(page)
        self._assert_hashtags_present(page, tags)
        self._close_publish_settings(page)

    def _wait_for_autosave_idle(self, page: Page, timeout_ms: int = 15000) -> None:
        """自動保存中(「保存中」の表示)が消えるまで待つ。

        固定のsleepではなく、「保存中」の表示が実際に消える(非表示になる)
        ことをPlaywrightのポーリング待機で確認する。「保存中」が最初から
        表示されていなければ即座に完了扱いになる。実機で、自動保存が
        完了する前に「公開に進む」を押すとパネルへ遷移せずダイアログが
        出ることが確認されたため、その対策として追加した。
        """
        saving_indicator = page.get_by_text("保存中", exact=False)
        try:
            saving_indicator.wait_for(state="hidden", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            self._capture_failure(page, "自動保存完了待ち")
            raise NotePosterError(
                "「保存中」の表示が消えず、自動保存の完了を確認できませんでした。"
            ) from exc

    def _visible_dialog_locator(self, page: Page) -> Locator | None:
        """現在画面に表示されているダイアログ(role=dialog/alertdialog)があれば返す。

        note.comのエディタには「AIと構成づくりや推敲を一緒に進められます」という
        AIアシスタントの案内ツールチップが常時(role=dialogとして)表示されており、
        「公開に進む」クリックとは無関係にこれが検出されてしまう実害が実機で
        確認された。そのためこのメソッド単体では「新しく出たダイアログ」までは
        判定できない。呼び出し側でクリック前後の状態を比較すること。
        """
        for role in ("dialog", "alertdialog"):
            locator = page.get_by_role(role).first
            if locator.is_visible():
                return locator
        return None

    def _classify_post_click_state(
        self,
        page: Page,
        dialog_was_visible_before: bool,
        timeout_ms: int = 8000,
        poll_interval_ms: int = 300,
    ) -> str:
        """「公開に進む」クリック後の状態を3つに分類する(固定sleepではなく
        実際の状態を短い間隔でポーリングして判定する)。

        - "opened": 公開設定パネルへ遷移した(「ハッシュタグ」の出現で判定)
        - "dialog": クリックの結果として新たにダイアログが表示された
          (dialog_was_visible_beforeがFalseで、かつ現在role=dialog/
          alertdialogが表示されている場合のみ。クリック前から表示されている
          ダイアログ(AIアシスタントの案内ツールチップ等)は無関係なので
          この状態には含めない)
        - "unchanged": どちらでもなく、タイムアウトまで編集画面のままだった
          (クリック前から表示されているダイアログがそのまま残っている
          だけの場合もここに含まれる)
        """
        panel_candidates = [
            page.get_by_role("heading", name="ハッシュタグ"),
            page.get_by_text("ハッシュタグ", exact=True),
        ]

        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for locator in panel_candidates:
                if locator.first.is_visible():
                    return "opened"
            if not dialog_was_visible_before and self._visible_dialog_locator(page) is not None:
                return "dialog"
            if time.monotonic() >= deadline:
                return "unchanged"
            page.wait_for_timeout(poll_interval_ms)

    def _extract_dialog_text(self, page: Page) -> str:
        locator = self._visible_dialog_locator(page)
        if locator is None:
            return "(取得できませんでした)"
        try:
            return (locator.inner_text() or "").strip()[:300]
        except PlaywrightTimeoutError:
            return "(取得できませんでした)"

    def _open_publish_settings(self, page: Page) -> None:
        """「公開に進む」を押して公開設定パネルを開く(公開はしない)。

        自動保存が完了する前にクリックすると、パネルへ遷移せず
        ダイアログが出ることが実機で確認されたため、まず自動保存の
        完了(「保存中」表示が消えること)を待ってからクリックする。
        クリック後は「パネルが開いた」「(クリック前には無かった)ダイアログが
        新たに出た」「何も変わらない」の3状態を判定し、それぞれに応じた
        エラーメッセージを出す。
        """
        self._wait_for_autosave_idle(page)

        candidates = [
            ("role=button name=公開に進む", page.get_by_role("button", name="公開に進む")),
            ("text=公開に進む", page.get_by_text("公開に進む", exact=False)),
        ]
        proceed_button = self._resolve_locator(page, candidates, step_name="公開設定を開くボタン")
        self._assert_not_publish_action(proceed_button)

        # クリック前から表示されているダイアログ(AIアシスタントの案内など、
        # 公開フローとは無関係なもの)を、クリック後の判定から除外するための基準。
        dialog_was_visible_before = self._visible_dialog_locator(page) is not None
        proceed_button.click()

        state = self._classify_post_click_state(page, dialog_was_visible_before)
        if state == "opened":
            return
        if state == "dialog":
            dialog_text = self._extract_dialog_text(page)
            self._capture_failure(page, "公開設定パネル表示確認")
            raise NotePosterError(
                "「公開に進む」を押した後、公開設定パネルではなく新しいダイアログが"
                f"表示されました(内容: {dialog_text!r})。"
            )
        self._capture_failure(page, "公開設定パネル表示確認")
        raise NotePosterError(
            "「公開に進む」を押しましたが、公開設定パネルへの遷移も新しい"
            "ダイアログの表示も確認できず、編集画面のままでした。クリックが"
            "正しく届いていない可能性があります。"
        )

    def _close_publish_settings(self, page: Page) -> None:
        """公開設定パネルの「キャンセル」を押して編集画面へ戻る(投稿しない)。"""
        candidates = [
            ("role=button name=キャンセル", page.get_by_role("button", name="キャンセル")),
            ("text=キャンセル", page.get_by_text("キャンセル", exact=True)),
        ]
        cancel_button = self._resolve_locator(page, candidates, step_name="公開設定キャンセルボタン")
        self._assert_not_publish_action(cancel_button)
        cancel_button.click()

    def _hashtag_input_candidates(self, page: Page) -> list[tuple[str, Locator]]:
        return [
            ("placeholder=ハッシュタグを追加する", page.get_by_placeholder("ハッシュタグを追加する")),
            ("placeholder*=ハッシュタグ", page.get_by_placeholder(re.compile("ハッシュタグ"))),
            ("css input[placeholder*=ハッシュタグ]", page.locator('input[placeholder*="ハッシュタグ"]')),
        ]

    def _hashtag_chip_candidate(self, page: Page, tag: str) -> Locator:
        """「#タグ名」というチップを指すロケータを1つだけ返す。

        以前は "#タグ名" に加えて "タグ名"(#無し)への部分一致も候補に
        入れていたが、実機テストで「テスト」「自動投稿」がタイトル・本文中の
        同じ単語(例:「自動投稿テスト...」)に誤って一致し、実際にはタグが
        反映されていないのに「確認できた」と誤判定する不具合が発生した。
        タグチップは必ず"#"付きで表示されることが実機で確認できているため、
        "#タグ名" への一致のみを判定基準にする(本文中の地の文に"#"付きで
        同じ語が出現する可能性は極めて低い)。
        """
        return page.get_by_text(f"#{tag}", exact=False)

    def _list_visible_hashtag_chips(self, page: Page) -> list[str]:
        """画面上に見えている「#で始まる短いテキスト」要素を列挙する(診断用)。

        タグチップの実際のコンテナ要素が不明なため確実な一覧取得は保証
        できないが、「意図したタグが本当に無いのか、想定外の別のタグに
        置き換わっているのか」を切り分けるための手がかりとして使う。
        """
        try:
            return page.evaluate(
                """
                () => {
                  const seen = new Set();
                  const results = [];
                  document.querySelectorAll('*').forEach(el => {
                    if (el.children.length > 0) return;
                    const text = (el.textContent || '').trim();
                    if (/^#\\S{1,30}$/.test(text) && !seen.has(text)) {
                      seen.add(text);
                      results.push(text);
                    }
                  });
                  return results.slice(0, 50);
                }
                """
            )
        except Exception:  # noqa: BLE001 - 診断目的なので失敗しても処理は続ける
            return []

    def _enter_hashtags(self, page: Page, tags: list[str]) -> None:
        tag_input = self._resolve_locator(
            page, self._hashtag_input_candidates(page), step_name="ハッシュタグ入力欄"
        )
        for tag in tags:
            tag_input.click()
            tag_input.press_sequentially(tag, delay=10)
            page.keyboard.press("Enter")
            # 入力したタグがチップとして確定表示されるまで確認する
            # (確定していなければ次のタグの入力に進まない)。
            self._resolve_locator(
                page,
                [(f"text=#{tag}", self._hashtag_chip_candidate(page, tag))],
                step_name=f"ハッシュタグ確定確認({tag})",
                timeout_ms=5000,
            )
            # noteの内部状態・非同期保存(自動保存)が反映されるのを待ってから
            # 次のタグの入力へ進む。前のタグの保存が完了する前に次を入力すると
            # 状態を上書きしてしまう可能性があるため。
            self._wait_for_autosave_idle(page, timeout_ms=10000)

    def _assert_hashtags_present(self, page: Page, tags: list[str]) -> None:
        """すべてのタグがチップとして残っているか確認する。

        1件ずつ確認して最初の不一致で即座に諦めるのではなく、まず全件を
        チェックして「何が欠けているか」をまとめて把握したうえでエラーに
        する。エラーメッセージには、欠けているタグに加えて、実際に画面上に
        見えている#付きの文字列一覧も含める。前回の実機テストでは、
        入力していないはずの無関係な単語(記事本文由来と見られるもの)が
        チップとして残っており、原因調査にはこの実際の一覧が重要な手がかりに
        なったため。
        """
        missing: list[str] = []
        for tag in tags:
            try:
                self._hashtag_chip_candidate(page, tag).first.wait_for(
                    state="visible", timeout=5000
                )
            except PlaywrightTimeoutError:
                missing.append(tag)

        if missing:
            actual_chips = self._list_visible_hashtag_chips(page)
            self._capture_failure(page, "ハッシュタグ保持確認")
            raise NotePosterError(
                f"以下のタグが「キャンセル」で編集画面に戻った後に見当たりません"
                f"でした: {missing}。画面上に実際に見えている#付きの文字列: "
                f"{actual_chips}。「公開に進む→タグ入力→キャンセル」では意図した"
                "タグが正しく反映されない可能性があるため、この経路でのタグ設定は"
                "安全に行えません。処理を中断します。"
            )

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
