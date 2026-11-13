"""全ページを縦に並べた連続スクロール表示と、拡大・縮小。

座標の呼び方:
- 内容座標: 全ページを並べた大きな面の座標（論理ピクセル）。スクロール位置は内容座標。
- ビュー座標: viewport() の左上を原点とする座標。page_rect と view_to_page はこちら。
- ページ座標: ページ左上を原点とするポイント単位（回転を反映した表示上の向き）。

倍率 zoom 1.0 は 1 ポイント = 1 論理ピクセル。描画は zoom × devicePixelRatio で頼む。
画像の持ち主は RenderService のキャッシュで、ビューは見えているページの前後 1 枚までしか
画像を持たない。
"""
import math
from bisect import bisect_right

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.render_service import RenderService

GAP = 12  # ページ間の隙間と、左右・上下の余白（論理ピクセル）
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0
ZOOM_STEP = 1.25
RENDER_DELAY_MS = 80  # 連続した拡大・縮小やリサイズの間は描画を頼まない
ARROW_STEP = 40
# 1 ページの描画の画素数の上限（RGB で約 48MB）。高倍率でメモリを使い過ぎないため。
# 超える倍率では収まる倍率で描いて引き伸ばす（段階 1 ではぼやけを許容する）
MAX_RENDER_PIXELS = 16_000_000

_BACKGROUND = QColor(128, 128, 128)
_PAPER = QColor(255, 255, 255)
_BORDER = QColor(90, 90, 90)


class PageView(QAbstractScrollArea):
    current_page_changed = Signal(int)
    zoom_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.doc: PdfDocument | None = None
        self.service: RenderService | None = None
        self.zoom = 1.0
        self._fit_mode: str | None = None  # "width" / "page" の間はリサイズで合わせ直す
        self._sizes: list[tuple[float, float]] = []  # 各ページの (幅, 高さ) ポイント
        self._tops: list[float] = []  # 各ページ上端の内容座標
        self._content_w = 0.0
        self._content_h = 0.0
        self._render_zoom = 0.0  # 最後に頼んだ倍率（zoom × devicePixelRatio を丸めたもの）
        self._images: dict[int, tuple[float, QImage]] = {}  # ページ -> (描画倍率, 画像)
        self._near: set[int] = set()
        self._current = -1
        # scroll_to_page で選んだページ。文書の端で上端まで送れない時も「今のページ」とする。
        # 利用者がスクロールするか、配置が変わるまで有効
        self._pinned: int | None = None
        self._relayouting = False
        self._moving = False  # 自分でスクロール位置を動かしている間

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(RENDER_DELAY_MS)
        self._render_timer.timeout.connect(self._request_visible)

        # 縦スクロールバーは常に出す。出たり消えたりすると幅合わせが揺れるため
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().setSingleStep(ARROW_STEP)
        self.horizontalScrollBar().setSingleStep(ARROW_STEP)

    # --- 文書 -------------------------------------------------------------
    def set_document(self, doc: PdfDocument | None, service: RenderService | None) -> None:
        if self.service is not None:
            self.service.rendered.disconnect(self._on_rendered)
        self.doc = doc
        self.service = service
        if service is not None:
            service.rendered.connect(self._on_rendered)
        self._images.clear()
        self._fit_mode = "width"  # 開いた直後は幅に合わせる
        self._read_sizes()
        if self._sizes:
            zoom = self._clamp(self._fit_zoom("width", 0))
            if zoom != self.zoom:
                self.zoom = zoom
                self.zoom_changed.emit(zoom)
        self._pinned = None
        self._relayout()
        self._set_scroll(0, 0)
        self._after_view_change()
        self._request_visible()
        self.viewport().update()

    def reload_layout(self) -> None:
        """ページの数・大きさ・回転が変わった後に呼ぶ（invalidate の後）。"""
        keep = self._current
        self._pinned = None
        self._images.clear()  # 番号がずれた古い画像は使えない
        self._read_sizes()
        if self._sizes and self._fit_mode is not None:  # 回転などで幅が変わったら合わせ直す
            page = min(max(keep, 0), len(self._sizes) - 1)
            zoom = self._clamp(self._fit_zoom(self._fit_mode, page))
            if zoom != self.zoom:
                self.zoom = zoom
                self.zoom_changed.emit(zoom)
        self._relayout()
        if self._sizes and keep >= 0:
            self.scroll_to_page(min(keep, len(self._sizes) - 1))
        else:
            self._after_view_change()
        self._request_visible()  # invalidate で待ち行列は捨てられている
        self.viewport().update()

    def release_images(self) -> None:
        """持っている画像を手放す（表示していないタブのメモリを空ける）。配置と位置は保つ。"""
        self._render_timer.stop()
        self._images.clear()
        self._near = set()

    def request_images(self) -> None:
        """見えているページの画像を頼み直す（release_images の後、表示に戻った時）。"""
        self._request_visible()
        self.viewport().update()

    def _read_sizes(self) -> None:
        if self.doc is None:
            self._sizes = []
            return
        with self.doc.lock:  # 数えてから読むまでの間に変わらないように
            self._sizes = [self.doc.page_size(i) for i in range(self.doc.page_count)]

    # --- 配置 -------------------------------------------------------------
    def _relayout(self) -> None:
        """倍率とページの大きさから縦位置の累積表を作り、スクロールバーを合わせる。"""
        tops = []
        y = GAP
        widest = 0.0
        for w, h in self._sizes:
            tops.append(y)
            y += h * self.zoom + GAP
            widest = max(widest, w * self.zoom)
        self._tops = tops
        self._content_h = y if self._sizes else 0.0
        self._content_w = widest + 2 * GAP if self._sizes else 0.0
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        self._relayouting = True
        try:
            vp = self.viewport()
            vbar, hbar = self.verticalScrollBar(), self.horizontalScrollBar()
            vbar.setRange(0, max(0, math.ceil(self._content_h) - vp.height()))
            vbar.setPageStep(vp.height())
            hbar.setRange(0, max(0, math.ceil(self._content_w) - vp.width()))
            hbar.setPageStep(vp.width())
        finally:
            self._relayouting = False

    def _page_left(self, index: int) -> float:
        """内容座標でのページ左端。ビューより狭い間は中央に置く。"""
        avail = max(self._content_w, self.viewport().width())
        return (avail - self._sizes[index][0] * self.zoom) / 2

    def page_rect(self, index: int) -> QRectF:
        """ページの矩形（ビュー座標）。範囲外は無効な QRectF。"""
        if not 0 <= index < len(self._sizes):
            return QRectF()
        w, h = self._sizes[index]
        return QRectF(
            self._page_left(index) - self.horizontalScrollBar().value(),
            self._tops[index] - self.verticalScrollBar().value(),
            w * self.zoom,
            h * self.zoom,
        )

    def view_to_page(self, pos: QPointF) -> tuple[int, float, float] | None:
        """ビュー座標の点を (ページ番号, ページ内 x, ページ内 y)（ポイント）にする。
        隙間や余白の上なら None。"""
        if not self._sizes:
            return None
        index = self._page_at_y(pos.y() + self.verticalScrollBar().value())
        rect = self.page_rect(index)
        if not rect.contains(pos):
            return None
        return (index, (pos.x() - rect.left()) / self.zoom, (pos.y() - rect.top()) / self.zoom)

    def _page_at_y(self, y: float) -> int:
        """内容座標 y を含むページ。隙間なら近い方。"""
        i = max(bisect_right(self._tops, y) - 1, 0)
        bottom = self._tops[i] + self._sizes[i][1] * self.zoom
        if y > bottom and i + 1 < len(self._tops) and self._tops[i + 1] - y < y - bottom:
            return i + 1
        return i

    def visible_pages(self) -> list[int]:
        if not self._sizes:
            return []
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        first = max(bisect_right(self._tops, top) - 1, 0)
        if self._tops[first] + self._sizes[first][1] * self.zoom < top:
            first += 1  # 上端が隙間にある
        last = bisect_right(self._tops, bottom) - 1
        return list(range(first, last + 1))

    def current_page(self) -> int:
        """画面の上端に出ているページ。scroll_to_page で選んだページが文書の端のため上端まで
        送れない時は、そのページ。文書がなければ -1。"""
        if not self._sizes:
            return -1
        if self._pinned is not None and self._pinned < len(self._sizes):
            return self._pinned
        return self._top_page()

    def _top_page(self) -> int:
        """下端が「ビュー上端 + 隙間 + 1px」より下にある最初のページ。
        上のページの下余白がわずかに見えているだけなら、そのページは数えない。"""
        y = self.verticalScrollBar().value() + GAP + 1
        i = max(bisect_right(self._tops, y) - 1, 0)
        if self._tops[i] + self._sizes[i][1] * self.zoom <= y and i + 1 < len(self._tops):
            i += 1
        return i

    def scroll_to_page(self, index: int) -> None:
        if not self._sizes:
            return
        index = min(max(index, 0), len(self._sizes) - 1)
        self._set_scroll(v=round(self._tops[index] - GAP))
        self._settle_current(index)

    def _set_scroll(self, h: int | None = None, v: int | None = None) -> None:
        """自分でスクロール位置を動かす（利用者のスクロールとは区別する）。"""
        self._moving = True
        try:
            if h is not None:
                self.horizontalScrollBar().setValue(h)
            if v is not None:
                self.verticalScrollBar().setValue(v)
        finally:
            self._moving = False

    def _settle_current(self, page: int) -> None:
        """page を今のページとする。上端に出せなかった時だけ覚えておく。"""
        self._pinned = page if self._top_page() != page else None
        self._after_view_change()

    # --- 倍率 -------------------------------------------------------------
    def set_zoom(self, z: float) -> None:
        self._apply_zoom(z, anchor=None)

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / ZOOM_STEP)

    def fit_width(self) -> None:
        if self._sizes:
            self._fit_mode = "width"
            self._apply_zoom(self._fit_zoom("width", self.current_page()), None, keep_mode=True)

    def fit_page(self) -> None:
        if self._sizes:
            page = self.current_page()
            self._fit_mode = "page"
            self._apply_zoom(self._fit_zoom("page", page), None, keep_mode=True)
            self.scroll_to_page(page)

    def _fit_zoom(self, mode: str, page: int) -> float:
        w, h = self._sizes[page]
        vp = self.viewport()
        zoom = (vp.width() - 2 * GAP) / w
        if mode == "page":
            zoom = min(zoom, (vp.height() - 2 * GAP) / h)
        return zoom

    def _apply_zoom(self, z: float, anchor: QPointF | None, keep_mode: bool = False) -> None:
        """倍率を変える。anchor（ビュー座標）があれば、その下にあったページ上の点を同じ位置に
        保つ。なければ今のページを保つ（上端の基準線にあった点を基準線に戻す）。"""
        if not keep_mode:
            self._fit_mode = None
        z = self._clamp(z)
        if z == self.zoom:
            return
        vp = self.viewport()
        keep_current = anchor is None
        held = None
        if self._sizes:
            if keep_current:
                index = self.current_page()
                rect = self.page_rect(index)
                anchor = QPointF(vp.width() / 2, GAP + 1)
                py = max(0.0, (anchor.y() - rect.top()) / self.zoom)
            else:
                index = self._page_at_y(anchor.y() + self.verticalScrollBar().value())
                rect = self.page_rect(index)
                py = (anchor.y() - rect.top()) / self.zoom
            held = (index, (anchor.x() - rect.left()) / self.zoom, py)
        self.zoom = z
        # 古い倍率の画像を引き伸ばして出しておき、落ち着いてから頼む。
        # 動いている間は scrollContentsBy も頼まない
        self._render_timer.start()
        self._relayout()
        if held is not None:
            index, px, py = held
            self._set_scroll(round(self._page_left(index) + px * z - anchor.x()),
                             round(self._tops[index] + py * z - anchor.y()))
        self.zoom_changed.emit(z)
        if held is not None and keep_current:
            self._settle_current(held[0])
        else:
            self._pinned = None
            self._after_view_change()
        self.viewport().update()

    @staticmethod
    def _clamp(z: float) -> float:
        return min(max(float(z), MIN_ZOOM), MAX_ZOOM)

    def _current_render_zoom(self) -> float:
        return round(self.zoom * self.devicePixelRatioF(), 4)

    # --- 描画の依頼 -------------------------------------------------------
    def _request_visible(self) -> None:
        self._render_timer.stop()
        visible = self.visible_pages()
        near = set(visible)
        if visible:
            near |= {i for i in (visible[0] - 1, visible[-1] + 1) if 0 <= i < len(self._sizes)}
        self._near = near
        for index in [i for i in self._images if i not in near]:
            del self._images[index]
        if self.service is None or not self.isVisible():
            return
        self._render_zoom = self._current_render_zoom()
        self.service.cancel_except(near)
        for index in near - set(visible):
            self.service.request(index, self._page_render_zoom(index), priority=1)
        for index in reversed(visible):  # 同じ優先度は後から頼んだ方が先
            self.service.request(index, self._page_render_zoom(index), priority=0)

    def _page_render_zoom(self, index: int) -> float:
        """ページ index を頼む倍率。画素数が MAX_RENDER_PIXELS を超えるなら、収まる最大の
        倍率に下げる（引き伸ばして出すのでぼやけるが、メモリを使い過ぎない）。
        小数 4 桁で切り捨て、毎回同じ値になるようにする（キャッシュのキー）。"""
        w, h = self._sizes[index]
        if w * h * self._render_zoom ** 2 <= MAX_RENDER_PIXELS:
            return self._render_zoom
        return math.floor(math.sqrt(MAX_RENDER_PIXELS / (w * h)) * 10000) / 10000

    def _on_rendered(self, index: int, zoom: float, image: QImage) -> None:
        if index not in self._near or not 0 <= index < len(self._sizes):
            return
        if zoom != self._page_render_zoom(index) and index in self._images:
            return  # 頼み直す前の倍率の結果。手元の画像で足りている
        self._images[index] = (zoom, image)
        self.viewport().update(self.page_rect(index).toAlignedRect().adjusted(-1, -1, 1, 1))

    def _after_view_change(self) -> None:
        current = self.current_page()
        if current != self._current:
            self._current = current
            self.current_page_changed.emit(current)

    # --- イベント ---------------------------------------------------------
    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()
        if self._relayouting:
            return
        if not self._moving:  # 利用者のスクロール。今のページは上端で決まる
            self._pinned = None
            self._after_view_change()
        # 自分で動かした時は、呼び出し側が最後に今のページを決める
        if not self._render_timer.isActive():  # 倍率変更の直後は落ち着くのを待つ
            self._request_visible()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        page = self.current_page()
        if self._sizes and self._fit_mode is not None:
            self._apply_zoom(self._fit_zoom(self._fit_mode, page), None, keep_mode=True)
        self._relayout()
        if self._sizes:
            self._settle_current(page)  # 大きさが変わっても今のページを保つ
        self._render_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._request_visible()

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), _BACKGROUND)
        if not self._sizes:
            return
        if self._current_render_zoom() != self._render_zoom and not self._render_timer.isActive():
            self._render_timer.start()  # 画面の表示倍率が変わった
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QPen(_BORDER, 1))
        for index in self.visible_pages():
            rect = self.page_rect(index)
            rect.moveTopLeft(QPointF(round(rect.left()), round(rect.top())))
            if not rect.intersects(QRectF(event.rect())):
                continue
            painter.fillRect(rect, _PAPER)
            held = self._images.get(index)
            if held is not None:
                painter.drawImage(rect, held[1])
            painter.drawRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5))

    def keyPressEvent(self, event) -> None:
        vbar = self.verticalScrollBar()
        key = event.key()
        if key == Qt.Key.Key_PageDown:
            vbar.setValue(vbar.value() + self.viewport().height())
        elif key == Qt.Key.Key_PageUp:
            vbar.setValue(vbar.value() - self.viewport().height())
        elif key == Qt.Key.Key_Home:
            vbar.setValue(vbar.minimum())
        elif key == Qt.Key.Key_End:
            vbar.setValue(vbar.maximum())
        else:
            super().keyPressEvent(event)  # 矢印キーは singleStep ずつ

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            if steps:
                self._apply_zoom(self.zoom * ZOOM_STEP ** steps, event.position())
            event.accept()
            return
        super().wheelEvent(event)
