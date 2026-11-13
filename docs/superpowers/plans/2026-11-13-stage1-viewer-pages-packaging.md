# PDF-editor 段階 1（表示・保存・ページ操作・配布）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理された PC でも起動でき、PDF を開いて表示し、ページの並べ替え・追加・削除・回転をして、元ファイルを壊さずに保存できる Windows アプリを、zip 配布物として作る。あわせて段階 3（文字の打ち替え）の実現性を試作で確かめる。

**Architecture:** PDF は開く時にファイル全体をメモリに読み、PyMuPDF の文書 1 つを作業用として持つ（ファイルをロックしないため）。PDF を触る処理は Qt に依存しない `pdfeditor.core` に置き、画面は `pdfeditor.ui` に置く。編集はすべて `QUndoCommand` を通し、描画は専用スレッド 1 本で行い、文書へのアクセスは 1 つのロックで直列化する。

**Tech Stack:** Python 3.12 / PyMuPDF 1.28 系 / PySide6 6.12 系 / pytest + pytest-qt / PyInstaller 6（onedir）/ uv

**Spec:** `docs/superpowers/specs/2026-11-13-pdf-editor-requirements.md`

**この計画の範囲:** 要件定義書 10 章の段階 1 と、同章が求める打ち替えの試作。段階 2〜4（テキスト・画像・図形の追加、黒塗り、文字の打ち替え、フォント置き換え、ヘッダー・フッター）は、段階 1 の起動確認と試作の結果を見てから別の計画にする。対象要件は F-01〜F-04、F-80〜F-84、F-100（ページ操作分）、F-110〜F-116、Q-01〜Q-06、Q-10〜Q-13、Q-20、Q-22、Q-23、Q-30、Q-31、および 3 章のパスワード・電子署名の扱い。

## Global Constraints

- Windows 10 / 11（64bit）、日本語版 Windows を前提とする。
- 管理者権限を求めない。追加のランタイムなしで、展開して `PDF-editor.exe` を実行すれば動く。
- ネットワーク通信をするコードを入れない。外部へ送信する依存（解析、更新確認、クラッシュ送信）を入れない。
- 画面の文言はすべて日本語。
- ライセンスは AGPL-3.0。有料の部品を使わない。
- 元のファイルを壊さない。保存は一時ファイルに書き、開けることを確かめてから置き換える。
- 編集していない部分の見た目を変えない。しおり、リンク、注釈、フォームの入力欄は保存後も残す。
- 処理できない場合もアプリは落ちず、理由を日本語で表示する。
- 基準機（メモリ 8GB、SSD）で、起動 3 秒以内（2 回目以降）、100 ページ・20MB の PDF の最初のページ表示 2 秒以内、配布 zip 150MB 以下。
- 元に戻す・やり直しは最低 50 段階。
- コミットメッセージの末尾に `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>` を付ける。
- `core` パッケージは PySide6 を import しない。画像を QImage に変換する処理は `ui` 側に置く。

## Review Focus

要件定義書が明示していないが、利用者が踏みやすい入力・状況。各行のテストは括弧内のタスクに入れてある。

1. 保存先のファイルが Acrobat Reader などで開かれていて書き込めない → 日本語で原因を示し、編集内容は残り、元ファイルは無傷（Task 4）。
2. PDF でないファイル、0 バイト、途中で切れた PDF を開く → 落ちずに「開けない」と表示（Task 2）。
3. 全ページを削除しようとする → 最後の 1 ページは消せず、理由を表示（Task 5）。
4. 開いている文書自身を「別の PDF から挿入」で選ぶ → 正しく複製して挿入される（Task 5）。
5. 500 ページを端から端までスクロールしてもメモリが増え続けない → 描画キャッシュに上限（Task 8、実測は Task 12）。

---

## File Structure

```
pyproject.toml                  依存と pytest 設定
LICENSE                         AGPL-3.0 全文
README.md                       入手・起動手順（SmartScreen の回避手順を含む）
pdfeditor/
  __init__.py                   __version__
  __main__.py                   python -m pdfeditor の入口
  app.py                        QApplication 起動、ログ、未処理例外の表示（Q-05）
  core/
    errors.py                   例外の型と日本語メッセージ
    document.py                 PdfDocument：開く・ページ情報・描画・ページ操作
    safe_save.py                壊さない保存、.bak.pdf
  ui/
    messages.py                 画面文言の定数
    main_window.py              ウィンドウ、メニュー、開く・保存の流れ
    page_commands.py            ページ操作の QUndoCommand
    render_service.py           描画スレッドとキャッシュ
    page_view.py                連続スクロール表示、拡大・縮小
    thumbnail_panel.py          サムネイル一覧、並べ替え、複数選択
    dialogs.py                  パスワード、空白ページ追加、エラー表示
packaging/
  smoke_app.py                  起動確認用の最小アプリ（Q-06）
  build.ps1                     配布 zip を作る
  pdfeditor.spec                PyInstaller 設定
tests/
  conftest.py                   テスト用 PDF を作る fixture
  helpers.py                    テスト共通の関数（下記）
  core/test_document_open.py
  core/test_document_render.py
  core/test_safe_save.py
  core/test_page_ops.py
  ui/test_page_commands.py
  ui/test_main_window.py
  ui/test_page_view.py
  ui/test_thumbnail_panel.py
  ui/test_save_flow.py
  perf/test_performance.py
  packaging/test_dist.py
spikes/text_replace/            試作（使い捨て、テスト対象外）
docs/superpowers/spikes/2026-11-text-replace-findings.md
```

テスト共通の関数は `tests/helpers.py` に置き、最初に使うタスクで作る。

- `labels(doc) -> list[int]`：各ページの文字 `"Page N"` から N の列を返す（空白ページは 0）。Task 5 で作る。
- `snapshot(doc) -> tuple`：`(labels, 各ページの回転, 各ページの大きさを丸めた値)`。Task 6 で作る。
- `make_view(qtbot, pdf_path, size=(800, 1000)) -> PageView`：文書と描画サービスを作って表示済みの `PageView` を返し、終了時に `shutdown` する。Task 9 で作る。
- `open_window(qtbot, pdf_path) -> MainWindow`：開いて表示済みのウィンドウを返す。閉じる時の確認は出さない設定にする。Task 10 で作る。
- `select(window, indices: list[int]) -> None`：サムネイル一覧で指定ページを選択する。Task 10 で作る。
- `lock_file(path)`：コンテキストマネージャ。`open(path, "rb")` で開いたままにする（Windows では、開かれているファイルは置き換えられない。Acrobat Reader で開いている状態の再現）。Task 4 で作る。

---

### Task 1: 開発環境と、起動確認用の最小配布物（Q-06）

要件定義書は、機能を作る前に管理された PC で起動できるかを確かめるよう求めている。このタスクの成果物は、依頼者が配布先 PC で試す zip である。

**Files:**
- Create: `pyproject.toml`, `LICENSE`, `.gitignore`, `pdfeditor/__init__.py`, `packaging/smoke_app.py`, `packaging/build.ps1`, `packaging/pdfeditor.spec`
- Test: `tests/packaging/test_dist.py`

**Interfaces:**
- Produces: `pdfeditor.__version__: str`（初期値 `"0.1.0"`）。`packaging/build.ps1 -Target smoke|app` が `dist/PDF-editor-smoke/` または `dist/PDF-editor/` と、同名の zip を `dist/` に作る。実行ファイル名は smoke が `PDF-editor-smoke.exe`、app が `PDF-editor.exe`。

- [ ] **Step 1: `pyproject.toml` を作る**

`requires-python = ">=3.12,<3.13"`。依存：`pymupdf>=1.28,<1.29`、`PySide6-Essentials>=6.12,<6.13`（Essentials を使い配布物を小さくする）。開発用依存：`pytest`、`pytest-qt`、`pyinstaller>=6.22,<7`、`psutil`。`[tool.pytest.ini_options]` に `testpaths = ["tests"]`、`markers = ["perf: 性能テスト", "dist: 配布物が必要"]`、`addopts = "-m 'not perf and not dist'"`。`uv sync` が通ることを確かめる。

- [ ] **Step 2: `LICENSE` に AGPL-3.0 の全文を置き、`.gitignore` に `.venv/ dist/ build/ __pycache__/ *.spec.bak` を書く**

- [ ] **Step 3: `packaging/smoke_app.py` を書く**

PySide6 のウィンドウを 1 つ開き、「PDF-editor 起動確認」「この画面が出れば起動できています」と、`pymupdf.__doc__` の 1 行目、Windows の版、実行フォルダのパスを表示する。pymupdf で 1 ページの PDF をメモリ上に作って描画し、その画像も表示する（PDF 部品が管理 PC で読み込めるかの確認）。引数 `--self-test` があれば、ウィンドウを出さずに同じ処理をして終了コード 0 で終わる。

- [ ] **Step 4: `packaging/pdfeditor.spec` と `packaging/build.ps1` を書く**

onedir、`console=False`、UPX は使わない（ウイルス対策ソフトの誤検知を増やすため）。`build.ps1` は `uv run pyinstaller` を呼び、`dist/<名前>/` を `Compress-Archive` で zip にする。

- [ ] **Step 5: 配布物のテストを書く**

```python
# tests/packaging/test_dist.py
import subprocess, pathlib, pytest
DIST = pathlib.Path("dist")

@pytest.mark.dist
def test_smoke_exe_self_test_exits_zero():
    exe = DIST / "PDF-editor-smoke" / "PDF-editor-smoke.exe"
    assert subprocess.run([exe, "--self-test"], timeout=60).returncode == 0

@pytest.mark.dist
def test_smoke_runs_from_japanese_path_with_space(tmp_path):
    import shutil
    dst = tmp_path / "日本語 フォルダ" / "PDF-editor-smoke"
    shutil.copytree(DIST / "PDF-editor-smoke", dst)
    assert subprocess.run([dst / "PDF-editor-smoke.exe", "--self-test"], timeout=60).returncode == 0

@pytest.mark.dist
def test_smoke_zip_under_150mb():
    assert (DIST / "PDF-editor-smoke.zip").stat().st_size < 150 * 1024 * 1024
```

- [ ] **Step 6: ビルドして確かめる**

Run: `powershell -File packaging/build.ps1 -Target smoke` の後 `uv run pytest -m dist tests/packaging -v`
Expected: 3 件 PASS

- [ ] **Step 7: コミットし、依頼者に zip を渡す**

`dist/PDF-editor-smoke.zip` を GitHub Releases に `v0.0.1-smoke`（プレリリース）として上げてよいかを依頼者に確認してから上げる。依頼者に、管理された PC で展開・実行し、(a) 画面が出たか、(b) 出た警告の文言、(c) 止められた場合の表示、を教えてもらう。**この結果は Task 12 の配布形式に反映する。止められた場合は Task 2 以降を続けつつ、対処を依頼者と決める（Q-06）。**

---

### Task 2: エラーの型と、PDF を開く

**Files:**
- Create: `pdfeditor/core/__init__.py`, `pdfeditor/core/errors.py`, `pdfeditor/core/document.py`, `tests/conftest.py`
- Test: `tests/core/test_document_open.py`

**Interfaces:**
- Produces:
  - `errors.PdfEditorError(Exception)`：属性 `user_message: str`（日本語、画面にそのまま出す）。
  - 派生：`NotAPdfError`、`PasswordRequiredError`、`WrongPasswordError`、`OwnerPasswordRequiredError`、`SaveFailedError`（属性 `reason: Literal["locked","permission","disk_full","verify_failed","other"]`）、`PageOperationError`。
  - `PdfDocument.open(path: Path, password: str | None = None) -> PdfDocument`
  - `PdfDocument.path: Path | None`、`.page_count: int`、`.is_signed: bool`、`.lock: threading.RLock`、`.fitz: pymupdf.Document`（作業用文書。`lock` を取ってから触る）、`.close() -> None`
  - `conftest.py` の fixture（すべて `Path` を返す。中身は pymupdf で新しく作る）：`simple_pdf`（3 ページ、各ページに `"Page 1"`〜`"Page 3"` の文字）、`mixed_size_pdf`（A4 縦・A4 横・B5）、`toc_links_pdf`（5 ページ、しおり 3 件、2 ページ目から 4 ページ目へのリンク 1 件、3 ページ目に注釈 1 件、1 ページ目にテキスト入力欄 1 件・値 `"入力値"`）、`user_pw_pdf`（開くパスワード `"user"`）、`owner_pw_pdf`（権限パスワード `"owner"` のみ、編集不可）、`signed_flag_pdf`（`SigFlags` が 3）、`big_pdf(pages: int)`（ファクトリ）。

- [ ] **Step 1: 失敗するテストを書く**

```python
# tests/core/test_document_open.py
import pytest
from pdfeditor.core.document import PdfDocument
from pdfeditor.core import errors

def test_open_reads_page_count(simple_pdf):
    assert PdfDocument.open(simple_pdf).page_count == 3

def test_open_does_not_lock_file(simple_pdf):
    doc = PdfDocument.open(simple_pdf)
    simple_pdf.rename(simple_pdf.with_name("renamed.pdf"))  # ロックされていれば PermissionError

def test_open_japanese_path(simple_pdf, tmp_path):
    p = tmp_path / "日本語 フォルダ" / "書類 １.pdf"; p.parent.mkdir(); p.write_bytes(simple_pdf.read_bytes())
    assert PdfDocument.open(p).page_count == 3

@pytest.mark.parametrize("content", [b"", b"hello", b"%PDF-1.7\n1 0 obj\n<<"])
def test_open_rejects_non_pdf(tmp_path, content):
    p = tmp_path / "x.pdf"; p.write_bytes(content)
    with pytest.raises(errors.NotAPdfError) as e:
        PdfDocument.open(p)
    assert "開けません" in e.value.user_message

def test_user_password_flow(user_pw_pdf):
    with pytest.raises(errors.PasswordRequiredError): PdfDocument.open(user_pw_pdf)
    with pytest.raises(errors.WrongPasswordError): PdfDocument.open(user_pw_pdf, "bad")
    assert PdfDocument.open(user_pw_pdf, "user").page_count >= 1

def test_owner_only_restriction_requires_owner_password(owner_pw_pdf):
    with pytest.raises(errors.OwnerPasswordRequiredError): PdfDocument.open(owner_pw_pdf)
    assert PdfDocument.open(owner_pw_pdf, "owner").page_count >= 1

def test_is_signed(signed_flag_pdf, simple_pdf):
    assert PdfDocument.open(signed_flag_pdf).is_signed is True
    assert PdfDocument.open(simple_pdf).is_signed is False
```

- [ ] **Step 2: 実行して失敗を確かめる**

Run: `uv run pytest tests/core/test_document_open.py -v` Expected: import エラーで FAIL

- [ ] **Step 3: `errors.py` と `PdfDocument.open` を実装する**

ファイルを `read_bytes()` で読み、`pymupdf.open(stream=data, filetype="pdf")` で開く。途中で切れた PDF は pymupdf が修復して 0 ページで開くことがあるため、`page_count == 0` も `NotAPdfError` にする。`authenticate()` の戻り値のビット 4 が立てば権限パスワード一致。暗号化されているが開くパスワード不要で、権限が編集不可の場合は `OwnerPasswordRequiredError`。`is_signed` は `get_sigflags() == 3`。

- [ ] **Step 4: 実行して通ることを確かめる** Run: 同上 Expected: PASS

- [ ] **Step 5: コミット** `git commit -m "feat: open PDF with password and error handling"`

---

### Task 3: ページ情報と描画

**Files:**
- Modify: `pdfeditor/core/document.py`
- Test: `tests/core/test_document_render.py`

**Interfaces:**
- Consumes: Task 2 の `PdfDocument`
- Produces:
  - `PdfDocument.page_size(index: int) -> tuple[float, float]`（回転を反映した幅・高さ、単位はポイント）
  - `PdfDocument.page_rotation(index: int) -> int`（0/90/180/270）
  - `@dataclass(frozen=True) RenderedPage: width: int; height: int; stride: int; samples: bytes`（RGB、アルファなし）
  - `PdfDocument.render(index: int, zoom: float) -> RenderedPage`（zoom 1.0 = 72dpi）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_page_size_reflects_rotation(mixed_size_pdf):
    d = PdfDocument.open(mixed_size_pdf)
    w, h = d.page_size(0); assert (round(w), round(h)) == (595, 842)
    w, h = d.page_size(1); assert (round(w), round(h)) == (842, 595)

def test_render_dimensions_follow_zoom(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    r = d.render(0, 2.0)
    assert (r.width, r.height) == (1190, 1684) and len(r.samples) == r.stride * r.height

def test_render_out_of_range_raises(simple_pdf):
    with pytest.raises(IndexError): PdfDocument.open(simple_pdf).render(3, 1.0)

def test_render_is_not_blank(simple_pdf):
    r = PdfDocument.open(simple_pdf).render(0, 1.0)
    assert len(set(r.samples[::997])) > 1
```

- [ ] **Step 2: 失敗を確かめる** Run: `uv run pytest tests/core/test_document_render.py -v`
- [ ] **Step 3: 実装する。**`render` は `self.lock` の中で `page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)` を呼び、`samples` を bytes にコピーして返す。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: コミット** `git commit -m "feat: page size and rendering"`

---

### Task 4: 壊さない保存

**Files:**
- Create: `pdfeditor/core/safe_save.py`
- Modify: `pdfeditor/core/document.py`
- Test: `tests/core/test_safe_save.py`

**Interfaces:**
- Consumes: `PdfDocument.fitz`, `PdfDocument.lock`, `errors.SaveFailedError`
- Produces:
  - `safe_save.write_atomically(data: bytes, target: Path, *, backup: bool) -> None`
  - `safe_save.backup_path(target: Path) -> Path`（`報告.pdf` → `報告.bak.pdf`）
  - `PdfDocument.to_bytes() -> bytes`（`garbage=3, deflate=True`）
  - `PdfDocument.save(target: Path | None = None) -> None`（`None` は上書き。成功後 `self.path = target`）
  - `PdfDocument.backup_done: bool`（この文書で最初の上書き時だけバックアップを作るための印）

手順（F-111）：同じフォルダに `<名前>.tmp-<乱数>.pdf` を書く → それを pymupdf で開き、ページ数が一致することを確かめる → `backup=True` かつ target が存在しバックアップがまだなら、元を `backup_path` に `shutil.copy2` → `os.replace(tmp, target)`。どの段階で失敗しても一時ファイルを消し、target には触れない。名前を付けて保存ではバックアップを作らない。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_save_as_roundtrip(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf); out = tmp_path / "out.pdf"; d.save(out)
    assert PdfDocument.open(out).page_count == 3 and d.path == out

def test_first_overwrite_creates_backup_once(simple_pdf):
    original = simple_pdf.read_bytes()
    d = PdfDocument.open(simple_pdf); d.save(); bak = simple_pdf.with_name(simple_pdf.stem + ".bak.pdf")
    assert bak.read_bytes() == original
    bak.write_bytes(b"marker"); d.save(); assert bak.read_bytes() == b"marker"

def test_save_as_makes_no_backup(simple_pdf, tmp_path):
    PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert not list(tmp_path.glob("*.bak.pdf"))

def test_locked_target_raises_and_keeps_original(simple_pdf):
    original = simple_pdf.read_bytes(); d = PdfDocument.open(simple_pdf)
    with lock_file(simple_pdf):
        with pytest.raises(errors.SaveFailedError) as e: d.save()
    assert e.value.reason == "locked" and "他のソフト" in e.value.user_message
    assert simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))

def test_verify_failure_keeps_original(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()
    monkeypatch.setattr(PdfDocument, "to_bytes", lambda self: b"not a pdf")
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save()
    assert e.value.reason == "verify_failed" and simple_pdf.read_bytes() == original

def test_readonly_folder_reports_permission(simple_pdf, tmp_path, monkeypatch):
    def deny(*a, **k): raise PermissionError(13, "denied")
    monkeypatch.setattr("pathlib.Path.write_bytes", deny)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "permission"

def test_disk_full_reported(simple_pdf, tmp_path, monkeypatch):
    def full(*a, **k): raise OSError(28, "No space left")
    monkeypatch.setattr("pathlib.Path.write_bytes", full)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "disk_full"

def test_toc_links_annots_widgets_survive(toc_links_pdf, tmp_path):
    import pymupdf
    out = tmp_path / "o.pdf"; PdfDocument.open(toc_links_pdf).save(out); f = pymupdf.open(out)
    assert len(f.get_toc()) == 3 and len(f[1].get_links()) == 1
    assert len(list(f[2].annots())) == 1 and next(f[0].widgets()).field_value == "入力値"
```

- [ ] **Step 2: 失敗を確かめる** Run: `uv run pytest tests/core/test_safe_save.py -v`
- [ ] **Step 3: 実装する。**一時ファイルは `Path.write_bytes` で書く。原因の判定は段階で決める：一時ファイルの書き込みで出た `PermissionError` は `"permission"`（そのフォルダに書けない）。一時ファイルが書けた後、バックアップ作成または `os.replace` で出た `PermissionError` は `"locked"`（フォルダには書けるのに置き換えられないのは、他のソフトが開いているため）。errno 28 と winerror 112 は `"disk_full"`。確認用に開いて失敗、またはページ数が違えば `"verify_failed"`。それ以外の `OSError` は `"other"`。`user_message` は原因と対処を 1〜2 文で書く（例：locked →「このファイルは他のソフトで開かれているため保存できません。そのソフトを閉じるか、別の名前で保存してください。」）。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: コミット** `git commit -m "feat: atomic save with verification and backup"`

---

### Task 5: ページ操作（core）

**Files:**
- Modify: `pdfeditor/core/document.py`
- Test: `tests/core/test_page_ops.py`

**Interfaces:**
- Consumes: `PdfDocument.fitz`, `.lock`, `errors.PageOperationError`
- Produces（すべて `lock` の中で実行し、範囲外は `IndexError`）：
  - `move_pages(indices: list[int], to: int) -> None`：`indices` のページを元の相対順のまま取り出し、取り出す前の番号で数えた位置 `to`（0〜page_count）の直前に置く。
  - `page_order_after_move(count: int, indices: list[int], to: int) -> list[int]`（モジュール関数。新しい並びを旧番号の列で返す。`move_pages` と元に戻す処理が共用する）
  - `reorder(order: list[int]) -> None`：`order` は旧番号の順列。`fitz.select(order)` で並べ替える。
  - `delete_pages(indices: list[int]) -> bytes`：削除したページだけを含む PDF のバイト列を返す（元に戻す用）。全ページ削除は `PageOperationError("最後の 1 ページは削除できません。")`。
  - `insert_blank(at: int, width: float, height: float) -> None`
  - `insert_from_bytes(data: bytes, at: int, password: str | None = None) -> int`：挿入したページ数を返す。
  - `insert_from_file(path: Path, at: int, password: str | None = None) -> int`
  - `set_rotation(indices: list[int], rotation: int) -> None`、`rotate_by(indices: list[int], delta: int) -> None`（delta は 90 の倍数）
  - `default_blank_size(at: int) -> tuple[float, float]`：`at-1` のページ（なければ `at`）の大きさ。
  - 定数 `PAGE_SIZES: dict[str, tuple[float, float]] = {"A4 縦": (595, 842), "A4 横": (842, 595), "A3 縦": (842, 1191), "A3 横": (1191, 842), "B5 縦": (516, 729), "B5 横": (729, 516)}`

- [ ] **Step 1: 失敗するテストを書く**

ヘルパー `labels(d)` は各ページの文字（`"Page N"`）から N の列を返す。

```python
def test_page_order_after_move():
    assert page_order_after_move(5, [0], 3) == [1, 2, 0, 3, 4]
    assert page_order_after_move(5, [1, 3], 0) == [1, 3, 0, 2, 4]
    assert page_order_after_move(5, [4], 5) == [0, 1, 2, 3, 4]

def test_move_pages(big_pdf):
    d = PdfDocument.open(big_pdf(5)); d.move_pages([0], 3); assert labels(d) == [2, 3, 1, 4, 5]

def test_reorder_keeps_toc_and_links(toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); d.reorder([4, 3, 2, 1, 0])
    assert len(d.fitz.get_toc()) == 3
    assert d.fitz[3].get_links()[0]["page"] == 1   # 旧 2→4 ページ目へのリンクが、新 4→2 ページ目を指す

def test_delete_returns_restorable_bytes(big_pdf):
    d = PdfDocument.open(big_pdf(5)); removed = d.delete_pages([1, 3]); assert labels(d) == [1, 3, 5]
    d.insert_from_bytes(removed, 1); assert labels(d) == [1, 2, 4, 3, 5]

def test_cannot_delete_all_pages(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.PageOperationError) as e: d.delete_pages([0, 1, 2])
    assert "最後の 1 ページ" in e.value.user_message and d.page_count == 3

def test_insert_blank_default_size_follows_neighbor(mixed_size_pdf):
    d = PdfDocument.open(mixed_size_pdf); w, h = d.default_blank_size(2); d.insert_blank(2, w, h)
    assert d.page_count == 4 and tuple(round(x) for x in d.page_size(2)) == (842, 595)

def test_insert_from_file_at_position(simple_pdf, big_pdf):
    d = PdfDocument.open(big_pdf(5)); n = d.insert_from_file(simple_pdf, 2)
    assert n == 3 and labels(d) == [1, 2, 1, 2, 3, 3, 4, 5]

def test_insert_document_into_itself(simple_pdf):
    d = PdfDocument.open(simple_pdf); d.insert_from_file(simple_pdf, 3); assert labels(d) == [1, 2, 3, 1, 2, 3]

def test_insert_encrypted_requires_password(simple_pdf, user_pw_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.PasswordRequiredError): d.insert_from_file(user_pw_pdf, 0)
    assert d.page_count == 3

def test_rotate_by(simple_pdf):
    d = PdfDocument.open(simple_pdf); d.rotate_by([0], 90); d.rotate_by([0], 270)
    assert d.page_rotation(0) == 0
    d.rotate_by([1], -90); assert d.page_rotation(1) == 270

def test_ops_survive_save(big_pdf, tmp_path):
    d = PdfDocument.open(big_pdf(5)); d.move_pages([4], 0); d.delete_pages([1]); d.rotate_by([0], 90)
    out = tmp_path / "o.pdf"; d.save(out); r = PdfDocument.open(out)
    assert labels(r) == [5, 2, 3, 4] and r.page_rotation(0) == 90
```

- [ ] **Step 2: 失敗を確かめる** Run: `uv run pytest tests/core/test_page_ops.py -v`
- [ ] **Step 3: 実装する。**`delete_pages` は、先に新しい空の文書へ `insert_pdf` で対象ページを写してバイト列にし、それから `fitz.delete_pages(indices)` を呼ぶ。`insert_from_file` は対象をバイト列で読んでから開くので、自分自身の挿入も同じ経路で動く。`test_reorder_keeps_toc_and_links` のリンク先が期待どおりにならない場合は、pymupdf の挙動を実測し、リンクの付け替えを自前で行う。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: コミット** `git commit -m "feat: page move, delete, insert, rotate"`

---

### Task 6: ページ操作の「元に戻す」

**Files:**
- Create: `pdfeditor/ui/__init__.py`, `pdfeditor/ui/page_commands.py`, `pdfeditor/ui/messages.py`
- Test: `tests/ui/test_page_commands.py`

**Interfaces:**
- Consumes: Task 5 の全メソッド
- Produces（すべて `QUndoCommand` の派生。`text()` は日本語で「元に戻す」メニューに出る）：
  - `MovePagesCommand(doc, indices: list[int], to: int)`（text: `"ページの移動"`）
  - `DeletePagesCommand(doc, indices: list[int])`（`"ページの削除"`）
  - `InsertBlankCommand(doc, at: int, width: float, height: float)`（`"空白ページの追加"`）
  - `InsertPdfCommand(doc, data: bytes, at: int, password: str | None)`（`"PDF の挿入"`）
  - `RotatePagesCommand(doc, indices: list[int], delta: int)`（`"ページの回転"`）
  - `make_undo_stack(parent) -> QUndoStack`：`setUndoLimit(50)`。
  - 各コマンドは実行・取り消しの後に `doc_changed: Callable[[], None]`（コンストラクタの省略可能な最後の引数）を呼ぶ。

- [ ] **Step 1: 失敗するテストを書く**

```python
@pytest.mark.parametrize("make", [
    lambda d: MovePagesCommand(d, [0, 2], 5),
    lambda d: DeletePagesCommand(d, [1, 3]),
    lambda d: InsertBlankCommand(d, 2, 595, 842),
    lambda d: RotatePagesCommand(d, [0, 4], 90),
])
def test_undo_restores_and_redo_reapplies(qtbot, big_pdf, make):
    d = PdfDocument.open(big_pdf(5)); before = snapshot(d)   # (labels, rotations, sizes)
    stack = make_undo_stack(None); stack.push(make(d)); after = snapshot(d); assert after != before
    stack.undo(); assert snapshot(d) == before
    stack.redo(); assert snapshot(d) == after

def test_insert_pdf_undo(qtbot, big_pdf, simple_pdf):
    d = PdfDocument.open(big_pdf(5)); stack = make_undo_stack(None)
    stack.push(InsertPdfCommand(d, simple_pdf.read_bytes(), 2, None)); assert d.page_count == 8
    stack.undo(); assert labels(d) == [1, 2, 3, 4, 5]

def test_delete_undo_restores_toc(qtbot, toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); toc = d.fitz.get_toc(); stack = make_undo_stack(None)
    stack.push(DeletePagesCommand(d, [0])); stack.undo(); assert d.fitz.get_toc() == toc

def test_undo_limit_is_50(qtbot): assert make_undo_stack(None).undoLimit() == 50
def test_clean_state_tracks_save(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf); s = make_undo_stack(None); s.push(RotatePagesCommand(d, [0], 90))
    assert not s.isClean(); s.setClean(); assert s.isClean(); s.undo(); assert not s.isClean()
```

- [ ] **Step 2: 失敗を確かめる** Run: `uv run pytest tests/ui/test_page_commands.py -v`
- [ ] **Step 3: 実装する。**`DeletePagesCommand` は、ページ削除でしおりが失われる場合に備え、実行前の `get_toc()` を保持し、取り消し時に `set_toc()` で戻す。`MovePagesCommand` の取り消しは `page_order_after_move` の逆順列を `reorder` に渡す。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: コミット** `git commit -m "feat: undoable page commands"`

---

### Task 7: アプリの入口とウィンドウ、開く流れ

**Files:**
- Create: `pdfeditor/__main__.py`, `pdfeditor/app.py`, `pdfeditor/ui/main_window.py`, `pdfeditor/ui/dialogs.py`
- Test: `tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `PdfDocument.open`, `errors.*`, `make_undo_stack`
- Produces:
  - `app.main(argv: list[str]) -> int`：`argv[1]` があればその PDF を開く（F-01 の「実行ファイルへのドロップ」）。
  - `app.install_excepthook(log_dir: Path) -> None`：未処理例外をログに書き、「予期しないエラーが発生しました」とログの場所をダイアログで表示する。ログは `%LOCALAPPDATA%\PDF-editor\logs\pdf-editor.log`（1MB で 3 世代ローテーション）。
  - `MainWindow(QMainWindow)`：`open_path(path: Path) -> bool`、`handle_dropped_paths(paths: list[Path]) -> None`、属性 `doc: PdfDocument | None`、`undo_stack: QUndoStack`、クラス属性 `instances: list[MainWindow]`（開いている全ウィンドウ。閉じたら外す）、シグナル `document_changed()`（ページ構成が変わった）と `document_loaded()`。
  - `dialogs.ask_password(parent, kind: Literal["user","owner"]) -> str | None`、`dialogs.show_error(parent, err: PdfEditorError) -> None`
  - メニュー：ファイル（開く Ctrl+O、上書き保存 Ctrl+S、名前を付けて保存 Ctrl+Shift+S、終了）、編集（元に戻す Ctrl+Z、やり直し Ctrl+Y）、ページ、表示。保存・ページ・表示の中身は Task 9〜11 が足す。
  - 既に文書を開いているウィンドウで別の PDF を開いた場合は、新しいウィンドウ（同じプロセス内の別の `MainWindow`）で開く（F-04）。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_open_path_loads_document(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    with qtbot.waitSignal(w.document_loaded): assert w.open_path(simple_pdf)
    assert w.doc.page_count == 3 and simple_pdf.name in w.windowTitle()

def test_open_bad_file_shows_error_and_does_not_crash(qtbot, tmp_path, monkeypatch):
    shown = []; monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    bad = tmp_path / "x.pdf"; bad.write_bytes(b"nope"); w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(bad) is False and w.doc is None and len(shown) == 1

def test_password_prompt_retries_until_cancel(qtbot, user_pw_pdf, monkeypatch):
    answers = iter(["bad", "user"]); monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: next(answers))
    w = MainWindow(); qtbot.addWidget(w); assert w.open_path(user_pw_pdf) and w.doc is not None

def test_password_cancel_leaves_window_empty(qtbot, user_pw_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: None)
    w = MainWindow(); qtbot.addWidget(w); assert w.open_path(user_pw_pdf) is False

def test_drop_pdf_opens(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w); w.handle_dropped_paths([simple_pdf]); assert w.doc.page_count == 3

def test_second_open_uses_new_window(qtbot, simple_pdf, mixed_size_pdf):
    w = MainWindow(); qtbot.addWidget(w); w.open_path(simple_pdf); w.open_path(mixed_size_pdf)
    assert w.doc.path == simple_pdf and len(MainWindow.instances) == 2

def test_excepthook_writes_log(tmp_path, monkeypatch):
    monkeypatch.setattr("pdfeditor.app._show_fatal", lambda msg: None)
    install_excepthook(tmp_path); import sys
    try: raise RuntimeError("boom")
    except RuntimeError: sys.excepthook(*sys.exc_info())
    assert "boom" in (tmp_path / "pdf-editor.log").read_text(encoding="utf-8")
```

- [ ] **Step 2: 失敗を確かめる** Run: `uv run pytest tests/ui/test_main_window.py -v`
- [ ] **Step 3: 実装する。**`handle_dropped_paths(paths: list[Path])` は拡張子 `.pdf` だけを受け、`dragEnterEvent`/`dropEvent` から呼ぶ。電子署名付き（`doc.is_signed`）の場合は、開いた直後に「この PDF には電子署名があります。編集して保存すると署名は無効になります。」を 1 度表示する。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: 手で起動して確かめる** Run: `uv run python -m pdfeditor` Expected: 空のウィンドウが出て、PDF をドロップするとタイトルにファイル名が出る
- [ ] **Step 6: コミット** `git commit -m "feat: app entry, main window, open flows"`

---

### Task 8: 描画サービス

**Files:**
- Create: `pdfeditor/ui/render_service.py`
- Test: `tests/ui/test_page_view.py`（このタスクでは描画サービスのテストだけ書く）

**Interfaces:**
- Consumes: `PdfDocument.render`, `RenderedPage`
- Produces:
  - `RenderService(QObject)`：`__init__(doc, cache_bytes: int = 256 * 1024 * 1024)`
  - `request(index: int, zoom: float, priority: int = 0) -> None`：キャッシュにあれば即 `rendered` を出す。なければ描画スレッドの待ち行列へ。
  - シグナル `rendered(index: int, zoom: float, image: QImage)`
  - `cancel_except(indices: set[int]) -> None`：待ち行列から、指定以外のページを捨てる（速くスクロールした時用）。
  - `invalidate(indices: Iterable[int] | None = None) -> None`：`None` は全消去。ページ構成が変わったら全消去する。
  - `cached_bytes() -> int`、`shutdown() -> None`
  - キャッシュは使った順の古いものから捨て、`cache_bytes` を超えない。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_request_emits_image(qtbot, simple_pdf):
    s = RenderService(PdfDocument.open(simple_pdf))
    with qtbot.waitSignal(s.rendered, timeout=3000) as sig: s.request(0, 1.0)
    idx, zoom, img = sig.args; assert idx == 0 and (img.width(), img.height()) == (595, 842); s.shutdown()

def test_cache_is_bounded_over_500_pages(qtbot, big_pdf):
    s = RenderService(PdfDocument.open(big_pdf(500)), cache_bytes=20 * 1024 * 1024)
    for i in range(500):
        with qtbot.waitSignal(s.rendered, timeout=5000): s.request(i, 1.0)
    assert s.cached_bytes() <= 20 * 1024 * 1024; s.shutdown()

def test_invalidate_forces_rerender(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf); s = RenderService(d)
    with qtbot.waitSignal(s.rendered): s.request(0, 1.0)
    d.rotate_by([0], 90); s.invalidate()
    with qtbot.waitSignal(s.rendered) as sig: s.request(0, 1.0)
    assert sig.args[2].width() == 842; s.shutdown()

def test_cancel_except_drops_queued(qtbot, big_pdf):
    s = RenderService(PdfDocument.open(big_pdf(200))); got = []
    s.rendered.connect(lambda i, z, im: got.append(i))
    for i in range(200): s.request(i, 2.0)
    s.cancel_except({199}); qtbot.waitUntil(lambda: 199 in got, timeout=10000)
    assert len(got) < 200; s.shutdown()
```

- [ ] **Step 2: 失敗を確かめる**
- [ ] **Step 3: 実装する。**`QThread` 1 本と待ち行列。`RenderedPage.samples` から `QImage(..., Format_RGB888).copy()` を作る（bytes の寿命に依存しないよう copy する）。描画中に文書が変わった結果は、`invalidate` のたびに増やす世代番号で見分けて捨てる。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: コミット** `git commit -m "feat: background render service with bounded cache"`

---

### Task 9: 連続スクロール表示と拡大・縮小

**Files:**
- Create: `pdfeditor/ui/page_view.py`
- Modify: `pdfeditor/ui/main_window.py`（中央に配置、表示メニュー）
- Test: `tests/ui/test_page_view.py`

**Interfaces:**
- Consumes: `RenderService`, `PdfDocument.page_size`, `MainWindow.document_changed`
- Produces:
  - `PageView(QAbstractScrollArea)`：`set_document(doc, service)`、`reload_layout()`（ページ構成が変わった時）、属性 `doc`、`service`。`MainWindow` は `page_view: PageView` と `render_service: RenderService` を持つ。
  - `zoom: float`、`set_zoom(z: float)`（0.1〜8.0 に収める）、`zoom_in()`、`zoom_out()`（×1.25、÷1.25）、`fit_width()`、`fit_page()`
  - `current_page() -> int`（画面中央にあるページ）、`scroll_to_page(index: int)`
  - `visible_pages() -> list[int]`
  - シグナル `current_page_changed(int)`、`zoom_changed(float)`
  - ページ間の隙間は 12px。描画が届くまでは白い紙の枠を出す。画面の表示倍率（devicePixelRatio）を掛けた解像度で描画を頼む。
  - 表示メニュー：拡大 Ctrl++、縮小 Ctrl+-、幅に合わせる Ctrl+1、ページ全体 Ctrl+0。Ctrl+ホイールで拡大・縮小。
  - 後の段階がページ上の編集枠を重ねられるよう、`page_rect(index: int) -> QRectF`（ビュー座標）と `view_to_page(pos: QPointF) -> tuple[int, float, float] | None`（ページ番号とページ内のポイント座標）を公開する。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_only_nearby_pages_requested(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(500)); s = RenderService(d); asked = []
    orig = s.request; s.request = lambda i, z, priority=0: (asked.append(i), orig(i, z, priority))
    v = PageView(); qtbot.addWidget(v); v.resize(800, 1000); v.set_document(d, s); v.show(); qtbot.wait(200)
    assert max(asked) < 10; s.shutdown()

def test_scroll_to_page_changes_current(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    with qtbot.waitSignal(v.current_page_changed): v.scroll_to_page(30)
    assert v.current_page() == 30 and 30 in v.visible_pages()

def test_fit_width_uses_viewport(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf, size=(1000, 800)); v.fit_width()
    assert abs(v.page_rect(0).width() - (v.viewport().width() - 24)) <= 2

def test_zoom_clamped(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf); v.set_zoom(100); assert v.zoom == 8.0; v.set_zoom(0); assert v.zoom == 0.1

def test_zoom_keeps_current_page(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50)); v.scroll_to_page(20); v.zoom_in(); assert v.current_page() == 20

def test_view_to_page_roundtrip(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf); r = v.page_rect(0)
    idx, x, y = v.view_to_page(r.center()); assert idx == 0 and abs(x - 297.5) < 2 and abs(y - 421) < 2
    assert v.view_to_page(QPointF(r.left() - 5, r.top())) is None

def test_layout_follows_page_changes(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(5)); v.doc.delete_pages([0, 1]); v.service.invalidate(); v.reload_layout()
    assert v.page_rect(2).isValid() and not v.page_rect(3).isValid()
```

- [ ] **Step 2: 失敗を確かめる**
- [ ] **Step 3: 実装する。**各ページの縦位置の累積表を持ち、スクロール位置から二分探索で見えているページを求める。見えているページと前後 1 ページだけを `request` し、それ以外は `cancel_except` で捨てる。拡大・縮小の直後は、手元の旧倍率の画像を引き伸ばして出し、新しい描画が届いたら差し替える（Q-11）。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: 手で確かめる。**`uv run python -m pdfeditor` で 500 ページの PDF（`uv run python -c "from tests.conftest import make_big; make_big('big.pdf', 500)"`）を開き、端から端までスクロールして引っかかりがないこと、タスクマネージャーでメモリが増え続けないこと。
- [ ] **Step 6: コミット** `git commit -m "feat: continuous page view with zoom"`

---

### Task 10: サムネイル一覧とページ操作の画面

**Files:**
- Create: `pdfeditor/ui/thumbnail_panel.py`
- Modify: `pdfeditor/ui/main_window.py`（左ドック、ページメニュー）、`pdfeditor/ui/dialogs.py`
- Test: `tests/ui/test_thumbnail_panel.py`

**Interfaces:**
- Consumes: Task 6 のコマンド、`RenderService`、`PageView.scroll_to_page`、`PageView.current_page_changed`、`PAGE_SIZES`、`default_blank_size`
- Produces:
  - `ThumbnailPanel(QListView)`：`set_document(doc, service, undo_stack)`、`selected_pages() -> list[int]`、`refresh()`、`move_rows(indices: list[int], to: int) -> None`（ドロップ処理が呼ぶ。`MovePagesCommand` を積む）。`MainWindow` は `thumbnails: ThumbnailPanel` を持つ。
  - シグナル `page_activated(int)`（クリックで本体表示がそのページへ移動）
  - サムネイルは幅 120px、下にページ番号。複数選択（Ctrl・Shift）可。ドラッグで並べ替え（F-80）。
  - `MainWindow` のページメニューと、サムネイルの右クリックメニュー：削除（Delete）、空白ページを追加…、PDF から挿入…、右に回転（Ctrl+R）、左に回転（Ctrl+Shift+R）。
  - `dialogs.ask_blank_page(parent, default_size: tuple[float,float]) -> tuple[float,float] | None`：選択肢は「前後のページに合わせる」（既定）と `PAGE_SIZES` の各項目。
  - `MainWindow.delete_selected_pages()`、`.insert_blank_page()`、`.insert_pdf_pages()`、`.rotate_selected(delta: int)`：いずれも `undo_stack.push(...)` を通す。挿入位置は、選択中の最後のページの次（選択がなければ末尾）。
  - `PdfEditorError` は `dialogs.show_error` で表示し、文書は変えない。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_thumbnail_count_matches_pages(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(7)); assert w.thumbnails.model().rowCount() == 7

def test_drop_reorders_via_undo_stack(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); w.thumbnails.move_rows([0], 3)   # ドロップ処理が呼ぶ内部メソッド
    assert labels(w.doc) == [2, 3, 1, 4, 5] and w.undo_stack.undoText() == "ページの移動"
    w.undo_stack.undo(); assert labels(w.doc) == [1, 2, 3, 4, 5]

def test_delete_selected(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); select(w, [1, 2]); w.delete_selected_pages()
    assert labels(w.doc) == [1, 4, 5] and w.thumbnails.model().rowCount() == 3

def test_delete_all_shows_error(qtbot, simple_pdf, monkeypatch):
    shown = []; monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    w = open_window(qtbot, simple_pdf); select(w, [0, 1, 2]); w.delete_selected_pages()
    assert w.doc.page_count == 3 and "最後の 1 ページ" in shown[0].user_message

def test_insert_blank_after_selection(qtbot, big_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page", lambda p, d: (595, 842))
    w = open_window(qtbot, big_pdf(3)); select(w, [0]); w.insert_blank_page()
    assert w.doc.page_count == 4 and labels(w.doc)[0] == 1 and w.doc.fitz[1].get_text().strip() == ""

def test_insert_pdf_with_password_prompt(qtbot, simple_pdf, user_pw_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *a, **k: (str(user_pw_pdf), ""))
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: "user")
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages(); assert w.doc.page_count > 3

def test_rotate_updates_view(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0]); before = w.page_view.page_rect(0).width()
    w.rotate_selected(90); assert w.doc.page_rotation(0) == 90 and w.page_view.page_rect(0).width() != before

def test_click_thumbnail_scrolls_view(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(30)); w.thumbnails.page_activated.emit(20); assert w.page_view.current_page() == 20

def test_title_shows_unsaved_mark(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90); assert w.isWindowModified()
```

- [ ] **Step 2: 失敗を確かめる**
- [ ] **Step 3: 実装する。**一覧のモデルは `QAbstractListModel`。サムネイル画像は `RenderService` に低い優先度・zoom `120 / ページ幅` で頼む。`document_changed` で `service.invalidate()`、`page_view.reload_layout()`、`thumbnails.refresh()` を呼ぶ。`undo_stack.cleanChanged` を `setWindowModified` につなぐ。
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: 手で確かめる。**ドラッグでの並べ替え、複数選択しての削除、Ctrl+Z / Ctrl+Y。
- [ ] **Step 6: コミット** `git commit -m "feat: thumbnail panel and page operations UI"`

---

### Task 11: 保存の画面

**Files:**
- Modify: `pdfeditor/ui/main_window.py`, `pdfeditor/ui/dialogs.py`
- Test: `tests/ui/test_save_flow.py`

**Interfaces:**
- Consumes: `PdfDocument.save`, `errors.SaveFailedError`, `undo_stack.setClean`
- Produces:
  - `MainWindow.save() -> bool`、`.save_as() -> bool`
  - `dialogs.ask_unsaved(parent, name: str) -> Literal["save","discard","cancel"]`
  - `dialogs.show_save_error(parent, err: SaveFailedError) -> Literal["save_as","cancel"]`：`user_message` を出し、「別の場所に保存」ボタンを付ける（F-113）。
  - 保存は別スレッドで行い、1 秒を超えたら「保存しています…」の進行表示を出す（Q-13）。保存中はページ操作を受け付けない。
  - `closeEvent`：未保存なら `ask_unsaved`。`"cancel"` と、保存を選んで失敗した場合は閉じない。
  - 保存に成功したら `undo_stack.setClean()`。失敗しても文書と元に戻す履歴はそのまま残す。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_save_clears_modified(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.save() and not w.isWindowModified()
    assert PdfDocument.open(simple_pdf).page_rotation(0) == 90
    assert simple_pdf.with_name(simple_pdf.stem + ".bak.pdf").exists()

def test_save_as_changes_title(qtbot, simple_pdf, tmp_path, monkeypatch):
    out = tmp_path / "別名.pdf"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    w = open_window(qtbot, simple_pdf); assert w.save_as() and "別名.pdf" in w.windowTitle() and out.exists()

def test_save_failure_keeps_edits_and_offers_save_as(qtbot, simple_pdf, tmp_path, monkeypatch):
    out = tmp_path / "逃げ先.pdf"; calls = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: (calls.append(e.reason), "save_as")[1])
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    with lock_file(simple_pdf): assert w.save() is True
    assert calls == ["locked"] and out.exists() and w.doc.page_rotation(0) == 90

def test_save_failure_cancel_stays_modified(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: "cancel")
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    with lock_file(simple_pdf): assert w.save() is False
    assert w.isWindowModified() and w.undo_stack.canUndo()

@pytest.mark.parametrize("answer,closes", [("discard", True), ("cancel", False), ("save", True)])
def test_close_with_unsaved_changes(qtbot, simple_pdf, monkeypatch, answer, closes):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, n: answer)
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.close() is closes

def test_close_without_changes_does_not_ask(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, n: pytest.fail("asked"))
    assert open_window(qtbot, simple_pdf).close()
```

- [ ] **Step 2: 失敗を確かめる**
- [ ] **Step 3: 実装する**
- [ ] **Step 4: 通ることを確かめる**
- [ ] **Step 5: 保存結果を他の閲覧ソフトで確かめる（F-116、Q-22）。**`toc_links_pdf` を開き、並べ替え・削除・回転・空白追加・PDF 挿入をして保存したファイルを、Adobe Acrobat Reader、Microsoft Edge、Google Chrome で開く。エラーや修復の表示が出ないこと、しおりとリンクが働くことを確かめ、結果をコミットメッセージに書く。
- [ ] **Step 6: コミット** `git commit -m "feat: save, save-as, unsaved-changes prompt"`

---

### Task 12: 性能の確認と、配布物

**Files:**
- Modify: `packaging/pdfeditor.spec`, `packaging/build.ps1`, `README.md`, `tests/packaging/test_dist.py`
- Create: `tests/perf/test_performance.py`

**Interfaces:**
- Consumes: Task 1 の `build.ps1`、`app.main`
- Produces: `dist/PDF-editor.zip`。`PDF-editor.exe --self-test <pdf>`：その PDF を開き、最初のページを描画し、一時フォルダに保存して終了コード 0 で終わる（画面は出さない）。`PDF-editor.exe --startup-benchmark`：ウィンドウが最初に描かれた時点で経過ミリ秒を標準出力に書いて終了する。

- [ ] **Step 1: 性能テストを書く**

```python
# tests/perf/test_performance.py   （pytest -m perf で実行）
@pytest.mark.perf
def test_first_page_of_100_pages_20mb_under_2s(big_pdf_20mb):       # 100 ページ、画像入りで約 20MB
    t = time.perf_counter(); d = PdfDocument.open(big_pdf_20mb); d.render(0, 1.5)
    assert time.perf_counter() - t < 2.0

@pytest.mark.perf
def test_500_pages_open_and_reorder(big_pdf):
    p = big_pdf(500); t = time.perf_counter(); d = PdfDocument.open(p); d.move_pages([499], 0); d.render(0, 1.0)
    assert time.perf_counter() - t < 3.0

@pytest.mark.perf
def test_500_pages_save_under_10s(big_pdf, tmp_path):
    d = PdfDocument.open(big_pdf(500)); t = time.perf_counter(); d.save(tmp_path / "o.pdf")
    assert time.perf_counter() - t < 10.0

@pytest.mark.perf
def test_memory_stable_while_scrolling_500_pages(qtbot, big_pdf):
    import psutil; proc = psutil.Process(); v = make_view(qtbot, big_pdf(500))
    for i in range(0, 500, 5): v.scroll_to_page(i); qtbot.wait(20)
    mid = proc.memory_info().rss
    for i in range(499, 0, -5): v.scroll_to_page(i); qtbot.wait(20)
    assert proc.memory_info().rss - mid < 100 * 1024 * 1024
```

`big_pdf_20mb` を `tests/conftest.py` に足す（乱数のノイズ画像を各ページに貼って約 20MB にする）。

- [ ] **Step 2: 実行し、落ちるものを直す** Run: `uv run pytest -m perf -v` Expected: 4 件 PASS。落ちた場合は原因を測ってから直す。

- [ ] **Step 3: 配布物のテストを足す**

```python
@pytest.mark.dist
def test_app_self_test(simple_pdf):
    assert subprocess.run([DIST / "PDF-editor" / "PDF-editor.exe", "--self-test", simple_pdf], timeout=60).returncode == 0

@pytest.mark.dist
def test_app_runs_from_japanese_path_with_space(tmp_path, simple_pdf):   # Q-04
    dst = tmp_path / "日本語 フォルダ" / "PDF-editor"; shutil.copytree(DIST / "PDF-editor", dst)
    assert subprocess.run([dst / "PDF-editor.exe", "--self-test", simple_pdf], timeout=60).returncode == 0

@pytest.mark.dist
def test_second_startup_under_3s():                                       # Q-03
    exe = DIST / "PDF-editor" / "PDF-editor.exe"
    subprocess.run([exe, "--startup-benchmark"], timeout=60)
    out = subprocess.run([exe, "--startup-benchmark"], capture_output=True, text=True, timeout=60).stdout
    assert int(out.strip()) < 3000

@pytest.mark.dist
def test_zip_under_150mb(): assert (DIST / "PDF-editor.zip").stat().st_size < 150 * 1024 * 1024

@pytest.mark.dist
def test_no_network_modules_bundled():
    names = {p.name.lower() for p in (DIST / "PDF-editor").rglob("*")}
    assert not any(n.startswith(("qt6network", "qt6webengine")) for n in names)
```

- [ ] **Step 4: `pdfeditor.spec` を本体用に仕上げ、ビルドして確かめる**

使わない Qt のモジュール（Network、Qml、Quick、WebEngine、Multimedia、Sql など）を除外する。Task 1 の起動確認で依頼者から止められた報告があった場合は、決めた対処をここで反映する。
Run: `powershell -File packaging/build.ps1 -Target app` の後 `uv run pytest -m dist -v` Expected: 全件 PASS

- [ ] **Step 5: `README.md` を利用者向けに書き直す**

入れる内容：できること（段階 1 の範囲）、入手方法（Releases から zip を取り、展開して `PDF-editor.exe` を実行）、「PC が保護されました」と出た場合の手順（「詳細情報」→「実行」）、管理された PC で起動できない場合は管理者に相談すること、PDF を外部に送らないこと、保存時に `.bak.pdf` を作ること、ライセンス（AGPL-3.0）とソースの場所、要件定義書へのリンク。

- [ ] **Step 6: 全テストを実行する** Run: `uv run pytest -v` と `uv run pytest -m "perf or dist" -v` Expected: 全件 PASS
- [ ] **Step 7: コミット** `git commit -m "feat: performance checks, packaged app, user README"`
- [ ] **Step 8: 依頼者に zip を渡す。**GitHub Releases に `v0.1.0` として上げてよいかを確認してから上げ、管理された PC での起動と、実際の PDF での操作を試してもらう。

---

### Task 13: 文字の打ち替えの試作（使い捨て）

段階 3 の計画を立てる前に、要件定義書 8.4a の未確認事項を実測する。このタスクのコードは製品に入れない。テスト駆動では進めず、成果物は調査結果の文書である。Task 2 が終われば、他のタスクと並行して進められる。

**Files:**
- Create: `spikes/text_replace/*.py`, `docs/superpowers/spikes/2026-11-text-replace-findings.md`

- [ ] **Step 1: 試料を作る。**Word で、游明朝・游ゴシック・MS 明朝・メイリオを使った 2 ページの文書（段落 5 つ、太字と色の混在、箇条書き、罫線のある表 1 つ）を作り、PDF に書き出す。Word が使えない場合は依頼者に試料の作成を頼む。中身は新しく書き起こし、実際の書類は使わない。
- [ ] **Step 2: 段落の推定。**`page.get_text("dict")` の行・スパンから段落をまとめる単純な方法を書き、試料の段落 5 つを正しく取れるか、表の中の文字を巻き込まないかを記録する。
- [ ] **Step 3: 文字だけの除去。**1 段落だけを `add_redact_annot` と `apply_redactions(images=0, graphics=0)` で消し、(a) 隣の段落の文字が消えないか、(b) 背景の罫線が残るか、(c) 他の段落の `get_text()` と描画結果（画素比較）が変わらないか、を記録する。
- [ ] **Step 4: 流し込みと禁則。**消した枠に `insert_htmlbox` で日本語の文章を流し込み、行頭に `、。）」` が来る文を作って禁則が働くかを見る。働かない場合、自前で改行位置を決めて `TextWriter` で 1 行ずつ置く方法を試し、手間を見積もる。画面に見せる折り返しと保存結果を一致させる方法（F-12a）もここで決める。
- [ ] **Step 5: フォント。**`C:\Windows\Fonts` の `.ttc`（游ゴシック、MS ゴシック、メイリオ）から書体を指定して埋め込めるか、`subset_fonts()` 後のファイルサイズ、埋め込み許可フラグ（OS/2 表の fsType）の読み方を確かめる。PDF 内のフォント名（`ABCDEF+YuMincho-Regular` のような形）と PC のフォントを照合する方法を決める。
- [ ] **Step 6: 黒塗りの漏れ。**文字、画像、一部だけ掛かる線画、注釈、フォームの入力値、ページ内の埋め込み部品（Form XObject）中の文字のそれぞれに黒塗りを掛け、保存後のファイルから `get_text()` と、ファイルを展開（`mutool clean -d` 相当）しての文字列検索で元の内容が取り出せないかを確かめる。
- [ ] **Step 7: 結果を文書にまとめる。**各項目について「そのまま使える／補う処理が要る（その内容と手間）／できない」を書き、要件定義書の変更が要る点があれば挙げる。
- [ ] **Step 8: コミットし、依頼者に結果を報告する。**段階 2・3 の計画はこの結果を基に作る。

---

## 段階 1 で扱わない要件

次は後の計画で扱う：F-10〜F-18（打ち替え）、F-20〜F-23、F-30〜F-37（フォント）、F-40〜F-42、F-50〜F-54、F-60〜F-65、F-70〜F-78（黒塗り）、F-90〜F-97（ヘッダー・フッター）、F-101〜F-103、F-100 のうちページ操作以外、Q-21 のうち Word・Excel・PowerPoint・Google ドキュメントの実出力を使った確認用 PDF 一式（段階 1 では pymupdf で作った試料を使う）。
