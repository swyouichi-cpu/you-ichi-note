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
固定文言(_PRODUCT_LINK_TEXT)だけをN番目の出現ごとに選択してリンクを
設定する(_apply_product_links())。ECの生URLは本文の文字列としては
一切登場せず、href属性としてのみ設定される。`product_links`が空または
`[]`の場合は導線セクション自体を追加しない(タグ0件時の設計と同じ)。
不正なJSON・必須フィールド欠落・不正なURL形式は、タグの内部空白と同じ
思想で自動修正せずProductLinkValidationErrorを送出してneeds_reviewへ
倒す(ARTICLE-001に限らず、どの記事にも同じロジックが適用される)。

★選択操作は位置ベースフォールバックではない★
「→ 商品を見る」というテキストは複数の商品がある場合、本文中に複数回
出現する。これをN番目の出現として`page.get_by_text(...).nth(N)`で
選択するが、これは _fill_body() で撤去した「画面上に見えるN番目の
contenteditable要素」のような構造推測とは性質が異なる。ここでのNは、
noteのDOM構造を推測しているのではなく、**このコード自身が直前に生成した
既知のテキスト**の出現順序を、内容(テキスト一致)で特定したうえで数えて
いるだけである。それでも安全のため、本文中の「→ 商品を見る」の出現数が
`product_links`の件数と一致しない場合(人間が書いた本文に偶然同じ文言が
含まれていた等)は、どれがどのリンクに対応するか一意に定まらないため、
リンクを設定せずneeds_reviewへ安全停止する。

★リンク設定UIのセレクタについて(未確定・要実機検証)★
本文editorのセレクタ(ProseMirror)とは異なり、選択時に現れるフローティング
ツールバーとリンクURL入力欄の正確なDOM構造は、まだ実機のHTMLダンプで
確認できていない(人間による目視確認のみ)。そのため_apply_product_links()
の候補セレクタは、一般的なリッチテキストエディタのツールバーで使われがちな
role/aria属性に基づく複数候補であり、_fill_body()のProseMirror候補ほど
確度が高いとは言えない。候補が一致しない場合は位置ベースの推測に頼らず
NotePosterErrorで安全停止する(needs_reviewに倒れる)。実機で候補が
一致しなかった場合は、_fill_body()のときと同様、失敗時の診断データ
(スクリーンショット・HTMLダンプ)を元に、実際のDOM構造に基づいてセレクタ
を更新する想定である。

★本文テキスト検証とリンク検証の分離★
_assert_body_matches()は引き続き「見えているテキスト」だけを検証する
(商品リンク導入後も、本文には生URLが一切含まれないため、この検証で
商品カード化が起きていないことも同時に確認できる。カード化が万一発生
すれば、カードの追加テキストによって文字数が期待値からずれ、この検証が
不一致として検出する)。これとは別に、_assert_links_match()が本文editor
内の商品導線部分だけを対象に、リンク(<a>要素)のhref・アンカーテキストを
個別に検証する。本文中に将来ふつうの参考リンク等が入る可能性があるため、
本文editor内の<a>要素の総数を数える検証は行わない(商品導線として
自分自身が生成した「→ 商品を見る」の出現箇所だけをスコープに検証する)。
どちらか一方でも失敗すれば成功扱いにせず、下書き保存の前後両方で
この2つの検証を行う。

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

# 診断データが際限なく増えないよう、記録件数の上限を設ける。
_MAX_DIAG_ENTRIES = 300


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
        選択ツールバー経由でインラインリンクを設定する
        (_apply_product_links)。

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
                lambda: self._apply_product_links(page, product_links),
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
            lambda: self._assert_links_match(page, product_links, stage="保存前"),
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
            lambda: self._assert_links_match(page, product_links, stage="保存後"),
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

    def _apply_product_links(self, page: Page, product_links: list[ProductLink]) -> None:
        """本文末尾の商品導線セクションにある「→ 商品を見る」だけに、
        対応するURLをインラインリンクとして設定する。

        本文editorには既に build_product_links_trailer() が生成した
        プレーンテキスト(ECの生URLを含まない)が入力済みであることが前提。
        「→ 商品を見る」という固定文言の、N番目の出現をproduct_links[N]と
        対応付ける。この出現数と product_links の件数が一致しない場合
        (人間が書いた本文に偶然同じ文言が含まれていた等)は、どの出現が
        どのリンクに対応するか一意に定まらないため、推測でリンクを設定
        せずNotePosterErrorで安全停止する。
        """
        if not product_links:
            return

        occurrences = page.get_by_text(_PRODUCT_LINK_TEXT, exact=True)
        actual_count = occurrences.count()
        if actual_count != len(product_links):
            self._capture_failure(page, "商品導線リンク設定")
            raise NotePosterError(
                f"商品導線のリンク対象テキスト('{_PRODUCT_LINK_TEXT}')が本文中に"
                f"{actual_count}件見つかりましたが、期待した件数は"
                f"{len(product_links)}件でした。本文中に同じ文字列が意図せず"
                "含まれている可能性があり、どの出現がどのリンクに対応するか"
                "一意に定まらないため、誤ったリンク設定を避けて処理を"
                "中断します。"
            )

        for index, link in enumerate(product_links):
            self._set_link_on_text_occurrence(page, occurrences.nth(index), link)

    def _set_link_on_text_occurrence(
        self, page: Page, target: Locator, link: ProductLink
    ) -> None:
        """指定したテキスト要素を選択し、noteの選択ツールバーからリンクを設定する。

        note.comのProseMirrorエディタでは、本文中の文字列を選択すると
        フローティングツールバーが表示され、その中のリンク機能で選択範囲
        だけにインラインリンクを設定できる(URLを商品カードへ変換せずに
        済む方式であることを人間が実機で確認済み)。ただし、このツール
        バー自体の正確なDOM構造(role/aria/class名)は、本文editorの
        ProseMirror要素とは異なりまだ実機のHTMLダンプで確認できていない。
        そのため以下の候補セレクタは一般的なリッチテキストエディタの
        選択ツールバーで使われがちなrole/aria属性に基づく best-effort な
        ものであり、_fill_body()のProseMirror候補ほどの確度は無い。

        候補が一致しない場合は位置ベースの推測(例: ツールバー内の最初の
        ボタン)に一切フォールバックせず、NotePosterErrorで安全停止する
        (needs_reviewに倒れる)。実機で候補が一致しなかった場合は、
        _fill_body()のときと同様、失敗時の診断データを元に実際のDOM構造
        に基づいてセレクタを更新する。
        """
        target.select_text()

        link_button_candidates = [
            ("role=button name=リンク", page.get_by_role("button", name=re.compile("リンク"))),
            (
                "role=button name=link(英語UI保険)",
                page.get_by_role("button", name=re.compile("link", re.IGNORECASE)),
            ),
            ("aria-label*=リンク", page.locator('[aria-label*="リンク"]')),
        ]
        link_button = self._resolve_locator(
            page, link_button_candidates, step_name="リンク設定ボタン", timeout_ms=3000
        )
        self._assert_not_publish_action(link_button)
        link_button.click()

        url_input_candidates = [
            ("css input[type=url]", page.locator('input[type="url"]')),
            ("placeholder*=URL", page.get_by_placeholder(re.compile("URL", re.IGNORECASE))),
            (
                "role=textbox name=URL/リンク",
                page.get_by_role("textbox", name=re.compile("URL|リンク", re.IGNORECASE)),
            ),
        ]
        url_input = self._resolve_locator(
            page, url_input_candidates, step_name="リンクURL入力欄", timeout_ms=3000
        )
        url_input.click()
        url_input.press_sequentially(link.url, delay=10)
        url_input.press("Enter")
        try:
            url_input.wait_for(state="hidden", timeout=3000)
        except PlaywrightTimeoutError:
            # 閉じたことを確認できなくても、成否は後続のread-back検証
            # (_assert_links_match)で判断するため、ここでは中断しない。
            pass

    def _assert_links_match(
        self,
        page: Page,
        product_links: list[ProductLink],
        *,
        stage: str,
    ) -> None:
        """商品導線セクションのリンクが、意図した通りに設定されているかを確認する。

        本文editor内の<a>要素を総数で数える検証は行わない(本文には
        将来ふつうの参考リンク等が入る可能性があるため)。代わりに、
        自分自身が生成した商品導線の各エントリ(label / 「→ 商品を見る」)
        だけをスコープに、以下を個別に確認する。
          - 対応する商品名(label)のテキストが存在すること
          - 商品名自体にはリンクが付いていないこと(誤って商品名まで
            リンクになっていないか)
          - 対応する「→ 商品を見る」のテキストが存在すること
          - そこにちょうど1件の<a>要素があること(余計なリンクが
            生成されていないこと)
          - その<a>要素のテキストが「→ 商品を見る」と完全一致すること
          - その<a>要素のhrefが期待したURLと一致すること
        商品導線をこの方法で安全にスコープできない場合(出現数が合わない
        等)も、推測はせずneeds_reviewへ安全停止する。
        """
        if not product_links:
            return

        link_text_occurrences = page.get_by_text(_PRODUCT_LINK_TEXT, exact=True)
        actual_count = link_text_occurrences.count()
        if actual_count != len(product_links):
            self._capture_failure(page, f"商品導線リンク確認_{stage}")
            raise NotePosterError(
                f"商品導線リンクの確認({stage})で、リンク対象テキスト"
                f"('{_PRODUCT_LINK_TEXT}')が{actual_count}件見つかりました"
                f"(期待: {len(product_links)}件)。商品導線を安全にスコープ"
                "できないため処理を中断します。"
            )

        mismatches: list[str] = []
        for index, link in enumerate(product_links):
            label_locator = page.get_by_text(link.label, exact=True)
            if label_locator.count() < 1:
                mismatches.append(f"『{link.label}』: 商品名のテキストが見つかりません")
            else:
                # get_by_text(exact=True)は完全一致テキストを持つ最も内側の
                # 要素を返すため、商品名自体がリンクになっている場合は
                # a要素自身が返る(その子にはa要素が無いため、子要素だけ
                # 見ても誤ってリンク無しと判定してしまう)。
                try:
                    label_tag_name = (
                        label_locator.first.evaluate("el => el.tagName") or ""
                    ).upper()
                except PlaywrightTimeoutError:
                    label_tag_name = ""
                label_anchor_count = (
                    1 if label_tag_name == "A" else label_locator.first.locator("a").count()
                )
                if label_anchor_count != 0:
                    mismatches.append(
                        f"『{link.label}』: 商品名自体に{label_anchor_count}件の"
                        "リンクが付いています(意図しないリンク)"
                    )

            link_text_locator = link_text_occurrences.nth(index)
            # get_by_text(exact=True)は「その完全一致テキストを持つ、最も
            # 内側の要素」を返す。リンク設定前は<p>→ 商品を見る</p>のような
            # 構造で<p>自身が返るが、リンク設定後は<p><a>→ 商品を見る</a></p>
            # のようにa要素の方が内側になるため、a要素自身が返る。そのため
            # 「自分自身がa要素かどうか」でどちらの状態かを判定する。
            try:
                tag_name = (link_text_locator.evaluate("el => el.tagName") or "").upper()
            except PlaywrightTimeoutError:
                tag_name = ""
            if tag_name == "A":
                anchor_count = 1
                anchor = link_text_locator
            else:
                anchors = link_text_locator.locator("a")
                anchor_count = anchors.count()
                anchor = anchors.first if anchor_count >= 1 else None
            if anchor_count != 1 or anchor is None:
                mismatches.append(
                    f"『{link.label}』: 「{_PRODUCT_LINK_TEXT}」のリンク要素が"
                    f"{anchor_count}件でした(期待: 1件)"
                )
                continue
            try:
                actual_text = (anchor.inner_text() or "").strip()
            except PlaywrightTimeoutError:
                actual_text = ""
            actual_href = anchor.get_attribute("href") or ""
            if actual_text != _PRODUCT_LINK_TEXT:
                mismatches.append(
                    f"『{link.label}』: リンクのテキストが{actual_text!r}でした"
                    f"(期待: {_PRODUCT_LINK_TEXT!r})"
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
