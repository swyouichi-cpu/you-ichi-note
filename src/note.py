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

★タグ(ハッシュタグ)は本文末尾への追記方式で扱う★
以前は「公開に進む」ボタンの先にある「公開設定」パネルでタグを入力し、
「キャンセル」で編集画面へ戻る方式を実装していた。しかし実機での
最終検証の結果、このパネルは「キャンセル」を押すと入力内容(タグを含む)を
すべて破棄する仕様であることが、ユーザーによる手動ブラウザ確認と
note公式のヘルプページの両方で確認された。これは自動化側の不具合ではなく
note.com側の公式な(ドキュメント化された)挙動である。

そのため公開設定パネルを一切使わない方式に変更した。note公式ヘルプが
案内している通り、本文中に半角の「#タグ名」を直接書くことが、公開前の
下書きにハッシュタグを設定する唯一の公式にサポートされた方法である。
具体的には、本文の末尾に5行分の改行を挟んで「#タグ1 #タグ2」のような
タグ行を追記してから本文入力欄に入力する(build_body_with_hashtags()、
_TAG_SEPARATOR = "\\n" * 5)。タグが1件も無い場合は区切り文字列自体を
追加せず、本文を一切変更しない。

この方式により、「公開に進む」ボタンへは一切遷移しなくなった(押さない
のではなく、そもそも画面遷移のコードパス自体が存在しない)。タグの
正規化(normalize_tags())は、前後の空白除去・先頭の「#」1つの除去・
空タグの除外・重複タグの除外のみを行う。タグ名の内部に空白が含まれる
場合(例:「広島 レモン」)は、本文末尾のタグ行が半角スペース区切りで
あるため、どこまでが1つのタグかを安全に判定できない。このような
あいまいな入力を自動で「直す」(内部の空白を詰めて1語にする、など)ことは
絶対に行わず、TagValidationError を送出して呼び出し側で needs_review に
倒す(推測でデータを書き換えない)。

なお実際にnote側が本文末尾の「#タグ名」をハッシュタグとして正しく
認識するかどうかの最終確認は、人間が下書きを手動で開き「公開に進む」を
押して公開設定パネルの表示を目視することでのみ行う(このリポジトリの
自動化コードでは絶対に行わない)。

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
# 「公開に進む」は本文末尾ハッシュタグ方式への移行後、このファイルの
# どのコードパスからもクリックされない(公開設定パネルへの画面遷移コード
# 自体を撤去した)。それでもなお、_assert_not_publish_action() による
# 保険を一段厚くするため、このリストにも含めておく。
_FORBIDDEN_PUBLISH_KEYWORDS = ["投稿する", "公開する", "予約投稿", "公開に進む", "Publish"]

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


class TagValidationError(NotePosterError):
    """タグの形式があいまいで安全に正規化できない場合に送出する。

    呼び出し側(main.py)ではNotePosterErrorのサブクラスとして
    needs_reviewに振り分けられる。あいまいな入力を推測で「直す」ことは
    絶対にせず、必ず人間の確認に回す。
    """


# 本文とタグ行の間に挟む区切り。note公式ヘルプの案内どおり、本文末尾に
# 5行分の改行を挟んでハッシュタグ行を追記する(仕様確定時の実装イメージ:
# body + "\n\n\n\n\n" + hashtags)。
_TAG_SEPARATOR = "\n" * 5


def normalize_tags(raw_tags: list[str]) -> list[str]:
    """Google Sheetsのtags列から取得した生のタグ文字列を正規化する。

    許可する正規化:
      - 前後の空白除去
      - 先頭の "#" を1つだけ除去(内部では"#"無しの裸の名前として扱う)
      - 空タグの除外
      - 重複タグの除外(正規化後の値で比較)

    禁止する正規化(絶対に行わない):
      - タグ名内部の空白を詰めたり書き換えたりすること
        (例:「広島 レモン」を「広島レモン」にしない)

    本文末尾のタグ行は半角スペース区切りで並べる仕様のため、タグ名の
    内部に空白(スペース・タブ・改行)が含まれていると、どこまでが
    1つのタグでどこからが別のタグかを安全に判定できない。このような
    あいまいな入力を自動で「直す」ことは絶対にせず、
    TagValidationError を送出して呼び出し側でneeds_reviewに倒す。
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags:
        candidate = raw.strip()
        if candidate.startswith("#"):
            candidate = candidate[1:]
        if not candidate:
            continue
        if any(ch.isspace() for ch in candidate):
            raise TagValidationError(
                f"タグ '{raw}' の内部に空白文字が含まれているため、"
                "本文末尾のタグ行(半角スペース区切り)にした場合に"
                "タグの区切りが一意に定まりません。安全のため自動では"
                "修正せず処理を中断します。Google Sheets側でタグの"
                "内容を確認し、意図を明確にしてから再度readyにして"
                "ください。"
            )
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def build_body_with_hashtags(body: str, tags: list[str]) -> str:
    """本文の末尾に、5行分の改行を挟んでハッシュタグ行を追加する。

    noteの現在のエディタでは、公開設定パネルでの一時的なタグ入力は
    「キャンセル」を押すと(note公式の仕様として)破棄される。
    note公式ヘルプが案内する「本文中に #タグ名 と直接書く」方式に
    統一する。

    tags が空の場合は区切り文字列を一切追加せず、本文をそのまま返す
    (Google Sheets側のbody/tags列自体はこの関数の呼び出し前後で
    変更しない。あくまでnoteへ入力する直前に組み立てるだけ)。
    """
    if not tags:
        return body
    hashtag_line = " ".join(f"#{tag}" for tag in tags)
    return f"{body}{_TAG_SEPARATOR}{hashtag_line}"


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

        絶対に公開ボタンは押さない。「公開に進む」ボタンへは一切遷移しない
        (押さないのではなく、そのコードパス自体が存在しない)。タグは
        note公式ヘルプが案内する方式にならい、本文末尾に5行分の改行を
        挟んで「#タグ1 #タグ2」の形で追記してから本文入力欄へ入力する。
        成功したら "draft_created" をログに残す。
        """
        tags = normalize_tags(article.tag_list())
        body_with_hashtags = build_body_with_hashtags(article.body, tags)

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

        logger.info("本文(末尾にタグを追記済み)を入力")
        self._run_step(page, "本文入力", lambda: self._fill_body(page, body_with_hashtags))
        self._screenshot(page, "03_body_filled")

        logger.info("本文がnote側に反映されたか確認")
        self._run_step(page, "本文反映確認", lambda: self._assert_body_registered(page))

        logger.info("自動保存の完了を確認")
        self._run_step(page, "自動保存完了待ち", lambda: self._wait_for_autosave_idle(page))

        logger.info("下書き保存")
        self._run_step(page, "下書き保存", lambda: self._save_draft(page))
        self._screenshot(page, "04_saved_draft")

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

    def _wait_for_autosave_idle(self, page: Page, timeout_ms: int = 15000) -> None:
        """自動保存中(「保存中」の表示)が消えるまで待つ。

        固定のsleepではなく、「保存中」の表示が実際に消える(非表示になる)
        ことをPlaywrightのポーリング待機で確認する。「保存中」が最初から
        表示されていなければ即座に完了扱いになる。本文入力(タグ追記込み)の
        直後、「下書き保存」を押す前に呼び出し、自動保存が競合しないことを
        確認する目的で使う。
        """
        saving_indicator = page.get_by_text("保存中", exact=False)
        try:
            saving_indicator.wait_for(state="hidden", timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            self._capture_failure(page, "自動保存完了待ち")
            raise NotePosterError(
                "「保存中」の表示が消えず、自動保存の完了を確認できませんでした。"
            ) from exc

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
