"""メインウィンドウ。文書の開く流れ・メニュー・ドロップを受け持つ。"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.errors import (
    OwnerPasswordRequiredError,
    PasswordRequiredError,
    PdfEditorError,
    WrongPasswordError,
)
from pdfeditor.ui import dialogs, messages
from pdfeditor.ui.page_commands import make_undo_stack


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
        self.password_retry = False  # dialogs.ask_password が「違う」の注記に使う
        self.undo_stack = make_undo_stack(self)
        self.setAcceptDrops(True)
        self.resize(1000, 750)

        placeholder = QLabel(messages.EMPTY_HINT)  # Task 9 でページ表示に置き換わる
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)

        self._build_menus()
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

        # 中身は Task 9〜11 が足す
        self.page_menu = bar.addMenu(messages.MENU_PAGE)
        self.view_menu = bar.addMenu(messages.MENU_VIEW)

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
        self._update_title()
        self.document_loaded.emit()
        if doc.is_signed:
            dialogs.show_info(self, messages.SIGNED_NOTICE)

    def _update_title(self) -> None:
        if self.doc is not None and self.doc.path is not None:
            self.setWindowTitle(f"{self.doc.path.name} - {messages.APP_NAME}")
        else:
            self.setWindowTitle(messages.APP_NAME)

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
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        super().closeEvent(event)
