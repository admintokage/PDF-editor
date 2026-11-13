"""左のページ一覧（サムネイル）。選択・ドラッグでの並べ替え・本体表示との連動を受け持つ。

画像は RenderService に低い優先度で頼み、見えている行（と前後少し）だけを頼む。
ページ表示は自分の見えているページ以外の待ち行列を cancel_except で捨てるので、
ここで頼んだものも捨てられうる。届いていない行は RETRY_MS ごとに頼み直す
（同じ依頼を重ねても RenderService の待ち行列で 1 件にまとまる）。
"""
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QUndoStack
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from pdfeditor.core.document import PdfDocument, page_order_after_move
from pdfeditor.core.errors import PdfEditorError
from pdfeditor.ui import dialogs
from pdfeditor.ui.page_commands import MovePagesCommand, push_command
from pdfeditor.ui.render_service import RenderService

THUMB_WIDTH = 120  # サムネイルの幅（論理ピクセル）
MAX_THUMB_HEIGHT = 240  # 極端に縦長のページはこの高さに収める
THUMB_PRIORITY = 10  # ページ表示（0〜1）より後に描く
PAD = 8  # 項目の内側の余白
LABEL_GAP = 4  # サムネイルとページ番号の間
ROW_MARGIN = 2  # 見えている行の前後に先読みする行数
RETRY_MS = 400  # 届いていない行を頼み直す間隔
MAX_ATTEMPTS = 20  # 描けないページを頼み続けないための上限（スクロールや変更で数え直す）
MIME_TYPE = "application/x-pdfeditor-pages"

_PAPER = QColor(255, 255, 255)
_BORDER = QColor(120, 120, 120)
_SHOWN = QColor(255, 140, 0)  # 本体に表示中のページの枠（選択の色と区別する）


def thumb_geometry(width: float, height: float) -> tuple[tuple[int, int], float]:
    """ページの (幅, 高さ) ポイントから、サムネイルの表示の大きさと描画倍率を求める。
    通常は幅 THUMB_WIDTH で、倍率は 120 / ページ幅（小数 4 桁）。"""
    scale = THUMB_WIDTH / width
    if height * scale > MAX_THUMB_HEIGHT:
        scale = MAX_THUMB_HEIGHT / height
    return (max(1, round(width * scale)), max(1, round(height * scale))), round(scale, 4)


class ThumbnailModel(QAbstractListModel):
    """1 行 = 1 ページ。画像は届いたものだけ持つ（文書が変わったら捨てる）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sizes: list[tuple[int, int]] = []
        self._zooms: list[float] = []
        self._images: dict[int, QImage] = {}

    def reload(self, doc: PdfDocument | None) -> None:
        self.beginResetModel()
        self._images.clear()
        pages: list[tuple[float, float]] = []
        if doc is not None:
            with doc.lock:  # 数えてから読むまでの間に変わらないように
                pages = [doc.page_size(i) for i in range(doc.page_count)]
        geometry = [thumb_geometry(w, h) for w, h in pages]
        self._sizes = [size for size, _ in geometry]
        self._zooms = [zoom for _, zoom in geometry]
        self.endResetModel()

    def thumb_size(self, row: int) -> tuple[int, int]:
        return self._sizes[row]

    def thumb_zoom(self, row: int) -> float:
        return self._zooms[row]

    def clear_images(self) -> None:
        self._images.clear()
        if self._sizes:
            self.dataChanged.emit(self.index(0), self.index(len(self._sizes) - 1),
                                  [Qt.ItemDataRole.DecorationRole])

    def image(self, row: int) -> QImage | None:
        return self._images.get(row)

    def set_image(self, row: int, image: QImage) -> None:
        self._images[row] = image
        index = self.index(row)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    # --- QAbstractListModel ---------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._sizes)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return str(index.row() + 1)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled  # ページの「間」にだけ落とせる
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled)

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [MIME_TYPE]

    def mimeData(self, indexes: Iterable[QModelIndex]) -> QMimeData:
        rows = sorted({i.row() for i in indexes if i.isValid()})
        mime = QMimeData()
        mime.setData(MIME_TYPE, QByteArray(",".join(map(str, rows)).encode("ascii")))
        return mime


def _rows_from_mime(mime: QMimeData) -> list[int]:
    text = bytes(mime.data(MIME_TYPE).data()).decode("ascii")
    return [int(part) for part in text.split(",") if part]


class _ThumbnailDelegate(QStyledItemDelegate):
    """白い紙（届いていれば画像）と枠、その下にページ番号を描く。"""

    def __init__(self, panel: "ThumbnailPanel") -> None:
        super().__init__(panel)
        self._panel = panel

    def sizeHint(self, option, index: QModelIndex) -> QSize:
        _, h = self._panel.model().thumb_size(index.row())
        width = max(THUMB_WIDTH + 2 * PAD, self._panel.viewport().width())
        return QSize(width, PAD + h + LABEL_GAP + option.fontMetrics.height() + PAD)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        row = index.row()
        model = self._panel.model()
        w, h = model.thumb_size(row)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            painter.fillRect(rect, option.palette.highlight())
        page = QRect(rect.left() + (rect.width() - w) // 2, rect.top() + PAD, w, h)
        painter.fillRect(page, _PAPER)
        image = model.image(row)
        if image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(page, image)
        if row == self._panel.shown_page():
            painter.setPen(QPen(_SHOWN, 3))
            painter.drawRect(page.adjusted(-2, -2, 1, 1))
        else:
            painter.setPen(QPen(_BORDER, 1))
            painter.drawRect(page.adjusted(0, 0, -1, -1))
        label = QRect(rect.left(), page.bottom() + 1 + LABEL_GAP,
                      rect.width(), option.fontMetrics.height())
        role = option.palette.ColorRole.HighlightedText if selected else option.palette.ColorRole.Text
        painter.setPen(option.palette.color(role))
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, str(row + 1))
        painter.restore()


class ThumbnailPanel(QListView):
    page_activated = Signal(int)  # 利用者がページを選んだ（本体表示をそのページへ）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._doc: PdfDocument | None = None
        self._service: RenderService | None = None
        self._stack: QUndoStack | None = None
        self._doc_changed: Callable[[], None] | None = None
        self._shown = -1  # 本体に表示中のページ
        self._attempts: dict[int, int] = {}
        self._syncing = False  # 自分で選択・現在行を変えている間

        self.setModel(ThumbnailModel(self))
        self.setItemDelegate(_ThumbnailDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)  # 他から受けない
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setUniformItemSizes(False)
        self.setSpacing(0)  # 項目の間に隙間を作らない（ドロップ位置の計算を単純にする）
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)

        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.timeout.connect(self._request_visible)
        self.clicked.connect(lambda index: self.page_activated.emit(index.row()))
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)

    def model(self) -> ThumbnailModel:
        return super().model()

    def panel_width(self) -> int:
        """サムネイル 1 列とスクロールバーが収まる幅。"""
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        return THUMB_WIDTH + 2 * PAD + bar + 2 * self.frameWidth()

    # --- 文書 -------------------------------------------------------------
    def set_document(self, doc: PdfDocument | None, service: RenderService | None,
                     undo_stack: QUndoStack | None,
                     doc_changed: Callable[[], None] | None = None) -> None:
        """doc_changed はページ操作のコマンドに渡す（文書が変わった後に呼ばれる）。"""
        if self._service is not None:
            self._service.rendered.disconnect(self._on_rendered)
        self._doc, self._service, self._stack = doc, service, undo_stack
        self._doc_changed = doc_changed
        self._request_timer.stop()
        self._shown = -1
        self._attempts.clear()
        if service is not None:
            service.rendered.connect(self._on_rendered)
        self.model().reload(doc)
        self.select_pages([], current=0)
        self.doItemsLayout()
        self.scrollToTop()
        self._request_visible()

    def refresh(self) -> None:
        """ページの数・大きさ・回転が変わった後に呼ぶ。選択とスクロール位置は保つ。"""
        selected = self.selected_pages()
        current = self.currentIndex().row()
        scroll = self.verticalScrollBar().value()
        self.model().reload(self._doc)
        self._attempts.clear()
        count = self.model().rowCount()
        self.select_pages([r for r in selected if r < count],
                          current=min(max(current, 0), count - 1))
        self.doItemsLayout()
        self.verticalScrollBar().setValue(scroll)
        self._request_visible()

    # --- 選択 -------------------------------------------------------------
    def selected_pages(self) -> list[int]:
        return sorted({i.row() for i in self.selectionModel().selectedIndexes()})

    def select_pages(self, rows: Iterable[int], current: int | None = None) -> None:
        """rows だけを選ぶ。現在行は current（省略時は最初の行）。page_activated は出さない。"""
        rows = list(rows)
        model = self.model()
        selection = QItemSelection()
        for row in rows:
            index = model.index(row)
            selection.select(index, index)
        if current is None:
            current = rows[0] if rows else -1
        self._syncing = True
        try:
            sm = self.selectionModel()
            sm.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if 0 <= current < model.rowCount():
                sm.setCurrentIndex(model.index(current), QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._syncing = False

    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        super().currentChanged(current, previous)
        if not self._syncing and current.isValid():  # キーボードやクリックで選んだ
            self.page_activated.emit(current.row())

    def shown_page(self) -> int:
        return self._shown

    def show_page(self, index: int) -> None:
        """本体に表示中のページを目立たせ、見える所まで送る。選択は変えず、
        page_activated も出さない（本体表示 → 一覧 → 本体表示と回らないように）。"""
        self._shown = index
        self.viewport().update()
        self._attempts.clear()
        if 0 <= index < self.model().rowCount():
            self.scrollTo(self.model().index(index), QAbstractItemView.ScrollHint.EnsureVisible)

    # --- 並べ替え ---------------------------------------------------------
    def move_rows(self, indices: list[int], to: int) -> None:
        """indices のページを、取り出す前の番号で数えた位置 to（0〜ページ数）の前へ移す。
        何も変わらない移動はコマンドを積まない。"""
        if self._doc is None or self._stack is None:
            return
        rows = sorted(set(indices))
        count = self.model().rowCount()
        if not rows or page_order_after_move(count, rows, to) == list(range(count)):
            return
        try:
            push_command(self._stack, MovePagesCommand(self._doc, rows, to, self._doc_changed))
        except PdfEditorError as err:
            dialogs.show_error(self, err)
            return
        first = to - sum(1 for r in rows if r < to)
        self.select_pages(range(first, first + len(rows)))
        self.page_activated.emit(first)

    def _row_top(self, row: int) -> int:
        return self.visualRect(self.model().index(row)).top()

    def drop_row(self, pos: QPoint) -> int:
        """ビュー座標 pos に落とした時の挿入位置（0〜ページ数）。項目の上半分ならその前、
        下半分なら後ろ。横位置は見ない。"""
        count = self.model().rowCount()
        if count == 0:
            return 0
        row = max(bisect_right(range(count), pos.y(), key=self._row_top) - 1, 0)
        rect = self.visualRect(self.model().index(row))
        return row if pos.y() < rect.center().y() else row + 1

    def dropEvent(self, event) -> None:
        if event.source() is not self or not event.mimeData().hasFormat(MIME_TYPE):
            event.ignore()
            return
        rows = _rows_from_mime(event.mimeData())
        to = self.drop_row(event.position().toPoint())
        # 並べ替えは自分で済ませる。MoveAction のままだと Qt がドラッグ元の行を消そうとする
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.stopAutoScroll()
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        self.move_rows(rows, to)

    # --- 画像の依頼 -------------------------------------------------------
    def rows_to_load(self) -> range:
        """見えている行と、その前後 ROW_MARGIN 行。"""
        count = self.model().rowCount()
        if count == 0:
            return range(0)
        model = self.model()
        first = bisect_left(range(count), 0,
                            key=lambda r: self.visualRect(model.index(r)).bottom())
        last = bisect_right(range(count), self.viewport().height(), key=self._row_top) - 1
        return range(max(first - ROW_MARGIN, 0), min(last + 1 + ROW_MARGIN, count))

    def release_images(self) -> None:
        """持っている画像を手放し、頼み直しも止める（表示していないタブのメモリを空ける）。"""
        self._request_timer.stop()
        self._attempts.clear()
        self.model().clear_images()

    def request_images(self) -> None:
        """見えている行の画像を頼み直す（release_images の後、表示に戻った時）。"""
        self._attempts.clear()
        self._request_visible()

    def _request_visible(self) -> None:
        self._request_timer.stop()
        if self._service is None or not self.isVisible():
            return
        model = self.model()
        missing = [r for r in self.rows_to_load()
                   if model.image(r) is None and self._attempts.get(r, 0) < MAX_ATTEMPTS]
        for row in reversed(missing):  # 同じ優先度は後から頼んだ方が先なので、上の行が先
            self._attempts[row] = self._attempts.get(row, 0) + 1
            self._service.request(row, model.thumb_zoom(row), priority=THUMB_PRIORITY)
        if missing:  # 捨てられていても、次の頼み直しで拾う
            self._request_timer.start(RETRY_MS)

    def _on_rendered(self, index: int, zoom: float, image: QImage) -> None:
        model = self.model()
        # rendered はページ表示と共用。この行のサムネイルの倍率のものだけ受け取る
        if 0 <= index < model.rowCount() and zoom == model.thumb_zoom(index):
            model.set_image(index, image)

    def _on_scrolled(self) -> None:
        self._attempts.clear()
        self._request_visible()

    # --- イベント ---------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._request_timer.start(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._request_timer.start(0)
