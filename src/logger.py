"""GitHub Actionsのログにそのまま出しても問題ない、進捗ベースのロガー。

本文・パスワード・Cookie・トークンなど中身そのものは絶対にログへ出さない。
「何が起きたか」だけを出す(例: "本文入力: 完了" であって本文そのものは出さない)。
"""
from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "note_auto_post"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def mask(value: str, keep: int = 4) -> str:
    """秘密情報の一部だけを見せて残りを隠す(デバッグ表示用)。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)
