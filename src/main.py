"""実行エントリポイント。

サブコマンド:
  reconcile   前回processingのまま残った行をneeds_reviewにする(実行の最初に必ず行う)
  fetch       次に処理すべき記事があるか確認するだけ(何も書き換えない)
  run         reconcile -> 対象記事取得 -> processing -> note下書き作成 -> draft
              (note.py が未実装のPhase1〜2段階では使えない)
"""
from __future__ import annotations

import argparse
import sys

from src.config import Config
from src.logger import get_logger
from src.sheets import SheetsClient
from src.status_manager import DoubleProcessingGuard, StatusManager

logger = get_logger()


def _connect() -> SheetsClient:
    config = Config.load_sheets_only()
    client = SheetsClient(config)
    client.connect()
    return client


def cmd_reconcile(_args: argparse.Namespace) -> int:
    sheets = _connect()
    manager = StatusManager(sheets)
    stale = manager.reconcile_stale_processing()
    if stale:
        logger.info("needs_reviewにした件数: %d", len(stale))
        for a in stale:
            logger.info("  id=%s title=%s", a.id, a.title[:30])
    else:
        logger.info("processingのまま残っている行はありませんでした")
    return 0


def cmd_fetch(_args: argparse.Namespace) -> int:
    sheets = _connect()
    manager = StatusManager(sheets)
    manager.reconcile_stale_processing()

    article = sheets.get_next_target_article()
    if article is None:
        logger.info("対象記事(status=ready, content_type=free)は見つかりませんでした")
        return 0

    logger.info("対象記事を検出しました: id=%s", article.id)
    logger.info("  title=%s", article.title)
    logger.info("  tags=%s", article.tags)
    logger.info("  content_type=%s", article.content_type)
    logger.info("  本文の文字数=%d", len(article.body))
    return 0


def cmd_run(_args: argparse.Namespace) -> int:
    sheets = _connect()
    manager = StatusManager(sheets)
    manager.reconcile_stale_processing()

    article = sheets.get_next_target_article()
    if article is None:
        logger.info("対象記事はありません。終了します。")
        return 0

    logger.info("対象記事: id=%s", article.id)
    try:
        manager.claim_article(article)
    except DoubleProcessingGuard as exc:
        logger.warning(str(exc))
        return 0

    try:
        from src.note import NotePoster  # 遅延import(Phase3で追加)
    except ImportError:
        manager.mark_needs_review(
            article, stage="note", message="note.py が未実装です(Phase3未着手)。"
        )
        return 1

    try:
        with NotePoster() as poster:
            note_url = poster.create_draft(article)
    except Exception as exc:  # noqa: BLE001 - 想定外の失敗も必ずSheetsに記録する
        logger.exception("note下書き作成に失敗しました")
        manager.mark_needs_review(article, stage="note", message=str(exc))
        return 1

    # Craft連携(Phase4)が未実装のうちは、note下書きが作れた時点でdraft_createdとする。
    manager.mark_draft_created(article, note_url=note_url, craft_url="")
    logger.info("draft_created: id=%s note_url=%s status=draft_created", article.id, note_url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="you-ichi-note 自動投稿パイプライン")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reconcile", help="processing残留をneeds_reviewに変更する").set_defaults(func=cmd_reconcile)
    sub.add_parser("fetch", help="対象記事があるか確認するだけ(書き換えなし)").set_defaults(func=cmd_fetch)
    sub.add_parser("run", help="1件分の自動投稿パイプラインを実行する").set_defaults(func=cmd_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
