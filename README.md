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
| 6 | GitHub Actions定時実行 | ⏳ 未着手 |

**重要な安全設計**
- このシステムは **noteの「公開」ボタンを絶対に自動で押しません**。行うのは「下書き保存」までです。
- 二重投稿防止を最優先しています。処理が途中で止まった場合、自動では復旧させず、
  必ず `needs_review` という状態にして人間の確認を求めます。

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

### 4-2. ローカルでの動作確認

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
# .env を開いて、GOOGLE_SERVICE_ACCOUNT_JSON / SPREADSHEET_ID を実際の値に書き換える

# シートに接続できるか、対象記事があるか確認する(何も書き換えない)
python -m src.main fetch

# processingのまま残っている行をneeds_reviewに変更する
python -m src.main reconcile
```

### 4-3. noteのログインセッション取得(Phase3で使用)

```bash
pip install playwright
playwright install chromium
python scripts/note_login_bootstrap.py
```

表示されたブラウザで手動でnoteにログインし、ターミナルでEnterを押すと
`note_storage_state.json` が生成されます。中身をGitHub Secretsの
`NOTE_STORAGE_STATE` に登録してください(詳細はスクリプト内のコメント参照)。

**注意**: このファイルにはログイン済みセッション情報が入っています。
パスワードそのものではありませんが、あなたのnoteアカウントを操作できてしまう
機密情報です。GitHubには絶対にコミットしないでください(`.gitignore`で除外済み)。

## 5. テスト実行

```bash
pip install -r requirements-dev.txt
pytest
```

Sheets・note実際のAPIへは一切アクセスせず、ロジック部分だけをテストしています。

## 6. 既知の未検証事項・今後の作業

- `src/note.py` のセレクタ(どのボタン・入力欄を操作するか)は、note.comの
  実際の画面を見ながらユーザーと一緒に検証する前提の暫定実装です。特に
  タグ入力部分は未実装(`NotImplementedError`)です。
- Craft連携(Phase4)は、Craftアプリの「Connections」から発行される
  API URL・認証情報が確定してから実装します。
- GitHub Actionsのワークフロー(Phase6)はまだ作成していません。
  スケジュールはUTC基準になるため、日本時間との対応をワークフロー作成時に
  明記します。
