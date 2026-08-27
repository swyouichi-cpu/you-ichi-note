"""環境変数(ローカルでは.env、GitHub ActionsではSecrets)から設定を読み込む。

秘密情報(サービスアカウントJSON、noteのセッション情報など)は
すべてここを経由して読み込み、他のモジュールにファイルパスや
生の文字列をベタ書きしない。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # ローカル実行時のみ .env を読む。GitHub Actionsでは何もしない。


class ConfigError(RuntimeError):
    """必須の環境変数が不足しているときに送出する。"""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"環境変数 {name} が設定されていません。"
            f"README.md の セットアップ手順 を確認し、"
            f".env (ローカル) または GitHub Secrets (Actions) に設定してください。"
        )
    return value


@dataclass(frozen=True)
class Config:
    google_service_account_json: str  # JSONファイルの中身そのもの(文字列)
    spreadsheet_id: str
    sheet_name: str

    @classmethod
    def load_sheets_only(cls) -> "Config":
        """Phase 1〜2 (Sheets関連) で必要な設定だけを読み込む。

        note / Craft 用の設定は、それぞれの機能を使う時点で別途読み込む
        (使わない処理のために無関係なSecretsを必須にしないため)。
        """
        return cls(
            google_service_account_json=_require("GOOGLE_SERVICE_ACCOUNT_JSON"),
            spreadsheet_id=_require("SPREADSHEET_ID"),
            sheet_name=os.environ.get("SHEET_NAME", "Sheet1").strip() or "Sheet1",
        )


@dataclass(frozen=True)
class NoteConfig:
    storage_state_json: str  # Playwright storage_state (JSON文字列)

    @classmethod
    def load(cls) -> "NoteConfig":
        return cls(storage_state_json=_require("NOTE_STORAGE_STATE"))
