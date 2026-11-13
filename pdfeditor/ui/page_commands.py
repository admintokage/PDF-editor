"""ページ操作を「元に戻す」に対応させる QUndoCommand 群。"""
from collections.abc import Callable

from PySide6.QtGui import QUndoCommand, QUndoStack

from pdfeditor.core.document import PdfDocument, page_order_after_move
from pdfeditor.ui import messages

DocChanged = Callable[[], None]
UNDO_LIMIT = 50


class _PageCommand(QUndoCommand):
    """redo/undo の共通処理。サブクラスは _apply / _revert を実装する。
    _apply が例外を投げたら文書は変わっていない（core の保証）。"""

    def __init__(self, text: str, doc: PdfDocument, doc_changed: DocChanged | None):
        super().__init__(text)
        self._doc = doc
        self._doc_changed = doc_changed
        self._preapplied = False

    def _apply(self) -> None:
        raise NotImplementedError

    def _revert(self) -> None:
        raise NotImplementedError

    def _notify(self) -> None:
        if self._doc_changed is not None:
            self._doc_changed()

    def redo(self) -> None:
        if self._preapplied:  # push_command が実行済み
            self._preapplied = False
        else:
            self._apply()
        self._notify()

    def undo(self) -> None:
        self._revert()
        self._notify()


class MovePagesCommand(_PageCommand):
    def __init__(self, doc, indices: list[int], to: int, doc_changed: DocChanged | None = None):
        super().__init__(messages.CMD_MOVE_PAGES, doc, doc_changed)
        self._indices = list(indices)
        self._to = to
        self._order: list[int] = []

    def _apply(self) -> None:
        with self._doc.lock:
            count = self._doc.page_count
            self._doc.move_pages(self._indices, self._to)  # 不正ならここで例外
            self._order = page_order_after_move(count, self._indices, self._to)

    def _revert(self) -> None:
        inverse = [0] * len(self._order)
        for new_pos, old_pos in enumerate(self._order):
            inverse[old_pos] = new_pos
        self._doc.reorder(inverse)


class DeletePagesCommand(_PageCommand):
    def __init__(self, doc, indices: list[int], doc_changed: DocChanged | None = None):
        super().__init__(messages.CMD_DELETE_PAGES, doc, doc_changed)
        self._indices = sorted(indices)
        self._removed = b""
        self._toc: list = []
        self._links: dict[int, list[dict]] = {}

    def _apply(self) -> None:
        with self._doc.lock:
            toc = self._doc.fitz.get_toc(simple=False)
            links = self._doc.capture_links(self._indices)  # 不正ならここで例外
            self._removed = self._doc.delete_pages(self._indices)
            self._toc, self._links = toc, links

    def _revert(self) -> None:
        if not self._removed:
            return
        self._doc.restore_deleted_pages(self._removed, self._indices, self._links, self._toc)


class InsertBlankCommand(_PageCommand):
    def __init__(self, doc, at: int, width: float, height: float,
                 doc_changed: DocChanged | None = None):
        super().__init__(messages.CMD_INSERT_BLANK, doc, doc_changed)
        self._at, self._width, self._height = at, width, height

    def _apply(self) -> None:
        self._doc.insert_blank(self._at, self._width, self._height)

    def _revert(self) -> None:
        self._doc.delete_pages([self._at])


class InsertPdfCommand(_PageCommand):
    def __init__(self, doc, data: bytes, at: int, password: str | None,
                 doc_changed: DocChanged | None = None):
        super().__init__(messages.CMD_INSERT_PDF, doc, doc_changed)
        self._data, self._at, self._password = data, at, password
        self._count = 0

    def _apply(self) -> None:
        self._count = self._doc.insert_from_bytes(self._data, self._at, self._password)

    def _revert(self) -> None:
        self._doc.delete_pages(list(range(self._at, self._at + self._count)))


class RotatePagesCommand(_PageCommand):
    def __init__(self, doc, indices: list[int], delta: int, doc_changed: DocChanged | None = None):
        super().__init__(messages.CMD_ROTATE_PAGES, doc, doc_changed)
        self._indices, self._delta = list(indices), delta
        self._previous: list[int] = []

    def _apply(self) -> None:
        with self._doc.lock:
            previous = [self._doc.page_rotation(i) for i in self._indices]  # 範囲外は IndexError
            self._doc.rotate_by(self._indices, self._delta)
            self._previous = previous

    def _revert(self) -> None:
        with self._doc.lock:
            for index, rotation in zip(self._indices, self._previous):
                self._doc.set_rotation([index], rotation)


def make_undo_stack(parent) -> QUndoStack:
    stack = QUndoStack(parent)
    stack.setUndoLimit(UNDO_LIMIT)
    return stack


def push_command(stack: QUndoStack, command: _PageCommand) -> None:
    """操作を先に実行し、成功したときだけスタックに積む。失敗は例外のまま呼び出し元へ伝わり、
    コマンドはスタックに残らない（push の中の redo で出た例外は PySide が握りつぶすため）。"""
    command._apply()
    command._preapplied = True
    stack.push(command)
