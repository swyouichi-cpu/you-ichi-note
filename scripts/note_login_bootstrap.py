"""【このスクリプトは、あなた自身のパソコンでローカル実行してください】
   GitHub Actions上や、Claudeが操作するクラウド環境では実行しないでください。

何をするスクリプトか:
  1. あなたのパソコンでブラウザ(Chromium)を開く
  2. あなたが手動でnoteにログインする(パスワードはこの画面にしか入力されず、
     どこにも送信・記録されません)
  3. ログイン完了後、Enterキーを押すと、実際に記事を作成する画面
     (note.com/notes/new。現在はeditor.note.comへ転送される)を一度開いてから、
     ログイン済みの状態(Cookie・localStorage等)を note_storage_state.json
     というファイルに保存する

     ※ 記事作成画面を一度開いてから保存するのは、note.comとは別ドメインの
     editor.note.com側でしか作られない情報(そのドメイン用のlocalStorage等)
     が、ログイン画面(note.com/login)だけを開いた状態だと保存されない
     可能性があるため。

保存されたファイルの中身(セッション情報)は、あなたのnoteアカウントに
そのままログインできてしまう機密情報です。次の用途以外に使わないでください。
  - GitHub Secrets の NOTE_STORAGE_STATE に登録する(下記手順を参照)
  - このファイル自体は絶対にGitHubへコミットしない(.gitignoreで除外済み)

★重要: このファイルの中身は、Claudeを含む誰にもチャットで貼り付けたり
共有したりしないでください。GitHub Secretsへ直接登録するだけで完結する
運用にしています。動作確認は、GitHub Actionsの実行ログや、
(デバッグ用に有効化した場合のみ)スクリーンショットのArtifactを
確認する形で行います。

事前準備:
  pip install playwright
  playwright install chromium

実行方法:
  python scripts/note_login_bootstrap.py

GitHub Secretsへの登録方法:
  1. 生成された note_storage_state.json の中身をすべてコピーする
  2. GitHubリポジトリの Settings > Secrets and variables > Actions を開く
  3. New repository secret で、名前を NOTE_STORAGE_STATE にして、
     ファイルの中身(JSON全体)をそのまま貼り付けて保存する

セッションの有効期限について:
  noteのログインセッションは、時間の経過や不審なアクセスの検知により
  無効になることがあります。GitHub Actionsの実行結果が
  「ログインセッションが無効」というエラーになった場合は、
  このスクリプトを再実行し、Secretsを更新してください。
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "note_storage_state.json"

# src/note.py の NOTE_NEW_NOTE_URL と同じ値。このスクリプトは単独でも
# 実行できるようにするため、あえてimportせず値を重複させている
# (src/note.py側を変更したら、ここも合わせて変更すること)。
NOTE_NEW_NOTE_URL = "https://note.com/notes/new"


def main() -> None:
    print("ブラウザを起動します。表示された画面で、手動でnoteにログインしてください。")
    print("(2段階認証などがあれば、それもこの画面で完了させてください)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://note.com/login")

        input("\nnoteへのログインが完了したら、ここでEnterキーを押してください... ")

        print("記事作成画面を開いて、その画面用の情報も取得します...")
        page.goto(NOTE_NEW_NOTE_URL, wait_until="networkidle")
        print(f"到達したURL: {page.url}")
        input(
            "記事作成画面が正常に表示されていることを確認したら、"
            "ここでEnterキーを押してください... "
        )

        context.storage_state(path=str(OUTPUT_PATH))
        browser.close()

    print(f"\n保存しました: {OUTPUT_PATH}")
    print("このファイルの中身を GitHub Secrets の NOTE_STORAGE_STATE に登録してください。")
    print("登録が終わったら、このファイルはパソコンから削除して問題ありません。")


if __name__ == "__main__":
    main()
