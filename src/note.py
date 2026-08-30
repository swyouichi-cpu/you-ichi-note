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
(1文字ずつ実際のキー入力に近いイベントを発生させる)に変更した。

★本文入力欄の誤検出防止とread-back検証について(実機で発生した重大な
不具合への対処)★
実機テスト(GitHub Actions Content Pipeline #16)で、パイプライン自体は
"success"で終了したにもかかわらず、実際のnote下書きの本文が完全に空
(文字数カウンタ「0 文字」)になるという重大な不具合が発生した。ログを
精査した結果、_fill_body() の候補セレクタが本命(role=textbox name=本文、
class名にbody/editorを含むcontenteditable)含め全て一致せず、当時存在
していた「画面上に見えている最初のcontenteditable要素を無条件に使う」
という位置ベースの最終フォールバックが、本文editorではない別の要素
(タイトル入力欄である可能性が高い)を誤って掴み、そこに本文全体を
入力してしまっていたことが判明した。

さらに旧 _assert_body_registered() は「ページ全体に“0 文字”という文字列が
見えているか」という間接的なチェックだったため、本文editor自体には一度も
フォーカスが当たらず本来の文字数カウンタが「0 文字」という正確な文字列と
して描画されなかった結果、このチェックをすり抜けて「成功」と判定されて
しまった。ページ全体を対象にした文字列探索は、正しい要素に入力した上で
内部状態だけが更新されない場合(insert_textの旧不具合)には有効だが、
「そもそも間違った要素に入力した」場合には無力であることが分かった。

この教訓から、以下の2つの安全装置を追加した。「何かに入力できる」ことより
「間違った場所に入力しない」ことを優先する設計方針である。

  1. 位置ベースの無条件フォールバック(「最初の/2番目のcontenteditable」)
     を _fill_body() の候補から完全に削除した。本文editorであることに
     根拠のある候補(role=textbox name=本文、class名ベース)のみを試し、
     いずれも一致しなければ本文への入力を一切行わずNotePosterErrorで
     中断する(呼び出し側でneeds_reviewに倒れる)。
  2. _same_element() で、本文入力欄として解決した要素がタイトル入力欄
     (_fill_title()が解決した要素)と同一のDOM要素でないことを確認する。
     同一だった場合は誤検出とみなし、入力せずに中断する。
  3. _assert_body_matches() で、_fill_body()が実際に入力に使った
     locatorそのものから inner_text() を読み戻し、期待した本文
     (build_body_with_hashtags()の戻り値)と一致するかを確認する。
     note側のcontenteditableは改行の表現(\\n / <br> / 空div等)が
     実装により変わりうるため、比較前に空白文字を全て除去して正規化
     する(期待値・実際値の両方に同じ正規化を適用するため、改行表現の
     違いによる誤検知を避けつつ、実際の文字内容の差異は検出できる)。
     不一致の場合は下書き保存へ進まずNotePosterErrorで中断する。
     この検証は「下書き保存」ボタンを押す前と、押した後の両方で行う
     (保存によって内容が失われていないかも確認するため)。

read-back検証だけでは「間違った要素に入力してそのまま読み戻す」ケースは
検知できない(自分が書いた場所を自分で読み返すだけなので一致してしまう)。
そのため上記1・2の「そもそも正しい要素にしか入力しない」対策と、3の
「入力した内容が実際に保持されているかの確認」を併用することで、今回の
不具合の再発を防ぐ設計にしている。

★本文editorの実機DOM特定(Content Pipeline #18)★
上記の安全装置により「本文入力欄」でneeds_reviewとして安全停止できる
ことを実機で確認した後、ユーザーが失敗時のHTMLダンプ(Artifact)から
本文editorの実際のDOM構造を特定した。

  <div contenteditable="true" role="textbox" aria-multiline="true"
       class="ProseMirror note-common-styles__textnote-body"
       data-placeholder="たのしかった旅行について、書いてみませんか？">

タイトルは <textarea placeholder="記事タイトル"> という別要素であり、
本文editorとは構造的に明確に区別できる。この実機DOMに基づき、
_fill_body() の最優先候補として次の2つを追加した。

  1. class名ベース: div.ProseMirror.note-common-styles__textnote-body
     [contenteditable="true"]
     ("note-common-styles__" のような意味のある接頭辞を持つクラス名を
     使い、styled-componentsのハッシュ由来クラス "sc-xxxx" には依存
     しない。ビルドごとに変わりうるため)
  2. role/aria属性ベース: div.ProseMirror[contenteditable="true"]
     [role="textbox"][aria-multiline="true"]
     (class名がリネームされた場合の保険。data-placeholderの日本語
     全文には依存しない設計にしている)

これらが一致しなかった場合のフォールバックとして、従来のrole=textbox
name=本文・class名に body/editor を含むcontenteditable、という候補も
残しているが、位置ベースの無条件フォールバックは引き続き用意しない
(全滅時はneeds_reviewへ倒れる)。

★商品リンク(本文末尾のテキストリンク方式)について★
実機テストで、本文中にECサイトの生URLを置いたところ、noteのエディタが
URLを自動的に商品カード(画像・商品名・価格・説明・購入導線を含む大きな
埋め込み)へ変換してしまい、_assert_body_matches()のread-back検証が
(正しく)不一致を検出してneeds_reviewへ安全停止する事象が発生した。
この安全停止は正しい挙動であり、文字数差の許容や比較の緩和、read-back
の無効化では対応しない。

根本対策として、本文に生URLを一切含めない方式に変更した。人間が実機で
確認した結果、noteのProseMirrorエディタでは、本文中の任意の文字列を
選択すると選択範囲に応じたフローティングツールバーが表示され、その中の
リンク(鎖アイコン)からURLを設定すると、その文字列だけがインラインリンク
になり、商品カードへは変換されないことが確認された(2026年8月29日、人間の
手動確認)。この方式をPlaywrightで自動化する。

具体的には、Google Sheetsに新設した`product_links`列(JSON配列。
`[{"label": "商品名", "url": "https://..."}, ...]`)から、本文末尾に

  この記事に出てきた商品

  {label1}
  → 商品を見る

  {label2}
  → 商品を見る

というプレーンテキストの導線セクションを組み立てて本文に追記し
(build_product_links_trailer())、その後で「→ 商品を見る」という
固定文言(_PRODUCT_LINK_TEXT)だけに、商品名(label)を含むブロックとの
対応関係で一意に特定した対象へリンクを設定する(_apply_product_links())。
ECの生URLは本文の文字列としては一切登場せず、href属性としてのみ設定
される。`product_links`が空または`[]`の場合は導線セクション自体を
追加しない(タグ0件時の設計と同じ)。不正なJSON・必須フィールド欠落・
不正なURL形式は、タグの内部空白と同じ思想で自動修正せず
ProductLinkValidationError を送出してneeds_reviewへ倒す(ARTICLE-001に
限らず、どの記事にも同じロジックが適用される)。

★実機DOM(TEST-004)で判明した構造とリンク対象の特定方法★
当初は「label行」と「→ 商品を見る」行が別々の<p>要素になり、商品名の
直後にある兄弟要素として一意特定できる想定だった
(_resolve_link_target_for_label()、2026年8月29日時点の実装)。
しかし実機のGitHub Actions実行(TEST-004)で、build_product_links_
trailer()が生成する「{label}\\n{_PRODUCT_LINK_TEXT}」という1つの
テキストの塊は、noteのエディタでは別々の<p>要素にはならず、**同一の
<p>要素内で<br>を挟んで描画される**ことが判明した。

  <p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>

このため、label単体を含む要素も「→ 商品を見る」単体を含む要素も、
そもそも本文中に単独では存在せず、`exact=True`のテキスト完全一致検索は
常に0件になる。これが原因で、商品導線のプレーンテキスト生成自体は
成功していたにもかかわらず、リンク設定が「対象テキストが0件」として
安全停止していた(TEST-004で判明した不具合)。

これを受けて_find_product_link_block()に置き換えた(2026年8月29日)。
本文editor(body_locator、ProseMirrorの実DOM)にスコープを絞ったうえで、
  1. 商品名(label)を含むブロック要素(<p>)が本文editor内にちょうど
     1件だけ存在すること
  2. そのブロックのテキストを行単位に分解すると、ちょうど
     [label, _PRODUCT_LINK_TEXT] の2行になっていること
     (=商品名の行の直後に「→ 商品を見る」の行が続いている)
を確認したうえで、そのブロック要素(<p>)自体を返す。いずれかが成立
しない場合(labelを含むブロックが0件・複数件、ブロック内の行構成が
想定と異なる等)は推測せずneeds_reviewへ安全停止する。これは
_fill_body() で撤去した「画面上に見えるN番目のcontenteditable要素」の
ような構造推測とは性質が異なる。noteのDOM構造そのものを推測している
のではなく、**このコード自身が直前に生成した既知の構造(label行→
リンク対象行という順序)**を、本文editorという明確なスコープの中で
確認しているだけである。

ブロック要素が特定できても、そのブロックをまるごと選択してリンクを
設定するわけではない(それでは商品名までリンク範囲に含まれてしまう)。
_select_product_link_text_in_block() が、ブロックの直接の子ノードの
うち「→ 商品を見る」と完全一致するテキストノードだけをブラウザの
Selection/Range API(`document.createRange()` / `window.getSelection()`)
で明示的に選択する。ブロックが上記の2行構造であることは既に確認済み
だが、念のためここでも一致するテキストノードの件数を数え、ちょうど
1件でなければ推測で選択せず安全停止する。

★リンク設定操作の変遷: (1)推測ボタン→(2)ショートカット→(3)実機確認済み
ボタン(観測専用実装)★

(1) 当初はフローティングツールバーのリンクボタンをrole/aria属性(推測
ベースのbest-effort候補)で探してクリックする方式を実装していたが、
そのボタンがアイコンのみでアクセシブルな名前を持たない場合に候補が
一つも一致しないリスクがあった。

(2) その後、note公式の「エディタのガイド」にリンク挿入のショートカット
としてMac: ⌘+K が明記されていると人間が手動確認し(実機で「→ 商品を
見る」を選択してこのショートカットを使うと、URL入力欄が表示されて
インラインリンクを設定でき、商品カード化されないことも確認済み)、
DOM構造の推測に頼らない公式ショートカット方式(Control+K→Meta+Kの順に
試す)に変更した。しかし実機のGitHub Actions実行(TEST-004)では
Control+K・Meta+Kとも無反応だった。追加で取得した実機Artifact
(04/05/06のHTML・スクリーンショット・診断データ)で、noteの
「エディタのガイド」パネルを実際にパースした結果、**リンク挿入は
ツールバーの「ボタン」一覧には存在するが、「キーボードショートカット」
一覧には存在しない**ことが判明した(登録されているショートカットは
元に戻す/やり直し/下書き保存/太字/取り消し線/引用/本文/見出し/小見出し/
コード/中央寄せ/左寄せ/右寄せ/箇条書きリストのみで、リンクは無い)。
つまりControl+K/Meta+Kが無反応だったのは偶然の失敗ではなく、そもそも
note側にリンク挿入のキーボードショートカットが実装されていないためで
あり、ショートカット方式は撤回した(_open_link_input_via_shortcut()を
完全に削除し、フォールバックとしても残していない)。

(3) 同じ実機Artifactから、フローティング編集ツールバー自体の実DOM構造
が判明した。

  <div data-active="true" role="toolbar" id="desktop-toolbar" ...>
    ...
    <button tabindex="0" aria-label="リンク" aria-pressed="false" ...>
    ...
  </div>

`role="toolbar"`かつ`data-active="true"`の要素はページ内に常にちょうど
1件、その内部の`aria-label="リンク"`ボタンもちょうど1件であることを
04/05/06すべてのArtifactで確認した。`_find_active_link_toolbar_button()`
が、この2段階の一意性(`div[role="toolbar"][data-active="true"]`が
ちょうど1件→その内部の`button[aria-label="リンク"]`がちょうど1件)を
確認したうえでボタンを返す(style属性内のclass名、`sc-xxxx`のような
styled-components由来のハッシュには一切依存しない)。いずれかが1件で
なければ`.first()`/`.nth()`等の推測に頼らずNotePosterErrorで安全停止
する。

(4) 上記(3)の実装を実際にGitHub Actionsで実行したところ、実行ログ上は
「ツールバーが0件」として安全停止していたが、その直後に`_capture_failure()`
が保存したHTMLダンプには実際にはツールバー(とリンクボタン)が存在して
いた。これはセレクタの誤りではなく、「→ 商品を見る」のテキスト選択が
完了してからnoteが実際にツールバーをDOMへマウントし`data-active="true"`
にするまでの短い非同期の遅延を考慮していなかったことが原因と判断した。
そこで`_find_active_link_toolbar_button()`に`_wait_for_locator_to_appear()`
(固定`time.sleep()`は使わず、Playwrightの`Locator.wait_for(state=
"visible")`による自動待機を使う)を組み込み、ツールバー・リンクボタンの
それぞれについて、上限`_LINK_TOOLBAR_APPEAR_TIMEOUT_MS`(既定3000ms)だけ
出現を待ってから、改めて`count()`で一意性を確認する設計に変更した。出現
を待っても0件のまま、または出現後に複数件になった場合は、これまで通り
`needs_review`へ安全停止する。セレクタ自体(`role`/`data-active`/
`aria-label`)は変更していない。

(5) 上記(4)の初回実装では`_wait_for_locator_to_appear()`が内部で
`locator.first.wait_for(...)`という形で`.first`を使っていた。これは
「複数件でも待機自体はできるように」という意図だったが、位置ベースの
要素選択(`.first`/`.nth()`)を一切使わないという安全要件に反していると
指摘を受け、`locator.first`ではなく**locatorそのもの**に対して
`wait_for(state="visible")`を呼ぶ形に修正した。Playwrightのlocatorは
「1件に一致する前提の操作」(`wait_for`を含む)を、実際に2件以上へ
一致した場合はstrict mode違反として即座に例外(`playwright.sync_api.
Error`。`TimeoutError`もこのサブクラス)を送出する。この例外も
「一意に特定できない」ケースとして扱い、位置ベースで1件を選んで先に
進むことはしない(`_wait_for_locator_to_appear()`はこの場合Falseを返し、
呼び出し側が改めて`count()`で具体的な件数を確認してから安全停止する)。

★観測専用実装であることについて・第1段階(2026年8月29日時点)★
当初、ボタンをクリックした後に実際にどのようなURL入力UI(ポップオーバー
かモーダルか、`input`要素か`contenteditable`か等)が出現するかは、まだ
一度も実機で観測できていなかった。この状態でURL入力欄のセレクタを推測
実装すると、ショートカット方式のときと同じ「未確認のDOM構造を前提に
した実装」を繰り返すことになる。そのため`_set_link_on_text_occurrence()`
は当時、意図的に、リンクボタンをクリックした直後にHTML/スクリーン
ショット/診断データを保存したうえで、`LinkButtonObservationStop`
(NotePosterErrorのサブクラス。通常の「リンク設定に失敗した」という
意味の例外とログ上で明確に区別するための専用クラス)を送出して処理を
止める設計にしていた。

★観測専用実装であることについて・第2段階(2026年8月29日時点)★
その後、追加の実機Artifactから、リンクボタンをクリックした後に

  <textarea inputmode="text" name="alt" placeholder="https://"></textarea>

というURL入力欄が出現することが判明した(`_find_url_input_textarea`、
`_URL_INPUT_SELECTOR`)。これを受けて、URL入力欄を一意に特定できた場合
のみ`product_links`の対象URLを入力し、入力に使った同じlocatorから
`input_value()`でread-backして期待したURLと完全一致することを確認する
段階まで実装を進めた。read-backが一致すればHTML/スクリーンショット/
診断データを保存したうえで`UrlInputObservationStop`(`LinkButtonObserv
ationStop`とはログ上で区別できる別のサブクラス)を送出して処理を止め、
不一致であれば(可能な限り診断データを保存したうえで)通常のNotePoster
Errorで安全停止する。ただし**URLの確定方法**(Enterキー送信・Tabキー
送信・意図的なフォーカス解除・確定ボタンのクリック・他要素のクリックの
いずれも)はまだ実機で確認できていないため、一切行わない。次回の実機
テストでこの観測データを取得したのち、URLの確定方法を本実装する想定
である。呼び出し側(main.py)では他のNotePosterErrorと同様にneeds_
reviewへ倒れ、下書き保存やSheetsのdraft_created化は一切行われない。

なお、この第2段階を実機で実行した際にリンクボタン自体のクリックが
viewport外で失敗する事象が発生し、クリック前にviewportを確認する仕組み
(`_ensure_link_button_in_viewport()`、`LinkButtonOutOfViewportError`)を
追加した(詳細は`_set_link_on_text_occurrence()`のdocstring内の「第3
段階」を参照)。

★観測専用実装であることについて・第4段階(2026年8月29日時点)★
上記の第2・第3段階を実機で実行したところ、URL入力・read-back一致まで
成功した。追加の実機Artifactから、URL入力欄と同じフローティング編集
ツールバー内に「適用」ボタンが存在することが判明した
(`_find_url_apply_button`)。「適用」ボタンの`id`(`:r16:`のような値)は
Reactの`useId`等が生成する動的IDの可能性が高いためセレクタには使わず、
ページ全体からの「適用」文字列検索でもなく、URL入力欄の特定に使ったのと
同じアクティブなツールバーをスコープとして`get_by_role("button",
name="適用", exact=True)`で一意に特定する。URL入力欄がそのツールバー内に
ちょうど1件存在することも再確認し(URL入力欄と「適用」ボタンが同じ
ツールバー内にあることを、DOM構造の推測やXPathでの祖先指定を使わずに、
スコープそのもので保証する)、viewport確認(`_ensure_link_button_in_
viewport()`の再利用)・`_assert_not_publish_action()`を経てからクリック
する。クリック直後にHTML/スクリーンショット/診断データを保存したうえで
`UrlApplyObservationStop`(`UrlInputObservationStop`とはログ上で区別
できる別のサブクラス)を送出して処理を止める。`<a>`要素が実際に生成
されたか、`href`が期待通りか、URL入力UIが消えるか等はまだ実機で確認
できていないため、`_assert_links_match()`や下書き保存へはまだ進まない。
`UrlInputObservationStop`自体はクラスとして削除せず、「URL入力・
read-backまでは確認できている」段階を明示する診断用の例外として残して
いる。

★本文テキスト検証とリンク検証の分離★
_assert_body_matches()は引き続き「見えているテキスト」だけを検証する
(商品リンク導入後も、本文には生URLが一切含まれないため、この検証で
商品カード化が起きていないことも同時に確認できる。カード化が万一発生
すれば、カードの追加テキストによって文字数が期待値からずれ、この検証が
不一致として検出する)。これとは別に、_assert_links_match()が本文editor
(body_locator)内の商品導線部分だけを対象に、_find_product_link_block()
で商品ごとのブロックを再特定したうえで、そのブロック内のリンク(<a>要素)
のhref・アンカーテキストを個別に検証する。本文中に将来ふつうの参考リンク
等が入る可能性があるため、本文editor内の<a>要素の総数を数える検証は行わ
ない(商品導線として自分自身が生成したブロックだけをスコープに検証する)。

ブロック内の<a>要素がちょうど1件で、かつそのテキストが「→ 商品を見る」
と完全一致することを確認する。この1つのチェックだけで、リンクが
設定されていない場合(<a>要素0件)・余計なリンクが付いた場合(<a>要素
2件以上)・商品名までリンク範囲に含まれてしまった場合(<a>要素のテキスト
が「label\n→ 商品を見る」のように商品名を含んでしまい完全一致しない)の
いずれも検出できる。どちらか一方でも失敗すれば成功扱いにせず、下書き
保存の前後両方でこの2つの検証を行う。

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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
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

# 「→ 商品を見る」選択直後からnoteのフローティング編集ツールバーが実際に
# DOMへ出現しvisibleになるまでの非同期の遅延を吸収するための待機上限
# (TEST-004で判明: 選択直後は0件でも、実際には短時間後にツールバーが
# 存在していた)。固定sleep()は使わず、Playwrightの自動待機と組み合わせて
# この時間内だけ出現を待つ。時間内に出現しなければ推測せずneeds_reviewへ
# 安全停止する。
_LINK_TOOLBAR_APPEAR_TIMEOUT_MS = 3000

# リンクボタンをクリックしてからURL入力欄(textarea)が実際にDOMへ出現する
# までの非同期の遅延を吸収するための待機上限。ツールバーの出現待ちとは
# 別の待機ポイントのため、値は同じでも独立した定数にしている。
_URL_INPUT_APPEAR_TIMEOUT_MS = 3000

# リンクボタンクリック後に現れるURL入力欄のセレクタ(TEST-004の追加観測で
# 実機確認済み: <textarea inputmode="text" name="alt" placeholder="https://">)。
# class名には依存せず、意味のある3つの属性をすべて満たすものだけを対象に
# する。
_URL_INPUT_SELECTOR = 'textarea[placeholder="https://"][inputmode="text"][name="alt"]'

# noteのフローティング編集ツールバーのセレクタ(_find_active_link_toolbar_
# button()とクリック前のviewport確認診断の両方で使うため定数化している)。
_ACTIVE_TOOLBAR_SELECTOR = 'div[role="toolbar"][data-active="true"]'

# リンクボタンをクリックする際のタイムアウト上限。TEST-004の実機実行で、
# ボタン自体は一意に特定できていたにもかかわらずclick()がPlaywright既定の
# 30秒タイムアウトいっぱいまで「element is outside of the viewport」の
# 再試行を繰り返して失敗する事象が発生した。ボタンがviewport内に収まって
# いることをクリック前に確認する設計に変更したため、この確認を通過した
# 状態でclick()が長時間ブロックする状況は本来起きないはずであり、既定の
# 30秒よりも十分短い上限に絞ることで、想定外の場合でも早期にneeds_review
# へ倒す。
_LINK_BUTTON_CLICK_TIMEOUT_MS = 5000

# 「適用」ボタンをクリックしてから、実際に<a>要素が対象ブロック内へ
# 反映されるまでの非同期の遅延を吸収するための待機上限(2026年8月29日、
# TEST-004で「適用」クリック後に<a>要素が生成されることを実機確認した後に
# 追加)。固定sleep()は使わず、Playwrightのlocator待機と組み合わせる。
_PRODUCT_LINK_APPLY_TIMEOUT_MS = 3000

# 診断データが際限なく増えないよう、記録件数の上限を設ける。
_MAX_DIAG_ENTRIES = 300

# クリック対象の要素自身、またはその祖先のいずれかがCSSの`position: fixed`
# かどうかを読み取り専用で調べるためのJS(2026年8月29日、ARTICLE-001の
# 実機実行でscroll_into_view_if_needed()後もbounding_box().yがほぼ変化
# しない事象が発生したことを踏まえて追加)。`position: fixed`の要素は
# ページをスクロールしても画面上の位置が変化しないため、
# scroll_into_view_if_needed()では表示範囲内へ移動できない。クリックや
# フォーカスの変更は一切行わない(getComputedStyle()による読み取りのみ)。
_FIXED_ANCESTOR_PROBE_JS = """
(el) => {
    let node = el;
    while (node && node !== document.documentElement) {
        const style = window.getComputedStyle(node);
        if (style.position === 'fixed') {
            return {
                fixed: true,
                tagName: node.tagName,
                className: node.className || null,
            };
        }
        node = node.parentElement;
    }
    return { fixed: false };
}
"""


def _strip_query(url: str) -> str:
    """URLからクエリ文字列を除去する(トークン等が紛れ込む可能性への念のための対策)。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _normalize_whitespace(text: str) -> str:
    """本文read-back比較用に、全ての空白文字(改行含む)を取り除く。

    noteのcontenteditableは改行の表現(\\n / <br> / 空div等)が実装により
    変わりうるため、比較前に期待値・実際値の両方へ同じ正規化を適用する。
    これにより改行表現の違いでは不一致にならず、実際の文字内容(空白以外)
    の差異だけを検出できる。
    """
    return re.sub(r"\s+", "", text)


def _bounding_box_within_viewport(box: dict, viewport: dict) -> bool:
    """`bounding_box()`の矩形が、`viewport_size`の範囲に完全に収まって
    いるかを判定する。

    `_ensure_link_button_in_viewport()`から使う純粋関数として切り出し、
    座標計算そのものの正しさを実際のブラウザ描画に依存せずテストできる
    ようにしている。境界(矩形の右端・下端がviewportの右端・下端と
    ちょうど一致する場合)は「収まっている」とみなす。
    """
    return (
        box["x"] >= 0
        and box["y"] >= 0
        and (box["x"] + box["width"]) <= viewport["width"]
        and (box["y"] + box["height"]) <= viewport["height"]
    )


class NotePosterError(RuntimeError):
    """note操作中の失敗。呼び出し側でneeds_review/errorに振り分けるための例外。"""


class TagValidationError(NotePosterError):
    """タグの形式があいまいで安全に正規化できない場合に送出する。

    呼び出し側(main.py)ではNotePosterErrorのサブクラスとして
    needs_reviewに振り分けられる。あいまいな入力を推測で「直す」ことは
    絶対にせず、必ず人間の確認に回す。
    """


class ProductLinkValidationError(NotePosterError):
    """product_links列の形式が不正で安全に解釈できない場合に送出する。

    TagValidationErrorと同じ思想: 不正なJSON・必須フィールド欠落・
    不正なURL形式などを推測で「直す」ことは絶対にせず、
    呼び出し側(main.py)でneeds_reviewに振り分けられる。
    """


class LinkButtonObservationStop(NotePosterError):
    """商品導線リンクのツールバーボタンをクリックした直後、意図的に
    処理を安全停止する際に送出する(観測専用実装、2026年8月29日)。

    ボタンクリック後に出現するURL入力UIのDOM構造がまだ実機で確認できて
    いないため、URL入力・確定操作へは進まず、HTML/スクリーンショット/
    診断データを保存したうえでここで停止する。これは「リンク設定に失敗
    した」という通常のNotePosterErrorとは意味が異なり、次の実装のための
    意図的な観測停止であることをログ・診断データ・呼び出し側の分岐
    (呼び出し側ではNotePosterErrorのサブクラスとして他と同様に
    needs_reviewへ倒れる)から明確に区別できるよう、専用のサブクラスに
    している。
    """


class UrlInputObservationStop(NotePosterError):
    """商品導線リンクのURL入力欄にURLを入力し、read-backで一致を確認した
    直後の状態を表す例外(観測専用実装・第2段階、2026年8月29日)。

    ★2026年8月29日時点の位置づけ★: 当初は、URL入力・read-back確認の
    直後にこの例外を送出して処理を停止していた(観測専用実装・第2段階)。
    その後、「適用」ボタンのクリックまで実装を進めた(第3段階、
    `UrlApplyObservationStop`)ため、通常の成功パスの終端としてはこの
    例外はもう送出されない。それでも、「URL入力・read-backまでは確認
    できている」という段階を明示できる診断用の例外として、クラス自体は
    削除せず残している(将来、「適用」ボタンの特定・クリックに失敗する
    ケースの切り分けに再利用する可能性があるため)。

    URLの確定方法(Enterキー送信・確定ボタンのクリック等)はまだ実機で
    確認できていない段階を表すためのものであり、確定操作へは進まず、
    HTML/スクリーンショット/診断データを保存したうえで停止する用途で
    設計されている。`LinkButtonObservationStop`(リンクボタンをクリック
    した直後の観測停止)とはログ上で明確に区別できるよう、別の専用
    サブクラスにしている。read-backが不一致だった場合はこの例外では
    なく、通常のNotePosterErrorを送出する(観測が成功した上での意図的な
    停止ではなく、実際に問題が起きたことを示すため)。
    """


class UrlApplyObservationStop(NotePosterError):
    """商品導線リンクのURL入力欄に対して「適用」ボタンをクリックした
    直後に、意図的に処理を安全停止する際に送出していた例外
    (観測専用実装・第4段階、2026年8月29日)。

    ★2026年8月29日時点の位置づけ★: 当初は、「適用」ボタンをクリックした
    後に実際に「→ 商品を見る」が`<a>`要素になるか、`href`が期待したURLと
    一致するか等がまだ実機で確認できていなかったため、この例外を送出して
    意図的に処理を停止していた(観測専用実装・第4段階)。その後、実機
    Artifactの解析で「適用」クリック後に実際に`<a>`要素が正しく生成される
    ことを確認できたため、`_wait_for_product_link_applied()`による
    locator待機と`_assert_links_match()`による検証を経て下書き保存まで
    進む完成実装に置き換えた(第6段階)。通常の処理経路ではこの例外は
    もう送出されないが、「リンクボタン→URL入力→read-back→『適用』
    クリック」までは確認できているという段階を明示できる診断用の例外
    として、クラス自体は削除せず残している。`LinkButtonObservationStop`・
    `UrlInputObservationStop`とはログ上で明確に区別できるよう、別の
    専用サブクラスにしている。
    """


class UrlInputDisappearedObservationStop(NotePosterError):
    """URLをtextareaへ入力した直後、同じURL入力欄セレクタで`count()`を
    再取得したところ、期待した1件ではなかった(典型的には0件=消失)場合に
    送出する(2026年8月29日)。

    実機のGitHub Actions実行(TEST-004)で、ある回では「→ 商品を見る」の
    選択・リンクボタンのクリック・URL入力欄の出現・URL入力までは前回と
    同じように進んだにもかかわらず、read-backの直前でURL入力欄が消失し
    (`input_value()`の結果が`None`相当になり)、失敗時のArtifactでは
    URL入力欄のtextarea自体がDOMから無くなっており、active toolbarも
    `data-active="false"`に戻っていた、という事象が発生した。

    これを受けて、`press_sequentially()`でURLを入力した直後に、
    read-backを試みる前にまず同じセレクタで`count()`を再確認するように
    した。1件でなければ「read-backした値がNoneだった」という曖昧な扱いは
    せず、この専用例外を送出して安全停止する(呼び出し側でneeds_review
    に倒れる)。`input_value()`自体の呼び出し中に例外が発生した場合
    (count()の再確認では1件だったにもかかわらず、その直後に消失した
    ケース)は、この例外ではなく通常のNotePosterErrorを送出して区別する
    (`_set_link_on_text_occurrence()`のdocstringを参照)。
    """


class LinkButtonOutOfViewportError(NotePosterError):
    """リンクボタンをクリックする前に、現在のviewport(表示領域)内に
    収まっていることを確認できなかった場合に送出する(2026年8月29日)。

    実機のGitHub Actions実行(TEST-004)で、リンクボタン自体は一意に特定
    できていたにもかかわらず、Playwrightの`click()`が「element is
    outside of the viewport」を繰り返し、既定の30秒タイムアウトいっぱい
    まで失敗し続ける事象が発生した。設定済みのviewport高さ(800px)に対し、
    ツールバーの実測`top`値(847px/871px)がこれを超えていたことから、
    クリック対象が実際には現在のviewportに描画されていなかった可能性が
    高いと判断した。

    そのため、クリックする前に`scroll_into_view_if_needed()`を試みたうえ
    で改めて`bounding_box()`を取得し、viewportの範囲に完全に収まっている
    ことを確認するようにした。収まっていない場合は、`force=True`や
    JavaScriptによる直接の`element.click()`のような、Playwrightの
    actionability check(表示中か・安定しているか・viewport内かなど)を
    迂回する手段には頼らず、推測でクリックせずにこの例外を送出して
    needs_reviewへ安全停止する。通常のNotePosterErrorではなく専用の
    サブクラスにしているのは、「リンクボタンがviewport外にあったために
    クリックしなかった」ことをログから明確に区別できるようにするため。
    """


@dataclass(frozen=True)
class ProductLink:
    """本文末尾の商品導線1件分(表示する商品名と、リンク先URL)。"""

    label: str
    url: str


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


def build_body_with_hashtags(
    body: str,
    tags: list[str],
    product_links: list[ProductLink] | None = None,
) -> str:
    """本文の末尾に、5行分の改行を挟んで商品導線・ハッシュタグ行を追加する。

    noteの現在のエディタでは、公開設定パネルでの一時的なタグ入力は
    「キャンセル」を押すと(note公式の仕様として)破棄される。
    note公式ヘルプが案内する「本文中に #タグ名 と直接書く」方式に
    統一する。

    product_links が指定されている場合、タグ行の手前に商品導線セクション
    (build_product_links_trailer()の結果)を追加する。商品導線・タグの
    いずれも無い場合は区切り文字列を一切追加せず、本文をそのまま返す
    (Google Sheetsのbody/tags/product_links列自体はこの関数の呼び出し
    前後で変更しない。あくまでnoteへ入力する直前に組み立てるだけ)。

    ECの生URLはこの関数の戻り値のどこにも文字列として含まれない
    (URLはhref属性としてのみ、_apply_product_links()が別途設定する)。
    """
    result = body
    if product_links:
        result = f"{result}{_TAG_SEPARATOR}{build_product_links_trailer(product_links)}"
    if tags:
        hashtag_line = " ".join(f"#{tag}" for tag in tags)
        result = f"{result}{_TAG_SEPARATOR}{hashtag_line}"
    return result


# 商品導線で使う固定文言。リンクを設定する対象はこのテキストだけであり、
# 商品名(label)はリンクしない通常テキストのまま表示する。
_PRODUCT_LINK_TEXT = "→ 商品を見る"
_PRODUCT_LINKS_HEADING = "この記事に出てきた商品"


def build_product_links_trailer(product_links: list[ProductLink]) -> str:
    """商品導線セクションのプレーンテキストを組み立てる(リンクはまだ無い)。

    「この記事に出てきた商品」という見出しの下に、商品ごとに
    「{label}\\n→ 商品を見る」を5行改行区切りで並べる。ECの生URLは
    ここには一切登場しない(URLはこの後、_apply_product_links()が
    「→ 商品を見る」というテキストだけにhrefとして設定する)。

    見出し文言は特定の商品カテゴリ(例:ジャム)に依存しない汎用的な
    ものにしている。ARTICLE-001のような特定記事向けのハードコードは
    行わない。
    """
    entries = [f"{link.label}\n{_PRODUCT_LINK_TEXT}" for link in product_links]
    return _PRODUCT_LINKS_HEADING + _TAG_SEPARATOR + _TAG_SEPARATOR.join(entries)


def parse_product_links(raw: str) -> list[ProductLink]:
    """Google Sheetsのproduct_links列(JSON配列の文字列)を解釈する。

    許可する形式: `[{"label": "商品名", "url": "https://..."}, ...]`。
    空文字列または `[]` は「商品導線なし」として空リストを返す
    (build_body_with_hashtags()は導線セクションを追加しない)。

    以下はいずれもTagValidationErrorと同じ思想で、推測で「直す」ことは
    絶対にせず、ProductLinkValidationErrorを送出して呼び出し側で
    needs_reviewに倒す。
      - JSONとして解釈できない
      - トップレベルが配列でない、または要素がオブジェクトでない
      - label / url のいずれかが欠落、空、または文字列でない
      - urlがhttp(s)の絶対URLとして解釈できない(スキームやホストが無い)
    label/urlの前後の空白のみ除去する(タグ正規化と同じく、内部の空白は
    書き換えない)。
    """
    stripped = raw.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ProductLinkValidationError(
            f"product_links列がJSONとして読み取れません: {raw!r}。"
            "安全のため自動では修正せず処理を中断します。"
        ) from exc

    if not isinstance(parsed, list):
        raise ProductLinkValidationError(
            f"product_links列はJSON配列である必要がありますが、"
            f"{type(parsed).__name__} でした: {raw!r}。"
        )

    links: list[ProductLink] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ProductLinkValidationError(
                f"product_links[{index}] がオブジェクトではありません: {item!r}。"
            )
        label = item.get("label")
        url = item.get("url")
        if not isinstance(label, str) or not label.strip():
            raise ProductLinkValidationError(
                f"product_links[{index}] の label が空、または文字列では"
                f"ありません: {item!r}。"
            )
        if not isinstance(url, str) or not url.strip():
            raise ProductLinkValidationError(
                f"product_links[{index}] の url が空、または文字列では"
                f"ありません: {item!r}。"
            )
        url_stripped = url.strip()
        parsed_url = urlsplit(url_stripped)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise ProductLinkValidationError(
                f"product_links[{index}] の url がhttp(s)の絶対URLとして"
                f"解釈できません: {url!r}。安全のため自動では補完せず"
                "処理を中断します。"
            )
        links.append(ProductLink(label=label.strip(), url=url_stripped))

    return links


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

    def _run_step(self, page: Page, step_name: str, action):
        """1ステップを実行し、どこで失敗しても診断データを残してから
        NotePosterError として送出し直す(呼び出し側での原因特定を助けるため)。
        action の戻り値をそのまま返す(タイトル/本文のlocatorを後続の
        ステップへ引き渡すために使う)。
        """
        try:
            return action()
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

        商品リンク(product_links)が指定されている場合、タグ行の手前に
        「この記事に出てきた商品」という商品導線セクションを追記する。
        ECの生URLは本文の文字列としては一切登場させず(自動カード化を
        誘発するため)、「→ 商品を見る」という固定文言だけに、noteの
        フローティング編集ツールバーの「リンク」ボタン経由でインライン
        リンクを設定しようとする(_apply_product_links)。商品名と「→ 商品を
        見る」は実機DOM上では同一のブロック要素内に<br>を挟んで存在する
        ため、本文editor(body_locator)にスコープを絞ったブロック単位の
        特定(_find_product_link_block)を経由してリンク対象を選択する。

        ★商品リンク設定は完成実装(2026年8月29日、TEST-004で「適用」
        クリック後に<a>要素が実際に生成されることを確認したことを受けて
        完成)★
        _apply_product_links は各商品ごとに、対象ブロックの特定→
        「→ 商品を見る」の選択→リンクボタンのクリック→URL入力欄への
        入力→read-back確認→「適用」ボタンのクリック→<a>要素が実際に
        反映されるまでの待機、までを行う(_set_link_on_text_occurrence)。
        いずれかの段階で一意に特定できない・read-backが不一致・反映が
        確認できない等の場合は、推測せずNotePosterErrorを送出して安全
        停止する(呼び出し側でneeds_reviewに倒れる)。1件でも失敗すれば
        後続の商品の処理には進まず、下書き保存へも進まない。

        本文入力欄は、タイトル入力欄と同一のDOM要素を誤って掴んでいない
        ことを確認したうえで使用し(_same_element)、実際に入力に使った
        locatorから読み戻した内容が期待した本文と一致することを確認する
        (_assert_body_matches)。商品リンクの設定内容も、本文editor内の
        <a>要素を個別に検証する(_assert_links_match)。この2つの検証は
        「下書き保存」を押す前と押した後の両方で行う。いずれかに失敗した
        場合は下書き保存を行わない、または最終的な成功とはみなさずに
        NotePosterErrorを送出する。
        """
        tags = normalize_tags(article.tag_list())
        product_links = parse_product_links(article.product_links)
        composed_body = build_body_with_hashtags(article.body, tags, product_links)
        hashtag_line = " ".join(f"#{tag}" for tag in tags) if tags else ""

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
        title_locator = self._run_step(
            page, "タイトル入力", lambda: self._fill_title(page, article.title)
        )
        self._screenshot(page, "02_title_filled")

        logger.info("本文入力欄を特定して入力(末尾に商品導線・タグを追記済み)")
        body_locator = self._run_step(
            page,
            "本文入力",
            lambda: self._fill_body(page, composed_body, title_locator=title_locator),
        )
        self._screenshot(page, "03_body_filled")

        if product_links:
            logger.info("商品導線に本文末尾の商品導線を設定(%d件)", len(product_links))
            self._run_step(
                page,
                "商品導線リンク設定",
                lambda: self._apply_product_links(page, body_locator, product_links),
            )
            self._screenshot(page, "04_product_links_applied")

        logger.info("本文・商品導線の読み戻しで内容を確認(下書き保存前)")
        self._run_step(
            page,
            "本文read-back確認(保存前)",
            lambda: self._assert_body_matches(
                page, body_locator, composed_body, hashtag_line, stage="保存前"
            ),
        )
        self._run_step(
            page,
            "商品導線リンク確認(保存前)",
            lambda: self._assert_links_match(
                page, body_locator, product_links, stage="保存前"
            ),
        )

        logger.info("自動保存の完了を確認")
        self._run_step(page, "自動保存完了待ち", lambda: self._wait_for_autosave_idle(page))

        logger.info("下書き保存")
        self._run_step(page, "下書き保存", lambda: self._save_draft(page))
        self._screenshot(page, "05_saved_draft")

        logger.info("保存完了(自動保存表示の解消)を確認")
        self._run_step(page, "保存完了確認", lambda: self._wait_for_autosave_idle(page))

        logger.info("本文・商品導線の読み戻しで内容を再確認(下書き保存後)")
        self._run_step(
            page,
            "本文read-back確認(保存後)",
            lambda: self._assert_body_matches(
                page, body_locator, composed_body, hashtag_line, stage="保存後"
            ),
        )
        self._run_step(
            page,
            "商品導線リンク確認(保存後)",
            lambda: self._assert_links_match(
                page, body_locator, product_links, stage="保存後"
            ),
        )

        note_url = page.url
        page.close()
        logger.info("note側の全確認が完了 id=%s note_url=%s", article.id, note_url)
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

    def _same_element(self, page: Page, a: Locator, b: Locator) -> bool:
        """2つのLocatorが画面上の同一のDOM要素を指しているかを確認する。

        本文入力欄としてタイトル入力欄と同じ要素を誤って掴んでいないかを
        確認するために使う(実機で、本文editorの候補セレクタが全滅し、
        位置ベースの無条件フォールバックがタイトル欄らしき要素を誤って
        掴んで本文全体を書き込んでしまった不具合への対処)。要素が取得
        できない場合は「別要素」として扱う(入力を止める側に倒さない
        ための安全側の判断ではなく、単に判定不能なため)。
        """
        try:
            handle_a = a.element_handle()
            handle_b = b.element_handle()
        except PlaywrightTimeoutError:
            return False
        if handle_a is None or handle_b is None:
            return False
        return bool(page.evaluate("([x, y]) => x === y", [handle_a, handle_b]))

    # -- 各ステップの実装(note.comのUI変更に備えて複数候補を用意) -------------

    def _fill_title(self, page: Page, title: str) -> Locator:
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
        return locator

    def _fill_body(self, page: Page, body: str, *, title_locator: Locator) -> Locator:
        """本文入力欄を特定して入力する。

        実機テスト(GitHub Actions Content Pipeline #16)で、当時の候補
        (role=textbox name=本文、class名にbody/editorを含むcontenteditable)
        がいずれも一致せず、当時存在していた「画面上に見えている最初の
        contenteditableを無条件に使う」という位置ベースの最終フォールバック
        が、本文editorではない別の要素(タイトル入力欄)を誤って掴み、
        そこに本文全体を書き込んでしまう不具合が発生した。

        その後(Content Pipeline #18)、実機のHTMLダンプから本文editorの
        実際のDOM構造が判明した:
          <div contenteditable="true" role="textbox" aria-multiline="true"
               class="ProseMirror note-common-styles__textnote-body"
               data-placeholder="...">
        タイトルは別要素( <textarea placeholder="記事タイトル"> )であり、
        本文とは構造的に明確に区別できる。noteのエディタはProseMirörを
        採用しているため、class名は "note-common-styles__" のような
        意味のある接頭辞を持つもの(styled-componentsのハッシュ由来クラス
        "sc-xxxx" ではない)を優先的に使う。data-placeholderの日本語
        全文には依存しない(文言変更に弱いため、属性の有無や他の属性との
        組み合わせに留める)。

        この実機DOMに基づく候補を最優先にしつつ、note側のUI変更に備えて
        role/aria属性ベースの候補も用意する。それでも本文editorだと
        確証できない位置ベースのフォールバックは一切用意しない。候補が
        全滅した場合は入力を行わずNotePosterErrorで中断する(呼び出し側
        でneeds_reviewに倒れる)。

        候補が一致した場合も、念のためタイトル入力欄(title_locator)と
        同一のDOM要素でないことを_same_element()で確認し、同一だった
        場合も入力せずに中断する。
        """
        candidates = [
            (
                "css div.ProseMirror.note-common-styles__textnote-body",
                page.locator(
                    'div.ProseMirror.note-common-styles__textnote-body'
                    '[contenteditable="true"]'
                ),
            ),
            (
                "css div.ProseMirror[role=textbox][aria-multiline=true]",
                page.locator(
                    'div.ProseMirror[contenteditable="true"]'
                    '[role="textbox"][aria-multiline="true"]'
                ),
            ),
            ("role=textbox name=本文", page.get_by_role("textbox", name=re.compile("本文"))),
            (
                "css [class*=body] 系のcontenteditable",
                page.locator(
                    '[class*="body" i] [contenteditable="true"], '
                    '[class*="Body" i] [contenteditable="true"], '
                    '[class*="editor" i] [contenteditable="true"]'
                ),
            ),
        ]
        locator = self._resolve_locator(page, candidates, step_name="本文入力欄")
        if self._same_element(page, locator, title_locator):
            self._capture_failure(page, "本文入力欄誤検出")
            raise NotePosterError(
                "本文入力欄として検出した要素が、タイトル入力欄と同一のDOM要素"
                "でした。本文editorを正しく特定できていない可能性が高いため、"
                "誤った要素への入力を避けて処理を中断します。"
            )
        self._set_multiline_text(page, locator, body)
        return locator

    def _assert_body_matches(
        self,
        page: Page,
        locator: Locator,
        expected_body: str,
        hashtag_line: str,
        *,
        stage: str,
    ) -> None:
        """本文入力欄からその場で読み戻し、期待した本文と一致するかを確認する。

        ページ全体から特定の文字列(旧実装の「0 文字」探索)を探すのではなく、
        実際に入力した本文入力欄そのもの(locator)の中身を読み、期待値と
        比較する。「本文editorではない別要素に入力してしまう」ケースは
        自分が書いた場所を自分で読み返すだけなので原理的に検知できない
        (それは_fill_body側の_same_element等で防ぐ役割)。ここで検知したい
        のは「正しい要素に入力したのに、内部状態への反映や保存によって
        内容が失われていないか」である。

        note側のcontenteditableは改行の表現(\\n / <br> / 空div等)が
        実装により変わりうるため、比較前に空白文字を全て取り除いて正規化
        する(期待値・実際値の両方に同じ正規化を適用するため、改行表現の
        違いでは不一致にならず、実際の文字内容の差異だけを検出できる)。

        全体が一致しない場合は、原因の切り分けのため本文の先頭・末尾・
        ハッシュタグ行それぞれが含まれているかを個別に確認し、エラー
        メッセージに含める。stageは"保存前"/"保存後"など呼び出し位置を
        表す(エラーメッセージ・診断ファイル名に使う)。
        """
        try:
            actual = locator.inner_text()
        except PlaywrightTimeoutError:
            actual = ""

        normalized_actual = _normalize_whitespace(actual)
        normalized_expected = _normalize_whitespace(expected_body)

        if normalized_actual == normalized_expected:
            return

        head = expected_body[:20]
        tail = expected_body[-20:] if expected_body else ""
        head_ok = bool(head) and _normalize_whitespace(head) in normalized_actual
        tail_ok = bool(tail) and _normalize_whitespace(tail) in normalized_actual
        hashtag_ok = (not hashtag_line) or (
            _normalize_whitespace(hashtag_line) in normalized_actual
        )

        self._capture_failure(page, f"本文read-back確認_{stage}")
        raise NotePosterError(
            f"本文入力欄の内容を読み戻したところ({stage})、入力しようとした"
            f"本文と一致しませんでした。先頭一致={head_ok} 末尾一致={tail_ok} "
            f"タグ行一致={hashtag_ok} 実際の文字数(概算)={len(actual)} "
            f"期待した文字数(概算)={len(expected_body)}"
        )

    # -- 商品導線(本文末尾のテキストリンク) ------------------------------------

    def _apply_product_links(
        self, page: Page, body_locator: Locator, product_links: list[ProductLink]
    ) -> None:
        """本文末尾の商品導線セクションにある「→ 商品を見る」だけに、
        対応するURLをインラインリンクとして設定する。

        本文editor(body_locator)には既に build_product_links_trailer() が
        生成したプレーンテキスト(ECの生URLを含まない)が入力済みである
        ことが前提。各商品の実際の対象ブロックは _find_product_link_block()
        で商品名(label)を含むブロックとして一意に特定する
        (詳細はそちらのdocstringを参照)。
        """
        if not product_links:
            return

        for link in product_links:
            block = self._find_product_link_block(page, body_locator, link)
            self._set_link_on_text_occurrence(page, block, link)

    def _find_product_link_block(
        self, page: Page, body_locator: Locator, link: ProductLink
    ) -> Locator:
        """商品名(label)と「→ 商品を見る」が同一ブロック内に存在するブロックを、
        本文editor(body_locator)内から一意に特定する。

        実機のGitHub Actions実行(TEST-004)で判明した通り、noteのエディタは
        「{label}\\n→ 商品を見る」という1つのテキストの塊を、別々の<p>要素
        にはせず、同一の<p>要素内で<br>を挟んで描画する
        (<p>TOY JAM 瀬戸内レモン<br>→ 商品を見る</p>)。そのため商品名を
        「直後の兄弟要素」として探すのではなく、本文editor内の各ブロック
        要素(<p>)を対象に、以下のいずれかが成立しない場合は安全に一意
        特定できないと判断し、推測せずNotePosterErrorを送出する
        (呼び出し側でneeds_reviewに倒れる)。
          - 商品名(label)を含むブロックが本文editor内にちょうど1件だけ存在する
          - そのブロックのテキストを行単位に分解すると、ちょうど
            [label, _PRODUCT_LINK_TEXT] の2行になっている
            (=商品名の行の直後に「→ 商品を見る」の行が続いている)

        ★商品名の行単位の完全一致(2026年8月29日、ARTICLE-001の実機実行を
        踏まえた修正)★
        `product_links`に2件以上の商品があり、片方の商品名がもう片方の
        商品名の先頭部分と一致する場合(例:「TOY JAM 瀬戸内レモン」と
        「TOY JAM 瀬戸内レモン月桂樹」)、実機のGitHub Actions実行
        (ARTICLE-001)で商品名を含むブロックが2件見つかったと誤判定され、
        `needs_review`へ安全停止する事象が発生した。

        ★商品名と「→ 商品を見る」の隣接2行判定(2026年8月29日、同じ
        ARTICLE-001の実機実行を踏まえた再修正)★
        単に「labelと完全一致する行がブロック内に存在するか」だけを候補
        条件にすると、本文中のふつうの文章(商品導線とは無関係な段落)に
        たまたま商品名だけの行が出現した場合にも誤って候補に含めて
        しまう(そしてその後、本来の商品導線ブロックと合わせて「2件」の
        あいまいな一致として安全停止してしまう)おそれがある。そこで
        候補条件を、`text.splitlines()`で行単位に分解し空行を除いた
        `lines`に対して、

          `lines[i] == link.label` かつ `lines[i + 1] == _PRODUCT_LINK_TEXT`

        となる**隣接する2行のペア**が存在するブロックだけ、に厳密化した。
        商品名の行だけがある場合(直後に「→ 商品を見る」が続かない)は
        候補にしない。部分一致・前方一致は一切行わない(前後の空白のみ
        `strip()`で吸収する)。候補が0件または2件以上であれば、これまで
        通り推測せず`needs_review`へ安全停止する。一意に特定できた場合も
        念のため、そのブロック全体の行構成がちょうど`[label,
        _PRODUCT_LINK_TEXT]`の2行であることを改めて確認する(隣接ペアが
        存在するだけでなく、ブロックにそれ以外の行が混在していないことも
        保証するため)。

        次回の実機実行でも1回で原因を切り分けられるよう、候補ブロックが
        1件以上見つかった場合(一意に特定できた場合・複数件で安全停止する
        場合の両方を含む)、そのブロックのindexと正規化済み`lines`を
        `logger.info()`で記録する(本文全体ではなく候補ブロックのみ)。

        ★direct child(`:scope > p`)へのスコープ限定(2026年8月29日、
        ARTICLE-001の実機Artifactの直接解析を踏まえた修正)★
        上記の隣接2行判定を実装した後も、実機のGitHub Actions実行
        (ARTICLE-001)で商品名を含むブロックが2件見つかる事象が再現した。
        今回はユーザーが実機のHTMLダンプを直接解析し、実際には「TOY JAM
        瀬戸内レモン→ 商品を見る」というテキストが2回入力されているわけ
        ではなく、本文editor配下に

          - 本文・商品導線などをまとめて内包している(ProseMirrorが内部で
            生成した)`<p>`要素
          - その内部に実際の商品導線として存在する、独立した`<p>`要素

        の両方が存在し、`[label, _PRODUCT_LINK_TEXT]`という隣接2行が
        **両方の`<p>`要素で(内包関係のまま)成立してしまう**ことが原因
        だと判明した。`body_locator.locator("p")`は通常のCSSセレクタ
        `"p"`をそのスコープ配下に適用するため、直接の子だけでなく
        **あらゆる深さのdescendant**(子孫)の`<p>`要素にマッチする
        (`document.querySelectorAll`と同じ挙動)。実機DOMのdirect child
        構造を確認すると、商品導線は本文editorの直接の子として独立した
        `<p>`で存在しており(タイトル・本文本体・「この記事に出てきた
        商品」見出し・各商品の導線・ハッシュタグ行が、いずれも本文editor
        の直接の子の`<p>`として並ぶ)、descendantまで拾う必要はそもそも
        無かった。

        これを受けて、商品導線ブロックの探索スコープを
        `body_locator.locator(":scope > p")`(本文editorの**直接の子**の
        `<p>`だけ)に限定した。これにより、内包関係にある「大きな`<p>`」
        (本文editorの直接の子ではない)は最初から候補探索の対象にすら
        ならず、隣接2行判定・行構成の最終チェックはそのままdirect child
        の候補だけに対して行われる。`.first()`/`.nth()`による位置依存の
        回避(例えば「2件なら後ろを採用する」)は行っていない。

        ★ブロック全文の完全一致への厳密化(2026年8月29日、ARTICLE-001の
        実機再実行Artifactを踏まえた再修正)★
        direct child(`:scope > p`)への限定後も、実機のGitHub Actions
        実行(ARTICLE-001)で商品名を含むブロックが2件見つかる事象が
        再現した。ユーザーが実機のHTMLダンプを直接解析した結果、本文
        editorの直接の子である`<p>`の中に、(A)記事本文ほぼ全体を
        `inner_text()`として含む巨大な`<p>`と、(B)実際の商品導線専用の
        `<p>`の両方が存在することが判明した。巨大な`<p>`(A)の
        `inner_text()`を行単位に分解した`lines`の**途中**にも
        `[label, _PRODUCT_LINK_TEXT]`という隣接2行がたまたま含まれて
        いたため、「ブロック内のどこかに隣接2行が存在すれば候補」という
        従来の判定では、(A)(B)の両方が候補になっていた。

        そこで候補条件を、「ブロック内のどこかに隣接2行が存在するか」
        ではなく、「そのブロックの`lines`全体が`[label,
        _PRODUCT_LINK_TEXT]`という**ちょうど2行だけ**で構成されているか」
        (`lines == [link.label, _PRODUCT_LINK_TEXT]`)に厳密化した。これに
        より、商品導線専用の`<p>`(B)だけが候補になり、本文の一部として
        たまたま同じ2行を内部に含む巨大な`<p>`(A)は、他にも本文の地の文
        を含み`lines`がちょうど2行にはならないため候補にならない。
        部分一致・前方一致・「ブロック内のどこかに存在すれば良い」という
        判定には一切戻さない。候補が0件または2件以上であれば、これまで
        通り推測せず`needs_review`へ安全停止する。
        """
        blocks = body_locator.locator(":scope > p")
        try:
            total = blocks.count()
        except PlaywrightTimeoutError:
            total = 0

        expected_lines = [link.label, _PRODUCT_LINK_TEXT]
        label_match_indices: list[int] = []
        label_match_lines: list[list[str]] = []
        for i in range(total):
            block = blocks.nth(i)
            try:
                text = block.inner_text()
            except PlaywrightTimeoutError:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines == expected_lines:
                label_match_indices.append(i)
                label_match_lines.append(lines)

        if label_match_indices:
            logger.info(
                "商品導線ブロック候補: label=%r 候補=%s",
                link.label,
                [
                    {"block_index": idx, "lines": lns}
                    for idx, lns in zip(label_match_indices, label_match_lines)
                ],
            )

        if len(label_match_indices) != 1:
            self._capture_failure(page, "商品導線ブロック特定")
            raise NotePosterError(
                f"商品名『{link.label}』と「{_PRODUCT_LINK_TEXT}」の2行だけで"
                f"構成されるブロックが本文editor内(直接の子)に"
                f"{len(label_match_indices)}件見つかりました(期待: 1件)。"
                "商品導線を安全にスコープできないため処理を中断します。"
            )

        block = blocks.nth(label_match_indices[0])
        try:
            lines = [
                line.strip() for line in block.inner_text().splitlines() if line.strip()
            ]
        except PlaywrightTimeoutError:
            lines = []

        if lines.count(_PRODUCT_LINK_TEXT) != 1:
            self._capture_failure(page, "商品導線ブロック特定")
            raise NotePosterError(
                f"商品名『{link.label}』を含むブロック内に"
                f"「{_PRODUCT_LINK_TEXT}」が{lines.count(_PRODUCT_LINK_TEXT)}件"
                "見つかりました(期待: 1件)。商品導線の構造が想定と異なるため"
                "処理を中断します。"
            )

        if lines != [link.label, _PRODUCT_LINK_TEXT]:
            self._capture_failure(page, "商品導線ブロック特定")
            raise NotePosterError(
                f"商品名『{link.label}』を含むブロックの行構成が"
                f"{lines!r}でした(期待: {[link.label, _PRODUCT_LINK_TEXT]!r})。"
                "商品導線の構造が想定と異なるため処理を中断します。"
            )
        return block

    def _select_product_link_text_in_block(self, page: Page, block: Locator) -> None:
        """ブロック内の直接の子テキストノードのうち、「→ 商品を見る」と完全一致
        するものだけをブラウザのSelection/Range APIで選択する(商品名は
        選択範囲に含めない)。

        _find_product_link_block() で、このブロックのテキストが行単位で
        ちょうど [label, _PRODUCT_LINK_TEXT] であることを確認済みであり、
        その構造(テキストノード - <br> - テキストノード)を前提に、
        「→ 商品を見る」に一致する直接の子テキストノードだけを検索して
        選択する。一致するテキストノードがちょうど1件でない場合は、
        推測で選択せずNotePosterErrorを送出する。
        """
        try:
            result = block.evaluate(
                """
                (el, linkText) => {
                    const matches = Array.from(el.childNodes).filter(
                        (node) => node.nodeType === Node.TEXT_NODE
                            && node.textContent.trim() === linkText
                    );
                    if (matches.length !== 1) {
                        return { ok: false, count: matches.length };
                    }
                    const range = document.createRange();
                    range.selectNodeContents(matches[0]);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    return { ok: true, count: 1 };
                }
                """,
                _PRODUCT_LINK_TEXT,
            )
        except PlaywrightTimeoutError:
            result = None

        if not result or not result.get("ok"):
            count = result.get("count") if result else "不明"
            self._capture_failure(page, "商品導線テキスト選択")
            raise NotePosterError(
                f"「{_PRODUCT_LINK_TEXT}」に一致する直接の子テキストノードが"
                f"{count}件でした(期待: 1件)。ブロックの構造が想定と異なる"
                "ため、推測で選択せず処理を中断します。"
            )

    def _wait_for_locator_to_appear(self, locator: Locator, timeout_ms: int) -> bool:
        """locator自体(意味のあるCSSセレクタで絞り込んだ、複数件を許容
        しうるLocator)が出現しvisibleになるのを、上限timeout_ms
        (ミリ秒)だけ待つ。

        `.first`や`.nth()`のような位置ベースの絞り込みは一切行わず、
        locatorそのものに対して`wait_for(state="visible")`を呼ぶ
        (Playwrightの自動待機の仕組みであり、固定`time.sleep()`は
        使わない)。Playwrightのstrict modeにより、待機中にlocatorが
        2件以上の要素に一致した場合はTimeoutErrorとは別の例外
        (`playwright.sync_api.Error`)が送出されるが、これも「一意に
        特定できない」ケースとして扱いFalseを返す(位置ベースで1件を
        選んで先に進むことはしない)。出現しないまま時間切れになった
        場合(`TimeoutError`。`Error`のサブクラス)も同様にFalseを返す。
        Falseが返った場合、呼び出し側は改めて`count()`を取り直して
        具体的な件数(0件なのか複数件なのか)を確認し、安全停止の
        メッセージに反映する。
        """
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
            return True
        except PlaywrightError:
            return False

    def _find_active_link_toolbar_button(
        self, page: Page, timeout_ms: int = _LINK_TOOLBAR_APPEAR_TIMEOUT_MS
    ) -> Locator:
        """選択中のテキストに対して表示される、noteのフローティング編集
        ツールバー内の「リンク」ボタンを一意に特定する。

        timeout_ms はテストで待機時間を短縮するために公開している引数
        であり、実際の呼び出し(_set_link_on_text_occurrence)では常に
        デフォルト値(_LINK_TOOLBAR_APPEAR_TIMEOUT_MS)を使う。

        実機のGitHub Actions実行(TEST-004の追加観測、04/05/06のHTML
        ダンプ)で、noteのフローティング編集ツールバーは

          <div data-active="true" role="toolbar" id="desktop-toolbar" ...>

        という単一のDOM要素として存在し、その内部に

          <button tabindex="0" aria-label="リンク" aria-pressed="false" ...>

        という、アイコンのみだが`aria-label`を持つ単純クリック型のボタンが
        ちょうど1件存在することが判明した。style属性内のclass名(`sc-xxxx`
        のようなstyled-components由来のハッシュ)には一切依存せず、
        `role`/`id`/`data-active`/`aria-label`という意味のある属性だけで
        スコープする(このセレクタ自体はTEST-004で妥当性が確認できている
        ため変更していない)。

        ★出現タイミングの非同期遅延への対応(TEST-004の追加解析、2026年
        8月29日)★
        TEST-004の実行ログでは「ツールバーが0件」として安全停止していたが、
        その直後に_capture_failure()が保存したHTMLには実際にはツールバー
        (およびリンクボタン)が存在していた。これは「セレクタが間違って
        いた」のではなく、「→ 商品を見る」のテキスト選択が完了してから
        noteがフローティングツールバーを実際にDOMへマウントし
        `data-active="true"`にするまでに短い非同期の遅延があり、選択直後に
        即座に`count()`を呼んだ場合はまだ0件だった、という**タイミングの
        問題**だったと判断できる。

        そのため、いきなり`count()`を確認するのではなく、まず
        `_wait_for_locator_to_appear()`で要素が出現しvisibleになるのを
        短時間(`_LINK_TOOLBAR_APPEAR_TIMEOUT_MS`)だけ待つ。固定の
        `time.sleep()`は使わず、Playwrightの自動待機の仕組みに委ねる。
        待っても出現しなければ0件として扱い、これまで通り推測せず
        `needs_review`へ安全停止する。出現した後は、必ず改めて`count()`を
        取り直し、ちょうど1件であることを検証してから使う(複数件出現した
        場合も安全停止する)。リンクボタン側についても同様に、ツールバーが
        見つかった後で短時間だけ出現を待ってから一意性を確認する。

        以下のいずれかが成立しない場合は、位置ベースの推測(`.first()`/
        `.nth()`等)に頼らず、NotePosterErrorを送出して安全停止する
        (呼び出し側でneeds_reviewに倒れる)。
          - `div[role="toolbar"][data-active="true"]` が(短時間の出現待ち
            の後)ページ内にちょうど1件だけ存在する
          - そのツールバー内に `button[aria-label="リンク"]` が(短時間の
            出現待ちの後)ちょうど1件だけ存在する(ツールバーの外にある
            同名要素は対象にしない)
        """
        toolbar = page.locator(_ACTIVE_TOOLBAR_SELECTOR)
        self._wait_for_locator_to_appear(toolbar, timeout_ms)
        try:
            toolbar_count = toolbar.count()
        except PlaywrightTimeoutError:
            toolbar_count = 0
        if toolbar_count != 1:
            self._capture_failure(page, "商品導線リンクツールバー特定")
            raise NotePosterError(
                f"選択中のフローティング編集ツールバー({_ACTIVE_TOOLBAR_SELECTOR})が"
                f"{timeout_ms}ms待っても{toolbar_count}件"
                "でした(期待: 1件)。ツールバーを安全に特定できないため"
                "処理を中断します。"
            )

        link_button = toolbar.locator('button[aria-label="リンク"]')
        self._wait_for_locator_to_appear(link_button, timeout_ms)
        try:
            link_button_count = link_button.count()
        except PlaywrightTimeoutError:
            link_button_count = 0
        if link_button_count != 1:
            self._capture_failure(page, "商品導線リンクボタン特定")
            raise NotePosterError(
                "ツールバー内の「リンク」ボタン"
                '(button[aria-label="リンク"])が'
                f"{timeout_ms}ms待っても"
                f"{link_button_count}件でした(期待: 1件)。ボタンを安全に"
                "特定できないため処理を中断します。"
            )
        return link_button

    def _ensure_link_button_in_viewport(
        self,
        page: Page,
        link_button: Locator,
        timeout_ms: int = _LINK_BUTTON_CLICK_TIMEOUT_MS,
    ) -> None:
        """リンクボタンをクリックする前に、実際に現在のviewport(表示
        領域)内に収まっていることを確認する。

        timeout_ms はテストで待機時間を短縮するために公開している引数
        であり、実際の呼び出し(_set_link_on_text_occurrence)では常に
        デフォルト値(_LINK_BUTTON_CLICK_TIMEOUT_MS)を使う。

        実機のGitHub Actions実行(TEST-004)で、`_find_active_link_toolbar_
        button()`でボタン自体は一意に特定できていたにもかかわらず、
        `click()`が「element is outside of the viewport」を繰り返し、
        既定の30秒タイムアウトいっぱいまで失敗し続ける事象が発生した。
        設定済みのviewport高さ(800px)に対し、ツールバーの実測`top`値
        (847px/871px)がこれを超えていたことから、クリック対象が実際には
        現在のviewportに描画されていなかった可能性が高いと判断した
        (`viewport={"width": 1280, "height": 800}`という設定自体は今回
        変更しない)。

        以下を行う。
          1. viewport・現在のスクロール位置・ツールバーとボタンの
             `bounding_box()`を診断ログに記録する(原因切り分けのため)。
          2. `link_button.scroll_into_view_if_needed()`を試みる
             (固定`time.sleep()`は使わない。実行結果にかかわらず、
             成否の判定は次のステップの実測値で行う)。
          3. scroll後に改めて`bounding_box()`を取得し、ボタンの矩形が
             viewportの範囲(`0 <= x`、`0 <= y`、`x + width <= viewport
             幅`、`y + height <= viewport高さ`)に完全に収まっているかを
             検証する。

        `bounding_box()`が取得できない場合、またはviewportに完全には
        収まっていない場合は、`force=True`やJavaScriptによる直接の
        `element.click()`のような、Playwrightのactionability checkを
        迂回する手段には頼らず、推測でクリックせずに`_capture_failure()`
        でHTML/スクリーンショット/診断データを保存したうえで
        `LinkButtonOutOfViewportError`を送出して安全停止する(呼び出し側
        でneeds_reviewに倒れる)。

        ★`position: fixed`祖先の検知(2026年8月29日、ARTICLE-001の実機
        再実行で判明した事象を踏まえた診断強化)★
        実機のGitHub Actions実行(ARTICLE-001)で、`scroll_into_view_if_
        needed()`を実行しても`bounding_box()`の`y`がほぼ変化しない
        (例: scroll前`y≈2094`→scroll後`y≈2090`。一方`window.scrollY`は
        `0`から`2010`へ変化していた)という事象が発生した。

        `Locator.bounding_box()`はPlaywrightの公式挙動として、要素の
        `getBoundingClientRect()`相当の**viewport相対座標**(スクロール量
        に応じて変化する座標)を返す(document(ページ全体)相対の座標では
        ない)。このことはローカルで実際に検証済みで、`position: static`
        (通常の文書フロー)の要素は`window.scrollTo()`によるスクロールに
        応じて`bounding_box().y`が変化する一方、`position: fixed`の要素は
        スクロールしても`bounding_box().y`が(誤差程度を除き)まったく
        変化しないことを確認している。したがって
        `_bounding_box_within_viewport()`が`box["y"]`をそのまま
        viewportの範囲と比較している実装自体は座標系の誤解ではなく正しい。

        実機で観測された「scroll前後でほぼ`y`が変化しない」という挙動は、
        クリック対象のボタン(またはその祖先、実機では商品導線ツールバーを
        包む`<div class="... fixed left-0 top-0 z-50 size-full">`)が
        `position: fixed`であるためだと考えられる。`position: fixed`の
        要素はCSSの仕様上、ページをどれだけスクロールしても画面上の位置が
        変化しない(=`window`のスクロールでは`viewport`内へ移動できない)
        ため、`scroll_into_view_if_needed()`は原理的にこの状況を解決
        できない。

        この原因を次回の実機実行時により明確に切り分けられるよう、
        ボタン自身とその祖先要素を`position: fixed`かどうか読み取り専用で
        検査し(`getComputedStyle()`。クリック・フォーカス等の操作は
        一切行わない)、診断ログおよび最終的なエラーメッセージへ含める
        ようにした。`position: fixed`の祖先が見つかった場合、それは
        「スクロールでは解決できない」ことを示す情報であり、`_bounding_
        box_within_viewport()`によるviewport内判定そのもの・安全停止の
        挙動(`needs_review`へ倒れること)は一切変更していない。viewport
        サイズ(1280x800)自体も今回は変更していない。
        """
        try:
            viewport_size = page.viewport_size
        except PlaywrightError:
            viewport_size = None
        try:
            scroll_position = page.evaluate(
                "() => ({x: window.scrollX, y: window.scrollY})"
            )
        except PlaywrightError:
            scroll_position = None
        try:
            toolbar_box = page.locator(_ACTIVE_TOOLBAR_SELECTOR).bounding_box()
        except PlaywrightError:
            toolbar_box = None
        try:
            button_box_before = link_button.bounding_box()
        except PlaywrightError:
            button_box_before = None
        try:
            fixed_position_ancestor = link_button.evaluate(_FIXED_ANCESTOR_PROBE_JS)
        except PlaywrightError:
            fixed_position_ancestor = None
        logger.info(
            "商品導線リンクボタン クリック前診断: viewport=%s scroll=%s "
            "toolbar_bbox=%s button_bbox(scroll前)=%s "
            "position_fixed祖先=%s",
            viewport_size,
            scroll_position,
            toolbar_box,
            button_box_before,
            fixed_position_ancestor,
        )

        try:
            link_button.scroll_into_view_if_needed(timeout=timeout_ms)
        except PlaywrightError:
            # scroll自体が失敗・timeoutしても、この後の実測bounding_box()
            # で最終的にviewport内に収まったかどうかを判定するため、
            # ここでは中断しない。
            pass

        try:
            button_box_after = link_button.bounding_box()
        except PlaywrightError:
            button_box_after = None

        if button_box_after is None or viewport_size is None:
            self._capture_failure(page, "商品導線リンクボタンviewport確認")
            raise LinkButtonOutOfViewportError(
                "リンクボタンの位置(bounding_box)またはviewportサイズを"
                f"取得できませんでした(button_bbox={button_box_after!r}, "
                f"viewport={viewport_size!r})。安全にクリック可能な状態を"
                "確認できないため処理を中断します。"
            )

        if not _bounding_box_within_viewport(button_box_after, viewport_size):
            self._capture_failure(page, "商品導線リンクボタンviewport確認")
            fixed_hint = ""
            if fixed_position_ancestor and fixed_position_ancestor.get("fixed"):
                fixed_hint = (
                    "position: fixedの要素"
                    f"({fixed_position_ancestor.get('tagName')}."
                    f"{fixed_position_ancestor.get('className')})が祖先に"
                    "見つかりました。position: fixedの要素はページの"
                    "スクロールでは画面上の位置が変化しないため、"
                    "scroll_into_view_if_needed()では表示範囲内へ移動でき"
                    "ません。 "
                )
            raise LinkButtonOutOfViewportError(
                f"{fixed_hint}リンクボタンをscroll_into_view_if_needed()"
                "した後も、"
                f"表示範囲(viewport {viewport_size['width']}x"
                f"{viewport_size['height']})に完全には収まっていません"
                f"(bounding_box(scroll後)={button_box_after!r}、"
                f"bounding_box(scroll前)={button_box_before!r}、"
                f"スクロール位置={scroll_position!r})。安全にクリックできる"
                "状態を確認できないため処理を中断します。"
            )

    def _log_url_input_diagnostics(self, page: Page, url_input: Locator, *, stage: str) -> None:
        """商品導線URL入力の各時点の状態を診断ログに記録する。

        実機のGitHub Actions実行(TEST-004)で、ある回はURL入力から
        read-backまで成功したのに、別の回ではURL入力後にURL入力欄が
        消失し、read-backが`None`になるという事象が発生した。原因を
        1回の実機実行でできるだけ切り分けられるよう、URL入力欄の一意
        特定からread-backまでの区間の複数時点(入力直前・クリック直後・
        文字入力の前後・read-back直前・消失検知時・read-back成功後)で
        この関数を呼び、状態を記録する。

        読み取るのはURL入力欄・active toolbarの`count()`/`is_visible()`/
        `input_value()`(1件のときのみ)/`bounding_box()`、
        `document.activeElement`のtagName/name/placeholder/aria-label、
        `window.scrollX`/`scrollY`のみであり、フォーカスを奪う操作や
        別要素をクリックする操作は一切行わない。取得できない項目があって
        もこの関数自体は例外を送出せず、取得できた範囲だけをログに残す
        (診断のための計測が本処理を止めてしまわないようにするため)。
        """
        try:
            url_input_count = url_input.count()
        except PlaywrightError:
            url_input_count = None
        try:
            url_input_visible = url_input.is_visible()
        except PlaywrightError:
            url_input_visible = None
        url_input_value: str | None = None
        url_input_box = None
        if url_input_count == 1:
            try:
                url_input_value = url_input.input_value()
            except PlaywrightError:
                url_input_value = "(取得失敗)"
            try:
                url_input_box = url_input.bounding_box()
            except PlaywrightError:
                url_input_box = None

        toolbar = page.locator(_ACTIVE_TOOLBAR_SELECTOR)
        try:
            toolbar_count = toolbar.count()
        except PlaywrightError:
            toolbar_count = None
        toolbar_data_active = None
        toolbar_box = None
        if toolbar_count == 1:
            try:
                toolbar_data_active = toolbar.get_attribute("data-active")
            except PlaywrightError:
                toolbar_data_active = None
            try:
                toolbar_box = toolbar.bounding_box()
            except PlaywrightError:
                toolbar_box = None

        try:
            active_element = page.evaluate(
                """
                () => {
                    const el = document.activeElement;
                    if (!el) return null;
                    return {
                        tagName: el.tagName,
                        name: el.getAttribute('name'),
                        placeholder: el.getAttribute('placeholder'),
                        ariaLabel: el.getAttribute('aria-label'),
                    };
                }
                """
            )
        except PlaywrightError:
            active_element = None

        try:
            scroll_position = page.evaluate(
                "() => ({x: window.scrollX, y: window.scrollY})"
            )
        except PlaywrightError:
            scroll_position = None

        logger.info(
            "商品導線URL入力 診断[%s]: url_input(count=%s visible=%s "
            "value=%r bbox=%s) toolbar(count=%s data-active=%s bbox=%s) "
            "activeElement=%s scroll=%s",
            stage,
            url_input_count,
            url_input_visible,
            url_input_value,
            url_input_box,
            toolbar_count,
            toolbar_data_active,
            toolbar_box,
            active_element,
            scroll_position,
        )

    def _find_url_input_textarea(
        self, page: Page, timeout_ms: int = _URL_INPUT_APPEAR_TIMEOUT_MS
    ) -> Locator:
        """リンクボタンをクリックした後に現れるURL入力欄(textarea)を
        一意に特定する。

        実機のGitHub Actions実行(TEST-004の追加観測)で、リンクボタンを
        クリックした直後に

          <textarea inputmode="text" name="alt" placeholder="https://">
          </textarea>
          <button aria-label="URLの入力をやめる">

        というインラインのURL入力UIが出現することが判明した。class名には
        一切依存せず、`placeholder`/`inputmode`/`name`という3つの独立した
        意味のある属性をすべて満たす`<textarea>`だけを対象にする
        (`_URL_INPUT_SELECTOR`)。「URLの入力をやめる」ボタンの存在は、
        このUIが確かにURL入力用であることの状況証拠として実機で確認した
        ものであり、セレクタそのものには使っていない(現時点では構造の
        全体像が不明なため、推測でスコープを広げない)。

        `_find_active_link_toolbar_button()`と同じ設計で、いきなり
        `count()`を確認するのではなく、まず`_wait_for_locator_to_appear()`
        で出現・可視化を待ってから、改めて`count()`を取り直してちょうど
        1件であることを検証する。`.first()`/`.nth()`のような位置ベースの
        絞り込みは一切行わない。待機中にlocatorが2件以上に一致した場合は
        Playwrightのstrict modeにより例外が送出されるが、これも「一意に
        特定できない」ケースとして扱う。timeout・0件・複数件・strict mode
        違反のいずれの場合も、推測せずNotePosterErrorを送出して安全停止
        する(呼び出し側でneeds_reviewに倒れる)。
        """
        url_input = page.locator(_URL_INPUT_SELECTOR)
        self._wait_for_locator_to_appear(url_input, timeout_ms)
        try:
            url_input_count = url_input.count()
        except PlaywrightTimeoutError:
            url_input_count = 0
        if url_input_count != 1:
            self._capture_failure(page, "商品導線URL入力欄特定")
            raise NotePosterError(
                f"商品導線のURL入力欄({_URL_INPUT_SELECTOR})が"
                f"{timeout_ms}ms待っても{url_input_count}件でした"
                "(期待: 1件)。URL入力欄を安全に特定できないため処理を"
                "中断します。"
            )
        return url_input

    def _find_url_apply_button(
        self, page: Page, timeout_ms: int = _URL_INPUT_APPEAR_TIMEOUT_MS
    ) -> Locator:
        """URL入力・read-back確認の後、noteのフローティング編集ツールバー
        内で「適用」ボタンを一意に特定する。

        実機のGitHub Actions実行(TEST-004)で、「適用」ボタンとURL入力欄
        (textarea)は、リンクボタンの特定に使っているのと同じフローティング
        編集ツールバー(`_ACTIVE_TOOLBAR_SELECTOR`)の内部に存在すること
        が判明した。

          <div data-active="true" role="toolbar" id="desktop-toolbar">
            ...
            <textarea inputmode="text" name="alt" placeholder="https://">
            </textarea>
            <button data-name="Button" type="button" id=":r16:">
              <span>適用</span>
            </button>
            <button aria-label="URLの入力をやめる">...</button>
            ...
          </div>

        「適用」ボタンの`id`(`:r16:`のような値)はReactの`useId`等が生成
        する動的IDの可能性が高く、実行のたびに変わりうるためセレクタには
        使わない。style属性内のclass名(styled-components由来のハッシュ)
        にも依存しない。

        代わりに、URL入力欄の特定(`_find_url_input_textarea`)と同じ
        アクティブなツールバーをスコープとして使う。ページ全体から
        「適用」という文字列を検索するのではなく、
          1. `_ACTIVE_TOOLBAR_SELECTOR`がページ内にちょうど1件であること
             を再確認する
          2. そのツールバー内で`_URL_INPUT_SELECTOR`に一致するURL入力欄が
             ちょうど1件であることを再確認する(URL入力欄と「適用」ボタン
             が同じツールバー内にあることを、DOM構造の推測やXPathでの
             祖先指定を使わずに、スコープそのもので保証する)
          3. そのツールバー内で`get_by_role("button", name="適用",
             exact=True)`に一致するボタンの出現を待ち、ちょうど1件で
             あることを確認する
        のいずれかが成立しない場合は、`.first()`/`.nth()`のような位置
        ベースの推測に頼らず、NotePosterErrorを送出して安全停止する
        (呼び出し側でneeds_reviewに倒れる)。
        """
        toolbar = page.locator(_ACTIVE_TOOLBAR_SELECTOR)
        self._wait_for_locator_to_appear(toolbar, timeout_ms)
        try:
            toolbar_count = toolbar.count()
        except PlaywrightTimeoutError:
            toolbar_count = 0
        if toolbar_count != 1:
            self._capture_failure(page, "商品導線URL適用_ツールバー再確認")
            raise NotePosterError(
                f"「適用」ボタンを探す前のツールバー再確認で"
                f"({_ACTIVE_TOOLBAR_SELECTOR})が{toolbar_count}件でした"
                "(期待: 1件)。安全に特定できないため処理を中断します。"
            )

        url_input_in_toolbar = toolbar.locator(_URL_INPUT_SELECTOR)
        try:
            url_input_count = url_input_in_toolbar.count()
        except PlaywrightTimeoutError:
            url_input_count = 0
        if url_input_count != 1:
            self._capture_failure(page, "商品導線URL適用_URL入力欄再確認")
            raise NotePosterError(
                f"「適用」ボタンを探す前のURL入力欄再確認で"
                f"({_URL_INPUT_SELECTOR})が同じツールバー内に"
                f"{url_input_count}件でした(期待: 1件)。安全に特定できない"
                "ため処理を中断します。"
            )

        apply_button = toolbar.get_by_role("button", name="適用", exact=True)
        self._wait_for_locator_to_appear(apply_button, timeout_ms)
        try:
            apply_button_count = apply_button.count()
        except PlaywrightTimeoutError:
            apply_button_count = 0
        if apply_button_count != 1:
            self._capture_failure(page, "商品導線URL適用ボタン特定")
            raise NotePosterError(
                "ツールバー内の「適用」ボタン"
                '(get_by_role("button", name="適用", exact=True))が'
                f"{timeout_ms}ms待っても{apply_button_count}件でした"
                "(期待: 1件)。ボタンを安全に特定できないため処理を"
                "中断します。"
            )
        return apply_button

    def _set_link_on_text_occurrence(
        self, page: Page, block: Locator, link: ProductLink
    ) -> None:
        """指定したブロック内の「→ 商品を見る」だけを選択し、noteのフロー
        ティング編集ツールバーの「リンク」ボタンをクリックしたうえで、
        URL入力欄にURLを入力してread-backを確認する。

        ★観測専用実装・第1段階(2026年8月29日時点、TEST-004の追加観測を
        踏まえた修正)★
        実機のGitHub Actions実行(TEST-004)で、note公式の「エディタの
        ガイド」パネルを実際にパースした結果、リンク挿入はツールバーの
        「ボタン」一覧には存在するものの、「キーボードショートカット」
        一覧には存在しないことが判明した。つまりControl+K/Meta+Kが実機で
        無反応だったのは偶然の失敗ではなく、そもそもnote側にそのような
        ショートカットが実装されていないためだった。これを受けてショート
        カット方式(旧`_open_link_input_via_shortcut`)を完全に撤去し、
        フォールバックとしても残さず、実機で構造を確認できたツールバーの
        ボタンをクリックする方式(`_find_active_link_toolbar_button`)に
        切り替えた。

        ★観測専用実装・第2段階(2026年8月29日時点、TEST-004の追加観測を
        踏まえた修正)★
        リンクボタンをクリックした後、実機で

          <textarea inputmode="text" name="alt" placeholder="https://">
          </textarea>

        というURL入力欄が出現することが確認できた
        (`_find_url_input_textarea`)。そこでこの段階では、URL入力欄を
        一意に特定できた場合のみ`product_links`の対象URLを入力し、入力に
        使った同じlocatorから`input_value()`でread-backして期待したURLと
        完全一致することを確認する。ただし**URLの確定方法**(Enterキー
        送信・Tabキー送信・意図的なフォーカス解除・確定ボタンのクリック・
        他要素のクリックのいずれも)はまだ実機で確認できていないため、
        一切行わない。read-backが一致した時点でHTML/スクリーンショット/
        診断データを保存したうえで`UrlInputObservationStop`(観測専用の
        安全停止。`LinkButtonObservationStop`とはログ上で区別できる別の
        サブクラス)を送出して処理を止める。read-backが不一致だった場合も
        (可能な限り)診断データを保存したうえで、通常のNotePosterErrorで
        安全停止する(観測の成功ではなく実際の問題として扱う)。
        `<a href>`が実際に生成されたかの確認や`_assert_links_match()`には
        まだ進まない。次回の実機テストでこの観測データを取得したのち、
        URLの確定方法を本実装する想定である。

        ★リンクボタンクリック前のviewport確認・第3段階(2026年8月29日
        時点、TEST-004の追加実機実行を踏まえた修正)★
        上記の第2段階を実機で実行したところ、URL入力欄には到達せず、
        リンクボタン自体のクリックが「element is outside of the
        viewport」を繰り返して既定の30秒タイムアウトで失敗した。ボタン
        自体は`_find_active_link_toolbar_button()`で一意に特定できていた
        ため、原因はセレクタではなく、クリック対象が実際には現在の
        viewport(1280x800)の外に描画されていたことだと判断した。これを
        受けて、クリックの前に`_ensure_link_button_in_viewport()`で
        `scroll_into_view_if_needed()`を試みたうえでボタンの`bounding_
        box()`を実測し、viewportに完全に収まっていることを確認してから
        でなければクリックしない設計に変更した。収まっていなければ
        `force=True`やJavaScriptによる直接の`element.click()`のような
        actionability checkを迂回する手段には頼らず、
        `LinkButtonOutOfViewportError`で安全停止する。クリック自体の
        タイムアウトも、既定の30秒ではなく短い上限
        (`_LINK_BUTTON_CLICK_TIMEOUT_MS`)に変更した。

        ★「適用」ボタンクリック・第4段階(2026年8月29日時点、TEST-004の
        追加実機実行を踏まえた修正)★
        上記の第3段階を実機で実行したところ、URL入力・read-back一致まで
        成功した。追加で取得した実機Artifactから、URL入力欄と同じ
        フローティング編集ツールバー内に「適用」ボタンが存在することが
        判明した(`_find_url_apply_button`)。そこでこの段階では、
        read-backが一致した後、この「適用」ボタンをツールバーというスコープ
        の中で一意に特定できた場合のみ、`_ensure_link_button_in_viewport()`
        でviewport内であることを確認し、`_assert_not_publish_action()`を
        適用したうえで通常の`click()`(短いタイムアウト)を行う。クリック
        直後にHTML/スクリーンショット/診断データを保存したうえで
        `UrlApplyObservationStop`(`UrlInputObservationStop`とはログ上で
        区別できる別のサブクラス)を送出して処理を止める。`<a>`要素が
        実際に生成されたか、`href`が期待通りか、URL入力UIが消えるか等は
        まだ実機で確認できていないため、`_assert_links_match()`や下書き
        保存へはまだ進まない。次回の実機テストでこの観測データを取得した
        のち、URL確定の完了確認・後続処理を本実装する想定である。

        ★URL入力〜read-back区間の診断強化・第5段階(2026年8月29日時点、
        TEST-004の追加実機実行を踏まえた修正)★
        上記の第4段階を実機で実行したところ、ある回はURL入力・read-back
        一致まで成功したが、別の回では「→ 商品を見る」の選択・リンク
        ボタンのクリック・URL入力欄の出現・URL入力までは成功と同じように
        進んだにもかかわらず、read-backの直前でURL入力欄が消失し
        (`input_value()`の結果が`None`相当になり)、失敗時のArtifactでは
        URL入力欄のtextarea自体がDOMから無くなっており、active toolbarも
        `data-active="false"`に戻っていた。この2回の実機実行の間でコード
        (commit)は変更していない(=コードの回帰ではない)ため、原因は
        note側の実行タイミングに依存する何らかの状態(非同期処理・
        バリデーション・再レンダリング等)にあると考えられる。次回の実機
        実行1回でできるだけ原因を切り分けられるよう、URL入力欄の一意
        特定からread-backまでの区間に`_log_url_input_diagnostics()`による
        複数時点(入力直前・クリック直後・文字入力の前後・read-back直前・
        read-back成功後)の状態記録を追加した。また、`press_sequentially()`
        完了直後に同じセレクタで`count()`を再確認し、1件でなければ
        (=消失または増減していれば)read-backを無理に続行せず、専用の
        `UrlInputDisappearedObservationStop`を送出して安全停止するように
        した。`input_value()`自体の呼び出し中に例外が発生した場合は、この
        消失検知(事前の`count()`チェック)とは区別し、通常のNotePoster
        Errorとして例外の内容を含めて報告する(「read-backした値が
        `None`だった」という曖昧な扱いはしない)。`press_sequentially()`
        自体や「適用」ボタン以降の処理は変更していない。

        ★「適用」クリック後の完成実装・第6段階(2026年8月29日時点、
        TEST-004で「適用」クリック後の完成DOMを確認したことを踏まえた
        修正)★
        上記の第4・第5段階を実機で実行したところ、URL入力・read-back
        一致・「適用」ボタンクリックまで安定して成功するようになった。
        取得した実機Artifact(`_capture_failure()`が保存したHTML)を解析
        したところ、「適用」クリック後は対象ブロック内に実際に

          <p ...>TOY JAM 瀬戸内レモン<br>
            <a href="https://you-ichi.jp/?pid=192116331" target="_blank"
               rel="noopener"><span class="highlight">→ 商品を見る</span>
            </a>
          </p>

        という`<a>`要素が生成されており、hrefは入力したURLと完全一致、
        アンカーテキストは(`<span class="highlight">`でラップされて
        いるが)「→ 商品を見る」と一致していた。フローティング編集
        ツールバーはURL入力欄・「適用」ボタンが消え、通常の選択ツール
        バー(見出し/太字/リンク/引用等)へ戻っていたが、DOM上の`data-
        active`属性自体は`"true"`のままだった(=ツールバーの存在自体で
        「まだ処理中か」を判定するのは不確実)。

        この観測結果を受けて、意図的な`UrlApplyObservationStop`による
        停止を撤去し、代わりに`_wait_for_product_link_applied()`で
        対象ブロック内に`<a>`要素(`get_by_role("link", name=
        _PRODUCT_LINK_TEXT, exact=True)`)が実際に出現するのを、固定
        `time.sleep()`ではなくPlaywrightのlocator待機(`wait_for(state=
        "visible")`)で待つように変更した。待っても出現しなければ推測
        せず`NotePosterError`で安全停止する。出現を確認できた場合は
        正常終了し、呼び出し元の`_apply_product_links()`のループへ
        戻る(複数の`product_links`がある場合、続けて次の商品のリンク
        設定に進む)。実際の件数・テキスト完全一致・href一致の検証は
        ここでは行わず、既存の`_assert_links_match()`(本ラウンドでは
        変更していない)に委ねる。`UrlInputObservationStop`・
        `UrlApplyObservationStop`のクラス自体は削除せず、それぞれの
        段階まで実機で確認できたことを示す診断用の例外として残して
        いるが、この完成実装以降はどちらも通常の処理経路では送出され
        ない。
        """
        self._select_product_link_text_in_block(page, block)

        try:
            selected_text = page.evaluate(
                "() => (window.getSelection() && window.getSelection().toString()) || ''"
            )
        except PlaywrightTimeoutError:
            selected_text = ""
        if selected_text.strip() != _PRODUCT_LINK_TEXT:
            self._capture_failure(page, "商品導線テキスト選択確認")
            raise NotePosterError(
                f"『{link.label}』の「{_PRODUCT_LINK_TEXT}」を選択したはずですが、"
                f"実際の選択内容が{selected_text.strip()!r}でした。安全のため"
                "処理を中断します。"
            )

        link_button = self._find_active_link_toolbar_button(page)
        self._ensure_link_button_in_viewport(page, link_button)
        self._assert_not_publish_action(link_button)
        try:
            link_button.click(timeout=_LINK_BUTTON_CLICK_TIMEOUT_MS)
        except PlaywrightError as exc:
            self._capture_failure(page, "商品導線リンクボタンクリック失敗")
            raise NotePosterError(
                f"『{link.label}』のリンクボタンのクリックに失敗しました: {exc}"
            ) from exc

        url_input = self._find_url_input_textarea(page)
        self._log_url_input_diagnostics(page, url_input, stage="A_URL入力直前")

        url_input.click()
        self._log_url_input_diagnostics(page, url_input, stage="B_click直後")

        self._log_url_input_diagnostics(page, url_input, stage="C_press_sequentially開始直前")
        url_input.press_sequentially(link.url, delay=10)
        self._log_url_input_diagnostics(page, url_input, stage="D_press_sequentially完了直後")

        try:
            post_type_count = url_input.count()
        except PlaywrightError:
            post_type_count = 0
        if post_type_count != 1:
            self._log_url_input_diagnostics(page, url_input, stage="F_textarea消失検知")
            self._capture_failure(page, "商品導線URL入力後textarea消失")
            raise UrlInputDisappearedObservationStop(
                f"『{link.label}』のURLをtextareaへ入力した直後、同じ"
                f"セレクタ({_URL_INPUT_SELECTOR})でcount()を再取得した"
                f"ところ{post_type_count}件でした(期待: 1件)。URL入力欄が"
                "消失または予期せず増減した可能性があるため、read-backを"
                "続行せず処理を中断します。"
            )

        self._log_url_input_diagnostics(page, url_input, stage="E_read-back直前")
        try:
            actual_value = url_input.input_value()
        except PlaywrightError as exc:
            self._capture_failure(page, "商品導線URL入力read-back例外")
            raise NotePosterError(
                f"『{link.label}』のURL入力欄のread-back(input_value())"
                f"実行中に例外が発生しました: {exc}。直前のcount()の"
                "再確認では1件だったにもかかわらず例外が発生したため、"
                "値の不一致とは区別して報告します。安全のため処理を"
                "中断します。"
            ) from exc

        if actual_value != link.url:
            self._capture_failure(page, "商品導線URL入力read-back不一致")
            raise NotePosterError(
                f"『{link.label}』のURL入力欄にURL({link.url!r})を"
                f"入力しましたが、read-backした値が{actual_value!r}でした。"
                "安全のため処理を中断します。"
            )

        self._log_url_input_diagnostics(page, url_input, stage="G_read-back成功後")

        apply_button = self._find_url_apply_button(page)
        self._ensure_link_button_in_viewport(page, apply_button)
        self._assert_not_publish_action(apply_button)
        try:
            apply_button.click(timeout=_LINK_BUTTON_CLICK_TIMEOUT_MS)
        except PlaywrightError as exc:
            self._capture_failure(page, "商品導線URL適用ボタンクリック失敗")
            raise NotePosterError(
                f"『{link.label}』の「適用」ボタンのクリックに失敗しました: {exc}"
            ) from exc

        self._wait_for_product_link_applied(page, block, link)

    def _wait_for_product_link_applied(
        self,
        page: Page,
        block: Locator,
        link: ProductLink,
        timeout_ms: int = _PRODUCT_LINK_APPLY_TIMEOUT_MS,
    ) -> None:
        """「適用」ボタンをクリックした直後、実際に<a>要素が対象ブロック内へ
        反映されるまで、固定sleepではなくPlaywrightのlocator待機で待つ
        (2026年8月29日、実機Artifactで「適用」クリック後の完成DOMを確認した
        ことを受けて追加)。

        実機Artifact(TEST-004)を解析したところ、「適用」クリック後は
        URL入力欄と「適用」ボタンがDOMから消え、フローティング編集
        ツールバーは通常の選択ツールバー(見出し/太字/リンク/引用...)へ
        戻る一方、対象ブロック内には

          <p ...><br><a href="https://you-ichi.jp/?pid=192116331"
             target="_blank" rel="noopener">
            <span class="highlight">→ 商品を見る</span>
          </a></p>

        のように<a>要素が実際に生成されており、hrefは入力したURLと完全に
        一致し、アンカーテキストは(<span>でラップされてはいるが)
        「→ 商品を見る」と一致していた。

        ここでは`block.get_by_role("link", name=_PRODUCT_LINK_TEXT,
        exact=True)`というアクセシブルロールベースのlocatorを対象ブロック
        にスコープして待機に使う(role="link"はネイティブの<a href>に
        自動的に付与され、アクセシブルネームは内部に<span>があっても
        テキスト全体から自動計算されるため、<span>でラップされているか
        どうかに依存しない)。このメソッドの責務は「反映されるまで待つ」
        ことだけであり、件数・テキスト完全一致・href一致といった実際の
        検証は行わない(それらは`_assert_links_match()`に委ねる。二重に
        同じ検証を実装しないことで、検証ロジックを1箇所に保つ)。

        上限`timeout_ms`(既定`_PRODUCT_LINK_APPLY_TIMEOUT_MS`)以内に
        リンクが反映されたことを確認できない場合は、推測で先へ進まず
        `NotePosterError`を送出して安全停止する(呼び出し側で
        `needs_review`に倒れる)。
        """
        anchor = block.get_by_role("link", name=_PRODUCT_LINK_TEXT, exact=True)
        if not self._wait_for_locator_to_appear(anchor, timeout_ms):
            self._capture_failure(page, "商品導線URL適用後のリンク未反映")
            raise NotePosterError(
                f"『{link.label}』の「適用」ボタンをクリックしましたが、"
                f"タイムアウト({timeout_ms}ms)以内に対象ブロック内へのリンク"
                "反映を確認できませんでした。安全のため処理を中断します。"
            )

    def _assert_links_match(
        self,
        page: Page,
        body_locator: Locator,
        product_links: list[ProductLink],
        *,
        stage: str,
    ) -> None:
        """商品導線セクションのリンクが、意図した通りに設定されているかを確認する。

        本文editor内の<a>要素を総数で数える検証は行わない(本文には
        将来ふつうの参考リンク等が入る可能性があるため)。代わりに、
        _find_product_link_block() で自分自身が生成した商品導線の各ブロック
        だけをスコープに、以下を確認する。
          - そのブロック内にちょうど1件の<a>要素があること
            (リンクが設定されていない・余計なリンクが付いている、を検出)
          - その<a>要素のテキストが「→ 商品を見る」と完全一致すること
            (商品名までリンク範囲に含まれてしまった場合、<a>要素のテキストは
            「label\\n→ 商品を見る」のようになり完全一致しなくなるため、
            この1つの比較だけで検出できる)
          - その<a>要素のhrefが期待したURLと一致すること
        商品導線をこの方法で安全にスコープできない場合(labelを含むブロックが
        複数件・0件見つかる等)も、推測はせずneeds_reviewへ安全停止する。
        """
        if not product_links:
            return

        mismatches: list[str] = []
        for link in product_links:
            try:
                block = self._find_product_link_block(page, body_locator, link)
            except NotePosterError as exc:
                mismatches.append(f"『{link.label}』: {exc}")
                continue

            anchors = block.locator("a")
            anchor_count = anchors.count()
            if anchor_count != 1:
                mismatches.append(
                    f"『{link.label}』: 「{_PRODUCT_LINK_TEXT}」のリンク要素が"
                    f"{anchor_count}件でした(期待: 1件)"
                )
                continue

            anchor = anchors.first
            try:
                actual_text = (anchor.inner_text() or "").strip()
            except PlaywrightTimeoutError:
                actual_text = ""
            actual_href = anchor.get_attribute("href") or ""
            if actual_text != _PRODUCT_LINK_TEXT:
                mismatches.append(
                    f"『{link.label}』: リンクのテキストが{actual_text!r}でした"
                    f"(期待: {_PRODUCT_LINK_TEXT!r})。商品名までリンク範囲に"
                    "含まれている可能性があります"
                )
            if actual_href != link.url:
                mismatches.append(
                    f"『{link.label}』: hrefが{actual_href!r}でした"
                    f"(期待: {link.url!r})"
                )

        if mismatches:
            self._capture_failure(page, f"商品導線リンク確認_{stage}")
            raise NotePosterError(
                f"商品導線リンクの確認({stage})で不一致が見つかりました: "
                + " / ".join(mismatches)
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
