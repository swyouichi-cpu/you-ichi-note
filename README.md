# you-ichi-note

ChatGPTで作成した記事を Google Sheets 経由で note に「下書き」として自動投稿し、
将来的には Craft へもアーカイブする仕組みです。

**現在の実装状況(2026年時点)**

| Phase | 内容 | 状態 |
|---|---|---|
| 1 | Google Sheets連携(対象記事の取得) | ✅ 実装済み |
| 2 | status管理(二重投稿防止・安全な復旧) | ✅ 実装済み |
| 3 | note下書き作成(Playwright) | 🚧 スケルトンのみ。実画面での検証が必要 |
| 4 | Craftアーカイブ | ⏳ 未着手(CraftのAPI Connection設定待ち) |
| 5 | 全体統合 | ⏳ 未着手 |
| 6 | GitHub Actions定時実行 | ✅ ワークフロー作成済み(手動実行で検証可能) |

**重要な安全設計**
- このシステムは **noteの「公開」ボタンを絶対に自動で押しません**。行うのは「下書き保存」までです。
- 二重投稿防止を最優先しています。処理が途中で止まった場合、自動では復旧させず、
  必ず `needs_review` という状態にして人間の確認を求めます。
- **秘密情報(Googleサービスアカウントの鍵、noteのログインセッション)は、
  あなたがGitHub Secretsに直接登録し、Claudeとのチャットには一切貼り付けません。**
  動作確認はGitHub Actionsの実行ログ、または(必要な場合のみ)デバッグ用の
  スクリーンショットArtifactを通じて行います(詳細は「5. 秘密情報を共有せずに動作確認する方法」)。

---

## 1. 全体の流れ

```
ChatGPTで記事作成
   ↓
Google Sheetsに登録 (status = ready)
   ↓
GitHub Actionsが1日1回起動(手動実行も可)
   ↓
Pythonスクリプトがシートを確認
   ↓
対象記事があれば status: ready → processing
   ↓
Playwrightでnoteに下書き作成(公開はしない)
   ↓
成功したら status: processing → draft (note_urlを記録)
   ↓
あなたがnoteを開いて確認 → 手動で公開ボタンを押す
```

## 2. Google Sheetsの列構成

| 列名 | 内容 |
|---|---|
| `id` | 記事固有ID(重複しない値。手動で採番してください) |
| `title` | タイトル |
| `body` | 本文 |
| `tags` | タグ(カンマ区切り。例: `思考,ブランド,経営`) |
| `category` | 記事カテゴリー |
| `source_theme` | 元になったテーマ |
| `content_type` | `free` または `paid`(現在は`free`のみ自動処理対象) |
| `status` | 下記「statusの状態遷移」を参照 |
| `publish_at` | 将来の公開予定日時(現時点では未使用) |
| `note_url` | note下書きのURL(自動で記録される) |
| `craft_url` | CraftのURL/ID(Phase4で自動記録予定) |
| `error_message` | エラー・要確認の内容(自動で記録される) |
| `created_at` | 作成日時 |
| `updated_at` | 最終更新日時(自動更新。**UTC基準**、日本時間ではない点に注意) |

1行目はヘッダー行(上記の列名)にしてください。列の並び順は自由です(列名で判定するため)。

## 3. statusの状態遷移

```
ready → processing → draft → published
                    ↘ error        (原因が明確な失敗)
                    ↘ needs_review (成否が不明。人間の確認が必要)
```

- `ready`: 自動処理の対象。まだ手つかず。
- `processing`: 現在処理中。**この状態のまま長時間残っていても、システムは絶対に自動で`ready`に戻しません。** 二重下書きを防ぐためです。次回実行時に自動的に`needs_review`へ変更され、`error_message`に状況(note_urlが記録済みかどうか)が書かれます。
- `draft`: note下書き作成(将来的にはCraft保存も)が成功した状態。あなたの確認待ち。
- `published`: あなたが手動でnoteの公開ボタンを押した後、手動でこのstatusに変更する想定(現時点では自動化していません)。
- `error`: 原因が明確な失敗。`error_message`に工程名と内容が入ります。
- `needs_review`: 成功したか失敗したかシステムが確定できない状態。**自動では再処理されません。** note側の実際の状態を確認し、手動で`ready`(下書きが存在しない場合)または`draft`(下書きが存在する場合、note_urlも手動で埋める)に書き換えてください。

## 4. セットアップ手順

### 4-1. Google Sheets連携

1. Google Cloud Consoleで新しいプロジェクトを作成
2. 「サービスアカウント」を作成し、鍵(JSON形式)をダウンロード
3. 作成したスプレッドシートを、サービスアカウントのメールアドレス
   (`xxxx@xxxx.iam.gserviceaccount.com` のような形式)と共有(**編集者**権限)
4. スプレッドシートのURLからID部分をコピー

### 4-2. ローカルでの動作確認(任意)

自分のPCで試したい場合は以下の手順です。**`.env`はあなたのPCの中だけに置き、
Claudeとのチャットには内容を貼り付けないでください。**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# .env を開いて、GOOGLE_SERVICE_ACCOUNT_JSON / SPREADSHEET_ID を実際の値に書き換える
# (この.envファイルはあなたのPCだけに置く。Claudeには絶対に共有しない)

# シートに接続できるか、対象記事があるか確認する(何も書き換えない)
python -m src.main fetch

# processingのまま残っている行をneeds_reviewに変更する
python -m src.main reconcile
```

これらのコマンドの**画面出力(ログ)には秘密情報は表示されません**。
うまく動かない場合は、その出力テキストだけをClaudeに共有してもらえれば
デバッグできます(鍵の中身を貼る必要はありません)。

### 4-3. noteのログインセッション取得(Phase3で使用)

```bash
pip install playwright
playwright install chromium
python scripts/note_login_bootstrap.py
```

表示されたブラウザで手動でnoteにログインし、ターミナルでEnterを押すと、
続けて記事作成画面(note.com/notes/new。現在はeditor.note.comへ転送される)
を自動で開きます。その画面が表示されたことを確認してもう一度Enterを押すと
`note_storage_state.json` が生成されます(記事作成画面を一度開くのは、
note.comとは別ドメインのeditor.note.com側でしか作られない情報を
取りこぼさないようにするためです)。**このファイルの中身はClaudeを含め
誰にも共有せず**、下記4-4の手順でGitHub Secretsへ直接登録してください。

### 4-4. GitHub Secretsへの登録(値は必ずGitHubの画面で直接入力する)

1. GitHubリポジトリの `Settings > Secrets and variables > Actions` を開く
2. `New repository secret` から、以下をそれぞれ登録する

| Secret名 | 登録する値 |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントの鍵ファイルの中身(JSON全文) |
| `SPREADSHEET_ID` | スプレッドシートのID |
| `NOTE_STORAGE_STATE` | `note_storage_state.json` の中身(JSON全文) |

3. (任意) `Settings > Secrets and variables > Actions > Variables` タブで
   `SHEET_NAME` を登録すると、シート(タブ)名を`Sheet1`以外にできる

**これらの値は、Claudeとのチャット・Issue・コミットメッセージなど、
GitHub Secrets以外のどこにも入力しないでください。**登録が終わったら、
ローカルに残っている `note_storage_state.json` や `.env` は削除して構いません。

## 5. 秘密情報を共有せずに動作確認する方法

Claudeは秘密情報の中身を一切受け取らずに、以下の方法で動作確認・デバッグを
手伝うことができます。

**方法A: GitHub Actionsを手動実行して、ログだけ確認する**

1. GitHubリポジトリの `Actions` タブ → `Content Pipeline` を開く
2. `Run workflow` から手動実行(`mode`に `fetch` を選ぶと、何も書き換えず
   Sheets接続と対象記事の検出だけを確認できる。`reconcile`はprocessing残留の
   整理のみ。`run`が本番の全体処理)
3. 実行が終わったら、そのログをコピーしてClaudeに共有する
   (ログには本文・パスワード・Cookie・トークンなどは一切出力されない設計)

**方法B: noteの画面操作をスクリーンショットで確認する(Phase3のセレクタ検証用)**

1. `Run workflow` 実行時に `mode: run` かつ `debug_screenshots: true` を指定する
2. 実行後、そのワークフロー実行ページ下部の `Artifacts` から
   `note-debug-screenshots` をダウンロードする(**パイプラインが途中で
   失敗した場合でも必ずアップロードされます**)
3. 失敗した場合、`FAILED_<工程名>.png`(画面)・`FAILED_<工程名>.html`
   (DOM構造)に加えて `FAILED_<工程名>_diag.txt` が生成されます。
   `diag.txt` には、失敗時点のURL・ページタイトル・JSコンソールの
   ログ/エラー・失敗したネットワークリクエスト・HTTPステータスが
   2xx/3xx以外だったレスポンスの一覧が入っています(Cookie・セッション
   情報・Secretsの値は一切含まれません)
4. 中身を確認し、**個人情報や記事本文が写っていないか自分の目で確認した
   うえで**問題なさそうなファイルだけをClaudeに共有する

この方法で、`src/note.py` の未検証部分(タイトル欄・本文欄・タグ欄の
指定など)や、「画面が正しく読み込まれているか」自体を、実際の画面を
見ながら一緒に直していきます。特に「ローディング状態のまま止まって
いないか」は `_wait_for_editor_mounted` というチェックで、セレクタの
不一致とは別の失敗として区別できるようにしています。

## 6. テスト実行

```bash
pip install -r requirements-dev.txt
pytest
```

Sheets・note実際のAPIへは一切アクセスせず、ロジック部分だけをテストしています。

## 7. GitHub Actionsのスケジュール

`.github/workflows/content-pipeline.yml` は毎日 UTC 00:00(=日本時間 09:00)に
自動実行されます。手動実行(`workflow_dispatch`)にも対応しています。
同時に複数の実行が重ならないよう`concurrency`設定を入れているため、
前回の実行が終わる前に手動実行しても、待機されるだけで二重実行にはなりません。

## 8. noteのタグ(ハッシュタグ)設定について

noteの現在のエディタでは、タグ入力欄は本文の編集画面には無く、
「公開に進む」ボタンの先にある**公開設定パネル**の中にあります
(実機で確認済み)。このパネルには実際に記事を投稿してしまう
「投稿する」ボタンもありますが、`src/note.py`のコードは**このボタンを
一切参照・クリックしません**。

タグ設定の流れ:
1. 「公開に進む」を押して公開設定パネルを開く(これ自体は投稿されない)
2. ハッシュタグを1件ずつ入力し、チップとして確定したことを都度確認
3. 「キャンセル」を押して編集画面へ戻る(投稿しない)
4. **もう一度**「公開に進む」を押してパネルを開き直し、タグが実際に
   保持されているかを確認する
5. 保持が確認できたら「キャンセル」でパネルを閉じ、通常どおり
   「下書き保存」を行う

手順4は、「キャンセルした場合にタグが保持されるか」を人が事前に
確認しきれなかったため、コード自身が実行時に確認する設計にしたものです。
万一保持されていない場合は、下書き保存を行わずに`needs_review`として
処理を中断します。

## 9. 既知の未検証事項・今後の作業

- `src/note.py` のセレクタ(どのボタン・入力欄を操作するか)は、note.comの
  実際の画面をClaude側から直接確認できない(ネットワーク制限)ため、
  1つの指定方法に頼らず複数の候補を順番に試す「フォールバック方式」に
  しています。それでも全滅した場合は、原因調査のため自動的に
  スクリーンショットとHTMLダンプを保存する仕組み(`NOTE_DEBUG_SCREENSHOT_DIR`)
  を用意しています。実際に動くかどうかは、上記「5. 秘密情報を共有せずに
  動作確認する方法」の手順で、GitHub Actions上の実行結果を見ながら
  引き続き検証していく前提です。
- Craft連携(Phase4)は、Craftアプリの「Connections」から発行される
  API URL・認証情報が確定してから実装します。
