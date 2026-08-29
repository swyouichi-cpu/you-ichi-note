# you-ichi-note

ChatGPTで作成した記事を Google Sheets 経由で note に「下書き」として自動投稿する
仕組みです。**下書き作成までを完全自動化し、実際の公開は必ず人間が行います。**

## Phase 1: 完成(実機検証済み)

**Phase 1「Google Sheets → GitHub Actions → note下書き自動生成」は、2026年8月28日、
TEST-003による実機検証をもって完成・安定版として確定しました。**

Phase 1の範囲は以下の通りです。

| 機能 | 状態 |
|---|---|
| Google Sheets連携(対象記事の取得・status管理) | ✅ 完成・実機検証済み |
| 二重投稿防止・安全な復旧(needs_review化) | ✅ 完成・実機検証済み |
| note下書き自動作成(Playwright) | ✅ 完成・実機検証済み(TEST-003) |
| 本文末尾ハッシュタグ方式 | ✅ 完成・実機検証済み(noteのハッシュタグとして認識されることを確認) |
| GitHub Actions定時実行 | ✅ 完成(ワークフロー作成済み・手動実行で検証済み) |

**Phase 1に含まれないもの(意図的にスコープ外。今後のPhaseで検討)**:
自動公開・予約投稿、公開設定パネルの自動操作、Craft連携、cronによる定期実行の本格運用、
AIによる記事生成、有料記事対応。これらはPhase 1の安全設計を変更する提案があった場合でも、
別途Phase 2以降として明示的に合意してから着手します。

**重要な安全設計(Phase 1で確定・今後も維持)**
- **自動化するのは「下書き保存」までです。noteの「公開」ボタンは絶対に自動で押しません。**
  実際の公開は必ず人間が、下書きを確認したうえで手動で行います。
- 二重投稿防止を最優先しています。処理が途中で止まった場合や状態が不整合になった場合、
  自動では復旧させず、必ず `needs_review` という状態にして人間の確認を求めます。
- 本文入力欄は、根拠のある候補セレクタでしか特定しません。特定できない場合や、
  タイトル入力欄と同一のDOM要素だった場合は、推測で入力せず安全停止します。
- 本文は、実際に入力した入力欄からその場で読み戻し(read-back)、期待した内容と
  一致するかを下書き保存の前後両方で確認します。
- **秘密情報(Googleサービスアカウントの鍵、noteのログインセッション)は、
  あなたがGitHub Secretsに直接登録し、Claudeとのチャットには一切貼り付けません。**
  動作確認はGitHub Actionsの実行ログ、または(必要な場合のみ)デバッグ用の
  スクリーンショットArtifactを通じて行います(詳細は「5. 秘密情報を共有せずに動作確認する方法」)。

詳細な安全設計は「11. Phase 1の安全要件(絶対に維持する)」を参照してください。

---

## 1. 全体の流れ(Phase 1完成フロー)

```
ChatGPTで記事作成
   ↓
Google Sheetsに登録 (status = ready、body/tags列は別々に保持)
   ↓
GitHub Actionsが1日1回起動(手動実行も可)
   ↓
Pythonスクリプトがシートを確認
   ↓
processing残留・ready+note_url不整合があれば先にneeds_reviewへ整理
   ↓
対象記事(status=ready かつ publish_atが空または現在時刻以前かつ
note_urlが空)があれば status: ready → processing
   ↓
noteで新規記事作成 → タイトル入力
   ↓
本文入力欄(ProseMirror)を特定して本文を入力
  (本文末尾に5行改行 + 商品導線セクション + 5行改行 + "#タグ1 #タグ2 ..."
   を追記した状態で入力。商品導線・タグそれぞれ0件なら追記しない。
   ECの生URLは本文の文字列としては一切登場させない)
   ↓
商品導線がある場合、「→ 商品を見る」だけにnoteの選択ツールバー経由で
リンクを設定(商品カードへ変換されない方式。実機で人間が確認済み)
   ↓
本文入力欄からread-back確認(テキスト)+ 商品導線のリンクをDOM上で確認(href)
   ↓
autosave完了を確認
   ↓
「下書き保存」のみ実行(公開系ボタンは一切押さない)
   ↓
保存後にもう一度、本文read-back確認 + 商品導線リンク確認
   ↓
note_url取得
   ↓
Sheetsへ書き戻し(status=draft_created, note_url, updated_at)
   ↓
書き戻し直後にSheetsを読み戻すread-back verification
   ↓
一致すれば正常終了。不一致ならneeds_reviewへ倒す
   ↓
あなたが下書きを手動で開いて確認 → 手動で公開ボタンを押す
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
| `publish_at` | 公開予定日時(ISO8601形式、UTC推奨)。空、または現在時刻以前の場合のみ自動処理の対象になる |
| `note_url` | note下書きのURL(自動で記録される) |
| `craft_url` | CraftのURL/ID(Craft連携は未着手。「13. Phase 2以降の検討事項」参照) |
| `error_message` | エラー・要確認の内容(自動で記録される) |
| `created_at` | 作成日時 |
| `updated_at` | 最終更新日時(自動更新。**UTC基準**、日本時間ではない点に注意) |
| `product_links` | 記事末尾に載せる商品導線(JSON配列)。空または`[]`なら商品導線なし。詳細は「10. 商品リンク(本文末尾のテキストリンク方式)」 |

1行目はヘッダー行(上記の列名)にしてください。列の並び順は自由です(列名で判定するため)。

## 3. statusの状態遷移

```
ready → processing → draft_created → published
                    ↘ error        (原因が明確な失敗)
                    ↘ needs_review (成否が不明。人間の確認が必要)
```

- `ready`: 自動処理の対象。まだ手つかず。ただし実際に対象になるのは、
  `publish_at`が空、または現在時刻(UTC)以前の行だけです。
- `processing`: 現在処理中。**この状態のまま長時間残っていても、システムは絶対に自動で`ready`に戻しません。** 二重下書きを防ぐためです。次回実行時に自動的に`needs_review`へ変更され、`error_message`に状況(note_urlが記録済みかどうか)が書かれます。
- `draft_created`: note下書き作成(将来的にはCraft保存も)が成功した状態。あなたの確認待ち。
- `published`: あなたが手動でnoteの公開ボタンを押した後、手動でこのstatusに変更する想定(現時点では自動化していません)。
- `error`: 原因が明確な失敗。`error_message`に工程名と内容が入ります。
- `needs_review`: 成功したか失敗したかシステムが確定できない状態。**自動では再処理されません。** 以下のいずれかで発生します。note側の実際の状態を確認し、手動で`ready`(下書きが存在しない場合)または`draft_created`(下書きが存在する場合、note_urlも手動で埋める)に書き換えてください。
  - タグの形式があいまいで自動判定できなかった場合(後述「8. ハッシュタグ方式」)
  - 本文入力欄を安全に特定できなかった場合(後述「9. 本文editorのセレクタ方針」)
  - `product_links`の形式が不正、またはリンク設定UIを安全に特定できなかった場合
    (後述「10. 商品リンク(本文末尾のテキストリンク方式)」)
  - 本文のread-back検証(入力直後・下書き保存後)で内容が一致しなかった場合
    (ECの生URLをnoteが商品カードへ自動変換してしまった場合もここで検出されます)
  - 商品導線のリンク検証(href・アンカーテキスト)が一致しなかった場合
  - Sheetsへの書き込み後、read-back検証で`status`等が実際には反映されていないと判明した場合
  - `status=ready`なのに`note_url`が既に入っている不整合な行を検出した場合
    (この場合、新しいnote下書きは重複作成しません)

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

### 4-3. noteのログインセッション取得

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

**方法B: noteの画面操作をスクリーンショットで確認する(セレクタ検証用)**

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

Phase 1では、この方法でタイトル欄・本文欄(ProseMirror)のセレクタを
実機のDOM構造から確定しました(詳細は「9. 本文editorのセレクタ方針」)。
今後noteのUI変更でセレクタが壊れた場合も、同じ方法で診断データを取得し、
実機DOMに基づいて修正します(推測でセレクタを追加することはしません)。
「画面が正しく読み込まれているか」自体は `_wait_for_editor_mounted` という
チェックで、セレクタの不一致とは別の失敗として区別できるようにしています。

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

## 8. ハッシュタグ方式(正式仕様・実機検証済み)

以前は、「公開に進む」ボタンの先にある**公開設定パネル**でタグを入力し、
「キャンセル」で編集画面に戻る方式を実装していました。しかし実機での
検証の結果、このパネルは「キャンセル」を押すと入力内容(タグを含む)を
すべて破棄する仕様であることが判明しました。これは自動化側の不具合では
なく、note.com側の公式な(ドキュメント化された)挙動です。

そのため、**公開設定パネルは一切使用しません**。代わりに、note公式ヘルプが
案内している「本文中に半角の `#タグ名` を直接書く」という、公開前の下書きに
ハッシュタグを設定する唯一の公式にサポートされた方法に統一しています。

具体的には、noteへ入力する直前に、本文の末尾へ5行分の改行を挟んで
`#タグ1 #タグ2` のようなタグ行を追記してから入力します
(`src/note.py` の `build_body_with_hashtags()`)。Google Sheetsの`body`列・
`tags`列そのものは変更しません(別々に保持したまま、noteへ入力する直前に
1回だけ組み立てます)。タグが1件も無い場合は本文を一切変更しません。

この方式への移行により、「公開に進む」ボタンへは**一切遷移しなくなりました**
(押さないだけでなく、その画面遷移を行うコード自体が存在しません)。

**タグの書式**:
- 半角の`#`+タグ名を、半角スペース区切りで1行に並べます(例: `#テスト #自動投稿`)
- Sheets側の`tags`列はカンマ区切りのままで構いません(例: `テスト,自動投稿`)
- 正規化として行うのは、前後の空白除去・先頭`#`の除去・空タグの除外・
  重複タグの除外のみです
- タグ名の内部に空白が含まれる場合(例:「広島 レモン」)は、本文末尾の
  タグ行が半角スペース区切りであるため、どこまでが1つのタグかを安全に
  判定できません。このようなあいまいな入力を自動で「直す」(空白を詰める
  など)ことは絶対に行わず、`status`を`needs_review`にして人間の確認を
  求めます

**実機確認済み(2026年8月28日、TEST-003)**: 生成された下書きを人間が
手動で開き、「公開に進む」を押して公開設定画面を確認したところ、本文末尾の

```
#テスト #自動投稿
```

が、noteの公開設定画面で正式に「#テスト」「#自動投稿」の2つのハッシュタグ
として自動認識されることを確認しました。つまり「Sheetsのtags列 → 本文末尾
へ5行改行 → `#タグ1 #タグ2` 形式で追記 → note下書き保存 → 人間が手動で
『公開に進む』→ noteがハッシュタグとして自動認識」という一連の流れは
実機検証済みです。**この確認は今後も人間が手動で行うものであり、
自動化していません**(自動化コードが「公開に進む」へ遷移することは
安全設計上ありません)。

## 9. 本文editorのセレクタ方針(正式仕様・実機DOM確定)

noteの本文編集欄は [ProseMirror](https://prosemirror.net/) というリッチ
テキストエディタで実装されています。実機の失敗時HTMLダンプ(2026年8月28日、
Content Pipeline #18)から、本文editorの実際のDOM構造を確認しました。

```html
<div
  contenteditable="true"
  role="textbox"
  aria-multiline="true"
  class="ProseMirror note-common-styles__textnote-body"
  data-placeholder="たのしかった旅行について、書いてみませんか？">
```

タイトルは `<textarea placeholder="記事タイトル">` という別要素であり、
本文editorとは構造的に明確に区別できます。この実機DOMに基づき、
`_fill_body()`(`src/note.py`)は以下の優先順位でセレクタを試します。

1. `div.ProseMirror.note-common-styles__textnote-body[contenteditable="true"]`
   (class名ベース。意味のある接頭辞を持つクラス名を使い、
   styled-components由来の `sc-xxxx` ハッシュクラスには依存しません)
2. `div.ProseMirror[contenteditable="true"][role="textbox"][aria-multiline="true"]`
   (class名が変わった場合の保険。role/aria属性の組み合わせを使い、
   `data-placeholder` の日本語全文には依存しません)
3. `role=textbox name=本文`、class名に body/editor を含むcontenteditable
   (さらなる保険として維持している旧来の候補)

**`.first` や `nth(1)` のような位置ベースの無条件フォールバックは意図的に
用意していません。** 過去にこの種のフォールバックが本文editorではない別の
要素(タイトル入力欄)を誤って掴み、本文が完全に失われる重大な不具合を
引き起こしたためです(2026年8月28日、Content Pipeline #16で発生)。上記の
候補がすべて一致しない場合は、本文への入力を一切行わずNotePosterErrorを
送出し、`needs_review`として安全停止します(推測で別要素へ入力しません)。

候補が一致した場合も、`_same_element()` で、解決した要素がタイトル入力欄
と同一のDOM要素でないことを必ず確認します。同一だった場合も誤検出とみなし、
入力せずに中断します。

**本文の検証方法**: 本文を入力した直後、旧来の「ページ内のどこかに
“0 文字” という文字列が見えるか」という間接的なチェックはもう使いません。
代わりに `_assert_body_matches()` が、実際に入力に使ったlocatorそのものから
`inner_text()` を読み戻し(read-back)、期待した本文(本文+5行改行+
ハッシュタグ)と一致するかを確認します。note側のcontenteditableは改行の
表現(`\n` / `<br>` / 空divなど)が実装により変わりうるため、比較前に
空白文字を全て除去して正規化します。この検証は「下書き保存」を押す前と、
押した後の両方で行い、保存によって内容が失われていないかも確認します。

## 10. 商品リンク(本文末尾のテキストリンク方式)

実機テストで、本文中にECサイトの生URLを2件置いたところ、noteのエディタが
URLを自動的に**商品カード**(画像・商品名・価格・説明・購入導線を含む大きな
埋め込み)へ変換し、本文read-back検証が(正しく)不一致を検出して
`needs_review`へ安全停止する事象が発生しました。この安全停止自体は正しい
挙動であり、文字数差の許容・比較の緩和・read-backの無効化では対応せず、
根本原因(本文中の生URL)を無くす方式に変更しました。

**方式**: 本文にはECの生URLを一切含めません。代わりに`product_links`列
(下記フォーマット)から、本文末尾に以下のようなプレーンテキストの商品導線
セクションを追記し、「→ 商品を見る」という固定文言だけに、noteのフロー
ティング編集ツールバーの「リンク」ボタン(下記参照)経由でインライン
リンクを設定します。2026年8月29日に「適用」クリック後の実機Artifactで
`<a>`要素の生成を確認できたため、下書き保存・保存後read-back確認まで
一連の流れとして実装済みです(詳細は後述)。

```
この記事に出てきた商品

{商品名1}
→ 商品を見る

{商品名2}
→ 商品を見る
```

**実機確認の経緯(2026年8月29日)**: 当初、人間による実機の手動確認で
note公式の「エディタのガイド」にリンク挿入のショートカット`⌘+K`(Mac)が
存在すると見え、本文中の「→ 商品を見る」を選択して`⌘+K`を押すとURL入力欄
が開き、選択した文字列だけがインラインリンクになる(商品カードへは変換
されない)ことを確認したため、`Control+K`/`Meta+K`ショートカット方式
(`_open_link_input_via_shortcut()`、以下「旧ショートカット方式」)を
実装しました。

しかし実機のGitHub Actions実行(`TEST-004`)では、`Control+K`・`Meta+K`
のどちらを送信してもURL入力UIは表示されませんでした。ここで得られた
追加の実機Artifact(04/05/06のHTMLダンプ・スクリーンショット・診断
データ)を詳細に解析した結果、**「たまたま動かなかった」のではなく、
noteの「エディタのガイド」の「キーボードショートカット」一覧に、
そもそもリンク操作が存在しないことが判明しました**。同パネルの「ボタン」
一覧には「リンク」という項目名が並んでいますが、これは単にツールバーの
ボタンの一覧であり、その下の「キーボードショートカット」セクション
(実際にショートカットキーが割り当てられている操作の一覧)には以下の
14件しか登録されていません。

```
元に戻す(Ctrl+Z) やり直し(Ctrl+Y) 下書き保存(Ctrl+S) 太字(Ctrl+B)
取り消し線(Ctrl+Shift+X) 引用(Ctrl+Shift+>) 本文(Ctrl+Alt+0)
見出し[h2](Ctrl+Alt+2) 小見出し[h3](Ctrl+Alt+3) コード(Ctrl+Alt+\)
中央寄せ(Ctrl+Shift+E) 左寄せ(Ctrl+Shift+L) 右寄せ(Ctrl+Shift+R)
箇条書きリスト(Ctrl+Shift+8)
```

「リンク」はこの一覧に含まれていません。そのため旧ショートカット方式は
**フォールバックとしても残さず完全に撤去しました**(`_open_link_input_
via_shortcut()`を削除)。

**リンク設定操作: フローティング編集ツールバーの「リンク」ボタン
(`_find_active_link_toolbar_button()`)**: 同じ実機Artifactから、ツール
バー自体の実DOM構造も判明しました。

```html
<div data-active="true" role="toolbar" id="desktop-toolbar" ...>
  ...
  <button tabindex="0" aria-label="リンク" aria-pressed="false" ...>
  ...
</div>
```

`04`/`05`/`06`のいずれの実機Artifactでも、`role="toolbar"`かつ
`data-active="true"`の要素はページ内にちょうど1件、その内部の
`aria-label="リンク"`ボタンもちょうど1件であることを確認しました。
`_find_active_link_toolbar_button()`は、この2段階の一意性
(`div[role="toolbar"][data-active="true"]`がちょうど1件→その内部の
`button[aria-label="リンク"]`がちょうど1件)を確認したうえでボタンを
返します。style属性内のclass名(`sc-xxxx`のようなstyled-components由来
のハッシュ)には一切依存しません。いずれかが1件でなければ`.first()`/
`.nth()`等の推測に頼らず`needs_review`へ安全停止します。

**出現タイミングの非同期遅延への対応**: 上記のセレクタを実際にGitHub
Actionsで実行したところ、実行ログでは「ツールバーが0件」として安全停止
していましたが、その直後に`_capture_failure()`が保存したHTMLダンプには
実際にはツールバー(とリンクボタン)が存在していました。これはセレクタの
誤りではなく、「→ 商品を見る」のテキスト選択が完了してからnoteが実際に
ツールバーをDOMへマウントし`data-active="true"`にするまでの**短い非同期の
遅延**を考慮していなかったことが原因でした。そこで`_find_active_link_
toolbar_button()`に`_wait_for_locator_to_appear()`を組み込み、ツール
バー・リンクボタンそれぞれについて、上限`_LINK_TOOLBAR_APPEAR_TIMEOUT_MS`
(既定3000ms)だけ出現を待ってから、改めて`count()`で一意性を確認する
設計に変更しました。固定の`time.sleep()`は使わず、Playwrightの
`Locator.wait_for(state="visible")`による自動待機を使います。出現を
待っても0件のまま、または出現後に複数件になった場合は、これまで通り
`needs_review`へ安全停止します。セレクタ自体(`role`/`data-active`/
`aria-label`)は変更していません。

**待機処理でも`.first()`/`.nth()`は使いません**: `_wait_for_locator_to_
appear()`の当初の実装は`locator.first.wait_for(...)`という形で`.first`を
使っていましたが、これは「待機目的であっても位置ベースの要素選択は
使わない」という安全要件に反するとの指摘を受け、`locator.first`ではなく
**locatorそのもの**に対して`wait_for(state="visible")`を呼ぶ形に修正
しました。Playwrightのlocatorは、待機中に実際に2件以上へ一致すると
strict mode違反として例外(`playwright.sync_api.Error`。`TimeoutError`も
このサブクラス)を送出しますが、これも「一意に特定できない」ケースとして
扱い、位置ベースで1件を選んで先に進むことはしません(`count()`を改めて
取り直してから安全停止します)。

**クリック前のviewport確認**: この方式を実際にGitHub Actionsで実行した
ところ、リンクボタン自体は`_find_active_link_toolbar_button()`で一意に
特定できていたにもかかわらず、`click()`が「element is outside of the
viewport」を繰り返し、既定の30秒タイムアウトいっぱいまで失敗し続ける
事象が発生しました。設定済みのviewport高さ(`viewport={"width": 1280,
"height": 800}`、この設定自体は変更していません)に対し、ツールバーの
実測`top`値(847px/871px)がこれを超えていたことから、クリック対象が
実際には現在のviewportに描画されていなかった可能性が高いと判断しました。

そこで`_ensure_link_button_in_viewport()`を新設し、クリックの前に
以下を行うようにしました。

1. viewport・現在のスクロール位置・ツールバーとボタンの`bounding_box()`
   を診断ログに記録する(原因切り分けのため)
2. `link_button.scroll_into_view_if_needed()`を試みる(固定`time.sleep()`
   は使いません)
3. scroll後に改めて`bounding_box()`を取得し、ボタンの矩形がviewportの
   範囲に完全に収まっているかを検証する(`_bounding_box_within_viewport()`
   という純粋関数に切り出し、座標計算だけを実機DOMなしにテストできる
   ようにしています)

`bounding_box()`が取得できない場合、またはviewportに完全には収まって
いない場合は、`force=True`(Playwrightのactionability check全体を無効化
する)やJavaScriptによる直接の`element.click()`(ブラウザの実際の
ポインタイベント処理・被覆判定を経由しない)のような、実際にユーザーが
クリックできる状態かどうかの保証を失う手段には一切頼らず、推測でクリック
せずに`LinkButtonOutOfViewportError`を送出して安全停止します
(`needs_review`に倒れます)。この2つの手段は「曖昧・不安定なら推測せず
needs_review」という設計方針と正面から矛盾するため、今回も今後も採用
しません。

viewport内に収まっていることを確認できた場合のみ、`_assert_not_publish_
action()`を適用したうえで`click()`します。このクリック自体のタイムアウト
も、既定の30秒(Playwrightの既定値)ではなく、短い上限
(`_LINK_BUTTON_CLICK_TIMEOUT_MS`、既定5000ms)に変更しました。viewport
確認を通過した状態でclick()が長時間ブロックする状況は本来起きないはずで
あり、想定外の場合でも早期に`needs_review`へ倒すためです。

クリックの前には、`_select_product_link_text_in_block()`で対象の
「→ 商品を見る」だけを選択し、`window.getSelection().toString()`を
読み取って選択内容が期待通り「→ 商品を見る」そのものであることを確認する
処理(既存のまま)に加え、クリック対象のボタンに対しても既存の
`_assert_not_publish_action()`を必ず適用してから`click()`します。

**★リンクボタンクリック後の観測専用実装だった段階(現在は完成実装に
置き換え済み)★**: 当初、リンクボタンをクリックした後に実際にどのような
URL入力UI(ポップオーバーかモーダルか、`input`要素か`contenteditable`か
等)が出現するかは一度も実機で観測できておらず、この状態でURL入力欄の
セレクタを推測実装すると旧ショートカット方式のときと同じ「未確認のDOM
構造を前提にした実装」を繰り返すことになるため、リンクボタンをクリック
した直後に意図的にHTML/スクリーンショット/診断データを保存したうえで
`LinkButtonObservationStop`(`NotePosterError`のサブクラス)を送出して
処理を止める観測専用実装にしていました。以降の実機テストでURL入力欄・
「適用」ボタンの実DOMを段階的に確認できたため、現在はこの停止は行わず
(通常の処理経路では`LinkButtonObservationStop`は送出されません)、
下記の通りURL入力・「適用」ボタンクリック・リンク反映確認・下書き保存
まで一連の流れとして実装済みです。`LinkButtonObservationStop`のクラス
自体は削除せず、診断用の例外として残しています。

**`product_links`列のフォーマット**(JSON配列):

```json
[
  {"label": "TOY JAM 瀬戸内レモン", "url": "https://you-ichi.jp/?pid=192116331"},
  {"label": "TOY JAM 瀬戸内レモン月桂樹", "url": "https://you-ichi.jp/?pid=191552342"}
]
```

- `label`: 商品名(本文中に通常テキストとして表示。リンクは付けません)
- `url`: 「→ 商品を見る」だけに設定するリンク先URL(http/httpsの絶対URL)
- 空、または`[]`の場合は商品導線セクション自体を追加しません
- 見出し文言(「この記事に出てきた商品」)は特定の商品カテゴリに依存しない
  汎用的なものにしています。特定記事向けのハードコードは行っていません
- 不正なJSON、`label`/`url`の欠落、不正なURL形式は、タグの内部空白と
  同じ思想で自動修正せず`needs_review`へ倒します(`ProductLinkValidationError`)

**リンク先の特定方法(`_find_product_link_block()`、TEST-004を踏まえた修正)**:
「→ 商品を見る」は商品が複数ある場合、本文中に複数回出現します。当初は
商品名(`label`)の**直後の兄弟要素**として一意特定する方式
(`_resolve_link_target_for_label()`)を実装していましたが、実機の
GitHub Actions実行(`TEST-004`)で、この前提が崩れていることが判明
しました。

noteのエディタは、`build_product_links_trailer()`が生成する
「{商品名}\n→ 商品を見る」という1つのテキストの塊を、別々の`<p>`要素には
せず、**同一の`<p>`要素内に`<br>`を挟んで描画します**。

```html
<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>
```

そのため商品名単体を含む要素も、「→ 商品を見る」単体を含む要素も、
そもそも本文中に単独では存在せず、`get_by_text(..., exact=True)`による
完全一致検索は常に0件になります。これが`TEST-004`で発生した不具合
(商品導線のプレーンテキスト生成自体は成功していたのに、リンク対象
テキストが「0件」として`needs_review`へ安全停止した)の直接の原因でした。

これを受けて`_find_product_link_block()`に置き換えました。本文editor
(`body_locator`、ProseMirrorの実DOM)にスコープを絞ったうえで、

1. 商品名(`label`)を含むブロック要素(`<p>`)が本文editor内にちょうど
   1件だけ存在すること
2. そのブロックのテキストを行単位に分解すると、ちょうど
   `[label, "→ 商品を見る"]` の2行になっていること
   (=商品名の行の直後に「→ 商品を見る」の行が続いている)

を確認したうえで、そのブロック要素(`<p>`)自体を返します。いずれかが
成立しない場合(labelを含むブロックが0件・複数件、ブロック内の行構成が
想定と異なる等)は推測せず`needs_review`へ安全停止します。これは
`_fill_body()`で撤去した「画面上に見えるN番目のcontenteditable要素」の
ような、DOM構造そのものを盲目的に推測する位置ベースフォールバックとは
明確に異なります。noteのDOM構造そのものを推測しているのではなく、
**このコード自身が直前に生成した既知の構造(label行→リンク対象行という
順序)**を、本文editorという明確なスコープの中で確認しているだけです。

ブロック要素が特定できても、そのブロックをまるごと選択してリンクを
設定するわけではありません(それでは商品名までリンク範囲に含まれて
しまいます)。`_select_product_link_text_in_block()`が、ブロックの直接の
子ノードのうち「→ 商品を見る」と完全一致するテキストノードだけを
ブラウザのSelection/Range API(`document.createRange()` /
`window.getSelection()`)で明示的に選択します。ブロックが上記の2行構造で
あることは既に確認済みですが、念のためここでも一致するテキストノードの
件数を数え、ちょうど1件でなければ推測で選択せず安全停止します。

**リンクURL入力欄のセレクタ(実機確認済み)と観測専用実装・第2段階**:
ツールバーの「リンク」ボタンをクリックした後に現れるURL入力欄の実DOMを、
追加の実機Artifactで確認できました。

```html
<textarea inputmode="text" name="alt" placeholder="https://"></textarea>
<button aria-label="URLの入力をやめる"></button>
```

`_find_url_input_textarea()`が、`_URL_INPUT_SELECTOR =
'textarea[placeholder="https://"][inputmode="text"][name="alt"]'`という、
3つの独立した意味のある属性をすべて満たすセレクタで一意に特定します
(class名には依存しません)。`placeholder`のみの案と比較検討した結果、
`name`のような機能的な属性はUIコピー変更の影響を受けにくく、複数属性の
AND条件は「どれか1つでも変われば安全に0件へ倒れる」という望ましい
fail-closed特性を持つため、こちらを採用しました。特定方法は
`_find_active_link_toolbar_button()`と同じ設計です。`_wait_for_locator_
to_appear()`で出現・可視化を待ってから、改めて`count()`を取り直して
ちょうど1件であることを検証します。timeout・0件・複数件・strict mode
違反のいずれも、推測せず`needs_review`へ安全停止します。

一意に特定できた場合のみ`product_links`の対象URLを入力し、入力に使った
**同じlocator**から`input_value()`でread-backして期待したURLと完全一致
することを確認します。read-backが不一致であれば(可能な限り診断データを
保存したうえで)通常の`NotePosterError`で安全停止します。

**「適用」ボタンのクリック(実機確認済み)**: read-backが一致した後、
実機Artifactで確認できた「適用」ボタンをクリックします。実DOMは以下の
通り、URL入力欄と同じフローティング編集ツールバーの内部に存在します。

```html
<div data-active="true" role="toolbar" id="desktop-toolbar">
  ...
  <textarea inputmode="text" name="alt" placeholder="https://"></textarea>
  <button data-name="Button" type="button" id=":r16:">
    <span>適用</span>
  </button>
  <button aria-label="URLの入力をやめる">...</button>
  ...
</div>
```

「適用」ボタンの`id`(`:r16:`のような値)はReactの`useId`等が生成する
動的IDの可能性が高いため、セレクタには使いません。また、ページ全体から
「適用」という文字列を検索するのでもありません。代わりに、URL入力欄の
特定に使ったのと**同じアクティブなツールバー**をスコープとして使い、
`toolbar.get_by_role("button", name="適用", exact=True)`で一意に特定
します(`_find_url_apply_button()`)。この際、
1. `div[role="toolbar"][data-active="true"]`がページ内にちょうど1件で
   あることを再確認
2. そのツールバー内にURL入力欄がちょうど1件存在することを再確認
   (URL入力欄と「適用」ボタンが同じツールバー内にあることを、DOM構造の
   推測やXPathでの祖先指定を使わずに、スコープそのもので保証します)
3. そのツールバー内で「適用」ボタンがちょうど1件であることを確認

のいずれかが成立しなければ`needs_review`へ安全停止します。特定できた
場合は、リンクボタンと同じ`_ensure_link_button_in_viewport()`でviewport
内であることを確認し、`_assert_not_publish_action()`を適用したうえで
通常の`click()`(短いタイムアウト)を行います。

`force=True`やJavaScriptによる直接の`element.click()`、Enterキー送信・
Tabキー送信・意図的なフォーカス解除は引き続き一切使いません。

**「適用」クリック後の完成実装(2026年8月29日)**: 「適用」ボタンを
クリックした実機Artifact(HTML)を解析したところ、対象ブロック内に
実際に

```html
<p ...>TOY JAM 瀬戸内レモン<br>
  <a href="https://you-ichi.jp/?pid=192116331" target="_blank"
     rel="noopener"><span class="highlight">→ 商品を見る</span></a>
</p>
```

という`<a>`要素が生成されており、hrefは入力したURLと完全一致、アンカー
テキストは(`<span class="highlight">`でラップされているが)「→ 商品を
見る」と一致していることを確認できました。フローティング編集ツール
バーはURL入力欄・「適用」ボタンが消え、通常の選択ツールバー(見出し/
太字/リンク/引用等)へ戻っていましたが、`data-active`属性自体は
`"true"`のままでした。

この観測結果を受けて、それまで意図的に行っていた`UrlApplyObservation
Stop`による停止を撤去し、`_wait_for_product_link_applied()`で対象
ブロック内に`<a>`要素(`get_by_role("link", name=_PRODUCT_LINK_TEXT,
exact=True)`)が実際に出現するのを、固定`sleep()`ではなくPlaywrightの
locator待機(`wait_for(state="visible")`)で待つように変更しました。
待っても出現しなければ推測せず`NotePosterError`で安全停止します。
出現を確認できた場合は正常終了し、複数の`product_links`があれば続けて
次の商品のリンク設定に進みます(`_apply_product_links()`のループ)。
実際の件数・テキスト完全一致・href一致の検証はこの待機処理では行わず、
既存の`_assert_links_match()`(この完成実装では変更していません)に
委ねます。`UrlInputObservationStop`・`UrlApplyObservationStop`のクラス
自体は削除せず、それぞれの段階まで実機で確認できたことを示す診断用の
例外として残していますが、通常の処理経路ではどちらも送出されなく
なりました。

以上により、`create_draft()`は本文入力→商品導線リンク設定→本文・
リンクのread-back確認(保存前)→自動保存完了待ち→下書き保存→保存完了
待ち→本文・リンクのread-back確認(保存後)、まで一連の流れとして
実行されます(この一連の流れ自体は本ラウンドより前から実装済みで
あり、`_apply_product_links()`が例外を送出せず正常終了するように
なったことで、初めて商品リンク付きの記事も`draft_created`まで到達
できるようになりました)。商品リンクのいずれか1件でも一意特定・
read-back一致・リンク反映のいずれかに失敗すれば、その時点で
`NotePosterError`が送出され、後続の商品の処理にも下書き保存にも
進みません。

**URL入力〜read-back区間の診断強化(2026年8月29日)**: 上記の第4段階を
実機実行したところ、ある回はURL入力・read-back一致・「適用」ボタン
クリックまで成功した一方、**コード(commit)を一切変更していない**別の
実機実行では、「→ 商品を見る」の選択・リンクボタンのクリック・URL入力欄
の出現・URLの入力までは成功回と同じように進んだにもかかわらず、
read-backの直前でURL入力欄が消失し(`input_value()`の結果が`None`相当に
なり)、失敗時のArtifactではURL入力欄のtextarea自体がDOMから無くなって
おり、active toolbarも`data-active="false"`に戻っていた。同一commitが
成功・失敗の両方を示したことから、原因はコードの回帰ではなく、note側の
実行タイミングに依存する何らかの状態(非同期処理・バリデーション・
再レンダリング等)にあると考えられる。

次回の実機実行1回でできるだけ原因を切り分けられるよう、`press_
sequentially()`自体や「適用」ボタン以降の処理は変更せず、URL入力欄の
一意特定からread-backまでの区間にのみ以下の診断強化・最小修正を追加した:

- `_log_url_input_diagnostics()`: URL入力欄・active toolbarの`count()`/
  `is_visible()`/`input_value()`(1件のときのみ)/`bounding_box()`、
  `document.activeElement`のtagName/name/placeholder/aria-label、
  `window.scrollX`/`scrollY`を読み取り専用で記録する(クリック・
  フォーカス・blur等の操作は一切行わない)。URL入力直前(A)・クリック
  直後(B)・文字入力の前後(C/D)・read-back直前(E)・消失検知時(F)・
  read-back成功後(G)の各段階でこの関数を呼び、状態を`logger.info()`に
  記録する。
- `press_sequentially(link.url, delay=10)`完了直後、read-backを試みる前に
  同じセレクタで`count()`を再確認する。1件でなければ「read-backした値が
  `None`だった」という曖昧な扱いはせず、専用の`UrlInputDisappeared
  ObservationStop`を送出して安全停止する(呼び出し側で`needs_review`へ
  倒れる)。この場合もHTML/スクリーンショット/診断データを保存する。
- `input_value()`自体の呼び出し中に例外が発生した場合(直前の`count()`
  再確認では1件だったにもかかわらず、その直後に消失したケース)は、
  上記の消失検知とは区別し、通常の`NotePosterError`として例外の内容を
  含めて報告する。つまり「locatorの消失(事前チェックで検知)」「read-
  back呼び出し中のPlaywright例外」「read-back値の不一致」の3つを、
  1つの曖昧な`None`比較へ握りつぶさず、それぞれ別のエラーとして報告する。
- URL入力欄が正常であれば(消失も例外も起きなければ)、read-back一致
  確認から「適用」ボタンのクリックまで、1回の呼び出しの中でこれまで
  通り自動的に到達する(このパスは今回変更していない)。

**検証方法(本文テキストとリンクの分離)**: `_assert_body_matches()`は
引き続き「見えているテキスト」だけを検証します(商品リンク導入後も本文に
生URLは一切含まれないため、この検証で商品カード化が起きていないことも
同時に確認できます。カード化が万一発生すれば、カードの追加テキストで
文字数が期待値からずれ、この検証が不一致として検出します)。これとは別に
`_assert_links_match()`が、`_find_product_link_block()`で商品ごとのブロック
を再特定したうえで、そのブロック内の`<a>`要素のhref・アンカーテキストを
個別に検証します。本文中に将来ふつうの参考リンク等が入る可能性があるため、
**本文editor内の`<a>`要素の総数を数える検証は行いません**(自分自身が
生成した商品導線のブロックだけをスコープに検証します)。

具体的には、ブロック内に`<a>`要素がちょうど1件あり、かつそのテキストが
「→ 商品を見る」と完全一致することを確認します。この1つのチェックだけで、
リンクが設定されていない場合(`<a>`要素0件)・余計なリンクが付いた場合
(`<a>`要素2件以上)・**商品名までリンク範囲に含まれてしまった場合**
(`<a>`要素のテキストが「商品名\n→ 商品を見る」のようになり完全一致
しなくなる)のいずれも検出できます。どちらか一方でも不一致なら成功扱いに
せず、下書き保存の前後両方でこの2つの検証を行います。

## 11. Phase 1の安全要件(絶対に維持する)

Phase 1の完成をもって、以下の安全要件を確定事項とします。今後のPhaseで
機能を追加する場合も、これらを変更する提案があった場合は必ず人間の
明示的な合意を得てから着手し、単独の判断で緩めることはしません。

1. 「投稿する」を自動クリックしない
2. 「公開する」を自動クリックしない
3. 「公開に進む」を自動クリックしない(画面遷移のコードパス自体が存在しない)
4. 予約投稿を自動実行しない
5. 公開系APIを直接呼ばない
6. 自動化するのは「下書き保存」まで
7. `_FORBIDDEN_PUBLISH_KEYWORDS`(`src/note.py`)を維持する
8. `_assert_not_publish_action()` を、危険な可能性のあるボタンをクリック
   する直前に必ず呼ぶ
9. 公開設定パネルを自動操作する旧タグ処理を復活させない
10. 想定外の状態では公開方向へ進まず、安全停止する(`needs_review`)
11. 本文editorを特定できない場合は推測で別要素へ入力せず、`needs_review`
12. Sheets側で不整合が発生した場合も、重複下書きを作らない
    (`status=ready`かつ`note_url`が既に入っている行は`needs_review`へ)
13. 商品リンクのリンク設定は、実機Artifactで構造を確認できたフロー
    ティング編集ツールバーの「リンク」ボタン(`aria-label="リンク"`)を
    クリックする方式で行う。クリック対象は`div[role="toolbar"]
    [data-active="true"]`かつ`button[aria-label="リンク"]`がそれぞれ
    ちょうど1件であることを確認したうえでのみ特定し(`.first()`/`.nth()`
    は使わない)、クリック直前には他のボタンクリック箇所と同様に
    `_assert_not_publish_action()`を必ず適用する
14. 商品リンクのリンクボタンをクリックした後に出現するURL入力欄
    (`textarea[placeholder="https://"][inputmode="text"][name="alt"]`)は
    実機Artifactで構造を確認できたため、一意に特定できた場合のみURLを
    入力し、入力に使った同じlocatorから`input_value()`でread-backして
    完全一致を確認する。read-backが不一致の場合(可能な限り)診断データを
    保存したうえで通常の`NotePosterError`で安全停止する
15. 商品リンクのURL入力欄が特定できない場合(0件・複数件・timeout・
    strict mode違反)は、リンクボタンのクリック処理と同様に推測せず
    `needs_review`へ安全停止する
16. **URLの確定方法**のうち、Enterキー送信・Tabキー送信・意図的な
    フォーカス解除・「URLの入力をやめる」ボタンのクリック・他要素の
    クリックは、実機で確認できた「適用」ボタンのクリック以外は一切
    実装しない。診断データの取得(`_capture_failure()`・`_log_url_
    input_diagnostics()`)自体もURL入力欄・ボタンのフォーカスを奪う
    操作を含まないことを確認済みである
17. 商品リンクのリンクボタン・「適用」ボタンをクリックする前には、
    `bounding_box()`を実測してviewportの範囲に完全に収まっていることを
    確認する(`_ensure_link_button_in_viewport()`)。収まっていない場合は
    `force=True`(actionability checkの無効化)やJavaScriptによる直接の
    `element.click()`のような、実際にクリック可能かどうかの保証を失う
    手段には頼らず、`LinkButtonOutOfViewportError`で安全停止する。
    クリック自体のタイムアウトも既定の30秒ではなく短い上限
    (`_LINK_BUTTON_CLICK_TIMEOUT_MS`)にする
18. 商品リンクの「適用」ボタンは、ページ全体からの文字列検索ではなく、
    URL入力欄の特定に使ったのと同じアクティブなツールバーをスコープと
    した`get_by_role("button", name="適用", exact=True)`で一意に特定する
    (`_find_url_apply_button()`)。動的に生成されるid(Reactの`useId`等
    由来と見られる`:r16:`のような値)はセレクタに使わない
19. 商品リンクのURL入力後、`press_sequentially()`完了直後に同じセレクタ
    で`count()`を再確認する。1件でなければ(URL入力欄が消失・増減した
    可能性があるため)read-backを続行せず、`UrlInputDisappeared
    ObservationStop`で安全停止し、HTML/スクリーンショット/診断データを
    保存する。`input_value()`自体の呼び出し中に例外が発生した場合は、
    この消失検知とは別の通常の`NotePosterError`として区別して報告し、
    「read-backした値が`None`だった」という曖昧な扱いはしない。この
    区間の診断ログ取得(`_log_url_input_diagnostics()`)自体も、クリック・
    フォーカス・blur等の操作を一切行わない読み取り専用とする
20. 商品リンクの「適用」ボタンをクリックした後、対象ブロック内に実際に
    `<a>`要素が反映されるまで、固定`sleep()`ではなくPlaywrightのlocator
    待機(`get_by_role("link", name=_PRODUCT_LINK_TEXT, exact=True).
    wait_for(state="visible")`)で待つ(`_wait_for_product_link_
    applied()`)。上限時間内に反映を確認できない場合(0件のまま・
    strict mode違反となる複数件のいずれも)は`needs_review`へ安全停止
    する。反映を確認できた場合も件数・テキスト完全一致・href一致の実際の
    検証はここでは行わず、既存の`_assert_links_match()`に委ねる
    (二重に検証ロジックを実装しない)。複数の`product_links`がある場合、
    1件でも反映を確認できなければ後続の商品の処理には進まず、下書き
    保存へも進まない

## 12. Phase 1 実機検証記録

| テスト行 | 目的 | 結果 |
|---|---|---|
| `TEST-001` | 公開設定パネル方式・Sheets書き込み不整合の検証記録 | `needs_review`のまま(検証用の記録として保持。対応不要) |
| `TEST-002` | 旧本文editorセレクタ(位置ベースフォールバック)失敗の検証記録 | `needs_review`のまま(検証用の記録として保持。対応不要) |
| `TEST-003` | **Phase 1完成確認用の実機検証** | `draft_created`。以下すべて実機確認済み |
| `ARTICLE-001` | 本文中の生URLによる商品カード自動変換の検証記録 | `needs_review`のまま(人間が確認するまで`ready`へ戻さない。対応不要) |
| `TEST-004` | 商品導線リンク設定の実機DOM構造確認(GitHub Actions Run #23) | `needs_review`(リンク対象検出ロジックの不具合を発見・修正。対応不要) |
| `TEST-004`(追加観測) | ショートカット無反応時のツールバー実DOM確認(04/05/06のArtifact) | `needs_review`(ショートカット方式を撤去し、ツールバーボタン方式の観測専用実装へ変更。対応不要) |
| `TEST-004`(ツールバーボタン方式・再実行) | ツールバーボタン方式の実機実行 | `needs_review`(「0件」判定が出現タイミングの非同期遅延によるものと判明。待機処理を追加。対応不要) |
| `TEST-004`(URL入力欄の観測) | リンクボタンクリック後のURL入力UIの実DOM確認 | `needs_review`(URL入力欄のセレクタを確認し、URL入力→read-back確認までの観測専用実装・第2段階を追加。対応不要) |
| `TEST-004`(リンクボタンクリック失敗) | ツールバーボタンクリック時のviewport外エラーの解析 | `needs_review`(クリックがviewport外で30秒timeoutすることが判明。クリック前のviewport確認・安全停止を追加。対応不要) |
| `TEST-004`(URL入力・適用ボタン到達) | viewport確認追加後の実機実行、URL入力・read-back一致・「適用」ボタンの実DOM確認 | `needs_review`(`UrlInputObservationStop`まで到達を確認。「適用」ボタンクリックまでの観測専用実装・第4段階を追加。対応不要) |
| `TEST-004`(URL入力欄消失・同一commitでの再現性差異) | 第4段階と同一commitでの再実行、URL入力後read-back直前でのURL入力欄消失の解析 | `needs_review`(同一commitで成功/失敗の両方を確認。コード回帰ではないと判断し、診断強化と消失検知の安全停止を追加。対応不要) |
| `TEST-004`(「適用」クリック後の完成DOM確認) | 診断強化後の実機実行、URL入力欄消失は再現せず`UrlApplyObservationStop`まで到達、「適用」クリック後の完成DOM確認 | `needs_review`(意図的な観測停止。`<a>`要素の生成を確認し、下書き保存まで進む完成実装に変更。対応不要) |

`TEST-003`(2026年8月28日)での確認事項:
GitHub Actions=Success / Sheets status=`draft_created` / note_url正常記録 /
note上に実際の下書きが作成 / タイトル正常 / 本文正常(文字数カウンター83文字で
正常認識) / 本文保存正常 / 本文末尾のハッシュタグ正常 / 本文とハッシュタグの
間は5行改行で正常 / 自動公開されていない / 公開設定画面への自動遷移なし /
人間による手動確認でハッシュタグとして正しく認識されることを確認。

`ARTICLE-001`(2026年8月29日)で判明した事項: 本文中の生URLがnoteによって
商品カードへ自動変換され、本文read-back検証が(正しく)不一致を検出して
`needs_review`へ安全停止した(詳細は「10. 商品リンク」)。この検証結果を
受けて商品リンク機能(本文末尾のテキストリンク方式)を実装した。当初は
選択ツールバーのボタンクリック方式だったが、同日中にnote公式の「エディタの
ガイド」でリンク挿入ショートカット(`⌘+K`)の存在を確認し、より安定した
自動化契約として`Control+K`/`Meta+K`ショートカット方式へ切り替え、リンク
対象の特定方法もページ全体の`.nth()`出現順序から、商品名との隣接関係に
基づく`_resolve_link_target_for_label()`(商品名の直後の兄弟要素)へ変更した。

`TEST-004`(2026年8月29日、GitHub Actions Run #23)で判明した事項:
商品導線のプレーンテキスト生成自体は成功していたが、リンク対象の検出
ロジックで安全停止した。実機の保存HTMLを確認したところ、ProseMirror内の
実DOMは`<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>`のように、商品名と
「→ 商品を見る」が**別々の`<p>`ではなく同一の`<p>`要素内に`<br>`を挟んで**
存在していた。`_resolve_link_target_for_label()`が前提としていた「商品名の
直後の兄弟要素」という構造(別々の`<p>`)がそもそも実機では成立して
おらず、`get_by_text(..., exact=True)`による完全一致検索が常に0件になる
ことが原因だった。これを受けて`_find_product_link_block()`(商品名を含む
ブロックを行単位のテキスト構造で特定する方式)と`_select_product_link_
text_in_block()`(ブロック内の直接の子テキストノードのうち「→ 商品を
見る」と完全一致するものだけをSelection/Range APIで選択する方式)に置き
換えた(詳細は「10. 商品リンク」)。この修正は`TEST-004`で判明したDOM
構造をfixtureとして再現したテスト(複数商品のケースを含む)で確認済み。
**この修正自体はまだ実際のGitHub Actions実行では再検証していない**
(ローカルpytestでの確認のみ)。

`TEST-004`(追加観測、GitHub Actions再実行)で判明した事項:
`_find_product_link_block()`/`_select_product_link_text_in_block()`による
「→ 商品を見る」の選択自体は実機で成功し、note側のフローティング編集
ツールバーが正しく表示された。しかし`Control+K`/`Meta+K`はどちらも
URL入力UIを開かなかった。ユーザーが実機で取得したHTMLダンプ・
スクリーンショット・診断データ(04: Control+K失敗時、05: Meta+K失敗時、
06: 最終失敗時)を解析した結果、note公式の「エディタのガイド」の
「キーボードショートカット」一覧にリンク操作が存在しないこと(たまたま
反応しなかったのではなく、そもそも存在しない)、および実際のツールバーが
`<div data-active="true" role="toolbar" id="desktop-toolbar">`という
単一のDOM要素で、内部に`aria-label="リンク"`のボタンがちょうど1件存在
することを確認した。これを受けてショートカット方式を完全に撤去し
(`_open_link_input_via_shortcut()`を削除、フォールバックとしても残さず)、
`_find_active_link_toolbar_button()`によるツールバーボタンクリック方式に
切り替えた。ただし、ボタンクリック後に現れるURL入力UIの構造はまだ未観測
のため、クリック直後に意図的に`LinkButtonObservationStop`で安全停止する
**観測専用実装**にとどめている(詳細は「10. 商品リンク」)。この観測
専用実装はローカルpytestでの確認のみで、まだ実際のGitHub Actions実行
では検証していない。

`TEST-004`(ツールバーボタン方式・再実行)で判明した事項:
`_find_active_link_toolbar_button()`によるツールバーボタン方式を実際に
GitHub Actionsで実行したところ、実行ログでは
`div[role="toolbar"][data-active="true"]`が「0件」として安全停止した。
しかし、その直後に`_capture_failure()`が保存したHTMLダンプを解析すると、
実際にはツールバー(および内部の`aria-label="リンク"`ボタン)が1件ずつ
存在していた。セレクタ自体は誤っておらず、「→ 商品を見る」のテキスト
選択が完了してからnoteが実際にツールバーをDOMへマウントし
`data-active="true"`にするまでの短い非同期の遅延を考慮していなかった
ことが原因と判断した。これを受けて`_wait_for_locator_to_appear()`
(Playwrightの`Locator.wait_for(state="visible")`による自動待機。固定
`time.sleep()`は使わない)を導入し、ツールバー・リンクボタンそれぞれに
ついて上限`_LINK_TOOLBAR_APPEAR_TIMEOUT_MS`(既定3000ms)だけ出現を
待ってから改めて`count()`で一意性を確認する設計に変更した(詳細は
「10. 商品リンク」)。セレクタ・観測専用実装の設計(クリック後は
`LinkButtonObservationStop`で安全停止し、URL入力・確定操作は行わない)
自体は変更していない。この待機処理の追加はローカルpytestでの確認のみで、
まだ実際のGitHub Actions実行では再検証していない。

`TEST-004`(URL入力欄の観測)で判明した事項: リンクボタンをクリックした
後に実際に出現するURL入力UIの実DOM(`<textarea inputmode="text"
name="alt" placeholder="https://"></textarea>`と、隣接する`aria-label=
"URLの入力をやめる"`ボタン)を確認できた。これを受けて`_find_url_input_
textarea()`(`_find_active_link_toolbar_button()`と同じ、待機→`count()`
再確認の設計)を追加し、`_set_link_on_text_occurrence()`を「リンクボタン
クリック→URL入力欄の特定→URL入力→同じlocatorからのread-back確認」まで
進めた(詳細は「10. 商品リンク」)。read-back一致時は新設した
`UrlInputObservationStop`で意図的に安全停止し(`LinkButtonObservation
Stop`とはログ上で区別可能)、不一致時は通常の`NotePosterError`で安全
停止する。URLの確定方法(Enter・Tab・フォーカス解除・確定ボタン・他要素
クリックのいずれも)はまだ実装していない。この観測専用実装・第2段階は
ローカルpytestでの確認のみで、まだ実際のGitHub Actions実行では検証して
いない。

`TEST-004`(リンクボタンクリック失敗)で判明した事項: 上記の観測専用
実装・第2段階を実機で実行したところ、URL入力欄には到達せず、リンク
ボタン自体のクリックが「element is outside of the viewport」を繰り返し、
既定の30秒タイムアウトで失敗した。ボタン自体は`_find_active_link_
toolbar_button()`で一意に特定できていたため、原因はセレクタではなく、
設定済みのviewport高さ(800px)に対しツールバーの実測`top`値(847px/
871px)がこれを超えており、クリック対象が実際には現在のviewportに
描画されていなかったことだと判断した。これを受けて`_ensure_link_
button_in_viewport()`を新設し、クリック前に`scroll_into_view_if_
needed()`を試みたうえで`bounding_box()`を実測し、viewportに完全に
収まっていることを確認してからでなければクリックしない設計に変更した
(詳細は「10. 商品リンク」)。`force=True`やJavaScriptによる直接の
`element.click()`のような、Playwrightのactionability checkを迂回する
手段は採用せず、収まっていない場合は`LinkButtonOutOfViewportError`で
安全停止する。クリック自体のタイムアウトも既定の30秒から短い上限
(`_LINK_BUTTON_CLICK_TIMEOUT_MS`、既定5000ms)に変更した。viewportの
設定(1280x800)自体、および「エディタのガイド」パネルを閉じる操作は、
今回は変更していない(まずscroll_into_view_if_needed()とbounding_box()
検証だけで問題が解消するかを単独で確認するため)。この変更もローカル
pytestでの確認のみで、まだ実際のGitHub Actions実行では検証していない。

`TEST-004`(URL入力・適用ボタン到達)で判明した事項: クリック前のviewport
確認を追加した状態で実機実行したところ、リンクボタンのクリックが成功し、
`UrlInputObservationStop`まで正常に到達した(URLの入力・同じlocatorから
のread-back完全一致を確認)。追加の実機Artifactから、URL入力欄と同じ
フローティング編集ツールバー内に「適用」ボタン(`<button data-name=
"Button" type="button" id=":r16:"><span>適用</span></button>`)が存在
することを確認した。これを受けて`_find_url_apply_button()`
(URL入力欄と同じアクティブなツールバーをスコープにした`get_by_role
("button", name="適用", exact=True)`による特定)を追加し、「適用」ボタン
クリックまでの観測専用実装・第4段階を実装した(詳細は「10. 商品リンク」)。
`UrlInputObservationStop`はクラス自体を削除せず、「URL入力・read-back
までは確認できている」段階を明示する診断用の例外として残している。この
変更もローカルpytestでの確認のみで、まだ実際のGitHub Actions実行では
検証していない。

`TEST-004`(URL入力欄消失・同一commitでの再現性差異)で判明した事項:
「適用」ボタンクリックまでの観測専用実装・第4段階(commit`5e18de7`)を
実機で再実行したところ、`git log`で確認する限りコードを一切変更して
いないにもかかわらず、ある回はURL入力・read-back一致・「適用」ボタン
クリックまで成功した一方、別の回では「→ 商品を見る」の選択・リンク
ボタンのクリック・URL入力欄の出現・URLの入力までは成功回と同じように
進んだのに、read-backの直前でURL入力欄が消失し(`input_value()`の結果が
`None`相当になり)、失敗時のArtifactではURL入力欄のtextarea自体がDOMから
無くなっており、active toolbarも`data-active="false"`に戻っていた。
2回の実機実行の間でcommitは同一であるため、この差異はコードの回帰では
なく、note側の実行タイミングに依存する何らかの状態(非同期処理・
バリデーション・再レンダリング等)によるものと判断した。これを受けて、
URL入力欄の一意特定からread-backまでの区間に`_log_url_input_
diagnostics()`による複数時点(入力直前・クリック直後・文字入力の前後・
read-back直前・消失検知時・read-back成功後)の読み取り専用の状態記録を
追加し、`press_sequentially()`完了直後の`count()`再確認で消失を検知した
場合は専用の`UrlInputDisappearedObservationStop`で安全停止するように
した(詳細は「10. 商品リンク」)。`input_value()`自体の呼び出し中の例外は
この消失検知とは別の通常の`NotePosterError`として区別して報告する。
`press_sequentially()`自体や「適用」ボタン以降の処理は変更していない。
URL入力欄が正常であれば、これまで通り1回の呼び出しの中で「適用」ボタン
クリック(`UrlApplyObservationStop`)まで自動的に到達する。この変更も
ローカルpytestでの確認のみで、まだ実際のGitHub Actions実行では検証して
いない。

`TEST-004`(「適用」クリック後の完成DOM確認)で判明した事項: 上記の診断
強化後に実機で再実行したところ、URL入力欄の消失は再現せず、「→ 商品を
見る」の選択・リンクボタンのクリック・URL入力欄の出現・URL入力・
read-back一致・「適用」ボタンのクリックまで安定して成功し、意図的な
`UrlApplyObservationStop`まで到達した。`_capture_failure()`が保存した
HTMLを解析したところ、対象ブロック内に実際に`<a href="https://you-ichi.
jp/?pid=192116331" target="_blank" rel="noopener"><span class="highlight">
→ 商品を見る</span></a>`という`<a>`要素が生成されており、hrefは入力した
URLと完全一致、アンカーテキストは`<span class="highlight">`でラップ
されているものの「→ 商品を見る」と一致していた。フローティング編集
ツールバーはURL入力欄・「適用」ボタンが消え、通常の選択ツールバーへ
戻っていた。既存の`_assert_links_match()`(`inner_text()`でアンカーの
テキストを読み取る設計)は、この`<span>`でラップされた実DOMに対しても
コード変更なしでそのまま正しく判定できることを、実DOMを再現したテスト
で確認した。これを受けて、意図的な`UrlApplyObservationStop`による停止を
撤去し、`_wait_for_product_link_applied()`による`<a>`要素反映のlocator
待機→`_assert_links_match()`による検証→下書き保存→保存後read-back確認、
までを一連の流れとして実装した(詳細は「10. 商品リンク」)。この変更も
ローカルpytestでの確認のみで、まだ実際のGitHub Actions実行では検証して
いない。

`ARTICLE-001`は人間が確認するまで`status`を`ready`に戻さない
(対応済み・変更していない)。

## 13. Phase 2以降の検討事項(Phase 1のスコープ外)

以下はPhase 1では意図的に対応していません。着手する場合は、Phase 1の
安全要件(前掲「11.」)を変更しない形で、別途合意のうえで進めます。

- Craft連携: Craftアプリの「Connections」から発行されるAPI URL・認証情報が
  確定してから実装します。
- cronによる定期実行の本格運用: ワークフロー自体は`schedule`トリガーを
  備えていますが、実運用に入れるかどうかは今後判断します。
- AIによる記事生成: 現在はChatGPT等で人間が作成した記事をSheetsに手動登録
  する前提です。
- 有料記事(`content_type=paid`)対応: 現在は`free`のみ自動処理対象です。
- 自動公開・予約投稿・公開設定パネルの自動操作: 「11. Phase 1の安全要件」で
  禁止している通り、これらはPhase 1はもちろん、今後のPhaseで追加する場合も
  安全設計自体を変更する重大な決定であるため、その都度明示的な合意が必要です。
- 商品リンクのリンク設定UIセレクタの実機確定: 現時点ではbest-effortな候補
  セレクタのみで、実際のGitHub Actions実行での検証がまだ済んでいません
  (詳細は「10. 商品リンク」「12. Phase 1実機検証記録」)。
- 本文の段落内改行(`Shift+Return`)の自動化: note公式の「エディタの
  ガイド」には、通常の`Enter`(新しい段落)とは別に`Shift+Return`
  (同じ段落内の改行)が文書化されています。当初は`press_sequentially()`が
  本文中の`\n`をすべて`Enter`キー入力として扱うため、生成されるのは常に
  新しい段落だろうと考えていましたが、**`TEST-004`の実機DOMダンプで、この
  想定が誤りだったことが判明しました**。`build_product_links_trailer()`が
  生成する「{商品名}\n→ 商品を見る」という1つの`\n`は、実際には新しい
  `<p>`にはならず、同一の`<p>`要素内の`<br>`(段落内改行)として描画されて
  いました。つまりnote側のEnterキー処理は、単純に「`\n`=常に新しい段落」
  ではなく、状況によって`<br>`(段落内改行)になる場合があるということが
  実機で確認されたことになります(どのような条件で段落分割/段落内改行が
  切り替わるのかは未解明です)。`TEST-003`で確認できていた「本文と
  ハッシュタグの間の5行の空行(`_TAG_SEPARATOR`)が見た目通りに反映される」
  という事実自体は変わりませんが、その内部表現が本当に複数の`<p>`要素
  (空の段落)なのか、`<br>`の連続なのかは、`TEST-003`の実機DOMを再確認
  しない限り確定できません。ARTICLE-001のような「意図的に空行を多用する
  読みやすさ重視のフォーマット」についても、見た目上は問題が起きていない
  ものの、実際の内部表現は未確認のままです。より確実にnoteの段落モデルへ
  意図通りに対応させたい場合は、`Shift+Return`(段落内改行)と通常の
  `Enter`(段落分割)をPlaywright側で明示的に使い分けて送信する方式への
  変更が将来的な改善候補になります。これはPhase 1の要件
  ではないため今回は実装せず、拡張候補として記録するのみに留めます。

なお、noteのUI変更で本文editorやリンク設定UIのセレクタが今後壊れる可能性は
ゼロではありません。その場合は「5. 秘密情報を共有せずに動作確認する方法」の
手順で診断データを取得し、実機DOMに基づいて候補セレクタを更新します
(推測では追加しません)。
