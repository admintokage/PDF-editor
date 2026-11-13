"""描画サービスのテスト。"""
import threading
import time

from PySide6.QtGui import QImage

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.render_service import RenderService


def _gate_render(doc: PdfDocument):
    """doc.render を、呼ばれた順を記録し、release が立つまで止まる版に差し替える。
    戻り値は (呼ばれたページ番号の列, 描画開始を知らせる Event, 再開させる Event)。"""
    original = doc.render
    calls: list[int] = []
    started = threading.Event()
    release = threading.Event()

    def gated(index, zoom):
        calls.append(index)
        started.set()
        if not release.wait(timeout=10):
            raise RuntimeError("テストが release を立てませんでした")
        return original(index, zoom)

    doc.render = gated
    return calls, started, release


def _image_bytes(width: int, height: int) -> int:
    return QImage(width, height, QImage.Format.Format_RGB888).sizeInBytes()


# ---- 仕様のテスト ----

def test_request_emits_image(qtbot, simple_pdf):
    s = RenderService(PdfDocument.open(simple_pdf))
    with qtbot.waitSignal(s.rendered, timeout=3000) as sig:
        s.request(0, 1.0)
    idx, zoom, img = sig.args
    assert idx == 0 and (img.width(), img.height()) == (595, 842)
    s.shutdown()


def test_cache_is_bounded_over_500_pages(qtbot, big_pdf):
    s = RenderService(PdfDocument.open(big_pdf(500)), cache_bytes=20 * 1024 * 1024)
    for i in range(500):
        with qtbot.waitSignal(s.rendered, timeout=5000):
            s.request(i, 1.0)
    assert s.cached_bytes() <= 20 * 1024 * 1024
    s.shutdown()


def test_invalidate_forces_rerender(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    with qtbot.waitSignal(s.rendered):
        s.request(0, 1.0)
    d.rotate_by([0], 90)
    s.invalidate()
    with qtbot.waitSignal(s.rendered) as sig:
        s.request(0, 1.0)
    assert sig.args[2].width() == 842
    s.shutdown()


def test_cancel_except_drops_queued(qtbot, big_pdf):
    s = RenderService(PdfDocument.open(big_pdf(200)))
    got = []
    s.rendered.connect(lambda i, z, im: got.append(i))
    for i in range(200):
        s.request(i, 2.0)
    s.cancel_except({199})
    qtbot.waitUntil(lambda: 199 in got, timeout=10000)
    assert len(got) < 200
    s.shutdown()


# ---- 追加のテスト ----

def test_image_owns_data_and_has_no_device_pixel_ratio(qtbot, simple_pdf):
    s = RenderService(PdfDocument.open(simple_pdf))
    with qtbot.waitSignal(s.rendered) as sig:
        s.request(1, 0.5)
    img = sig.args[2]
    assert sig.args[0] == 1 and sig.args[1] == 0.5
    assert img.devicePixelRatio() == 1.0
    assert img.format() == QImage.Format.Format_RGB888
    assert not img.isNull() and img.pixelColor(0, 0).name() == "#ffffff"
    s.shutdown()


def test_cached_hit_is_delivered_asynchronously(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    with qtbot.waitSignal(s.rendered):
        s.request(0, 1.0)
    calls, _, release = _gate_render(d)
    release.set()
    got = []
    s.rendered.connect(lambda i, z, im: got.append(i))
    s.request(0, 1.0)
    assert got == []  # request の中では出さない
    qtbot.waitUntil(lambda: got == [0], timeout=3000)
    assert calls == []  # キャッシュから出た
    s.shutdown()


def test_stale_result_discarded_after_invalidate(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    calls, started, release = _gate_render(d)
    got = []
    s.rendered.connect(lambda i, z, im: got.append(i))
    s.request(0, 1.0)
    assert started.wait(timeout=3)  # ページ 0 を描画中
    s.invalidate()
    s.request(1, 1.0)
    release.set()
    with qtbot.waitSignal(s.rendered) as sig:
        pass
    # ページ 0 の結果はページ 1 より先に届くが、古い世代なので捨てられる
    assert sig.args[0] == 1 and got == [1]
    assert calls == [0, 1]
    assert s.cached_bytes() == sig.args[2].sizeInBytes()
    s.shutdown()


def test_invalidate_specific_pages_keeps_others(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    for i in (0, 1):
        with qtbot.waitSignal(s.rendered):
            s.request(i, 1.0)
    calls, _, release = _gate_render(d)
    release.set()
    s.invalidate([0])
    for i in (0, 1):
        with qtbot.waitSignal(s.rendered):
            s.request(i, 1.0)
    assert calls == [0]  # ページ 1 はキャッシュから
    s.shutdown()


def test_invalidate_specific_pages_keeps_unrelated_in_flight(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    _, started, release = _gate_render(d)
    s.request(2, 1.0)
    assert started.wait(timeout=3)
    s.invalidate([0])  # 描画中のページ 2 とは関係ない
    with qtbot.waitSignal(s.rendered) as sig:
        release.set()
    assert sig.args[0] == 2
    s.shutdown()


def test_out_of_range_request_is_dropped(qtbot, simple_pdf):
    s = RenderService(PdfDocument.open(simple_pdf))
    got = []
    s.rendered.connect(lambda i, z, im: got.append(i))
    s.request(99, 1.0, priority=0)
    s.request(0, 1.0, priority=1)  # 99 の後に描かれる
    qtbot.waitUntil(lambda: got == [0], timeout=3000)
    assert got == [0]
    s.shutdown()


def test_duplicate_requests_are_coalesced(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    s = RenderService(d)
    calls, started, release = _gate_render(d)
    s.request(0, 1.0)
    assert started.wait(timeout=3)
    for _ in range(3):
        s.request(1, 1.0)  # 待ち行列にあるものと重複
    s.request(0, 1.0)  # 描画中のものと重複
    release.set()
    with qtbot.waitSignal(s.rendered, check_params_cb=lambda i, z, im: i == 1):
        pass
    assert calls == [0, 1]
    s.shutdown()


def test_priority_then_most_recent_first(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(5))
    s = RenderService(d)
    calls, started, release = _gate_render(d)
    s.request(0, 0.5)
    assert started.wait(timeout=3)
    s.request(4, 0.5, priority=5)
    s.request(1, 0.5)
    s.request(2, 0.5)
    s.request(3, 0.5)
    release.set()
    with qtbot.waitSignal(s.rendered, check_params_cb=lambda i, z, im: i == 4):
        pass
    assert calls == [0, 3, 2, 1, 4]
    s.shutdown()


def test_cache_evicts_least_recently_used(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf)
    one = _image_bytes(595, 842)
    s = RenderService(d, cache_bytes=2 * one)
    for i in (0, 1):
        with qtbot.waitSignal(s.rendered):
            s.request(i, 1.0)
    with qtbot.waitSignal(s.rendered):
        s.request(0, 1.0)  # キャッシュから。0 が最近使ったものになる
    with qtbot.waitSignal(s.rendered):
        s.request(2, 1.0)  # 1 が追い出される
    assert s.cached_bytes() == 2 * one
    calls, _, release = _gate_render(d)
    release.set()
    for i in (0, 2, 1):
        with qtbot.waitSignal(s.rendered):
            s.request(i, 1.0)
    assert calls == [1]
    s.shutdown()


def test_image_larger_than_cache_is_delivered_not_cached(qtbot, simple_pdf):
    s = RenderService(PdfDocument.open(simple_pdf), cache_bytes=1000)
    with qtbot.waitSignal(s.rendered) as sig:
        s.request(0, 1.0)
    assert sig.args[2].width() == 595
    assert s.cached_bytes() == 0
    s.shutdown()


def test_shutdown_is_prompt_idempotent_and_disables_request(qtbot, big_pdf):
    s = RenderService(PdfDocument.open(big_pdf(200)))
    for i in range(200):
        s.request(i, 2.0)
    began = time.monotonic()
    s.shutdown()
    assert time.monotonic() - began < 2.0
    s.shutdown()
    with qtbot.assertNotEmitted(s.rendered, wait=300):
        s.request(0, 1.0)
