"""MainWindow のメニューバーと QAction を作る（作るだけ。動きは MainWindow と DocumentTab にある）。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

from pdfeditor.ui import messages


def _action(window, text: str, shortcut: str | None, slot, menu=None) -> QAction:
    action = QAction(text, window)
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    action.triggered.connect(lambda _checked=False: slot())  # checked を slot に渡さない
    if menu is not None:
        menu.addAction(action)
    return action


def build_menus(window) -> None:
    """メニューバーと、メニューが使う QAction を window の属性として作る。"""
    bar = window.menuBar()
    file_menu = bar.addMenu(messages.MENU_FILE)
    window.open_action = _action(window, messages.ACTION_OPEN, "Ctrl+O", window._choose_and_open,
        file_menu)
    window.close_tab_action = _action(window, messages.ACTION_CLOSE_TAB, "Ctrl+W",
        window.close_current_tab, file_menu)
    file_menu.addSeparator()
    window.save_action = _action(window, messages.ACTION_SAVE, "Ctrl+S", window.save, file_menu)
    window.save_as_action = _action(window, messages.ACTION_SAVE_AS, "Ctrl+Shift+S", window.save_as,
        file_menu)
    file_menu.addSeparator()
    window.quit_action = _action(window, messages.ACTION_QUIT, None, window.close, file_menu)

    # タブの切り替え（メニューには出さない）
    window.next_tab_action = _action(window, messages.ACTION_NEXT_TAB, None, window.next_tab)
    window.next_tab_action.setShortcuts(QKeySequence.StandardKey.NextChild)
    window.prev_tab_action = _action(window, messages.ACTION_PREV_TAB, None, window.previous_tab)
    window.prev_tab_action.setShortcuts(QKeySequence.StandardKey.PreviousChild)
    window.addActions([window.next_tab_action, window.prev_tab_action])

    edit_menu = bar.addMenu(messages.MENU_EDIT)
    window.undo_action = window.undo_group.createUndoAction(window, messages.UNDO_PREFIX)
    window.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
    window.redo_action = window.undo_group.createRedoAction(window, messages.REDO_PREFIX)
    window.redo_action.setShortcut(QKeySequence("Ctrl+Y"))
    edit_menu.addAction(window.undo_action)
    edit_menu.addAction(window.redo_action)

    # ページメニューとサムネイルの右クリックメニューは同じ QAction を使う。
    # 各タブのサムネイル一覧に足すので、ここでは並び（区切りを含む）を作っておく
    window.page_menu = bar.addMenu(messages.MENU_PAGE)
    window.cut_action = _action(window, messages.ACTION_CUT_PAGES, "Ctrl+X", window.cut_selected_pages)
    window.copy_action = _action(window, 
        messages.ACTION_COPY_PAGES, "Ctrl+C", window.copy_selected_pages)
    window.paste_action = _action(window, messages.ACTION_PASTE_PAGES, "Ctrl+V", window.paste_pages)
    window.delete_pages_action = _action(window, 
        messages.ACTION_DELETE_PAGES, "Delete", window.delete_selected_pages)
    window.insert_blank_action = _action(window, 
        messages.ACTION_INSERT_BLANK, None, window.insert_blank_page)
    window.insert_pdf_action = _action(window, 
        messages.ACTION_INSERT_PDF, None, window.insert_pdf_pages)
    window.rotate_right_action = _action(window, 
        messages.ACTION_ROTATE_RIGHT, "Ctrl+R", lambda: window.rotate_selected(90))
    window.rotate_left_action = _action(window, 
        messages.ACTION_ROTATE_LEFT, "Ctrl+Shift+R", lambda: window.rotate_selected(-90))
    # これらのキーはページ一覧にフォーカスがある時だけ効かせる（ダイアログや、後の段階の
    # 文字の編集でのコピー・貼り付けを横取りしない）。メニューにはキーを表示する
    for action in (window.cut_action, window.copy_action, window.paste_action,
                   window.delete_pages_action):
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    groups = [[window.cut_action, window.copy_action, window.paste_action],
              [window.delete_pages_action],
              [window.insert_blank_action, window.insert_pdf_action],
              [window.rotate_right_action, window.rotate_left_action]]
    window._thumbnail_actions: list[QAction] = []
    for n, group in enumerate(groups):
        if n:
            window.page_menu.addSeparator()
            separator = QAction(window)
            separator.setSeparator(True)
            window._thumbnail_actions.append(separator)
        window.page_menu.addActions(group)
        window._thumbnail_actions += group

    window.view_menu = bar.addMenu(messages.MENU_VIEW)
    # JIS 配列では「+」に Shift が要るので、Ctrl+; と Ctrl+= でも拡大する（表示は Ctrl++）
    window.zoom_in_action = _action(window, messages.ACTION_ZOOM_IN, "Ctrl++",
        lambda: window._view("zoom_in"), window.view_menu)
    window.zoom_in_action.setShortcuts(
        [QKeySequence("Ctrl++"), QKeySequence("Ctrl+;"), QKeySequence("Ctrl+=")])
    window.zoom_out_action = _action(window, messages.ACTION_ZOOM_OUT, "Ctrl+-",
        lambda: window._view("zoom_out"), window.view_menu)
    window.fit_width_action = _action(window, messages.ACTION_FIT_WIDTH, "Ctrl+1",
        lambda: window._view("fit_width"), window.view_menu)
    window.fit_page_action = _action(window, messages.ACTION_FIT_PAGE, "Ctrl+0",
        lambda: window._view("fit_page"), window.view_menu)
