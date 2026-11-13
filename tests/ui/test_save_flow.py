import threading
import time

import pymupdf
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QProgressDialog

from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import main_window
from pdfeditor.ui.main_window import MainWindow
from pdfeditor.ui.page_commands import MovePagesCommand, push_command
from tests.helpers import lock_file, open_window, select

SAMPLE = (r"C:\Claude\PDF-editor\.superpowers\sdd\2026-11-13-stage1-viewer-pages-packaging"
          r"\task-11-sample-saved.pdf")


def test_save_clears_modified(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.save() and not w.isWindowModified()
    assert PdfDocument.open(simple_pdf).page_rotation(0) == 90
    assert simple_pdf.with_name(simple_pdf.stem + ".bak.pdf").exists()


def test_save_as_changes_title(qtbot, simple_pdf, tmp_path, monkeypatch):
    out = tmp_path / "別名.pdf"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    w = open_window(qtbot, simple_pdf); assert w.save_as() and "別名.pdf" in w.windowTitle() and out.exists()
    assert w.doc.path == out


def test_save_as_appends_pdf_extension(qtbot, simple_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda *a, **k: (str(tmp_path / "noext"), ""))
    w = open_window(qtbot, simple_pdf)
    assert w.save_as() and (tmp_path / "noext.pdf").exists()


def test_save_as_suggests_current_file(qtbot, simple_pdf, monkeypatch):
    seen = []
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda parent, title, start, *a, **k: (seen.append(start), ("", ""))[1])
    w = open_window(qtbot, simple_pdf)
    assert w.save_as() is False and seen == [str(simple_pdf)]


def test_save_as_cancel_shows_no_error(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: pytest.fail("error shown"))
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.save_as() is False and w.isWindowModified()


def test_undo_after_save_keeps_history(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.save() and w.undo_stack.canUndo()
    w.undo_stack.undo()
    assert w.isWindowModified() and w.doc.page_rotation(0) == 0


def test_save_actions_enabled_with_document(qtbot, simple_pdf):
    empty = MainWindow(); qtbot.addWidget(empty)
    assert not empty.save_action.isEnabled() and not empty.save_as_action.isEnabled()
    w = open_window(qtbot, simple_pdf)
    assert w.save_action.isEnabled() and w.save_as_action.isEnabled()


def test_save_failure_keeps_edits_and_offers_save_as(qtbot, simple_pdf, tmp_path, monkeypatch):
    out = tmp_path / "逃げ先.pdf"; calls = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: (calls.append(e.reason), "save_as")[1])
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    with lock_file(simple_pdf): assert w.save() is True
    assert calls == ["locked"] and out.exists() and w.doc.page_rotation(0) == 90


def test_save_failure_cancel_stays_modified(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: "cancel")
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    with lock_file(simple_pdf): assert w.save() is False
    assert w.isWindowModified() and w.undo_stack.canUndo()


def test_save_as_failure_loops_until_success(qtbot, simple_pdf, tmp_path, monkeypatch):
    good = tmp_path / "ok.pdf"
    bad = tmp_path / "no_such_dir" / "bad.pdf"
    paths = iter([str(bad), str(good)]); errors = []
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda *a, **k: (next(paths), ""))
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error",
                        lambda p, e: (errors.append(e.reason), "save_as")[1])
    w = open_window(qtbot, simple_pdf)
    assert w.save_as() is True and len(errors) == 1 and good.exists()


def test_unexpected_exception_is_reported_as_other(qtbot, simple_pdf, monkeypatch):
    reasons = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error",
                        lambda p, e: (reasons.append(e.reason), "cancel")[1])
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)

    def boom(target=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(w.doc, "save", boom)
    assert w.save() is False and reasons == ["other"]
    assert w.isWindowModified() and w.doc.page_rotation(0) == 90


def test_save_without_path_acts_as_save_as(qtbot, simple_pdf, tmp_path, monkeypatch):
    out = tmp_path / "new.pdf"
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    w = open_window(qtbot, simple_pdf)
    w.doc.path = None
    assert w.save() is True and out.exists()


def test_saving_blocks_reentry_and_edits(qtbot, simple_pdf, monkeypatch):
    w = open_window(qtbot, simple_pdf); select(w, [0])
    release = threading.Event()
    real = w.doc.save

    def slow(target=None):
        release.wait(5); real(target)
    monkeypatch.setattr(w.doc, "save", slow)
    seen = {}

    def during():
        seen["save2"] = w.save()
        seen["open"] = w.open_action.isEnabled()
        seen["page"] = w.rotate_right_action.isEnabled()
        seen["undo"] = w.undo_action.isEnabled()
        seen["save_action"] = w.save_action.isEnabled()
        seen["close"] = w.close()
        release.set()
    QTimer.singleShot(50, during)
    assert w.save() is True
    assert seen == {"save2": False, "open": False, "page": False, "undo": False,
                    "save_action": False, "close": False}
    assert w.open_action.isEnabled() and w.rotate_right_action.isEnabled()


def test_progress_only_when_slow(qtbot, simple_pdf, monkeypatch):
    shown = []
    monkeypatch.setattr(QProgressDialog, "show", lambda self: shown.append(self.labelText()))
    w = open_window(qtbot, simple_pdf)
    assert w.save() and shown == []  # 速い保存では出ない
    monkeypatch.setattr(main_window, "PROGRESS_DELAY_MS", 20)
    real = w.doc.save
    monkeypatch.setattr(w.doc, "save", lambda target=None: (time.sleep(0.4), real(target))[1])
    assert w.save() and shown == ["保存しています…"]


@pytest.mark.parametrize("answer,closes", [("discard", True), ("cancel", False), ("save", True)])
def test_close_with_unsaved_changes(qtbot, simple_pdf, monkeypatch, answer, closes):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, n: answer)
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    assert w.close() is closes
    if answer == "save":
        assert PdfDocument.open(simple_pdf).page_rotation(0) == 90


def test_close_save_failure_keeps_window(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, n: "save")
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_save_error", lambda p, e: "cancel")
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.rotate_selected(90)
    with lock_file(simple_pdf): assert w.close() is False
    assert w.doc is not None and w.isWindowModified()


def test_close_without_changes_does_not_ask(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda p, n: pytest.fail("asked"))
    assert open_window(qtbot, simple_pdf).close()


def test_saved_file_has_no_repair_warnings_and_keeps_toc(qtbot, toc_links_pdf, tmp_path, monkeypatch):
    other = tmp_path / "other.pdf"
    src = pymupdf.open()
    for i in range(2):
        src.new_page().insert_text((72, 100), f"Ins {i}")
    src.save(other); src.close()
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page", lambda p, size: (595.0, 842.0))
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(other), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName",
                        lambda *a, **k: (SAMPLE, ""))
    w = open_window(qtbot, toc_links_pdf)
    push_command(w.undo_stack, MovePagesCommand(w.doc, [0], 4, w.document_changed.emit))  # 並べ替え
    select(w, [3]); w.delete_selected_pages()
    select(w, [0]); w.rotate_selected(90)
    select(w, [1]); w.insert_blank_page()
    select(w, [0]); w.insert_pdf_pages()
    assert w.save_as()
    pymupdf.TOOLS.mupdf_warnings()  # 溜まった分を捨てる
    saved = pymupdf.open(SAMPLE)
    for page in saved:
        page.get_text(); page.get_links(); page.get_pixmap(dpi=36)
    assert pymupdf.TOOLS.mupdf_warnings() == ""
    assert saved.page_count == 5 - 1 + 1 + 2
    assert len(saved.get_toc()) >= 1
    saved.close()
