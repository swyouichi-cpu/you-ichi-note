"""note.com への下書き作成を Playwright で自動操作する。

★重要な注意(このファイルを読む/直す人へ)★
note.com は公式APIを提供しておらず、ここに書かれているセレクタ(画面上の
どのボタン・入力欄を操作するかの指定)は、実際の画面を見ながら
ユーザーと一緒に検証・修正することが前提の「たたき台」です。
一度書いたら終わりではなく、note側のUI変更で頻繁に壊れうる前提で
運用してください。

★安全設計(絶対に守ること)★
このファイルには「公開する」ボタンを押すコードを一切含めない。
実装できるのは「下書き保存」までであり、公開操作を自動化する
コードパスを追加する場合は、必ず別途ユーザーの明示的な合意を得ること。

★ログイン方式★
メールアドレス・パスワードを直接入力させる方式は、note側のreCAPTCHA
導入により機能しない可能性が高いため採用しない。代わりに、
ユーザーが scripts/note_login_bootstrap.py をローカルで実行して
事前に取得した「ログイン済みセッション情報(storage_state)」を
そのまま使い回す方式にしている。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from src.config import NoteConfig
from src.logger import get_logger
from src.models import Article

logger = get_logger()

NOTE_NEW_NOTE_URL = "https://note.com/notes/new"

# デバッグ用にスクリーンショットを保存したい場合、環境変数で有効化する。
# (ユーザーと一緒にnote側の挙動を確認する検証フェーズで使う想定)
_SCREENSHOT_DIR = os.environ.get("NOTE_DEBUG_SCREENSHOT_DIR", "").strip()


class NotePosterError(RuntimeError):
    """note操作中の失敗。呼び出し側でneeds_review/errorに振り分けるための例外。"""


class NotePoster:
    def __init__(self, config: NoteConfig | None = None):
        self._config = config or NoteConfig.load()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._step_count = 0

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

    def _screenshot(self, page: Page, label: str) -> None:
        if not _SCREENSHOT_DIR:
            return
        self._step_count += 1
        out_dir = Path(_SCREENSHOT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self._step_count:02d}_{label}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info("スクリーンショット保存: %s", path)

    def _assert_logged_in(self, page: Page) -> None:
        """ログイン済みか確認する。ログイン画面に飛ばされていたらセッション切れ。

        UNVERIFIED: 実際のnoteのログイン画面URL・要素を見ながら調整が必要。
        """
        if "login" in page.url:
            raise NotePosterError(
                "noteのログインセッションが無効になっている可能性があります"
                "(ログイン画面にリダイレクトされました)。"
                "scripts/note_login_bootstrap.py を再実行し、"
                "NOTE_STORAGE_STATE を更新してください。"
            )

    def create_draft(self, article: Article) -> str:
        """記事を入力し、下書き保存する。戻り値は下書き編集画面のURL。

        絶対に公開ボタンは押さない。
        """
        assert self._context is not None, "with文の中で使ってください"
        page = self._context.new_page()

        logger.info("noteの新規作成画面へアクセス")
        page.goto(NOTE_NEW_NOTE_URL, wait_until="networkidle")
        self._screenshot(page, "01_opened_new_note")
        self._assert_logged_in(page)

        logger.info("タイトルを入力")
        self._fill_title(page, article.title)
        self._screenshot(page, "02_title_filled")

        logger.info("本文を入力")
        self._fill_body(page, article.body)
        self._screenshot(page, "03_body_filled")

        tags = article.tag_list()
        if tags:
            logger.info("タグを入力(%d件)", len(tags))
            self._fill_tags(page, tags)
            self._screenshot(page, "04_tags_filled")

        logger.info("下書き保存")
        self._save_draft(page)
        self._screenshot(page, "05_saved_draft")

        note_url = page.url
        page.close()
        return note_url

    # -- 以下、UNVERIFIED: 実際の画面で検証・調整が必要な部分 -----------------

    def _fill_title(self, page: Page, title: str) -> None:
        # note のタイトル欄は placeholder="タイトル" のtextareaであることが多い、
        # という一般的な情報をもとにした暫定実装。要検証。
        locator = page.get_by_placeholder("タイトル")
        locator.click()
        locator.fill(title)

    def _fill_body(self, page: Page, body: str) -> None:
        # note の本文はリッチテキストエディタ(contenteditable)。
        # fill()が効かない場合はclick()してtype()する方式に切り替える必要がある。要検証。
        editor = page.locator('div[contenteditable="true"]').first
        editor.click()
        editor.type(body)

    def _fill_tags(self, page: Page, tags: list[str]) -> None:
        # タグ入力欄の特定方法は未検証。公開設定パネルを開く必要がある可能性がある。
        raise NotImplementedError(
            "タグ入力の実装は、実際のnote画面をユーザーと一緒に確認してから追加します。"
        )

    def _save_draft(self, page: Page) -> None:
        # 「公開に進む」ボタンではなく、明示的に「下書き保存」に該当する
        # ボタン/操作のみを押すこと。ボタン文言は要検証。
        save_button = page.get_by_role("button", name="下書き保存")
        save_button.click()
        page.wait_for_load_state("networkidle")
