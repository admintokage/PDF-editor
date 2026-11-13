"""1 つのウィンドウで複数の PDF をタブで開く（F-04、F-85〜F-85e）。"""
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QtMsgType, QTimer, Qt
from PySide6.QtWidgets import QLabel

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import messages
from pdfeditor.ui.main_window import MainWindow
from tests.helpers import open_window, select

REPO = Path(__file__).resolve().parents[2]


def _modify(window, tab) -> None:
    window.tab_widget.setCurrentWidget(tab)
    select(window, [0])
    window.rotate_selected(90)


def _loaded_thumbs(tab) -> set[int]:
    model = tab.thumbnails.model()
    return {r for r in range(model.rowCount()) if model.image(r) is not None}


def test_two_files_open_as_two_tabs_in_one_window(qtbot, simple_pdf, big_pdf):
    second = big_pdf(5)
    w = open_window(qtbot, simple_pdf, second)
    assert len(w.tabs()) == 2 and MainWindow.instances == [w]
    assert w.current_tab() is w.tabs()[1] and w.doc.path == second.resolve()
    assert w.tab_widget.tabsClosable() and w.tab_widget.isMovable() and w.tab_widget.documentMode()


def test_opening_an_open_file_switches_to_its_tab(qtbot, simple_pdf, big_pdf, monkeypatch):
    monkeypatch.chdir(simple_pdf.parent)
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    first = w.tabs()[0]
    assert w.open_path(Path(simple_pdf.name.upper()))  # 相対パス・大文字小文字の違い
    assert len(w.tabs()) == 2 and w.current_tab() is first
    assert w.open_path(big_pdf(5)) and w.current_tab() is w.tabs()[1] and len(w.tabs()) == 2


def test_drop_opens_every_pdf_and_ignores_others(qtbot, simple_pdf, mixed_size_pdf, big_pdf,
                                                 tmp_path):
    other = tmp_path / "a.txt"; other.write_text("x")
    w = MainWindow(); qtbot.addWidget(w)
    w.handle_dropped_paths([simple_pdf, other, mixed_size_pdf, big_pdf(4)])
    assert [t.doc.page_count for t in w.tabs()] == [3, 3, 4]
    assert [w.tab_widget.tabText(i) for i in range(3)] == ["simple.pdf", "mixed.pdf", "big_4.pdf"]


def test_switching_tabs_switches_title_thumbnails_and_undo(qtbot, simple_pdf, big_pdf):
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    a, b = w.tabs()
    _modify(w, a)
    assert w.undo_action.isEnabled() and messages.CMD_ROTATE_PAGES in w.undo_action.text()
    assert w.windowTitle() == "simple.pdf[*] - PDF-editor" and w.isWindowModified()
    w.tab_widget.setCurrentWidget(b)
    assert w.windowTitle() == "big_5.pdf[*] - PDF-editor" and not w.isWindowModified()
    assert w.thumbnails.model().rowCount() == 5 and w.page_view is b.page_view
    assert not w.undo_action.isEnabled() and messages.CMD_ROTATE_PAGES not in w.undo_action.text()
    w.tab_widget.setCurrentWidget(a)
    assert w.undo_action.isEnabled() and w.thumbnails.model().rowCount() == 3
    w.undo_action.trigger()
    assert a.doc.page_rotation(0) == 0 and not a.is_modified() and not w.isWindowModified()
    assert b.doc.page_rotation(0) == 0 and b.undo_stack.count() == 0


def test_tab_label_marks_unsaved_and_tooltip_is_full_path(qtbot, simple_pdf, big_pdf):
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    _modify(w, w.tabs()[0])
    assert [w.tab_widget.tabText(i) for i in range(2)] == ["simple.pdf*", "big_5.pdf"]
    assert w.tab_widget.tabToolTip(0) == str(simple_pdf.resolve())
    assert w.save()
    assert w.tab_widget.tabText(0) == "simple.pdf"


def test_close_current_tab_asks_and_empty_window_shows_hint(qtbot, simple_pdf, monkeypatch):
    asked, answers = [], iter(["cancel", "discard"])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved",
                        lambda p, name: (asked.append(name), next(answers))[1])
    w = open_window(qtbot, simple_pdf)
    _modify(w, w.current_tab())
    assert w.close_current_tab() is False and len(w.tabs()) == 1 and asked == ["simple.pdf"]
    assert w.close_current_tab() is True and w.tabs() == [] and w.doc is None
    hint = [l for l in w.findChildren(QLabel) if l.text() == messages.EMPTY_HINT]
    assert hint and hint[0].isVisible()
    assert w.windowTitle() == messages.APP_NAME and not w.isWindowModified()
    for action in (w.save_action, w.save_as_action, w.close_tab_action, w.insert_blank_action,
                   w.insert_pdf_action, w.delete_pages_action, w.rotate_right_action,
                   w.zoom_in_action, w.undo_action):
        assert not action.isEnabled(), action.text()
    assert w.open_action.isEnabled() and w.isVisible()


def test_close_window_asks_each_unsaved_tab_and_cancel_keeps_the_rest(qtbot, simple_pdf, big_pdf,
                                                                       monkeypatch):
    """Review Focus 4: 未保存 3 つ、2 つ目で「キャンセル」→ 1 つ目だけ閉じる（F-85d）。"""
    w = open_window(qtbot, simple_pdf, big_pdf(4), big_pdf(5))
    for tab in w.tabs():
        _modify(w, tab)
    asked, answers = [], iter(["discard", "cancel"])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, name: (
        asked.append((name, w.current_tab().title())), next(answers))[1])
    assert w.close() is False
    assert asked == [("simple.pdf", "simple.pdf"), ("big_4.pdf", "big_4.pdf")]
    assert [t.title() for t in w.tabs()] == ["big_4.pdf", "big_5.pdf"]
    assert all(t.is_modified() for t in w.tabs())
    assert w.isVisible() and MainWindow.instances == [w]


def test_saving_blocks_tabs_open_and_close(qtbot, simple_pdf, big_pdf, monkeypatch):
    """Review Focus 5: 保存中はタブを閉じる・開く・切り替えるを受け付けない。"""
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    a, b = w.tabs()
    _modify(w, a)
    third = big_pdf(3)
    release = threading.Event()
    real = a.doc.save
    monkeypatch.setattr(a.doc, "save", lambda target=None: (release.wait(5), real(target))[1])
    seen = {}

    def during():
        seen["close"] = w.close_current_tab()
        seen["close_b"] = w.close_tab(1)
        seen["open"] = w.open_path(third)
        w.tab_widget.setCurrentIndex(1)
        seen["switch"] = w.current_tab() is a
        w.next_tab()
        seen["next"] = w.current_tab() is a
        seen["bar"] = w.tab_widget.tabBar().isEnabled()
        seen["actions"] = [x.isEnabled() for x in (w.close_tab_action, w.next_tab_action,
                                                   w.open_action, w.save_action)]
        seen["window"] = w.close()
        release.set()
    QTimer.singleShot(50, during)
    assert w.save() is True
    assert seen == {"close": False, "close_b": False, "open": False, "switch": True,
                    "next": True, "bar": False, "actions": [False] * 4, "window": False}
    assert w.tabs() == [a, b] and not a.is_modified()
    assert w.tab_widget.tabBar().isEnabled() and w.close_tab_action.isEnabled()


def test_save_belongs_to_the_tab_that_started_it(qtbot, simple_pdf, big_pdf):
    other = big_pdf(5)
    w = open_window(qtbot, simple_pdf, other)
    a, b = w.tabs()
    _modify(w, b)
    _modify(w, a)
    QTimer.singleShot(0, lambda: w.tab_widget.setCurrentWidget(b))  # 保存の直前に切り替えようとした
    assert w.save()
    assert w.current_tab() is a and not a.is_modified() and b.is_modified()
    assert PdfDocument.open(simple_pdf).page_rotation(0) == 90
    assert PdfDocument.open(other).page_rotation(0) == 0
    assert b.save() and PdfDocument.open(other).page_rotation(0) == 90  # 表示していないタブの保存
    assert w.current_tab() is a and not b.is_modified()


def test_inactive_tab_releases_render_memory(qtbot, simple_pdf, big_pdf):
    """F-85e: 表示していないタブは画像を持たず、描画サービスのキャッシュも空。戻すと描き直す。"""
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    a, b = w.tabs()
    w.tab_widget.setCurrentWidget(a)
    qtbot.waitUntil(lambda: bool(a.page_view._images) and _loaded_thumbs(a) == {0, 1, 2}
                    and a.render_service.cached_bytes() > 0)
    w.tab_widget.setCurrentWidget(b)
    assert a.page_view._images == {} and _loaded_thumbs(a) == set()
    assert a.render_service.cached_bytes() == 0
    qtbot.wait(300)  # 遅れて届く描画結果でまた溜まらない
    assert a.page_view._images == {} and a.render_service.cached_bytes() == 0
    w.tab_widget.setCurrentWidget(a)
    qtbot.waitUntil(lambda: bool(a.page_view._images) and _loaded_thumbs(a) == {0, 1, 2})
    assert b.render_service.cached_bytes() == 0 and b.page_view._images == {}


def test_set_active_directly_releases_and_requests_again(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf)
    tab = w.current_tab()
    qtbot.waitUntil(lambda: bool(tab.page_view._images) and _loaded_thumbs(tab) == {0, 1, 2})
    tab.set_active(False)
    assert tab.page_view._images == {} and _loaded_thumbs(tab) == set()
    assert tab.render_service.cached_bytes() == 0
    tab.set_active(True)
    qtbot.waitUntil(lambda: bool(tab.page_view._images) and _loaded_thumbs(tab) == {0, 1, 2})


def test_closed_tab_stops_its_render_thread(qtbot, simple_pdf, big_pdf, qtlog):
    w = open_window(qtbot, simple_pdf, big_pdf(5))
    tab = w.current_tab()
    thread, doc = tab.render_service._thread, tab.doc
    assert w.close_current_tab()
    assert thread.isFinished() and doc.fitz.is_closed and w.tabs() == [w.tabs()[0]]
    assert w.current_tab().doc.path == simple_pdf.resolve()
    qtbot.wait(50)  # deleteLater を済ませる
    assert not [r for r in qtlog.records
                if r.type != QtMsgType.QtDebugMsg and "propagateSizeHints" not in r.message]


def test_keyboard_closes_and_switches_tabs(qtbot, simple_pdf, big_pdf):
    w = open_window(qtbot, simple_pdf, big_pdf(4), big_pdf(5))
    assert w.close_tab_action.shortcut().toString() == "Ctrl+W"
    w.activateWindow(); w.page_view.setFocus()
    qtbot.waitUntil(w.page_view.hasFocus)
    qtbot.keyClick(w.page_view, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
    assert w.tab_widget.currentIndex() == 0
    qtbot.keyClick(w.page_view, Qt.Key.Key_Backtab,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert w.tab_widget.currentIndex() == 2
    qtbot.keyClick(w.page_view, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert [t.title() for t in w.tabs()] == ["simple.pdf", "big_4.pdf"]


SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from pdfeditor.ui import dialogs
    from pdfeditor.ui.main_window import MainWindow

    sys.excepthook = lambda t, v, tb: print("HOOK:", t.__name__, v, flush=True)
    app = QApplication([])
    dialogs.ask_unsaved = lambda parent, name: "discard"
    w = MainWindow()
    w.show()
    assert w.open_path(Path(sys.argv[1])) and w.open_path(Path(sys.argv[2]))
    w.tab_widget.setCurrentIndex(0)
    w.thumbnails.select_pages([0])
    w.delete_selected_pages()
    w.tab_widget.setCurrentIndex(1)
    assert w.tabs()[0].is_modified() and not w.isWindowModified()
    print("closed:", w.close(), flush=True)
    """
)


def test_no_error_at_exit_after_closing_window_with_two_tabs(simple_pdf, big_pdf, tmp_path):
    """tests/ui/test_shutdown.py と同じ確かめ方を、タブ 2 つ（片方は未保存）で行う。"""
    script = tmp_path / "close_two_tabs.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO))
    result = subprocess.run([sys.executable, str(script), str(simple_pdf), str(big_pdf(4))],
                            capture_output=True, text=True, env=env, timeout=60)
    output = result.stdout + result.stderr
    assert "closed: True" in output
    assert "HOOK:" not in output and "RuntimeError" not in output
    assert result.returncode == 0
