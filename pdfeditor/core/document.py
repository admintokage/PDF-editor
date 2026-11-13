"""PDF 文書の読み込み。Qt には依存しない。"""
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfeditor.core import errors, safe_save

_PERM_OWNER_BIT = 4  # authenticate() の戻り値で、権限パスワード一致を示すビット


@dataclass(frozen=True)
class RenderedPage:
    """描画結果。samples は RGB（アルファなし）で 1 行 stride バイト。"""
    width: int
    height: int
    stride: int
    samples: bytes


class PdfDocument:
    def __init__(self, fitz_doc: pymupdf.Document, path: Path | None):
        self.fitz = fitz_doc
        self.path = path
        self.lock = threading.RLock()
        self.backup_done = False  # この文書で上書き用バックアップを作ったか

    @classmethod
    def open(cls, path: Path, password: str | None = None) -> "PdfDocument":
        path = Path(path)
        try:
            data = path.read_bytes()  # ファイルを掴みっぱなしにしない
        except OSError as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。ファイルが見つからないか、読み取れません。"
            ) from exc
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。PDF ファイルではないか、壊れています。"
            ) from exc

        try:
            owner_ok = False
            if doc.needs_pass:
                if password is None:
                    raise errors.PasswordRequiredError(
                        f"「{path.name}」を開くにはパスワードが必要です。"
                    )
                result = doc.authenticate(password)
                if not result:
                    raise errors.WrongPasswordError("パスワードが違います。")
                owner_ok = bool(result & _PERM_OWNER_BIT)
            elif password is not None:
                owner_ok = bool(doc.authenticate(password) & _PERM_OWNER_BIT)

            if doc.page_count == 0:
                raise errors.NotAPdfError(
                    f"「{path.name}」を開けません。PDF ファイルが壊れています。"
                )
            if not owner_ok and not doc.permissions & pymupdf.PDF_PERM_MODIFY:
                raise errors.OwnerPasswordRequiredError(
                    f"「{path.name}」は編集が制限されています。"
                    "権限パスワード（編集用のパスワード）を入力してください。"
                )
        except BaseException:
            doc.close()
            raise
        return cls(doc, path)

    @property
    def page_count(self) -> int:
        with self.lock:
            return self.fitz.page_count

    @property
    def is_signed(self) -> bool:
        return self.fitz.get_sigflags() == 3

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

    def to_bytes(self) -> bytes:
        with self.lock:
            return self.fitz.tobytes(garbage=3, deflate=True)

    def save(self, target: Path | None = None) -> None:
        """target が None または現在のパスと同じなら上書き（初回のみバックアップ）。
        失敗時は SaveFailedError で、元のファイルには触れない。成功後は self.path が保存先になる。"""
        with self.lock:  # バイト列とページ数を同じ瞬間の状態で取る
            data = self.to_bytes()
            pages = self.fitz.page_count
        dest = Path(target) if target is not None else self.path
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
