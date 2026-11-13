import sys

import pytest
from PySide6.QtCore import QtMsgType

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


@pytest.fixture
def user_and_owner_restricted_pdf(tmp_path):
    """開くパスワード "user" と、編集を制限する権限パスワード "owner" の両方がある。"""
    import pymupdf
    doc = pymupdf.open(); doc.new_page()
    path = tmp_path / "both.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner",
             permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY)
    doc.close()
    return path


def test_owner_prompt_after_correct_user_password_does_not_say_wrong(
        qtbot, user_and_owner_restricted_pdf, monkeypatch):
    asked = []
    answers = iter(["user", "bad", "owner"])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password",
                        lambda p, k: asked.append((k, p.password_retry)) or next(answers))
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(user_and_owner_restricted_pdf) and w.doc is not None
    assert asked == [("user", False), ("owner", False), ("owner", True)]


def test_failure_while_showing_document_closes_it_and_keeps_window_usable(
        qtbot, simple_pdf, mixed_size_pdf, monkeypatch):
    from pdfeditor.core.document import PdfDocument
    shown, closed = [], []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    real_size, real_close = PdfDocument.page_size, PdfDocument.close
    monkeypatch.setattr(PdfDocument, "page_size",
                        lambda self, i: (_ for _ in ()).throw(RuntimeError("code=7")))
    monkeypatch.setattr(PdfDocument, "close", lambda self: (closed.append(1), real_close(self)))
    w = MainWindow(); qtbot.addWidget(w); w.show()
    assert w.open_path(simple_pdf) is False
    assert w.doc is None and w.render_service is None and closed
    assert len(shown) == 1 and "「simple.pdf」を表示できません" in shown[0].user_message
    assert not w.save_action.isEnabled() and not w.zoom_in_action.isEnabled()
    monkeypatch.setattr(PdfDocument, "page_size", real_size)
    assert w.open_path(mixed_size_pdf) and w.doc.page_count == 3  # そのまま次を開ける


def test_relative_path_and_absolute_path_are_the_same_window(qtbot, simple_pdf, tmp_path, monkeypatch):
    monkeypatch.chdir(simple_pdf.parent)
    w = MainWindow(); qtbot.addWidget(w)
    assert w.open_path(simple_pdf.__class__(simple_pdf.name))
    monkeypatch.chdir(tmp_path.parent)
    assert w.doc.path == simple_pdf.resolve()
    assert w.open_path(simple_pdf) is True and len(MainWindow.instances) == 1


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


def test_view_menu_shortcuts_and_enabling(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w)
    actions = [w.zoom_in_action, w.zoom_out_action, w.fit_width_action, w.fit_page_action]
    assert [a.shortcut().toString() for a in actions] == ["Ctrl++", "Ctrl+-", "Ctrl+1", "Ctrl+0"]
    assert [s.toString() for s in w.zoom_in_action.shortcuts()] == ["Ctrl++", "Ctrl+;", "Ctrl+="]
    assert w.view_menu.actions() == actions
    assert not any(a.isEnabled() for a in actions)
    w.open_path(simple_pdf)
    assert all(a.isEnabled() for a in actions)


def test_view_actions_drive_page_view(qtbot, simple_pdf):
    w = MainWindow(); qtbot.addWidget(w); w.show()
    w.open_path(simple_pdf)
    w.page_view.set_zoom(1.0)
    w.zoom_in_action.trigger(); assert abs(w.page_view.zoom - 1.25) < 1e-9
    w.zoom_out_action.trigger(); assert abs(w.page_view.zoom - 1.0) < 1e-9
    w.fit_width_action.trigger()
    vp = w.page_view.viewport()
    assert abs(w.page_view.page_rect(0).width() - (vp.width() - 24)) <= 2


def test_open_shows_pages_and_close_shuts_service_first(qtbot, simple_pdf, qtlog):
    w = MainWindow(); qtbot.addWidget(w); w.show()
    assert w.render_service is None
    w.open_path(simple_pdf)
    qtbot.waitExposed(w)
    assert w.page_view.doc is w.doc and w.page_view.service is w.render_service
    assert w.page_view.visible_pages()
    order = []
    service, doc = w.render_service, w.doc
    orig_shutdown, orig_close = service.shutdown, doc.close
    service.shutdown = lambda: (order.append("shutdown"), orig_shutdown())
    doc.close = lambda: (order.append("close"), orig_close())
    w.close()
    assert order == ["shutdown", "close"]
    assert w.render_service is None and w.page_view.doc is None
    # offscreen プラグイン自身が出す propagateSizeHints の注意以外は出ていない
    assert not [r for r in qtlog.records
                if r.type != QtMsgType.QtDebugMsg and "propagateSizeHints" not in r.message]


def test_document_changed_reloads_layout(qtbot, big_pdf):
    w = MainWindow(); qtbot.addWidget(w); w.show()
    w.open_path(big_pdf(5))
    calls = []
    orig = w.render_service.invalidate
    w.render_service.invalidate = lambda indices=None: (calls.append(indices), orig(indices))
    w.doc.delete_pages([0, 1])
    w.document_changed.emit()
    assert calls == [None]
    assert w.page_view.page_rect(2).isValid() and not w.page_view.page_rect(3).isValid()


def test_excepthook_writes_log(tmp_path, monkeypatch):
    monkeypatch.setattr("pdfeditor.app._show_fatal", lambda msg: None)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # テスト後に元へ戻す
    install_excepthook(tmp_path)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    assert "boom" in (tmp_path / "pdf-editor.log").read_text(encoding="utf-8")
