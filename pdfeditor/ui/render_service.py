"""ページ画像を描画スレッドで作り、使った順に古いものから捨てるキャッシュに置く。

スレッドをまたぐのは _RenderQueue（条件変数で守る）と、描画結果のシグナル（キューで届く）
だけ。キャッシュと rendered シグナルは、RenderService を作ったスレッド（GUI スレッド）
だけで扱う。
"""
import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QImage

from pdfeditor.core.document import PdfDocument

_logger = logging.getLogger("pdfeditor")

_Key = tuple[int, float]  # (ページ番号, 倍率)


@dataclass(frozen=True)
class _Job:
    index: int
    zoom: float
    token: int  # 取り出した時の世代番号


class _RenderQueue:
    """描画待ちの行列と世代番号。GUI スレッドと描画スレッドの両方から呼ばれる。

    優先度の数が小さいものから、同じ優先度なら後から頼まれたものから取り出す。
    invalidate のたびに世代番号を増やし、それより前に取り出した結果を古いと判定する。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pending: dict[_Key, tuple[int, int]] = {}  # key -> (優先度, 受付順)
        self._in_flight: dict[_Key, int] = {}  # 描画中の key -> 世代番号
        self._seq = 0
        self._generation = 0
        self._all_stamp = 0  # 全ページを無効にした世代
        self._page_stamp: dict[int, int] = {}  # ページごとに無効にした世代
        self._closed = False

    def token(self) -> int:
        with self._cond:
            return self._generation

    def _is_current(self, index: int, token: int) -> bool:
        return token >= self._all_stamp and token >= self._page_stamp.get(index, 0)

    def is_current(self, index: int, token: int) -> bool:
        with self._cond:
            return self._is_current(index, token)

    def put(self, index: int, zoom: float, priority: int) -> None:
        key = (index, zoom)
        with self._cond:
            if self._closed:
                return
            flying = self._in_flight.get(key)
            if flying is not None and self._is_current(index, flying):
                return  # 描画中の結果がそのまま使える
            self._seq += 1
            old = self._pending.get(key)
            if old is not None:
                priority = min(priority, old[0])
            self._pending[key] = (priority, self._seq)
            self._cond.notify()

    def take(self) -> _Job | None:
        """次の仕事を待って返す。close 後は None。"""
        with self._cond:
            while not self._closed and not self._pending:
                self._cond.wait()
            if self._closed:
                return None
            key = min(self._pending, key=lambda k: (self._pending[k][0], -self._pending[k][1]))
            del self._pending[key]
            self._in_flight[key] = self._generation
            return _Job(key[0], key[1], self._generation)

    def done(self, job: _Job) -> None:
        with self._cond:
            self._in_flight.pop((job.index, job.zoom), None)

    def retain(self, indices: set[int]) -> None:
        with self._cond:
            for key in [k for k in self._pending if k[0] not in indices]:
                del self._pending[key]

    def invalidate(self, indices: set[int] | None) -> None:
        with self._cond:
            self._generation += 1
            if indices is None:
                self._all_stamp = self._generation
                self._page_stamp.clear()
                self._pending.clear()
                return
            for i in indices:
                self._page_stamp[i] = self._generation
            for key in [k for k in self._pending if k[0] in indices]:
                del self._pending[key]

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._pending.clear()
            self._cond.notify_all()


class _RenderThread(QThread):
    finished_job = Signal(int, float, object, int)  # index, zoom, QImage, 世代番号

    def __init__(self, doc: PdfDocument, queue: _RenderQueue) -> None:
        super().__init__()
        self._doc = doc
        self._queue = queue

    def run(self) -> None:
        while (job := self._queue.take()) is not None:
            try:
                page = self._doc.render(job.index, job.zoom)
            except IndexError:
                continue  # 頼まれた後でページが減った
            except Exception:
                _logger.exception("ページ %d の描画に失敗しました", job.index)
                continue
            finally:
                self._queue.done(job)
            image = QImage(
                page.samples, page.width, page.height, page.stride, QImage.Format.Format_RGB888
            ).copy()  # samples の寿命に依存しないよう、画素を自分で持つ
            self.finished_job.emit(job.index, job.zoom, image, job.token)


class RenderService(QObject):
    rendered = Signal(int, float, QImage)
    _cache_hit = Signal(int, float, object, int)

    def __init__(self, doc: PdfDocument, cache_bytes: int = 256 * 1024 * 1024,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._limit = cache_bytes
        self._cache: OrderedDict[_Key, QImage] = OrderedDict()  # 古い順
        self._bytes = 0
        self._stopped = False
        self._queue = _RenderQueue()
        self._thread = _RenderThread(doc, self._queue)
        # 描画スレッドからの結果もキャッシュからの結果も、イベントループを経て同じ経路で出す
        self._thread.finished_job.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._cache_hit.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._thread.start()

    def request(self, index: int, zoom: float, priority: int = 0) -> None:
        """優先度は小さいほど先。結果は後で rendered で届く（キャッシュにあっても）。"""
        if self._stopped:
            return
        image = self._cache.get((index, zoom))
        if image is not None:
            self._cache.move_to_end((index, zoom))
            self._cache_hit.emit(index, zoom, image, self._queue.token())
        else:
            self._queue.put(index, zoom, priority)

    def cancel_except(self, indices: set[int]) -> None:
        self._queue.retain(set(indices))

    def invalidate(self, indices: Iterable[int] | None = None) -> None:
        """None は全消去。描画中・配送待ちの該当ページの結果も捨てる。"""
        pages = None if indices is None else set(indices)
        self._queue.invalidate(pages)
        for key in [k for k in self._cache if pages is None or k[0] in pages]:
            self._bytes -= self._cache.pop(key).sizeInBytes()

    def cached_bytes(self) -> int:
        return self._bytes

    def shutdown(self) -> None:
        """描画スレッドを止める（描画中の 1 枚は待つ）。何度呼んでもよい。"""
        if self._stopped:
            return
        self._stopped = True
        self._queue.close()
        self._thread.wait()
        self._cache.clear()
        self._bytes = 0

    def _deliver(self, index: int, zoom: float, image: QImage, token: int) -> None:
        if self._stopped or not self._queue.is_current(index, token):
            return
        self._store((index, zoom), image)
        self.rendered.emit(index, zoom, image)

    def _store(self, key: _Key, image: QImage) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return
        size = image.sizeInBytes()
        if size > self._limit:
            return  # 1 枚でも入りきらないものは届けるだけ
        self._cache[key] = image
        self._bytes += size
        while self._bytes > self._limit:
            _, old = self._cache.popitem(last=False)
            self._bytes -= old.sizeInBytes()
