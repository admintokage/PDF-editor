"""アプリ内だけのページ用クリップボード（F-86d）。

コピー・切り取りしたページを、暗号化なしの PDF のバイト列としてメモリにだけ持つ。
ファイルにも、OS のクリップボードにも、ログにも出さない。アプリを終了すると消える。
"""
from PySide6.QtCore import QObject, Signal


class PageClipboard(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._data: bytes | None = None
        self._count = 0

    def __repr__(self) -> str:  # 中身（ページの内容）は出さない
        return f"PageClipboard(count={self._count})"

    def set_pages(self, data: bytes, count: int) -> None:
        self._data, self._count = bytes(data), count
        self.changed.emit()

    def data(self) -> bytes | None:
        return self._data

    def count(self) -> int:
        return self._count

    def clear(self) -> None:
        self._data, self._count = None, 0
        self.changed.emit()


_clipboard: PageClipboard | None = None


def clipboard() -> PageClipboard:
    """アプリに 1 つのクリップボード。どのウィンドウ・タブからも同じものを使う。"""
    global _clipboard
    if _clipboard is None:
        _clipboard = PageClipboard()
    return _clipboard
