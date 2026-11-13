# タブとページの切り取り・貼り付け Implementation Plan

> **For agentic workers:** この計画は 1 つのタスクとして実装する。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1 つのウィンドウで複数の PDF をタブで開き、ページをコピー・切り取りして、同じ PDF や別のタブの PDF に貼り付けられるようにする。

**Architecture:** いま `MainWindow` が直接持っている「1 文書ぶんの部品」（文書、元に戻す履歴、描画サービス、ページ表示、サムネイル、ページ操作、保存）を `DocumentTab` という 1 つの部品にまとめ、`MainWindow` は `QTabWidget` でそれを並べる。メニューとショートカットは表示中のタブに働く。ページの受け渡しは、アプリ内だけの `PageClipboard`（PDF のバイト列を持つ）で行う。

**Tech Stack:** 既存どおり（Python 3.12 / PyMuPDF 1.28 / PySide6 6.12 / pytest + pytest-qt）。

**Spec:** `docs/superpowers/specs/2026-11-13-pdf-editor-requirements.md` の F-04、4.9a（F-85〜F-85e、F-86〜F-86g）。

## Global Constraints

- 段階 1 の計画（`2026-11-13-stage1-viewer-pages-packaging.md`）の Global Constraints をすべて引き継ぐ（日本語の文言は `pdfeditor/ui/messages.py`、`core` は PySide6 を import しない、通信しない、元のファイルを壊さない、など）。
- 描画サービスの約束：文書を変えてから `invalidate()`、すべて GUI スレッドで。`shutdown()` は `doc.close()` より前。`doc.lock` を持ったまま `shutdown()` しない。
- 文書を変える操作は必ず `push_command(stack, command)` を通す。`PdfEditorError` は `dialogs.show_error` で表示する（モジュール属性を呼ぶ時に引く。テストが差し替える）。
- ウィンドウやタブを閉じる時は、履歴の `cleanChanged` の受け口を切り離してから履歴を消す（コミット 35fec82 と同じ扱い。破棄済みの部品に通知が届くと終了時に例外になる）。
- テストは画面を出さない（offscreen）。本物のモーダルを出すテストは失敗扱い（`tests/ui/conftest.py` の自動 fixture）。
- 利用者の画面に何も出さない。`git add` は変更したパスを明示する。push しない。

## Review Focus

1. コピーした後で元のタブを閉じる、元の PDF を編集・保存する → それでも貼り付けられ、内容はコピーした時点のもの。
2. 同じ PDF に自分のページを貼り付ける、同じ内容を 3 回続けて貼り付ける → 毎回正しい位置に入り、それぞれ元に戻せる。
3. 切り取り → 貼り付け → 切り取り元で「元に戻す」→ ページが元の PDF に戻り、貼り付け先にも残る（複製になる。どちらも壊れない）。
4. 未保存のタブが 3 つある状態でウィンドウを閉じ、2 つ目で「キャンセル」→ 1 つ目だけ閉じ、残りは開いたまま、ウィンドウも開いたまま。
5. 保存の最中にタブを閉じる・切り替える・貼り付ける → 保存が終わるまで受け付けない。
6. パスワード付き PDF からコピーして、パスワードなしの PDF に貼り付けて保存 → 貼り付け先は開けて、貼り付けたページが正しく表示される。

## File Structure

```
pdfeditor/core/document.py        export_pages を追加
pdfeditor/ui/document_tab.py      新規：1 文書ぶんの部品（表示、サムネイル、履歴、ページ操作、保存）
pdfeditor/ui/page_clipboard.py    新規：アプリ内のページ用クリップボード
pdfeditor/ui/page_commands.py     CutPagesCommand、PastePagesCommand を追加
pdfeditor/ui/main_window.py       タブの入れ物、メニュー、開く流れ、閉じる流れだけに縮める
pdfeditor/ui/messages.py          文言を追加
pdfeditor/app.py                  引数の PDF をすべて開く
tests/core/test_export_pages.py   新規
tests/ui/test_tabs.py             新規
tests/ui/test_page_clipboard.py   新規
tests/helpers.py                  open_window などをタブに合わせる
README.md, packaging/README.txt   できることに追記
```

### Task 1: タブとページの切り取り・貼り付け

**Interfaces（Produces）:**

- `PdfDocument.export_pages(indices: list[int]) -> bytes`：指定ページだけを、番号の小さい順に含む新しい PDF のバイト列。`self.lock` の中で作る。範囲外は `IndexError`、空・重複は `ValueError`。元の文書は変えない。暗号化された文書からでも、暗号化なしのバイト列を返す（アプリ内のメモリにしか置かない）。指定していないページの内容を含めない。入れ子のページツリー（継承された MediaBox / Rotate / Resources）でも、取り出したページの大きさ・向き・描画が元と同じ。
- `page_clipboard.PageClipboard(QObject)`：`set_pages(data: bytes, count: int) -> None`、`data() -> bytes | None`、`count() -> int`、`clear() -> None`、シグナル `changed()`。`page_clipboard.clipboard() -> PageClipboard`（アプリに 1 つ）。テスト用に、各テストの後で `clear()` する自動 fixture を `tests/ui/conftest.py` に足す。
- `page_commands.CutPagesCommand(doc, indices, doc_changed=None)`（text: `"ページの切り取り"`。動作は `DeletePagesCommand` と同じ）、`PastePagesCommand(doc, data: bytes, at: int, doc_changed=None)`（text: `"ページの貼り付け"`。動作は `InsertPdfCommand` と同じで、パスワードは使わない）。
- `document_tab.DocumentTab(QWidget)`：
  - 属性 `doc`, `undo_stack`, `render_service`, `page_view`, `thumbnails`（左にサムネイル、右にページ表示の `QSplitter`）
  - シグナル `document_changed()`, `modified_changed(bool)`, `title_changed()`
  - `title() -> str`（ファイル名）、`tool_tip() -> str`（フルパス）、`is_modified() -> bool`
  - ページ操作：`delete_selected_pages()`, `insert_blank_page()`, `insert_pdf_pages()`, `rotate_selected(delta)`, `copy_selected_pages() -> bool`, `cut_selected_pages() -> bool`, `paste_pages() -> bool`
  - `save() -> bool`, `save_as() -> bool`（いまの `MainWindow` の保存の流れを移す。保存中は `saving_changed(bool)` を出し、ウィンドウ全体が操作を止める）
  - `set_active(active: bool) -> None`：表示していない間は、ページ表示とサムネイルが持つ画像を手放し、描画サービスのキャッシュを空にする。表示に戻ったら見えている分を頼み直す（F-85e）。
  - `confirm_close() -> bool`：未保存なら `dialogs.ask_unsaved`。`"save"` で保存に失敗、または `"cancel"` なら False。
  - `shutdown() -> None`：受け口の切り離し、履歴の消去、描画サービスの停止、文書を閉じる。2 回呼んでも安全。
- `MainWindow`：
  - `tab_widget: QTabWidget`（タブは閉じるボタン付き、ドラッグで並べ替え可、`documentMode`）。タブが 0 個の時は、いまの空の案内を出す。
  - `tabs() -> list[DocumentTab]`、`current_tab() -> DocumentTab | None`
  - `open_path(path) -> bool`：新しいタブで開いて、そのタブに切り替える。すでに開いているファイルは、そのタブ（別ウィンドウにあればそのウィンドウ）に切り替えるだけ。`handle_dropped_paths(paths)` と `app.main` は、渡された PDF をすべて開く。
  - `close_tab(index) -> bool`、`close_current_tab() -> bool`（Ctrl+W）
  - 既存のテストと `--self-test` 以外の呼び出しのために、`doc`, `undo_stack`, `render_service`, `page_view`, `thumbnails` を「表示中のタブのもの（タブがなければ None）」を返すプロパティとして残す。`delete_selected_pages()` などのページ操作と `save()` / `save_as()` も、表示中のタブへ渡す形で残す。
  - 元に戻す・やり直しのメニューは `QUndoGroup` で表示中のタブの履歴に付け替える。
  - ページメニューとサムネイルの右クリックに「コピー（Ctrl+C）」「切り取り（Ctrl+X）」「貼り付け（Ctrl+V）」を足す。コピー・切り取りは選択がある時、貼り付けはクリップボードにページがあり文書が開いている時だけ有効。ショートカットはサムネイル一覧にフォーカスがある時に働く。
  - ウィンドウの題名は「表示中のタブのファイル名[*] - PDF-editor」。タブの見出しは未保存なら末尾に `*`。
  - `closeEvent`：保存中は閉じない。未保存のタブを左から順に、そのタブへ切り替えてから `confirm_close()`。True ならそのタブを閉じて次へ、False ならそこで止めてウィンドウは閉じない（F-85d）。全部閉じられたらウィンドウを閉じる。
  - `MainWindow.instances` はそのまま（通常は 1 つ）。

**Steps:**

- [ ] **Step 1: `export_pages` のテストを書く（失敗を確かめる）→ 実装 → 通す**

  `tests/core/test_export_pages.py`：
  - `big_pdf(5)` から `[3, 1]` を取り出すと 2 ページで、ラベルは `[2, 4]`。元の文書は 5 ページのまま、`snapshot` が変わらない。
  - 取り出したバイト列に、指定していないページの文字（`"Page 1"`, `"Page 3"`, `"Page 5"`）が、どのオブジェクトにも展開後のストリームにも含まれない。
  - `nested_pdf` から 1 ページずつ取り出し、大きさ・向き・描画のハッシュが元のページと同じ。
  - `user_pw_pdf` をパスワードで開いて取り出したバイト列は、パスワードなしで開ける。
  - 回転したページ、注釈のあるページ（`toc_links_pdf` の 3 ページ目）は、回転と注釈を保つ。
  - `[]`、`[0, 0]` は `ValueError`、`[9]` は `IndexError`。

- [ ] **Step 2: `PageClipboard` と 2 つのコマンドのテスト → 実装**

  - `set_pages` で `changed` が出る。`clear` で `data()` が None、`count()` が 0。
  - `PastePagesCommand` を `[0, 1]` の内容で 5 ページの文書の位置 2 に → `[1, 2, a, b, 3, 4, 5]`。undo で元どおり、redo で再び入る。
  - `CutPagesCommand` は undo でページ・リンク・しおり・フォームが戻る（`DeletePagesCommand` のテストと同じ確認を 1 つ）。

- [ ] **Step 3: `DocumentTab` に部品と流れを移す。既存のテストを通したまま**

  `MainWindow` から、文書 1 つぶんの処理（`_attach` 以降の表示の組み立て、ページ操作、保存の流れ、`document_changed` の後始末）を `DocumentTab` に移す。`MainWindow` の互換プロパティとメソッドを付け、`tests/helpers.py` の `open_window` / `select` は今の使い方のまま動くようにする。左のドックはなくし、サムネイルはタブの中の `QSplitter` の左に置く（幅は今と同じ）。
  この段階では挙動を変えない。Run: `uv run pytest -q` Expected: 既存のテストがすべて通る（「2 つ目を開くと新しいウィンドウ」のテストは Step 4 で改める）。

- [ ] **Step 4: タブのテストを書く → 実装**

  `tests/ui/test_tabs.py`：
  - 2 つ開くとタブが 2 つ、`MainWindow.instances` は 1 つのまま。表示中は 2 つ目。`window.doc` は 2 つ目の文書。
  - 同じファイルをもう一度開いても、タブは増えず、そのタブに切り替わる（相対パスや大文字小文字が違っても同じ扱い）。
  - `handle_dropped_paths([a, b, c])` で 3 つ開く。PDF でないものは無視。
  - タブを切り替えると、題名、サムネイルの件数、元に戻すメニューの文言が、そのタブのものになる。タブ A で回転 → タブ B に切り替えると「元に戻す」は無効。A に戻すと有効で、元に戻すと A だけが戻る。
  - 未保存のタブの見出しに `*` が付き、保存すると消える。
  - `close_current_tab()`：未保存なら `ask_unsaved` が呼ばれ、`"cancel"` なら閉じない。`"discard"` で閉じ、タブが 0 になると空の案内が出て、ページ・保存のメニューが無効になる。
  - Review Focus 4（未保存 3 つ、2 つ目でキャンセル）。
  - Review Focus 5（保存中は `close_current_tab`、`open_path`、`paste_pages`、タブ切り替えを受け付けない）。
  - `set_active(False)` の後、そのタブのページ表示とサムネイルが画像を持たず、描画サービスの `cached_bytes()` が 0。戻すと再び描画される（F-85e）。
  - 閉じたタブの描画スレッドが止まっている（Qt の警告が出ない）。
  - `tests/ui/test_shutdown.py` と同じ形で、「タブを 2 つ開き、片方を変更して保存せずにウィンドウを閉じる」を別プロセスで行い、終了時に例外が出ない。
  - `app.main(["prog", a, b])` で 2 つ開く。
  既存の `test_second_open_uses_new_window` は「2 つ目は新しいタブ」に改める。

- [ ] **Step 5: 切り取り・コピー・貼り付けのテストを書く → 実装**

  `tests/ui/test_page_clipboard.py`（`open_window` で 2 つのファイルを 1 つのウィンドウに開く）：
  - A でページ 2, 3 を選んでコピー → B でページ 1 を選んで貼り付け → B は `[1, A2, A3, 2, 3]`、貼り付けたページが選択され、表示がそこへ移る。A は変わらず、未保存の印も付かない。B の「元に戻す」は「ページの貼り付け」で、戻すと元どおり。
  - 切り取り：A から消え、A の「元に戻す」は「ページの切り取り」。B に貼り付け。A で元に戻すと A に戻り、B にも残る（Review Focus 3）。
  - 同じ文書の中で貼り付け、3 回続けて貼り付け（Review Focus 2）。
  - コピー後に A のタブを閉じてから B に貼り付け。コピー後に A を回転・保存してから貼り付けても、コピー時点の内容（Review Focus 1）。
  - 全ページを選んで切り取り → `show_error`、「最後の 1 ページ」の文言、文書もクリップボードも変わらない。
  - クリップボードが空なら貼り付けは無効で、呼んでも何も起きず False。
  - Review Focus 6（パスワード付きからコピーして保存）。
  - 切り取って保存した A のファイルに、切り取ったページの文字がどのオブジェクトにも残らない（F-86g）。
  - メニューの有効・無効：選択なしでコピー・切り取りは無効。コピーすると貼り付けが有効になる。
  - サムネイル一覧にフォーカスがある時に Ctrl+C / Ctrl+X / Ctrl+V のキー操作で動く。

- [ ] **Step 6: 文言と説明を更新する**

  `README.md` と `packaging/README.txt` の「できること」に、複数の PDF をタブで開けること、ページのコピー・切り取り・貼り付けを足す。`messages.py` に文言を足す。

- [ ] **Step 7: 全体を確かめてコミット**

  Run: `uv run pytest -q`、`uv run pytest -m perf -q`、`powershell -ExecutionPolicy Bypass -File packaging/build.ps1 -Target app`、`uv run pytest -m dist -q`
  Expected: すべて通る。`main_window.py` は 400 行以下を目安にし、超える場合は理由を報告に書く。
  画面全体を offscreen で `grab()` した PNG（タブ 2 つ、片方は未保存）を 1 枚、報告と一緒に `.superpowers/sdd/2026-11-13-stage1-viewer-pages-packaging/tabs-window.png` に保存する。
  コミットは意味のまとまりごとに分けてよい。
