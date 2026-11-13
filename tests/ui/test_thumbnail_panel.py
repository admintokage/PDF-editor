import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QDialog

from pdfeditor.core.document import PAGE_SIZES
from pdfeditor.ui import dialogs, messages
from pdfeditor.ui.dialogs import ask_blank_page as real_ask_blank_page  # autouse で差し替える前に
from pdfeditor.ui.thumbnail_panel import THUMB_PRIORITY, THUMB_WIDTH
from tests.helpers import labels, open_window, select


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """取りこぼしでモーダルが出てテストが止まらないようにする。"""
    def boom(*a, **k):
        raise AssertionError("unexpected modal dialog")
    for name in ("show_info", "show_error", "ask_password", "ask_blank_page"):
        monkeypatch.setattr(f"pdfeditor.ui.dialogs.{name}", boom)
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", boom)


# --- brief のテスト ---------------------------------------------------------
def test_thumbnail_count_matches_pages(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(7)); assert w.thumbnails.model().rowCount() == 7


def test_drop_reorders_via_undo_stack(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); w.thumbnails.move_rows([0], 3)
    assert labels(w.doc) == [2, 3, 1, 4, 5] and w.undo_stack.undoText() == "ページの移動"
    w.undo_stack.undo(); assert labels(w.doc) == [1, 2, 3, 4, 5]


def test_delete_selected(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); select(w, [1, 2]); w.delete_selected_pages()
    assert labels(w.doc) == [1, 4, 5] and w.thumbnails.model().rowCount() == 3


def test_delete_all_shows_error(qtbot, simple_pdf, monkeypatch):
    shown = []; monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    w = open_window(qtbot, simple_pdf); select(w, [0, 1, 2]); w.delete_selected_pages()
    assert w.doc.page_count == 3 and "最後の 1 ページ" in shown[0].user_message
    assert w.undo_stack.count() == 0


def test_insert_blank_after_selection(qtbot, big_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page", lambda p, d: (595, 842))
    w = open_window(qtbot, big_pdf(3)); select(w, [0]); w.insert_blank_page()
    assert w.doc.page_count == 4 and labels(w.doc)[0] == 1 and w.doc.fitz[1].get_text().strip() == ""


def test_insert_pdf_with_password_prompt(qtbot, simple_pdf, user_pw_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(user_pw_pdf), ""))
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password", lambda p, k: "user")
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages(); assert w.doc.page_count > 3


def test_rotate_updates_view(qtbot, simple_pdf):
    # 幅合わせ表示では回転してもページの表示幅は変わらない（幅に合わせ直す）ので、
    # 大きさ（縦横）が変わることで確かめる
    w = open_window(qtbot, simple_pdf); select(w, [0]); before = w.page_view.page_rect(0).size()
    w.rotate_selected(90); assert w.doc.page_rotation(0) == 90 and w.page_view.page_rect(0).size() != before


def test_click_thumbnail_scrolls_view(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(30)); w.thumbnails.page_activated.emit(20)
    assert w.page_view.current_page() == 20


def test_title_shows_unsaved_mark(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); assert not w.isWindowModified()
    select(w, [0]); w.rotate_selected(90); assert w.isWindowModified()
    assert "[*]" in w.windowTitle()
    w.undo_stack.undo(); assert not w.isWindowModified()


# --- サムネイル画像 -----------------------------------------------------------
def _loaded_rows(panel) -> set[int]:
    return {r for r in range(panel.model().rowCount()) if panel.model().image(r) is not None}


def test_thumbnails_render_at_120px_width(qtbot, mixed_size_pdf):
    w = open_window(qtbot, mixed_size_pdf)
    model = w.thumbnails.model()
    qtbot.waitUntil(lambda: _loaded_rows(w.thumbnails) == {0, 1, 2})
    assert [model.thumb_zoom(r) for r in range(3)] == [
        round(120 / 595, 4), round(120 / 842, 4), round(120 / 516, 4)]
    assert all(abs(model.image(r).width() - THUMB_WIDTH) <= 1 for r in range(3))


def test_only_visible_rows_are_requested(qtbot, big_pdf, monkeypatch):
    w = open_window(qtbot, big_pdf(500))
    asked = []
    orig = w.render_service.request
    monkeypatch.setattr(w.render_service, "request",
                        lambda i, z, priority=0: (asked.append((i, priority)), orig(i, z, priority)))
    w.thumbnails.refresh()
    thumbs = [i for i, p in asked if p == THUMB_PRIORITY]
    assert thumbs and max(thumbs) < 20
    w.thumbnails.scrollToBottom(); qtbot.wait(10)
    thumbs = [i for i, p in asked if p == THUMB_PRIORITY]
    assert max(thumbs) == 499 and len(set(thumbs)) < 40


def test_ignores_results_for_other_zoom(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf)
    qtbot.waitUntil(lambda: len(_loaded_rows(w.thumbnails)) == 3)
    w.thumbnails.refresh()  # 画像を捨てる（キャッシュからまた届く前に調べる）
    w.render_service.rendered.emit(0, 1.0, QImage(10, 10, QImage.Format.Format_RGB888))
    assert w.thumbnails.model().image(0) is None


def test_rerequests_after_page_view_cancels_queue(qtbot, big_pdf):
    """ページ表示の cancel_except で捨てられたサムネイルの依頼は、時間をおいて頼み直す。"""
    w = open_window(qtbot, big_pdf(10))
    panel = w.thumbnails
    qtbot.waitUntil(lambda: bool(_loaded_rows(panel)))
    with w.doc.lock:  # 描画スレッドを止めた状態で、頼んでから捨てさせる
        w.render_service.invalidate()
        panel.refresh()
        visible = set(panel.rows_to_load())
        assert len(visible) > 2
        w.render_service.cancel_except({0})
    qtbot.waitUntil(lambda: visible <= _loaded_rows(panel), timeout=5000)


# --- 選択とページ表示の連動 ---------------------------------------------------
def test_page_view_scroll_highlights_without_feedback(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(30)); select(w, [1, 3])
    emitted = []
    w.thumbnails.page_activated.connect(emitted.append)
    w.page_view.scroll_to_page(12)
    assert w.thumbnails.shown_page() == 12
    assert emitted == [] and w.thumbnails.selected_pages() == [1, 3]
    rect = w.thumbnails.visualRect(w.thumbnails.model().index(12))
    assert w.thumbnails.viewport().rect().intersects(rect)


def test_mouse_click_on_thumbnail_scrolls_view(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(10))
    panel = w.thumbnails
    rect = panel.visualRect(panel.model().index(2))
    qtbot.mouseClick(panel.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert w.page_view.current_page() == 2 and panel.selected_pages() == [2]


# --- 操作後の選択 ---------------------------------------------------------------
def test_move_keeps_moved_pages_selected(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); select(w, [0, 1])
    w.thumbnails.move_rows([0, 1], 4)
    assert labels(w.doc) == [3, 4, 1, 2, 5] and w.thumbnails.selected_pages() == [2, 3]
    assert w.page_view.current_page() == 2


def test_noop_move_pushes_nothing(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5))
    w.thumbnails.move_rows([1, 2], 2); w.thumbnails.move_rows([1, 2], 3)
    assert w.undo_stack.count() == 0 and labels(w.doc) == [1, 2, 3, 4, 5]


def test_delete_selects_row_at_first_deleted_position(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); select(w, [1, 3]); w.delete_selected_pages()
    assert labels(w.doc) == [1, 3, 5] and w.thumbnails.selected_pages() == [1]
    select(w, [2]); w.delete_selected_pages()
    assert labels(w.doc) == [1, 3] and w.thumbnails.selected_pages() == [1]


def test_insert_blank_selects_inserted_and_uses_default_size(qtbot, mixed_size_pdf, monkeypatch):
    seen = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page",
                        lambda p, d: seen.append(d) or d)
    w = open_window(qtbot, mixed_size_pdf); select(w, [1]); w.insert_blank_page()
    assert seen == [(842, 595)] and w.doc.page_size(2) == (842, 595)
    assert w.thumbnails.selected_pages() == [2] and w.page_view.current_page() == 2


def test_insert_blank_without_selection_appends(qtbot, big_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page", lambda p, d: (842, 1191))
    w = open_window(qtbot, big_pdf(3)); select(w, []); w.insert_blank_page()
    assert labels(w.doc) == [1, 2, 3, 0] and w.doc.page_size(3) == (842, 1191)


def test_insert_blank_cancel_changes_nothing(qtbot, big_pdf, monkeypatch):
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_blank_page", lambda p, d: None)
    w = open_window(qtbot, big_pdf(3)); w.insert_blank_page()
    assert w.doc.page_count == 3 and w.undo_stack.count() == 0


def test_insert_pdf_into_itself_selects_inserted(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(simple_pdf), ""))
    w = open_window(qtbot, simple_pdf); select(w, [0]); w.insert_pdf_pages()
    assert labels(w.doc) == [1, 1, 2, 3, 2, 3]
    assert w.thumbnails.selected_pages() == [1, 2, 3] and w.page_view.current_page() == 1
    assert w.undo_stack.undoText() == messages.CMD_INSERT_PDF


def test_insert_pdf_password_retry_and_cancel(qtbot, simple_pdf, user_pw_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(user_pw_pdf), ""))
    asked = []
    answers = iter(["bad", None])
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_password",
                        lambda p, k: asked.append((k, p.password_retry)) or next(answers))
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages()
    assert asked == [("user", False), ("user", True)]
    assert w.doc.page_count == 3 and w.undo_stack.count() == 0 and w.password_retry is False


def test_insert_pdf_bad_file_shows_error(qtbot, simple_pdf, tmp_path, monkeypatch):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"nope")
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(bad), ""))
    shown = []; monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages()
    assert len(shown) == 1 and w.doc.page_count == 3 and w.undo_stack.count() == 0


def test_insert_pdf_missing_file_shows_error(qtbot, simple_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                        lambda *a, **k: (str(tmp_path / "gone.pdf"), ""))
    shown = []; monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages()
    assert len(shown) == 1 and "gone.pdf" in shown[0].user_message


def test_insert_pdf_dialog_cancel(qtbot, simple_pdf, monkeypatch):
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *a, **k: ("", ""))
    w = open_window(qtbot, simple_pdf); w.insert_pdf_pages(); assert w.doc.page_count == 3


def test_rotate_keeps_selection_and_left_rotation(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(4)); select(w, [1, 2])
    w.rotate_left_action.trigger()
    assert [w.doc.page_rotation(i) for i in range(4)] == [0, 270, 270, 0]
    assert w.thumbnails.selected_pages() == [1, 2]
    assert w.thumbnails.model().thumb_size(1)[0] < w.thumbnails.model().thumb_size(0)[0] or \
        w.thumbnails.model().thumb_size(1)[1] < w.thumbnails.model().thumb_size(0)[1]


def test_undo_from_edit_menu_refreshes_thumbnails(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf); select(w, [0])
    portrait = w.thumbnails.model().thumb_size(0)
    w.rotate_right_action.trigger()
    assert w.thumbnails.model().thumb_size(0) != portrait
    w.undo_action.trigger()
    assert w.doc.page_rotation(0) == 0 and w.thumbnails.model().thumb_size(0) == portrait
    w.redo_action.trigger()
    assert w.thumbnails.model().thumb_size(0) != portrait


def test_undo_delete_restores_rows(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); select(w, [0, 4]); w.delete_selected_pages()
    assert w.thumbnails.model().rowCount() == 3
    w.undo_action.trigger()
    assert w.thumbnails.model().rowCount() == 5 and labels(w.doc) == [1, 2, 3, 4, 5]


# --- ドラッグ＆ドロップの計算 -------------------------------------------------
class _InternalDrop(QDropEvent):
    def __init__(self, pos, mime, source):
        super().__init__(QPointF(pos), Qt.DropAction.MoveAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self._source = source

    def source(self):
        return self._source


def _drop(panel, rows, pos, source=None):
    model = panel.model()
    mime = model.mimeData([model.index(r) for r in rows])
    event = _InternalDrop(pos, mime, panel if source is None else source)
    panel.dropEvent(event)
    return event


def test_drop_row_between_items(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); panel = w.thumbnails
    r1 = panel.visualRect(panel.model().index(1))
    assert panel.drop_row(r1.topLeft() + QPoint(5, 2)) == 1
    assert panel.drop_row(r1.bottomLeft() + QPoint(5, -2)) == 2
    # 幅の外れ（右端）でも縦位置で決まる
    assert panel.drop_row(QPoint(panel.viewport().width() - 1, r1.top() + 2)) == 1
    last = panel.visualRect(panel.model().index(4))
    assert panel.drop_row(QPoint(5, last.bottom() + 30)) == 5


def test_drop_event_moves_multi_selection(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); panel = w.thumbnails
    select(w, [0, 2])
    r4 = panel.visualRect(panel.model().index(3))
    event = _drop(panel, [0, 2], r4.bottomLeft() + QPoint(5, -2))  # 4 の下 → to=4
    assert event.isAccepted() and event.dropAction() == Qt.DropAction.IgnoreAction
    assert labels(w.doc) == [2, 4, 1, 3, 5] and panel.selected_pages() == [2, 3]
    assert w.undo_stack.undoText() == messages.CMD_MOVE_PAGES


def test_drop_onto_itself_pushes_nothing(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); panel = w.thumbnails
    r1 = panel.visualRect(panel.model().index(1))
    _drop(panel, [1], r1.center())
    assert w.undo_stack.count() == 0 and labels(w.doc) == [1, 2, 3, 4, 5]


def test_drop_from_other_source_is_ignored(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(5)); panel = w.thumbnails
    r = panel.visualRect(panel.model().index(4))
    event = _drop(panel, [0], r.bottomLeft(), source=w.page_view)
    assert not event.isAccepted() and w.undo_stack.count() == 0


def test_drag_drop_is_configured(qtbot, big_pdf):
    from PySide6.QtWidgets import QAbstractItemView
    w = open_window(qtbot, big_pdf(3)); panel = w.thumbnails
    assert panel.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert panel.showDropIndicator()
    assert panel.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    model = panel.model()
    assert not model.flags(model.index(0)) & Qt.ItemFlag.ItemIsDropEnabled  # 間にだけ落とす
    assert model.flags(model.index(0)) & Qt.ItemFlag.ItemIsDragEnabled


# --- メニュー・ショートカット ---------------------------------------------------
def test_page_actions_shared_and_shortcuts(qtbot):
    from pdfeditor.ui.main_window import MainWindow
    w = MainWindow(); qtbot.addWidget(w)
    actions = [w.delete_pages_action, w.insert_blank_action, w.insert_pdf_action,
               w.rotate_right_action, w.rotate_left_action]
    assert [a for a in w.page_menu.actions() if not a.isSeparator()] == actions
    assert [a for a in w.thumbnails.actions() if not a.isSeparator()] == actions
    assert w.thumbnails.contextMenuPolicy() == Qt.ContextMenuPolicy.ActionsContextMenu
    assert [a.shortcut().toString() for a in actions] == [
        "Del", "", "", "Ctrl+R", "Ctrl+Shift+R"]
    assert not any(a.isEnabled() for a in actions)


def test_page_actions_enabled_by_document_and_selection(qtbot, simple_pdf):
    w = open_window(qtbot, simple_pdf)
    assert w.insert_blank_action.isEnabled() and w.insert_pdf_action.isEnabled()
    select(w, [])
    assert not w.delete_pages_action.isEnabled() and not w.rotate_right_action.isEnabled()
    select(w, [1])
    assert w.delete_pages_action.isEnabled() and w.rotate_left_action.isEnabled()


def test_delete_key_in_thumbnail_list(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(4)); select(w, [3])
    w.activateWindow(); w.thumbnails.setFocus()
    qtbot.waitUntil(w.thumbnails.hasFocus)
    qtbot.keyClick(w.thumbnails, Qt.Key.Key_Delete)
    assert labels(w.doc) == [1, 2, 3]


def test_delete_key_in_page_view_does_nothing(qtbot, big_pdf):
    w = open_window(qtbot, big_pdf(4)); select(w, [3])
    w.activateWindow(); w.page_view.setFocus()
    qtbot.waitUntil(w.page_view.hasFocus)
    qtbot.keyClick(w.page_view, Qt.Key.Key_Delete)
    assert labels(w.doc) == [1, 2, 3, 4]


def test_thumbnail_dock(qtbot):
    from PySide6.QtWidgets import QDockWidget
    from pdfeditor.ui.main_window import MainWindow
    w = MainWindow(); qtbot.addWidget(w)
    docks = w.findChildren(QDockWidget)
    assert len(docks) == 1 and docks[0].windowTitle() == "ページ"
    assert docks[0].widget() is w.thumbnails
    assert w.dockWidgetArea(docks[0]) == Qt.DockWidgetArea.LeftDockWidgetArea


# --- 空白ページのダイアログ -----------------------------------------------------
def test_blank_page_dialog_choices(qtbot):
    dlg = dialogs.BlankPageDialog(None, (612.0, 792.0)); qtbot.addWidget(dlg)
    texts = [dlg.combo.itemText(i) for i in range(dlg.combo.count())]
    assert texts == ["前後のページに合わせる", *PAGE_SIZES.keys()]
    assert dlg.combo.currentIndex() == 0 and dlg.chosen_size() == (612.0, 792.0)
    dlg.combo.setCurrentIndex(3)
    assert dlg.chosen_size() == PAGE_SIZES["A3 縦"]


def test_ask_blank_page_cancel_returns_none(qtbot, monkeypatch):
    monkeypatch.setattr(dialogs.BlankPageDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert real_ask_blank_page(None, (595, 842)) is None
    monkeypatch.setattr(dialogs.BlankPageDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert real_ask_blank_page(None, (595, 842)) == (595, 842)
