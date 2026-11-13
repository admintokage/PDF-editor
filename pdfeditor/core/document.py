"""PDF 文書の読み込み。Qt には依存しない。"""
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfeditor.core import errors, page_detach, safe_save
from pdfeditor.core.page_detach import DetachedPages

_PERM_OWNER_BIT = 4  # authenticate() の戻り値で、権限パスワード一致を示すビット

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A4 縦": (595, 842), "A4 横": (842, 595),
    "A3 縦": (842, 1191), "A3 横": (1191, 842),
    "B5 縦": (516, 729), "B5 横": (729, 516),
}


def _not_a_pdf(name: str) -> errors.NotAPdfError:
    return errors.NotAPdfError(f"{name}を開けません。PDF ファイルではないか、壊れています。")


def _open_authenticated(
    data: bytes, name: str, password: str | None, *, require_edit: bool
) -> pymupdf.Document:
    """バイト列を PDF として開き、パスワードを確認する。失敗時は閉じて例外を投げる。
    require_edit=False なら、編集が制限された PDF も開く（読み取るだけの用途）。
    PyMuPDF のどんな失敗（ページ数が読めない壊れた PDF、メモリ不足など）も NotAPdfError にする。"""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # MemoryError も含む
        raise _not_a_pdf(name) from exc

    try:
        return _check_opened(doc, name, password, require_edit=require_edit)
    except errors.PdfEditorError:
        doc.close()
        raise
    except Exception as exc:
        doc.close()
        raise _not_a_pdf(name) from exc
    except BaseException:
        doc.close()
        raise


def _check_opened(
    doc: pymupdf.Document, name: str, password: str | None, *, require_edit: bool
) -> pymupdf.Document:
    owner_ok = False
    if doc.needs_pass:
        if password is None:
            raise errors.PasswordRequiredError(f"{name}を開くにはパスワードが必要です。")
        result = doc.authenticate(password)
        if not result:
            raise errors.WrongPasswordError("パスワードが違います。")
        owner_ok = bool(result & _PERM_OWNER_BIT)
    elif password is not None:
        owner_ok = bool(doc.authenticate(password) & _PERM_OWNER_BIT)

    if doc.page_count == 0:
        raise errors.NotAPdfError(f"{name}を開けません。PDF ファイルが壊れています。")
    if require_edit and not owner_ok and not doc.permissions & pymupdf.PDF_PERM_MODIFY:
        raise errors.OwnerPasswordRequiredError(
            f"{name}は編集が制限されています。"
            "権限パスワード（編集用のパスワード）を入力してください。"
        )
    return doc


def page_order_after_move(count: int, indices: list[int], to: int) -> list[int]:
    """indices のページを元の相対順で取り出し、取り出す前の番号で数えた位置 to の直前に置いた
    新しい並びを、旧ページ番号の列で返す。"""
    moved = sorted(indices)
    taken = set(moved)
    rest = [i for i in range(count) if i not in taken]
    pos = to - sum(1 for i in moved if i < to)
    return rest[:pos] + moved + rest[pos:]


@dataclass(frozen=True)
class RenderedPage:
    """描画結果。samples は RGB（アルファなし）で 1 行 stride バイト。"""
    width: int
    height: int
    stride: int
    samples: bytes


def absolute_path(path: Path) -> Path:
    """作業フォルダが変わっても同じファイルを指すよう、絶対パスにする（シンボリックリンクは
    たどらない。たどれない時も失敗しない）。"""
    path = Path(path)
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


class PdfDocument:
    def __init__(self, fitz_doc: pymupdf.Document, path: Path | None,
                 password: str | None = None):
        self.fitz = fitz_doc
        self.path = absolute_path(path) if path is not None else None
        self.lock = threading.RLock()
        self.backup_done = False  # この文書で上書き用バックアップを作ったか
        # 開いた時のパスワード。保存時に、暗号化を保ったまま写しを作り直すのに使う。
        # 記録やログには出さない。
        self._password = password

    def __repr__(self) -> str:  # パスワードを表示しない
        return f"PdfDocument(path={self.path!r})"

    @classmethod
    def open(cls, path: Path, password: str | None = None) -> "PdfDocument":
        path = absolute_path(path)
        try:
            data = path.read_bytes()  # ファイルを掴みっぱなしにしない
        except OSError as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。ファイルが見つからないか、読み取れません。"
            ) from exc
        doc = _open_authenticated(data, f"「{path.name}」", password, require_edit=True)
        return cls(doc, path, password)

    @property
    def page_count(self) -> int:
        with self.lock:
            return self.fitz.page_count

    @property
    def is_signed(self) -> bool:
        """SigFlags の SignaturesExist（ビット 1）が立っていれば署名済みとみなす。"""
        with self.lock:
            flags = self.fitz.get_sigflags()  # AcroForm がなければ -1
        return flags > 0 and bool(flags & 1)

    def _page(self, index: int) -> pymupdf.Page:
        """呼び出し側が self.lock を保持していること。Page は保持せず毎回取得する。"""
        if not 0 <= index < self.fitz.page_count:
            raise IndexError(f"ページ番号が範囲外です: {index}")
        return self.fitz[index]

    def page_size(self, index: int) -> tuple[float, float]:
        """回転を反映した表示上の (幅, 高さ)。単位はポイント。"""
        with self.lock:
            rect = self._page(index).rect
            return rect.width, rect.height

    def page_rotation(self, index: int) -> int:
        with self.lock:
            return self._page(index).rotation

    def render(self, index: int, zoom: float) -> RenderedPage:
        """zoom 1.0 = 72dpi。"""
        with self.lock:
            pix = self._page(index).get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return RenderedPage(pix.width, pix.height, pix.stride, bytes(pix.samples))

    # ---- ページ操作。すべて lock の中で実行し、検証してから変更する ----
    # pymupdf の Page は追加・削除・並べ替えの後に無効になるので、保持せず毎回 _page で取り直す。

    def _check_indices(self, indices: list[int]) -> None:
        """範囲外は IndexError、重複は ValueError。lock を保持して呼ぶ。"""
        for i in indices:
            if not 0 <= i < self.fitz.page_count:
                raise IndexError(f"ページ番号が範囲外です: {i}")
        if len(set(indices)) != len(indices):
            raise ValueError("ページ番号が重複しています。")

    def _check_position(self, at: int) -> None:
        if not 0 <= at <= self.fitz.page_count:
            raise IndexError(f"挿入位置が範囲外です: {at}")

    def _permute(self, order: list[int]) -> None:
        """ページを order（旧ページ番号の順列）の並びにする。lock を保持して呼ぶ。
        Document.select は AcroForm（フォーム）を消すので使わない。ページの参照を
        ページツリーの中で動かすだけの move_page なら、フォーム・しおり・リンクの行き先は
        ページオブジェクトについたまま残る。"""
        current = list(range(self.fitz.page_count))
        for k, wanted in enumerate(order):
            j = current.index(wanted, k)
            if j != k:
                self.fitz.move_page(j, k)  # j にあるページを k の前へ
                current.insert(k, current.pop(j))

    def move_pages(self, indices: list[int], to: int) -> None:
        with self.lock:
            self._check_indices(indices)
            self._check_position(to)
            if not indices:
                return
            self._permute(page_order_after_move(self.fitz.page_count, indices, to))

    def reorder(self, order: list[int]) -> None:
        """order は旧ページ番号の順列。"""
        with self.lock:
            if sorted(order) != list(range(self.fitz.page_count)):
                raise ValueError("並べ替えの指定がページ番号の順列になっていません。")
            self._permute(order)

    def delete_pages(self, indices: list[int]) -> DetachedPages | None:
        """ページを削除し、元に戻すための控え（restore_pages に渡す）を返す。空なら None。
        ページはページツリーから外すだけで、オブジェクトは文書に残る（保存したファイルには
        入らない）。外すページのフォーム部品は AcroForm から外す。"""
        with self.lock:
            self._check_indices(indices)
            if not indices:
                return None
            if len(indices) >= self.fitz.page_count:
                raise errors.PageOperationError("最後の 1 ページは削除できません。")
            order = sorted(indices)
            doomed = set(order)
            keep = [i for i in range(self.fitz.page_count) if i not in doomed]
            page_xrefs = [self._page(i).xref for i in order]
            widgets = [
                xref for i in order for xref, kind, _ in self._page(i).annot_xrefs()
                if kind == pymupdf.PDF_ANNOT_WIDGET
            ]
            before = page_detach.objects_touched_by_delete(self.fitz, keep)
            removals = page_detach.remove_form_fields(self.fitz, widgets)
            try:
                self.fitz.delete_pages(order)
            except BaseException:
                page_detach.restore_form_fields(self.fitz, removals)
                raise
            changed = page_detach.only_changed(self.fitz, before)
            return DetachedPages(order, page_xrefs, removals, changed)

    def restore_pages(self, detached: DetachedPages) -> None:
        """delete_pages で外したページを、同じオブジェクトのまま元の位置に戻す。フォーム部品、
        ほかのページから削除ページへのリンク、しおりの行き先も削除前の形に戻す。
        delete_pages の後に行った変更をすべて元に戻してから呼ぶこと（元に戻すの順序）。"""
        with self.lock:
            page_detach.insert_page_objects(self.fitz, detached.indices, detached.page_xrefs)
            page_detach.restore_form_fields(self.fitz, detached.removals)
            page_detach.restore_objects(self.fitz, detached.changed_objects)

    def insert_blank(self, at: int, width: float, height: float) -> None:
        with self.lock:
            self._check_position(at)
            self.fitz.new_page(pno=at, width=width, height=height)

    def default_blank_size(self, at: int) -> tuple[float, float]:
        """at-1 のページ（なければ at）の大きさ。"""
        with self.lock:
            return self.page_size(at - 1 if at > 0 else min(at, self.fitz.page_count - 1))

    def insert_from_bytes(self, data: bytes, at: int, password: str | None = None) -> int:
        """挿入したページ数を返す。挿入元は読み取るだけなので、編集制限があっても挿入できる。"""
        with self.lock:
            self._check_position(at)
            src = _open_authenticated(data, "挿入する PDF", password, require_edit=False)
            try:
                count = src.page_count
                self.fitz.insert_pdf(src, start_at=at)
            finally:
                src.close()
            return count

    def insert_from_file(self, path: Path, at: int, password: str | None = None) -> int:
        path = Path(path)
        try:
            data = path.read_bytes()  # 先にバイト列で読むので、自分自身の挿入も同じ経路で動く
        except OSError as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。ファイルが見つからないか、読み取れません。"
            ) from exc
        return self.insert_from_bytes(data, at, password)

    @staticmethod
    def _check_multiple_of_90(value: int) -> None:
        if value % 90 != 0:
            raise ValueError(f"回転角は 90 の倍数で指定してください: {value}")

    def set_rotation(self, indices: list[int], rotation: int) -> None:
        with self.lock:
            self._check_multiple_of_90(rotation)
            self._check_indices(indices)
            for i in indices:
                self._page(i).set_rotation(rotation % 360)

    def rotate_by(self, indices: list[int], delta: int) -> None:
        with self.lock:
            self._check_multiple_of_90(delta)
            self._check_indices(indices)
            for i in indices:
                page = self._page(i)
                page.set_rotation((page.rotation + delta) % 360)

    def to_bytes(self) -> bytes:
        """保存するバイト列。パスワードと編集制限は元のまま残す（PDF_ENCRYPT_KEEP）。

        不要オブジェクトの掃除（garbage）は、生きている文書ではなく写しで行う。生きている文書で
        掃除すると、削除を元に戻すために残してあるページ（page_detach）が消えるため。"""
        keep = pymupdf.PDF_ENCRYPT_KEEP
        with self.lock:
            raw = self.fitz.tobytes(encryption=keep)  # garbage なし: 文書の中身を変えない
        copy = pymupdf.open(stream=raw, filetype="pdf")
        try:
            if copy.needs_pass:
                copy.authenticate(self._password or "")
            return copy.tobytes(garbage=3, deflate=True, encryption=keep)
        finally:
            copy.close()

    def save(self, target: Path | None = None) -> None:
        """target が None または現在のパスと同じなら上書き（初回のみバックアップ）。
        失敗時は SaveFailedError で、元のファイルには触れない。成功後は self.path が保存先になる。"""
        with self.lock:  # バイト列とページ数を同じ瞬間の状態で取る
            data = self.to_bytes()
            pages = self.fitz.page_count
        dest = absolute_path(target) if target is not None else self.path
        if dest is None:
            raise errors.SaveFailedError("保存先が指定されていません。", "other")
        overwrite = self.path is not None and dest == self.path
        made = safe_save.write_atomically(
            data, dest, backup=overwrite and not self.backup_done, expected_pages=pages
        )
        if made:
            self.backup_done = True
        self.path = dest

    def close(self) -> None:
        with self.lock:
            if not self.fitz.is_closed:
                self.fitz.close()
