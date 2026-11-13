"""メインウィンドウ。文書の開く流れ・メニュー・ドロップを受け持つ。"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDockWidget, QFileDialog, QLabel, QMainWindow, QStackedWidget

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.errors import (
    NotAPdfError,
    OwnerPasswordRequiredError,
    PasswordRequiredError,
    PdfEditorError,
    WrongPasswordError,
)
from pdfeditor.ui import dialogs, messages
from pdfeditor.ui.page_commands import (
    DeletePagesCommand,
    InsertBlankCommand,
    InsertPdfCommand,
    RotatePagesCommand,
    make_undo_stack,
    push_command,
)
from pdfeditor.ui.page_view import PageView
from pdfeditor.ui.render_service import RenderService
from pdfeditor.ui.thumbnail_panel import ThumbnailPanel


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


class MainWindow(QMainWindow):
    instances: list["MainWindow"] = []

    document_changed = Signal()  # ページ構成が変わった
    document_loaded = Signal()

    def __init__(self):
        super().__init__()
        self.doc: PdfDocument | None = None
        self.render_service: RenderService | None = None  # 文書を開いた時に作る
        self.password_retry = False  # dialogs.ask_password が「違う」の注記に使う
        self.undo_stack = make_undo_stack(self)
        self.setAcceptDrops(True)
        self.resize(1000, 750)

        # 文書がない間は案内文、開いたらページ表示
        self._placeholder = QLabel(messages.EMPTY_HINT)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_view = PageView()
        self._central = QStackedWidget()
        self._central.addWidget(self._placeholder)
        self._central.addWidget(self.page_view)
        self.setCentralWidget(self._central)

        # 左のページ一覧
        self.thumbnails = ThumbnailPanel()
        self._pages_dock = QDockWidget(messages.PAGES_DOCK_TITLE, self)
        self._pages_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self._pages_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self._pages_dock.setWidget(self.thumbnails)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._pages_dock)
        self.resizeDocks([self._pages_dock], [self.thumbnails.panel_width()],
                         Qt.Orientation.Horizontal)

        self.document_changed.connect(self._on_document_changed)
        self.thumbnails.page_activated.connect(self.page_view.scroll_to_page)
        self.page_view.current_page_changed.connect(self.thumbnails.show_page)
        self.undo_stack.cleanChanged.connect(lambda clean: self.setWindowModified(not clean))

        self._build_menus()
        self.thumbnails.selectionModel().selectionChanged.connect(self._update_page_actions)
        self._update_page_actions()
        self._update_title()
        MainWindow.instances.append(self)

    # --- メニュー ---------------------------------------------------------
    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu(messages.MENU_FILE)

        self.open_action = QAction(messages.ACTION_OPEN, self)
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self._choose_and_open)
        file_menu.addAction(self.open_action)

        # 保存は Task 11 が接続する
        self.save_action = QAction(messages.ACTION_SAVE, self)
        self.save_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)
        self.save_as_action = QAction(messages.ACTION_SAVE_AS, self)
        self.save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_action.setEnabled(False)
        file_menu.addAction(self.save_as_action)

        file_menu.addSeparator()
        self.quit_action = QAction(messages.ACTION_QUIT, self)
        self.quit_action.triggered.connect(self.close)
        file_menu.addAction(self.quit_action)

        edit_menu = bar.addMenu(messages.MENU_EDIT)
        self.undo_action = self.undo_stack.createUndoAction(self, messages.UNDO_PREFIX)
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.redo_action = self.undo_stack.createRedoAction(self, messages.REDO_PREFIX)
        self.redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        # ページメニューとサムネイルの右クリックメニューは同じ QAction を使う
        self.page_menu = bar.addMenu(messages.MENU_PAGE)
        self.delete_pages_action = self._page_action(
            messages.ACTION_DELETE_PAGES, "Delete", self.delete_selected_pages)
        # Delete はページ一覧にフォーカスがある時だけ効かせる
        self.delete_pages_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.insert_blank_action = self._page_action(
            messages.ACTION_INSERT_BLANK, None, self.insert_blank_page)
        self.insert_pdf_action = self._page_action(
            messages.ACTION_INSERT_PDF, None, self.insert_pdf_pages)
        self.rotate_right_action = self._page_action(
            messages.ACTION_ROTATE_RIGHT, "Ctrl+R", lambda: self.rotate_selected(90))
        self.rotate_left_action = self._page_action(
            messages.ACTION_ROTATE_LEFT, "Ctrl+Shift+R", lambda: self.rotate_selected(-90))
        groups = [[self.delete_pages_action],
                  [self.insert_blank_action, self.insert_pdf_action],
                  [self.rotate_right_action, self.rotate_left_action]]
        for n, group in enumerate(groups):
            if n:
                self.page_menu.addSeparator()
                separator = QAction(self)
                separator.setSeparator(True)
                self.thumbnails.addAction(separator)
            self.page_menu.addActions(group)
            self.thumbnails.addActions(group)

        self.view_menu = bar.addMenu(messages.MENU_VIEW)
        # JIS 配列では「+」に Shift が要るので、Ctrl+; と Ctrl+= でも拡大する（表示は Ctrl++）
        self.zoom_in_action = self._view_action(
            messages.ACTION_ZOOM_IN, "Ctrl++", self.page_view.zoom_in)
        self.zoom_in_action.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+;"), QKeySequence("Ctrl+=")])
        self.zoom_out_action = self._view_action(
            messages.ACTION_ZOOM_OUT, "Ctrl+-", self.page_view.zoom_out)
        self.fit_width_action = self._view_action(
            messages.ACTION_FIT_WIDTH, "Ctrl+1", self.page_view.fit_width)
        self.fit_page_action = self._view_action(
            messages.ACTION_FIT_PAGE, "Ctrl+0", self.page_view.fit_page)

    def _view_action(self, text: str, shortcut: str, slot) -> QAction:
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.setEnabled(False)  # 文書を開くまでは使えない
        action.triggered.connect(slot)
        self.view_menu.addAction(action)
        return action

    def _view_actions(self) -> list[QAction]:
        return [self.zoom_in_action, self.zoom_out_action,
                self.fit_width_action, self.fit_page_action]

    def _page_action(self, text: str, shortcut: str | None, slot) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _update_page_actions(self) -> None:
        """文書があれば追加・挿入、さらに選択があれば削除・回転を使える。"""
        has_doc = self.doc is not None
        has_selection = has_doc and bool(self.thumbnails.selected_pages())
        self.insert_blank_action.setEnabled(has_doc)
        self.insert_pdf_action.setEnabled(has_doc)
        for action in (self.delete_pages_action, self.rotate_right_action,
                       self.rotate_left_action):
            action.setEnabled(has_selection)

    # --- 開く -------------------------------------------------------------
    def _choose_and_open(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, messages.OPEN_DIALOG_TITLE, "", messages.OPEN_DIALOG_FILTER
        )
        if name:
            self.open_path(Path(name))

    def handle_dropped_paths(self, paths: list[Path]) -> None:
        for path in paths:
            if path.suffix.lower() == ".pdf":
                self.open_path(path)

    @classmethod
    def _window_showing(cls, path: Path) -> "MainWindow | None":
        for window in cls.instances:
            if window.doc is not None and window.doc.path and _same_file(window.doc.path, path):
                return window
        return None

    def open_path(self, path: Path) -> bool:
        existing = self._window_showing(path)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return True
        if self.doc is not None:  # 文書のあるウィンドウは触らず、新しいウィンドウで開く
            window = MainWindow()
            window.show()
            if window.open_path(path):
                return True
            window.close()
            return False
        doc = self._open_with_prompts(path)
        if doc is None:
            return False
        self._attach(doc)
        return True

    def _open_with_prompts(self, path: Path) -> PdfDocument | None:
        """パスワードが要る間は尋ね直す。キャンセルとエラーは None。"""
        password: str | None = None
        self.password_retry = False
        try:
            while True:
                try:
                    return PdfDocument.open(path, password)
                except (PasswordRequiredError, WrongPasswordError):
                    kind = "user"
                except OwnerPasswordRequiredError:
                    kind = "owner"
                except PdfEditorError as err:
                    dialogs.show_error(self, err)
                    return None
                self.password_retry = password is not None  # 2回目以降は「違う」を添える
                password = dialogs.ask_password(self, kind)
                if password is None:
                    return None
        finally:
            self.password_retry = False

    def _attach(self, doc: PdfDocument) -> None:
        self.doc = doc
        self.undo_stack.clear()
        self.render_service = RenderService(doc, parent=self)
        self.page_view.set_document(doc, self.render_service)
        self.thumbnails.set_document(doc, self.render_service, self.undo_stack,
                                     doc_changed=self.document_changed.emit)
        self.thumbnails.show_page(self.page_view.current_page())
        self._central.setCurrentWidget(self.page_view)
        self.page_view.setFocus()
        for action in self._view_actions():
            action.setEnabled(True)
        self._update_page_actions()
        self._update_title()
        self.document_loaded.emit()
        if doc.is_signed:
            dialogs.show_info(self, messages.SIGNED_NOTICE)

    def _on_document_changed(self) -> None:
        """文書は既に変わっている。古い画像を捨ててから並びを読み直す。"""
        if self.render_service is not None:
            self.render_service.invalidate()
            self.page_view.reload_layout()
            self.thumbnails.refresh()
            self._update_page_actions()

    def _update_title(self) -> None:
        # [*] は Qt が未保存の印（*）に置き換える。cleanChanged で setWindowModified する
        if self.doc is not None and self.doc.path is not None:
            self.setWindowTitle(f"{self.doc.path.name}[*] - {messages.APP_NAME}")
        else:
            self.setWindowTitle(messages.APP_NAME)

    # --- ページ操作 -------------------------------------------------------
    def _run(self, command) -> bool:
        """文書を変える操作はすべてここを通す。失敗は知らせ、文書は変わらない。"""
        try:
            push_command(self.undo_stack, command)
        except PdfEditorError as err:
            dialogs.show_error(self, err)
            return False
        return True

    def _insert_position(self) -> int:
        """選択中の最後のページの次。選択がなければ末尾。"""
        selected = self.thumbnails.selected_pages()
        return selected[-1] + 1 if selected else self.doc.page_count

    def _show_inserted(self, at: int, count: int) -> None:
        self.thumbnails.select_pages(range(at, at + count))
        self.page_view.scroll_to_page(at)

    def delete_selected_pages(self) -> None:
        selected = self.thumbnails.selected_pages()
        if self.doc is None or not selected:
            return
        if self._run(DeletePagesCommand(self.doc, selected, self.document_changed.emit)):
            self.thumbnails.select_pages([min(selected[0], self.doc.page_count - 1)])

    def insert_blank_page(self) -> None:
        if self.doc is None:
            return
        at = self._insert_position()
        size = dialogs.ask_blank_page(self, self.doc.default_blank_size(at))
        if size is None:
            return
        width, height = size
        if self._run(InsertBlankCommand(self.doc, at, width, height, self.document_changed.emit)):
            self._show_inserted(at, 1)

    def insert_pdf_pages(self) -> None:
        if self.doc is None:
            return
        name, _ = QFileDialog.getOpenFileName(
            self, messages.INSERT_DIALOG_TITLE, "", messages.OPEN_DIALOG_FILTER
        )
        if not name:
            return
        path = Path(name)
        try:
            data = path.read_bytes()  # 先に読むので、開いている文書自身も挿入できる
        except OSError:
            dialogs.show_error(self, NotAPdfError(messages.INSERT_READ_FAILED.format(name=path.name)))
            return
        at = self._insert_position()
        before = self.doc.page_count
        password: str | None = None
        self.password_retry = False
        try:
            while True:
                command = InsertPdfCommand(self.doc, data, at, password, self.document_changed.emit)
                try:
                    push_command(self.undo_stack, command)
                    break
                except (PasswordRequiredError, WrongPasswordError):
                    pass
                except PdfEditorError as err:
                    dialogs.show_error(self, err)
                    return
                self.password_retry = password is not None  # 2回目以降は「違う」を添える
                password = dialogs.ask_password(self, "user")
                if password is None:
                    return
        finally:
            self.password_retry = False
        self._show_inserted(at, self.doc.page_count - before)

    def rotate_selected(self, delta: int) -> None:
        selected = self.thumbnails.selected_pages()
        if self.doc is None or not selected:
            return
        self._run(RotatePagesCommand(self.doc, selected, delta, self.document_changed.emit))

    # --- ドロップ ---------------------------------------------------------
    @staticmethod
    def _pdf_paths(mime) -> list[Path]:
        if not mime.hasUrls():
            return []
        return [
            Path(u.toLocalFile())
            for u in mime.urls()
            if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")
        ]

    def dragEnterEvent(self, event) -> None:
        if self._pdf_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = self._pdf_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.handle_dropped_paths(paths)

    # --- 終了 -------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self in MainWindow.instances:
            MainWindow.instances.remove(self)
        self.page_view.set_document(None, None)
        self.thumbnails.set_document(None, None, None)
        if self.render_service is not None:  # 描画スレッドを止めてから文書を閉じる
            self.render_service.shutdown()
            self.render_service = None
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        super().closeEvent(event)
