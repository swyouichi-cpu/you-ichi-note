"""実行エントリポイント(Phase 1: 完成・実機検証済み)。

サブコマンド:
  reconcile   前回processingのまま残った行、およびready+note_url不整合な行を
              needs_reviewにする(実行の最初に必ず行う)
  fetch       次に処理すべき記事があるか確認するだけ(何も書き換えない)
  run         reconcile -> 対象記事取得 -> processing -> note下書き作成
              -> Sheetsへの書き戻し(read-back検証込み) -> draft_created

product_links(商品リンク)が指定されている記事は、下書き作成自体には
成功しても draft_created にはせず、note_url を保持したまま needs_review
へ倒す(2026年8月29日、ARTICLE-001の実機実行を踏まえた運用方針の変更。
詳細はsrc/note.pyのcreate_draft()・src/status_manager.pyの
mark_needs_review_with_note_url()のdocstringを参照)。商品導線テキスト
(商品名・「→ 商品を見る」)は本文にプレーンテキストとして残るが、実際の
リンク設定は人間が手動で行う。
"""
from __future__ import annotations

import argparse
import sys

from src.config import Config
from src.logger import get_logger
from src.sheets import SheetsClient
from src.status_manager import (
    DoubleProcessingGuard,
    DraftCreationVerificationError,
    ManualLinkSetupVerificationError,
    StatusManager,
)

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
    inconsistent = manager.reconcile_inconsistent_ready_with_note_url()
    if stale:
        logger.info("processing残留でneeds_reviewにした件数: %d", len(stale))
        for a in stale:
            logger.info("  id=%s title=%s", a.id, a.title[:30])
    else:
        logger.info("processingのまま残っている行はありませんでした")
    if inconsistent:
        logger.info("ready+note_url不整合でneeds_reviewにした件数: %d", len(inconsistent))
        for a in inconsistent:
            logger.info("  id=%s title=%s note_url=%s", a.id, a.title[:30], a.note_url)
    else:
        logger.info("ready+note_urlの不整合な行はありませんでした")
    return 0


def cmd_fetch(_args: argparse.Namespace) -> int:
    sheets = _connect()
    manager = StatusManager(sheets)
    manager.reconcile_stale_processing()
    manager.reconcile_inconsistent_ready_with_note_url()

    article = sheets.get_next_target_article()
    if article is None:
        logger.info("対象記事(status=ready, content_type=free)は見つかりませんでした")
        return 0

    logger.info("対象記事を検出しました: id=%s", article.id)
    logger.info("  title=%s", article.title)
    logger.info("  tags=%s", article.tags)
    logger.info("  content_type=%s", article.content_type)
    logger.info("  本文の文字数=%d", len(article.body))
    logger.info("  product_links列の文字数=%d", len(article.product_links))
    return 0


def cmd_run(_args: argparse.Namespace) -> int:
    sheets = _connect()
    manager = StatusManager(sheets)
    manager.reconcile_stale_processing()
    manager.reconcile_inconsistent_ready_with_note_url()

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
        # 遅延import。Playwright/note関連の依存を、Sheetsのみを使う
        # reconcile/fetchサブコマンドの実行時には読み込ませないため。
        from src.note import NotePoster, parse_product_links
    except ImportError:
        manager.mark_needs_review(
            article, stage="note", message="src/note.py の読み込みに失敗しました。"
        )
        return 1

    try:
        with NotePoster() as poster:
            note_url = poster.create_draft(article)
    except Exception as exc:  # noqa: BLE001 - 想定外の失敗も必ずSheetsに記録する
        logger.exception("note下書き作成に失敗しました")
        logger.error(
            "最終結果: id=%s note_url=(未取得) final_status=needs_review", article.id
        )
        manager.mark_needs_review(article, stage="note", message=str(exc))
        return 1

    # product_linksが指定されている記事は、商品リンクの自動設定を行って
    # いない(2026年8月29日、ARTICLE-001の実機実行を踏まえた運用方針の
    # 変更。note.com側の商品リンク設定UIの実機での不安定さを理由に、
    # create_draft()は_apply_product_links()を呼ばなくなった。詳細は
    # src/note.pyのcreate_draft()のdocstringを参照)。create_draft()が
    # 例外を出さずに戻ってきた時点でparse_product_links()自体は既に
    # 成功しているため、ここでの再解釈は失敗しないはずだが、万一失敗
    # した場合も安全側に倒しneeds_reviewとする。
    try:
        product_links = parse_product_links(article.product_links)
    except Exception as exc:  # noqa: BLE001 - 想定外でも必ずSheetsに記録する
        logger.exception("product_links列の再解釈に失敗しました")
        logger.error(
            "最終結果: id=%s note_url=%s final_status=needs_review"
            "(product_links再解釈失敗)",
            article.id,
            note_url,
        )
        manager.mark_needs_review(
            article,
            stage="note",
            message=(
                f"note下書き作成(note_url取得)には成功しましたが、"
                f"product_links列の再解釈に失敗しました: {exc}"
                f" note_url={note_url}"
            ),
        )
        return 1

    if product_links:
        message = (
            f"note下書きの作成・下書き保存は成功しました(note_url={note_url})。"
            f"商品リンク({len(product_links)}件)の自動設定は行っていません"
            "(note.com側の商品リンク設定UIの実機での不安定さのため)。本文には"
            "商品名・「→ 商品を見る」が通常テキストとして入っています。note"
            "下書きを開き、該当箇所へ手動でリンクを設定したうえで、status を "
            "draft_created に変更してください。"
        )
        try:
            manager.mark_needs_review_with_note_url(
                article, note_url=note_url, message=message
            )
        except ManualLinkSetupVerificationError as exc:
            logger.error(
                "最終結果: id=%s note_url=%s final_status=needs_review"
                "(read-back検証失敗) detail=%s",
                article.id,
                note_url,
                exc,
            )
            return 1

        logger.info(
            "最終結果: id=%s note_url=%s "
            "final_status=needs_review(商品リンク手動設定待ち)",
            article.id,
            note_url,
        )
        return 0

    # Craft連携(Phase4)が未実装のうちは、note下書きが作れた時点でdraft_createdとする。
    # mark_draft_created() は書き込み後にSheetsを読み戻し、実際にstatusが
    # draft_createdへ反映されたことを確認できた場合のみ正常終了する
    # (書き込みAPIがエラーを出さなかっただけでは成功とみなさない)。
    try:
        manager.mark_draft_created(article, note_url=note_url, craft_url="")
    except DraftCreationVerificationError as exc:
        logger.error(
            "最終結果: id=%s note_url=%s final_status=needs_review(read-back検証失敗) "
            "detail=%s",
            article.id,
            note_url,
            exc,
        )
        return 1

    logger.info(
        "最終結果: id=%s note_url=%s final_status=draft_created", article.id, note_url
    )
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
