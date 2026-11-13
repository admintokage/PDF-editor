"""PDF 文書の読み込み。Qt には依存しない。"""
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfeditor.core import errors, safe_save

_PERM_OWNER_BIT = 4  # authenticate() の戻り値で、権限パスワード一致を示すビット

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A4 縦": (595, 842), "A4 横": (842, 595),
    "A3 縦": (842, 1191), "A3 横": (1191, 842),
    "B5 縦": (516, 729), "B5 横": (729, 516),
}


def _open_authenticated(
    data: bytes, name: str, password: str | None, *, require_edit: bool
) -> pymupdf.Document:
    """バイト列を PDF として開き、パスワードを確認する。失敗時は閉じて例外を投げる。
    require_edit=False なら、編集が制限された PDF も開く（読み取るだけの用途）。"""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise errors.NotAPdfError(
            f"{name}を開けません。PDF ファイルではないか、壊れています。"
        ) from exc

    try:
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
    except BaseException:
        doc.close()
        raise
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
        doc = _open_authenticated(data, f"「{path.name}」", password, require_edit=True)
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

    def move_pages(self, indices: list[int], to: int) -> None:
        with self.lock:
            self._check_indices(indices)
            self._check_position(to)
            if not indices:
                return
            self.fitz.select(page_order_after_move(self.fitz.page_count, indices, to))

    def reorder(self, order: list[int]) -> None:
        """order は旧ページ番号の順列。"""
        with self.lock:
            if sorted(order) != list(range(self.fitz.page_count)):
                raise ValueError("並べ替えの指定がページ番号の順列になっていません。")
            self.fitz.select(order)

    def delete_pages(self, indices: list[int]) -> bytes:
        """削除したページだけを含む PDF のバイト列（元に戻す用）を返す。"""
        with self.lock:
            self._check_indices(indices)
            if not indices:
                return b""
            if len(indices) >= self.fitz.page_count:
                raise errors.PageOperationError("最後の 1 ページは削除できません。")
            removed = pymupdf.open()
            try:
                for i in sorted(indices):
                    removed.insert_pdf(self.fitz, from_page=i, to_page=i)
                data = removed.tobytes()
            finally:
                removed.close()
            self.fitz.delete_pages(indices)
            return data

    def capture_links(self, indices: list[int]) -> dict[int, list[dict]]:
        """削除の取り消し用に、削除で変わりうるページ（削除するページと、削除するページへ
        リンクしているページ）のリンクを、ページ番号つきで控える。削除の前に呼ぶ。"""
        with self.lock:
            self._check_indices(indices)
            doomed = set(indices)
            out: dict[int, list[dict]] = {}
            for i in range(self.fitz.page_count):
                links = self._page(i).get_links()
                if i in doomed or any(
                    link.get("kind") == pymupdf.LINK_GOTO and link.get("page") in doomed
                    for link in links
                ):
                    out[i] = [dict(link) for link in links]
            return out

    def restore_deleted_pages(
        self, data: bytes, indices: list[int], links: dict[int, list[dict]],
        toc: list | None = None,
    ) -> None:
        """delete_pages が返した data のページを元の位置 indices に戻し、
        capture_links で控えたリンクとしおり toc を元通りにする。"""
        with self.lock:
            src = pymupdf.open(stream=data, filetype="pdf")
            try:
                # 元の位置の小さい順に 1 ページずつ戻せば、非連続の選択でも元の並びになる
                for k, index in enumerate(sorted(indices)):
                    self.fitz.insert_pdf(src, from_page=k, to_page=k, start_at=index)
            finally:
                src.close()
            # 戻した後のページ番号は削除前と同じなので、控えたリンクをそのまま付け直せる
            for index, saved in links.items():
                page = self._page(index)
                for existing in page.get_links():
                    page.delete_link(existing)
                for link in saved:
                    link = {k: v for k, v in link.items() if k not in ("xref", "id")}
                    page.insert_link(link)
            if toc is not None:
                self.fitz.set_toc(toc)

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
