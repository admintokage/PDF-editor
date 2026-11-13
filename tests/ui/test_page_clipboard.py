"""アプリ内のページ用クリップボードと、ページのコピー・切り取り・貼り付け（F-86）。"""
import binascii
import threading

import pymupdf
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import messages, page_clipboard
from pdfeditor.ui.main_window import MainWindow
from tests.helpers import labels, open_window, select


def _pdf(path, numbers):
    doc = pymupdf.open()
    for n in numbers:
        doc.new_page().insert_text((72, 100), f"Page {n}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def two(tmp_path):
    """A はページ 11〜15、B はページ 1〜3（どちらの文書のページか区別できるように）。"""
    return _pdf(tmp_path / "a.pdf", range(11, 16)), _pdf(tmp_path / "b.pdf", range(1, 4))


def _show(window, tab):
    window.tab_widget.setCurrentWidget(tab)


def _hits(fitz_doc, text: str) -> bool:
    raw = text.encode()
    needles = [raw, binascii.hexlify(raw), binascii.hexlify(raw).upper()]
    for x in range(1, fitz_doc.xref_length()):
        data = fitz_doc.xref_object(x, compressed=True).encode("utf-8", "replace")
        if fitz_doc.xref_is_stream(x):
            data += fitz_doc.xref_stream(x) or b""
        if any(n in data for n in needles):
            return True
    return False


# --- クリップボード -------------------------------------------------------------
def test_clipboard_set_and_clear(qtbot):
    board = page_clipboard.clipboard()
    assert board is page_clipboard.clipboard()  # アプリに 1 つ
    assert board.data() is None and board.count() == 0
    with qtbot.waitSignal(board.changed):
        board.set_pages(b"%PDF-data", 2)
    assert board.data() == b"%PDF-data" and board.count() == 2
    with qtbot.waitSignal(board.changed):
        board.clear()
    assert board.data() is None and board.count() == 0


def test_clipboard_does_not_show_its_bytes(qtbot):
    board = page_clipboard.clipboard()
    board.set_pages(b"SECRET-BYTES", 1)
    assert "SECRET" not in repr(board)


# --- コピー・切り取り・貼り付け ---------------------------------------------------
def test_copy_from_a_and_paste_into_b(qtbot, two):
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [1, 2])
    assert w.copy_selected_pages() is True
    assert page_clipboard.clipboard().count() == 2
    assert labels(a.doc) == [11, 12, 13, 14, 15] and not a.is_modified()
    assert a.undo_stack.count() == 0
    _show(w, b); select(w, [0])
    assert w.paste_pages() is True
    assert labels(b.doc) == [1, 12, 13, 2, 3]
    assert w.thumbnails.selected_pages() == [1, 2] and w.page_view.current_page() == 1
    assert w.undo_stack.undoText() == messages.CMD_PASTE_PAGES
    assert messages.CMD_PASTE_PAGES in w.undo_action.text() and w.isWindowModified()
    w.undo_action.trigger()
    assert labels(b.doc) == [1, 2, 3] and not b.is_modified()
    assert not a.is_modified()


def test_cut_then_paste_then_undo_cut_duplicates_safely(qtbot, two, tmp_path):
    """Review Focus 3: 切り取り → 貼り付け → 切り取り元で元に戻す → 両方にある。"""
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [0, 3])
    assert w.cut_selected_pages() is True
    assert labels(a.doc) == [12, 13, 15] and w.undo_stack.undoText() == messages.CMD_CUT_PAGES
    assert w.thumbnails.selected_pages() == [0]
    _show(w, b); select(w, [])
    assert w.paste_pages()  # 選択がなければ末尾
    assert labels(b.doc) == [1, 2, 3, 11, 14]
    _show(w, a); w.undo_action.trigger()
    assert labels(a.doc) == [11, 12, 13, 14, 15] and labels(b.doc) == [1, 2, 3, 11, 14]
    for tab, name in ((a, "a2.pdf"), (b, "b2.pdf")):
        tab.doc.save(tmp_path / name)
        again = PdfDocument.open(tmp_path / name)
        assert labels(again) == labels(tab.doc)
        for i in range(again.page_count):
            assert again.render(i, 0.2).width > 0
        again.close()


def test_paste_into_same_document_three_times(qtbot, two):
    """Review Focus 2: 同じ PDF に、同じ内容を 3 回続けて貼り付ける。"""
    w = open_window(qtbot, two[0])
    select(w, [0, 1]); w.copy_selected_pages()
    select(w, [3])
    for _ in range(3):
        assert w.paste_pages()
    assert labels(w.doc) == [11, 12, 13, 14, 11, 12, 11, 12, 11, 12, 15]
    assert w.thumbnails.selected_pages() == [8, 9]
    for expected in ([11, 12, 13, 14, 11, 12, 11, 12, 15], [11, 12, 13, 14, 11, 12, 15],
                     [11, 12, 13, 14, 15]):
        w.undo_action.trigger()
        assert labels(w.doc) == expected
    assert not w.isWindowModified()


def test_paste_after_source_tab_closed(qtbot, two):
    """Review Focus 1 / F-86d: 元のタブを閉じた後でも貼り付けられる。"""
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [4]); w.copy_selected_pages()
    assert w.close_tab(0)
    assert w.current_tab() is b
    select(w, [2]); assert w.paste_pages()
    assert labels(b.doc) == [1, 2, 3, 15]


def test_paste_keeps_content_from_copy_time(qtbot, two):
    """Review Focus 1: コピー後に元を回転・保存しても、貼り付けるのはコピーした時点の内容。"""
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [0]); w.copy_selected_pages()
    w.rotate_selected(90); assert w.save()
    _show(w, b); select(w, [0]); assert w.paste_pages()
    assert labels(b.doc) == [1, 11, 2, 3] and b.doc.page_rotation(1) == 0
    assert PdfDocument.open(two[0]).page_rotation(0) == 90


def test_cutting_every_page_is_refused_and_changes_nothing(qtbot, two, monkeypatch):
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    page_clipboard.clipboard().set_pages(b"before", 7)
    w = open_window(qtbot, two[1])
    select(w, [0, 1, 2])
    assert w.cut_selected_pages() is False
    assert len(shown) == 1 and "最後の 1 ページ" in shown[0].user_message
    assert labels(w.doc) == [1, 2, 3] and w.undo_stack.count() == 0
    board = page_clipboard.clipboard()
    assert (board.data(), board.count()) == (b"before", 7)


def test_failed_removal_leaves_clipboard_as_it_was(qtbot, two, monkeypatch):
    """書き出せても、取り除くのに失敗したら、クリップボードは試す前のまま。"""
    from pdfeditor.core import errors
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    page_clipboard.clipboard().set_pages(b"before", 7)
    w = open_window(qtbot, two[0])
    monkeypatch.setattr(w.doc, "delete_pages", lambda indices: (_ for _ in ()).throw(
        errors.PageOperationError("だめ")))
    select(w, [1])
    assert w.cut_selected_pages() is False and len(shown) == 1
    assert (page_clipboard.clipboard().data(), page_clipboard.clipboard().count()) == (b"before", 7)
    assert labels(w.doc) == [11, 12, 13, 14, 15]


def test_paste_with_empty_clipboard_does_nothing(qtbot, two):
    w = open_window(qtbot, two[1])
    assert not w.paste_action.isEnabled()
    assert w.paste_pages() is False and w.current_tab().paste_pages() is False
    assert labels(w.doc) == [1, 2, 3] and w.undo_stack.count() == 0


def test_copy_from_password_pdf_and_save_into_plain_pdf(qtbot, user_pw_pdf, simple_pdf,
                                                        monkeypatch):
    """Review Focus 6: パスワード付きからコピーし、パスワードなしの PDF に貼り付けて保存。"""
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: "user")
    w = open_window(qtbot, user_pw_pdf, simple_pdf)
    src, dst = w.tabs()
    _show(w, src); select(w, [0]); assert w.copy_selected_pages()
    _show(w, dst); select(w, [2]); assert w.paste_pages() and w.save()
    saved = PdfDocument.open(simple_pdf)  # パスワードなしで開ける
    assert saved.page_count == 4 and not saved.fitz.is_encrypted
    assert "Page 1" in saved.fitz[3].get_text()
    page = saved.render(3, 0.5)
    assert len(set(page.samples)) > 1  # 白紙ではない
    saved.close()


def test_cut_and_saved_file_keeps_nothing_of_the_cut_page(qtbot, two):
    """F-86g: 切り取って保存した PDF には、切り取ったページの内容を残さない。"""
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [1]); assert w.cut_selected_pages() and w.save()
    _show(w, b); select(w, [0]); assert w.paste_pages()
    saved = pymupdf.open(two[0])
    assert saved.page_count == 4 and not _hits(saved, "Page 12") and _hits(saved, "Page 13")
    saved.close()


def test_page_menu_and_context_menu_entries(qtbot, two):
    w = open_window(qtbot, two[1])
    clip = [w.cut_action, w.copy_action, w.paste_action]
    assert [a.text() for a in clip] == [messages.ACTION_CUT_PAGES, messages.ACTION_COPY_PAGES,
                                        messages.ACTION_PASTE_PAGES]
    assert [a.shortcut().toString() for a in clip] == ["Ctrl+X", "Ctrl+C", "Ctrl+V"]
    assert all(a.shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut for a in clip)
    assert all(a in w.page_menu.actions() and a in w.thumbnails.actions() for a in clip)


def test_menu_enabling_follows_selection_and_clipboard(qtbot, two):
    empty = MainWindow(); qtbot.addWidget(empty)
    w = open_window(qtbot, two[1])
    select(w, [])
    assert not w.copy_action.isEnabled() and not w.cut_action.isEnabled()
    assert not w.paste_action.isEnabled()
    select(w, [1])
    assert w.copy_action.isEnabled() and w.cut_action.isEnabled()
    w.copy_selected_pages()
    assert w.paste_action.isEnabled()
    assert not empty.paste_action.isEnabled()  # 文書がなければ貼り付けられない
    page_clipboard.clipboard().clear()
    assert not w.paste_action.isEnabled()


def test_paste_refused_while_saving(qtbot, two, monkeypatch):
    """Review Focus 5: 保存中は貼り付けを受け付けない。"""
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    select(w, [0]); w.copy_selected_pages()
    release = threading.Event()
    real = b.doc.save
    monkeypatch.setattr(b.doc, "save", lambda target=None: (release.wait(5), real(target))[1])
    seen = {}

    def during():
        seen["action"] = w.paste_action.isEnabled()
        seen["window"] = w.paste_pages()
        seen["tab"] = b.paste_pages()
        seen["other_tab"] = a.paste_pages()
        release.set()
    QTimer.singleShot(50, during)
    assert w.save()
    assert seen == {"action": False, "window": False, "tab": False, "other_tab": False}
    assert labels(a.doc) == [11, 12, 13, 14, 15] and labels(b.doc) == [1, 2, 3]


def test_keys_in_thumbnail_list(qtbot, two):
    QGuiApplication.clipboard().setText("system text")
    w = open_window(qtbot, *two)
    a, b = w.tabs()
    _show(w, a); select(w, [2])
    w.activateWindow(); w.thumbnails.setFocus()
    qtbot.waitUntil(w.thumbnails.hasFocus)
    qtbot.keyClick(w.thumbnails, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert page_clipboard.clipboard().count() == 1
    assert QGuiApplication.clipboard().text() == "system text"  # OS のクリップボードには置かない
    qtbot.keyClick(w.thumbnails, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert labels(a.doc) == [11, 12, 13, 13, 14, 15]
    select(w, [0])
    qtbot.keyClick(w.thumbnails, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert labels(a.doc) == [12, 13, 13, 14, 15]
    _show(w, b); select(w, [])
    w.thumbnails.setFocus(); qtbot.waitUntil(w.thumbnails.hasFocus)
    qtbot.keyClick(w.thumbnails, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert labels(b.doc) == [1, 2, 3, 11]


def test_keys_in_page_view_do_not_touch_pages(qtbot, two):
    w = open_window(qtbot, two[0])
    select(w, [1])
    w.activateWindow(); w.page_view.setFocus()
    qtbot.waitUntil(w.page_view.hasFocus)
    qtbot.keyClick(w.page_view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(w.page_view, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert page_clipboard.clipboard().count() == 0 and labels(w.doc) == [11, 12, 13, 14, 15]
