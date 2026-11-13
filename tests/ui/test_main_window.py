import sys

import pytest

from pdfeditor.app import install_excepthook
from pdfeditor.ui import messages
from pdfeditor.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """取りこぼしでモーダルが出てテストが止まらないようにする。"""
    def boom(*a, **k):
        raise AssertionError("unexpected modal dialog")
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_info", boom)
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", boom)
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", boom)


def test_open_path_loads_document(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    with qtbot.waitSignal(w.document_loaded):
        assert w.open_path(simple_pdf)
    assert w.doc.page_count == 3 and simple_pdf.name in w.windowTitle()


def test_open_bad_file_shows_error_and_does_not_crash(qtbot, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    bad = tmp_path / "x.pdf"; bad.write_bytes(b"nope")
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(bad) is False and w.doc is None and len(shown) == 1


def test_password_prompt_retries_until_success(qtbot, user_pw_pdf, monkeypatch):
    asked = []
    answers = iter(["bad", "user"])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password",
                        lambda p, k: asked.append((k, p.password_retry)) or next(answers))
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(user_pw_pdf) and w.doc is not None
    assert asked == [("user", False), ("user", True)]
    assert w.password_retry is False


def test_password_cancel_leaves_window_empty(qtbot, user_pw_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: None)
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(user_pw_pdf) is False and w.doc is None


def test_owner_password_prompts_owner_kind(qtbot, owner_pw_pdf, monkeypatch):
    kinds = []
    answers = iter(["wrong", "owner"])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password",
                        lambda p, k: kinds.append(k) or next(answers))
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(owner_pw_pdf) and w.doc is not None
    assert kinds == ["owner", "owner"]


def test_drop_pdf_opens(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    w.handle_dropped_paths([simple_pdf])
    assert w.doc.page_count == 3


def test_drop_ignores_non_pdf(qtbot, tmp_path):
    other = tmp_path / "a.txt"; other.write_text("x")
    w = MainWindow(); qtbot.addWidget(w)
    w.handle_dropped_paths([other])
    assert w.doc is None


def test_second_open_uses_new_window(qtbot, simple_pdf, mixed_size_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    w.open_path(simple_pdf); w.open_path(mixed_size_pdf)
    assert w.doc.path == simple_pdf and len(MainWindow.instances) == 2
    assert MainWindow.instances[1].doc.path == mixed_size_pdf


def test_opening_already_open_path_does_not_duplicate(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    w.open_path(simple_pdf)
    assert w.open_path(simple_pdf) is True
    assert len(MainWindow.instances) == 1


def test_failed_open_in_new_window_leaves_no_extra_window(qtbot, simple_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: None)
    bad = tmp_path / "x.pdf"; bad.write_bytes(b"nope")
    w = MainWindow(); qtbot.addWidget(w)
    w.open_path(simple_pdf)
    assert w.open_path(bad) is False
    assert MainWindow.instances == [w]


def test_closed_window_leaves_instances(qtbot):
    w = MainWindow(); qtbot.addWidget(w)
    assert w in MainWindow.instances
    w.close()
    assert w not in MainWindow.instances


def test_signed_pdf_shows_notice_once(qtbot, signed_flag_pdf, monkeypatch):
    notes = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_info", lambda p, t: notes.append(t))
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(signed_flag_pdf)
    assert notes == [messages.SIGNED_NOTICE]


def test_menus_and_actions(qtbot):
    w = MainWindow(); qtbot.addWidget(w)
    titles = [a.text() for a in w.menuBar().actions()]
    assert titles == [messages.MENU_FILE, messages.MENU_EDIT, messages.MENU_PAGE, messages.MENU_VIEW]
    assert w.open_action.shortcut().toString() == "Ctrl+O"
    assert w.save_action.shortcut().toString() == "Ctrl+S"
    assert w.save_as_action.shortcut().toString() == "Ctrl+Shift+S"
    assert w.undo_action.shortcut().toString() == "Ctrl+Z"
    assert w.redo_action.shortcut().toString() == "Ctrl+Y"
    assert not w.save_action.isEnabled()
    assert w.undo_action.text().startswith(messages.UNDO_PREFIX)


def test_excepthook_writes_log(tmp_path, monkeypatch):
    monkeypatch.setattr("pdfeditor.app._show_fatal", lambda msg: None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # テスト後に元へ戻す
    install_excepthook(tmp_path)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    assert "boom" in (tmp_path / "pdf-editor.log").read_text(encoding="utf-8")
