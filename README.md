# PDF-editor

Windows で使える、無料の PDF エディタです。PDF の中身を、手元の PC の中だけで編集できます。

## この版（v0.1.0）でできること

- PDF を開いて見る（拡大・縮小、ページ送り、ドラッグ＆ドロップで開く）
- ページの並べ替え（サムネイルをドラッグ）、削除、空白ページの追加、別の PDF からのページ挿入、回転
- 操作の取り消し（元に戻す・やり直し）
- 上書き保存、名前を付けて保存

まだできないこと（今後の版で対応する予定です）：文字の打ち替え、テキスト・画像・図形の追加、黒塗り削除、ヘッダー・フッターの追加と変更。

## 使い方

1. [Releases ページ](https://github.com/admintokage/PDF-editor/releases)から最新の `PDF-editor.zip` をダウンロードします。
2. zip を右クリックして「すべて展開」し、できた `PDF-editor` フォルダを好きな場所に置きます（インストールは不要です）。
3. フォルダの中の `PDF-editor.exe` をダブルクリックします。PDF を `PDF-editor.exe` にドラッグしても開けます。

### 「Windows によって PC が保護されました」と出たとき

署名を付けていないため、初回だけ出ることがあります。画面の「詳細情報」をクリックし、出てきた「実行」を押してください。

会社などの管理された PC で起動できない場合は、PC の管理者に相談してください。

## 安心して使うために

- PDF の内容を外部に送ることはありません。通信は一切行わず、すべてお使いの PC の中で処理します。
- 上書き保存するとき、最初の 1 回だけ元のファイルを `ファイル名.bak.pdf` として残します。
- 保存に失敗しても、元のファイルは壊れないようにしてあります。

## ライセンスとソース

GNU Affero General Public License v3.0 (AGPL-3.0)。全文は [LICENSE](LICENSE) にあります。ソースコードはこのリポジトリ（https://github.com/admintokage/PDF-editor ）にあります。

- [要件定義書](docs/superpowers/specs/2026-11-13-pdf-editor-requirements.md)

## 開発者向け

Python 3.12 と [uv](https://docs.astral.sh/uv/) が必要です。

```powershell
uv sync                         # 依存関係を入れる
uv run python -m pdfeditor      # 起動する（PDF のパスを引数に渡すと開く）
uv run pytest                   # テスト（画面なしの通常テスト）
uv run pytest -m perf           # 性能テスト
```

配布物（`dist/PDF-editor.zip`）の作り方と確認：

```powershell
powershell -ExecutionPolicy Bypass -File packaging/build.ps1 -Target app
uv run pytest -m dist           # 作った配布物を確かめる（画面は出さない）
```

`PDF-editor.exe --self-test <pdf>` は、画面を出さずに PDF を開く・描画・保存を試して終了コードで結果を返します。`--startup-benchmark` は、起動からウィンドウが最初に描かれるまでのミリ秒を出力します。どちらも `--result-file <パス>` を付けると結果をファイルにも書きます。
