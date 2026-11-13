"""連続スクロール表示のテスト。"""
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui.page_view import PageView
from pdfeditor.ui.render_service import RenderService
from tests.helpers import make_view


def _record_requests(service: RenderService) -> list[tuple[int, float]]:
    asked: list[tuple[int, float]] = []
    orig = service.request

    def recording(i, z, priority=0):
        asked.append((i, z))
        orig(i, z, priority)

    service.request = recording
    return asked


# ---- 仕様のテスト ----

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


# ---- 追加のテスト ----

@pytest.mark.parametrize("zoom", [0.1, 0.25, 1.0, 3.0])
def test_scroll_to_page_makes_it_current(qtbot, big_pdf, zoom):
    v = make_view(qtbot, big_pdf(50))
    v.set_zoom(zoom)
    for i in (0, 1, 25, 48, 49, 25, 0):
        got = []
        v.current_page_changed.connect(got.append)
        before = v.current_page()
        v.scroll_to_page(i)
        v.current_page_changed.disconnect(got.append)
        assert v.current_page() == i
        assert got == ([] if before == i else [i])  # 途中のページを経由して知らせない


def test_current_page_is_page_at_top(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(10))
    v.set_zoom(0.5)
    vbar = v.verticalScrollBar()
    v.scroll_to_page(3)
    top = vbar.value()
    vbar.setValue(top - 1)  # 上のページの下余白が 1px 見えるだけ
    assert v.current_page() == 3
    vbar.setValue(top - 14)  # 上のページの本体が 2px 見える
    assert v.current_page() == 2
    vbar.setValue(top + 100)  # ページ 3 の途中
    assert v.current_page() == 3


def test_pinned_page_released_by_user_scroll(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    v.set_zoom(0.1)
    vbar = v.verticalScrollBar()
    v.scroll_to_page(49)  # 端なので上端までは送れない
    assert vbar.value() == vbar.maximum() and v.current_page() == 49
    with qtbot.waitSignal(v.current_page_changed) as sig:
        vbar.setValue(vbar.value() - 1)
    assert sig.args[0] < 49 and v.current_page() == sig.args[0]


def test_pinned_page_survives_zoom_and_reload(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    v.set_zoom(0.1)
    v.scroll_to_page(48)
    v.zoom_in(); assert v.current_page() == 48
    v.zoom_out(); assert v.current_page() == 48
    v.service.invalidate(); v.reload_layout()
    assert v.current_page() == 48


def test_zoom_keeps_current_page_at_various_zooms(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    for zoom in (0.1, 0.25, 1.0, 3.0):
        v.set_zoom(zoom)
        v.scroll_to_page(20)
        v.verticalScrollBar().setValue(v.verticalScrollBar().value() + 5)  # 少しずらす
        v.zoom_in(); assert v.current_page() == 20
        v.zoom_out(); assert v.current_page() == 20


def test_rotation_refits_width(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.doc.rotate_by([0], 90); v.service.invalidate(); v.reload_layout()
    r = v.page_rect(0)
    assert abs(r.width() - (v.viewport().width() - 24)) <= 2
    assert abs(r.height() / r.width() - 595 / 842) < 0.01


def test_no_document_is_safe(qtbot):
    v = PageView(); qtbot.addWidget(v); v.resize(600, 400); v.show()
    qtbot.waitExposed(v)
    v.set_zoom(2.0); v.zoom_in(); v.zoom_out(); v.fit_width(); v.fit_page()
    v.scroll_to_page(3); v.reload_layout(); v.resize(700, 500)
    assert not v.grab().isNull()
    assert v.visible_pages() == [] and v.current_page() == -1
    assert not v.page_rect(0).isValid()
    assert v.view_to_page(QPointF(10, 10)) is None


def test_initial_zoom_is_fit_width(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    assert abs(v.page_rect(0).width() - (v.viewport().width() - 24)) <= 2


def test_fit_width_follows_resize(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.resize(600, 1000)
    qtbot.waitUntil(lambda: abs(v.page_rect(0).width() - (v.viewport().width() - 24)) <= 2)


def test_explicit_zoom_leaves_fit_mode(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.set_zoom(0.5)
    v.resize(600, 1000)
    qtbot.wait(50)
    assert v.zoom == 0.5


def test_fit_page_shows_whole_page(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.scroll_to_page(1)
    v.fit_page()
    r = v.page_rect(v.current_page())
    vp = v.viewport().rect()
    assert v.current_page() == 1
    assert r.height() <= vp.height() - 24 + 1 and r.width() <= vp.width() - 24 + 1
    assert r.top() >= -1 and r.bottom() <= vp.height() + 1


def test_narrow_page_is_centered(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.set_zoom(0.5)
    assert abs(v.page_rect(0).center().x() - v.viewport().width() / 2) <= 1
    assert v.horizontalScrollBar().maximum() == 0


def test_wide_page_gets_horizontal_scroll(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.set_zoom(3.0)
    hbar = v.horizontalScrollBar()
    assert hbar.maximum() > 0
    left, value = v.page_rect(0).left(), hbar.value()
    hbar.setValue(value - 100)
    assert abs(v.page_rect(0).left() - (left + 100)) <= 1


def test_mixed_sizes_laid_out_individually(qtbot, mixed_size_pdf):
    v = make_view(qtbot, mixed_size_pdf, size=(1200, 1000))
    v.set_zoom(1.0)
    rects = [v.page_rect(i) for i in range(3)]
    for r, size in zip(rects, [(595, 842), (842, 595), (516, 729)]):
        assert abs(r.width() - size[0]) <= 1 and abs(r.height() - size[1]) <= 1
    for upper, lower in zip(rects, rects[1:]):
        assert abs(lower.top() - upper.bottom() - 12) <= 1
        assert abs(lower.center().x() - upper.center().x()) <= 1


def test_requests_use_device_pixel_ratio(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    asked = _record_requests(v.service)
    v.set_zoom(1.5)
    qtbot.waitUntil(lambda: bool(asked))
    expected = round(1.5 * v.devicePixelRatioF(), 4)
    assert {z for _, z in asked} == {expected}


def test_zoom_steps_are_debounced(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(10))
    asked = _record_requests(v.service)
    for _ in range(4):
        v.zoom_in()
    assert asked == []  # まだ頼まない
    qtbot.waitUntil(lambda: bool(asked))
    qtbot.wait(150)
    assert {z for _, z in asked} == {round(v.zoom * v.devicePixelRatioF(), 4)}


def test_scroll_requests_immediately(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    asked = _record_requests(v.service)
    v.scroll_to_page(30)
    assert 30 in {i for i, _ in asked}


def test_rendered_page_is_painted(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    r = v.page_rect(0).toAlignedRect()

    def has_text() -> bool:
        img = v.viewport().grab().toImage()
        # 「Page 1」の文字は左上あたり。白でない画素があれば描画済み
        for x in range(r.left() + 5, r.left() + r.width() // 3, 2):
            for y in range(r.top() + 5, r.top() + r.height() // 6, 2):
                if img.pixelColor(x, y).lightness() < 100:
                    return True
        return False

    qtbot.waitUntil(has_text, timeout=5000)


def test_held_images_stay_near_viewport(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(60))
    for i in range(0, 60, 5):
        v.scroll_to_page(i)
        qtbot.wait(20)
    held = set(v._images)  # ビュー自身は近くのページの画像しか持たない
    near = set(v.visible_pages())
    near |= {min(near) - 1, max(near) + 1}
    assert held <= near


def test_current_page_changed_only_on_change(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    got = []
    v.current_page_changed.connect(got.append)
    v.scroll_to_page(30)
    v.scroll_to_page(30)
    v.verticalScrollBar().setValue(v.verticalScrollBar().value() + 1)
    assert got == [30]


def test_zoom_changed_signal(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    with qtbot.waitSignal(v.zoom_changed) as sig:
        v.set_zoom(2.0)
    assert sig.args == [2.0]
    with qtbot.assertNotEmitted(v.zoom_changed):
        v.set_zoom(2.0)


def test_zoom_in_out_factor(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    v.set_zoom(1.0); v.zoom_in(); assert abs(v.zoom - 1.25) < 1e-9
    v.zoom_out(); v.zoom_out(); assert abs(v.zoom - 0.8) < 1e-9


def test_keyboard_navigation(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(20))
    vbar = v.verticalScrollBar()
    assert vbar.value() == 0  # 開いた直後は先頭
    qtbot.keyClick(v, Qt.Key.Key_PageDown)
    assert vbar.value() == v.viewport().height()
    qtbot.keyClick(v, Qt.Key.Key_PageUp)
    assert vbar.value() == 0
    qtbot.keyClick(v, Qt.Key.Key_Down)
    assert 0 < vbar.value() < v.viewport().height()
    qtbot.keyClick(v, Qt.Key.Key_End)
    assert v.current_page() == 19 and vbar.value() == vbar.maximum()
    qtbot.keyClick(v, Qt.Key.Key_Home)
    assert v.current_page() == 0 and vbar.value() == 0


def test_ctrl_wheel_zooms_and_keeps_page(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(50))
    v.scroll_to_page(20)
    before = v.zoom
    pos = QPointF(v.viewport().width() / 2, v.viewport().height() / 2)
    event = QWheelEvent(pos, v.viewport().mapToGlobal(pos), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(v.viewport(), event)
    assert abs(v.zoom - before * 1.25) < 1e-6
    assert v.current_page() == 20


def test_ctrl_wheel_anchors_at_cursor(qtbot, simple_pdf):
    v = make_view(qtbot, simple_pdf)
    pos = QPointF(200, 300)
    before = v.view_to_page(pos)
    event = QWheelEvent(pos, v.viewport().mapToGlobal(pos), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(v.viewport(), event)
    after = v.view_to_page(pos)
    assert after[0] == before[0]
    assert abs(after[1] - before[1]) < 2 and abs(after[2] - before[2]) < 2


def test_plain_wheel_scrolls(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(10))
    pos = QPointF(100, 100)
    event = QWheelEvent(pos, v.viewport().mapToGlobal(pos), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    zoom = v.zoom
    QApplication.sendEvent(v.viewport(), event)
    assert v.verticalScrollBar().value() > 0 and v.zoom == zoom


def test_scroll_500_pages_end_to_end(qtbot, big_pdf):
    v = make_view(qtbot, big_pdf(500))
    vbar = v.verticalScrollBar()
    asked = _record_requests(v.service)
    seen = set()
    while vbar.value() < vbar.maximum():
        vbar.setValue(vbar.value() + vbar.pageStep())
        QApplication.processEvents()
        seen.add(v.current_page())
        assert len(v._images) <= len(v.visible_pages()) + 2
    assert v.current_page() == 499 and len(seen) > 400
    assert max(i for i, _ in asked) == 499
