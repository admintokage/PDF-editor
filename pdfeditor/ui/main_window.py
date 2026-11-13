"""メインウィンドウ。文書のタブ・メニュー・開く流れ・閉じる流れ・ドロップを受け持つ。
1 文書ぶんの部品と操作は DocumentTab にあり、メニューは表示中のタブに働く。"""
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QUndoGroup
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QStackedWidget, QTabWidget

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.errors import (
    NotAPdfError,
    OwnerPasswordRequiredError,
    PasswordRequiredError,
    PdfEditorError,
    WrongPasswordError,
)
from pdfeditor.ui import dialogs, messages
from pdfeditor.ui.document_tab import DocumentTab

log = logging.getLogger(__name__)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _current(name: str) -> property:
    """表示中のタブの属性（タブがなければ None）。既存の呼び出しとテストのため。"""
    return property(lambda self: getattr(self.current_tab(), name, None))


class MainWindow(QMainWindow):
    instances: list["MainWindow"] = []

    document_loaded = Signal()

    doc = _current("doc")
    undo_stack = _current("undo_stack")
    render_service = _current("render_service")
    page_view = _current("page_view")
    thumbnails = _current("thumbnails")
    document_changed = _current("document_changed")

    def __init__(self):
        super().__init__()
        self.password_retry = False  # dialogs.ask_password が「違う」の注記に使う
        self._saving = False  # どれかのタブが保存中。ウィンドウ全体が操作を止める
        self._active_tab: DocumentTab | None = None
        self._closed = False
        self.setAcceptDrops(True)
        self.resize(1000, 750)

        # 文書がない間は案内文、開いたらタブ
        self._placeholder = QLabel(messages.EMPTY_HINT)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tab_widget = QTabWidget()
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self._central = QStackedWidget()
        self._central.addWidget(self._placeholder)
        self._central.addWidget(self.tab_widget)
        self.setCentralWidget(self._central)

        self.undo_group = QUndoGroup(self)
        self._build_menus()
        self.tab_widget.currentChanged.connect(self._on_current_changed)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self._on_current_changed()
        MainWindow.instances.append(self)

    # --- メニュー ---------------------------------------------------------
    def _action(self, text: str, shortcut: str | None, slot, menu=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        if menu is not None:
            menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu(messages.MENU_FILE)
        self.open_action = self._action(messages.ACTION_OPEN, "Ctrl+O", self._choose_and_open,
                                        file_menu)
        self.close_tab_action = self._action(messages.ACTION_CLOSE_TAB, "Ctrl+W",
                                             self.close_current_tab, file_menu)
        file_menu.addSeparator()
        self.save_action = self._action(messages.ACTION_SAVE, "Ctrl+S", self.save, file_menu)
        self.save_as_action = self._action(messages.ACTION_SAVE_AS, "Ctrl+Shift+S", self.save_as,
                                           file_menu)
        file_menu.addSeparator()
        self.quit_action = self._action(messages.ACTION_QUIT, None, self.close, file_menu)

        # タブの切り替え（メニューには出さない）
        self.next_tab_action = self._action(messages.ACTION_NEXT_TAB, None, self.next_tab)
        self.next_tab_action.setShortcuts(QKeySequence.StandardKey.NextChild)
        self.prev_tab_action = self._action(messages.ACTION_PREV_TAB, None, self.previous_tab)
        self.prev_tab_action.setShortcuts(QKeySequence.StandardKey.PreviousChild)
        self.addActions([self.next_tab_action, self.prev_tab_action])

        edit_menu = bar.addMenu(messages.MENU_EDIT)
        self.undo_action = self.undo_group.createUndoAction(self, messages.UNDO_PREFIX)
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.redo_action = self.undo_group.createRedoAction(self, messages.REDO_PREFIX)
        self.redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)

        # ページメニューとサムネイルの右クリックメニューは同じ QAction を使う。
        # 各タブのサムネイル一覧に足すので、ここでは並び（区切りを含む）を作っておく
        self.page_menu = bar.addMenu(messages.MENU_PAGE)
        self.delete_pages_action = self._action(
            messages.ACTION_DELETE_PAGES, "Delete", self.delete_selected_pages)
        self.insert_blank_action = self._action(
            messages.ACTION_INSERT_BLANK, None, self.insert_blank_page)
        self.insert_pdf_action = self._action(
            messages.ACTION_INSERT_PDF, None, self.insert_pdf_pages)
        self.rotate_right_action = self._action(
            messages.ACTION_ROTATE_RIGHT, "Ctrl+R", lambda: self.rotate_selected(90))
        self.rotate_left_action = self._action(
            messages.ACTION_ROTATE_LEFT, "Ctrl+Shift+R", lambda: self.rotate_selected(-90))
        # Delete はページ一覧にフォーカスがある時だけ効かせる
        self.delete_pages_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        groups = [[self.delete_pages_action],
                  [self.insert_blank_action, self.insert_pdf_action],
                  [self.rotate_right_action, self.rotate_left_action]]
        self._thumbnail_actions: list[QAction] = []
        for n, group in enumerate(groups):
            if n:
                self.page_menu.addSeparator()
                separator = QAction(self)
                separator.setSeparator(True)
                self._thumbnail_actions.append(separator)
            self.page_menu.addActions(group)
            self._thumbnail_actions += group

        self.view_menu = bar.addMenu(messages.MENU_VIEW)
        # JIS 配列では「+」に Shift が要るので、Ctrl+; と Ctrl+= でも拡大する（表示は Ctrl++）
        self.zoom_in_action = self._action(messages.ACTION_ZOOM_IN, "Ctrl++",
                                           lambda: self._view("zoom_in"), self.view_menu)
        self.zoom_in_action.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+;"), QKeySequence("Ctrl+=")])
        self.zoom_out_action = self._action(messages.ACTION_ZOOM_OUT, "Ctrl+-",
                                            lambda: self._view("zoom_out"), self.view_menu)
        self.fit_width_action = self._action(messages.ACTION_FIT_WIDTH, "Ctrl+1",
                                             lambda: self._view("fit_width"), self.view_menu)
        self.fit_page_action = self._action(messages.ACTION_FIT_PAGE, "Ctrl+0",
                                            lambda: self._view("fit_page"), self.view_menu)

    def _view_actions(self) -> list[QAction]:
        return [self.zoom_in_action, self.zoom_out_action,
                self.fit_width_action, self.fit_page_action]

    def _view(self, name: str) -> None:
        tab = self.current_tab()
        if tab is not None:
            getattr(tab.page_view, name)()

    def _update_actions(self) -> None:
        """文書があれば保存・表示、保存中でなければ開く・閉じる・タブの切り替え・元に戻す。"""
        idle = not self._saving
        has_doc = self.current_tab() is not None
        self.open_action.setEnabled(idle)
        for action in (self.save_action, self.save_as_action, self.close_tab_action):
            action.setEnabled(has_doc and idle)
        for action in self._view_actions():
            action.setEnabled(has_doc)
        for action in (self.next_tab_action, self.prev_tab_action):
            action.setEnabled(idle and self.tab_widget.count() > 1)
        self.undo_action.setEnabled(idle and self.undo_group.canUndo())
        self.redo_action.setEnabled(idle and self.undo_group.canRedo())
        self.tab_widget.tabBar().setEnabled(idle)
        self._update_page_actions()

    def _update_page_actions(self, *_args) -> None:
        """文書があれば追加・挿入、さらに選択があれば削除・回転を使える。"""
        tab = self.current_tab()
        has_doc = tab is not None and not self._saving
        has_selection = has_doc and bool(tab.thumbnails.selected_pages())
        self.insert_blank_action.setEnabled(has_doc)
        self.insert_pdf_action.setEnabled(has_doc)
        for action in (self.delete_pages_action, self.rotate_right_action,
                       self.rotate_left_action):
            action.setEnabled(has_selection)

    # --- タブ -------------------------------------------------------------
    def tabs(self) -> list[DocumentTab]:
        return [self.tab_widget.widget(i) for i in range(self.tab_widget.count())]

    def current_tab(self) -> DocumentTab | None:
        tab = self.tab_widget.currentWidget()
        return tab if isinstance(tab, DocumentTab) else None

    def _add_tab(self, tab: DocumentTab) -> None:
        tab.modified_changed.connect(self._update_title)
        tab.title_changed.connect(self._update_title)
        tab.saving_changed.connect(self._on_saving_changed)
        tab.selection_changed.connect(self._update_page_actions)
        tab.document_changed.connect(self._update_page_actions)
        tab.thumbnails.addActions(self._thumbnail_actions)
        self.undo_group.addStack(tab.undo_stack)
        self.tab_widget.setCurrentIndex(self.tab_widget.addTab(tab, tab.title()))
        self._update_title()
        tab.page_view.setFocus()

    def _on_current_changed(self, _index: int = -1) -> None:
        tab = self.current_tab()
        if self._saving and self._active_tab is not None and tab is not self._active_tab:
            self.tab_widget.setCurrentWidget(self._active_tab)  # 保存中は切り替えない
            return
        if self._active_tab is not None and self._active_tab is not tab:
            self._active_tab.set_active(False)
        self._active_tab = tab
        self.undo_group.setActiveStack(tab.undo_stack if tab is not None else None)
        if tab is not None:
            tab.set_active(True)
        self._central.setCurrentWidget(self.tab_widget if tab is not None else self._placeholder)
        self._update_actions()
        self._update_title()

    def _switch_tab(self, step: int) -> None:
        count = self.tab_widget.count()
        if not self._saving and count > 1:
            self.tab_widget.setCurrentIndex((self.tab_widget.currentIndex() + step) % count)

    def next_tab(self) -> None:
        self._switch_tab(1)

    def previous_tab(self) -> None:
        self._switch_tab(-1)

    def _update_title(self, *_args) -> None:
        # [*] は Qt が未保存の印（*）に置き換える
        tab = self.current_tab()
        if tab is not None and tab.doc.path is not None:
            self.setWindowTitle(f"{tab.title()}[*] - {messages.APP_NAME}")
        else:
            self.setWindowTitle(messages.APP_NAME)
        self.setWindowModified(tab is not None and tab.is_modified())
        for i, each in enumerate(self.tabs()):
            self.tab_widget.setTabText(i, each.title() + ("*" if each.is_modified() else ""))
            self.tab_widget.setTabToolTip(i, each.tool_tip())

    def _on_saving_changed(self, saving: bool) -> None:
        """保存中はファイルを開く・保存・閉じる・タブの切り替え・ページ操作・元に戻すを止める。"""
        self._saving = saving
        self._update_actions()

    def close_tab(self, index: int) -> bool:
        """タブを閉じる。未保存なら、そのタブに切り替えてから尋ねる。"""
        tab = self.tab_widget.widget(index)
        if self._saving or not isinstance(tab, DocumentTab):
            return False
        if tab.is_modified():
            self.tab_widget.setCurrentWidget(tab)
        if not tab.confirm_close():
            return False
        self._remove_tab(tab)
        return True

    def close_current_tab(self) -> bool:
        return self.close_tab(self.tab_widget.currentIndex())

    def _remove_tab(self, tab: DocumentTab) -> None:
        if tab is self._active_tab:
            self._active_tab = None
        self.undo_group.removeStack(tab.undo_stack)
        self.tab_widget.removeTab(self.tab_widget.indexOf(tab))
        tab.shutdown()  # 受け口を切り離し、描画スレッドを止めてから文書を閉じる
        tab.deleteLater()
        self._update_title()

    # --- 表示中のタブへの操作（メニューと既存の呼び出しから） ---------------
    def _on_tab(self, method: str, *args):
        tab = self.current_tab()
        if tab is None or self._saving:
            return False
        return getattr(tab, method)(*args)

    def delete_selected_pages(self) -> None:
        self._on_tab("delete_selected_pages")

    def insert_blank_page(self) -> None:
        self._on_tab("insert_blank_page")

    def insert_pdf_pages(self) -> None:
        self._on_tab("insert_pdf_pages")

    def rotate_selected(self, delta: int) -> None:
        self._on_tab("rotate_selected", delta)

    def save(self) -> bool:
        return bool(self._on_tab("save"))

    def save_as(self) -> bool:
        return bool(self._on_tab("save_as"))

    # --- 開く -------------------------------------------------------------
    def _choose_and_open(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, messages.OPEN_DIALOG_TITLE, "", messages.OPEN_DIALOG_FILTER
        )
        if name:
            self.open_path(Path(name))

    def handle_dropped_paths(self, paths: list[Path]) -> None:
        if self._saving:
            return
        for path in paths:
            if path.suffix.lower() == ".pdf":
                self.open_path(path)

    @classmethod
    def _tab_showing(cls, path: Path) -> "tuple[MainWindow, DocumentTab] | None":
        for window in cls.instances:
            for tab in window.tabs():
                if tab.doc.path is not None and _same_file(tab.doc.path, path):
                    return window, tab
        return None

    def open_path(self, path: Path) -> bool:
        if self._saving:
            return False
        existing = self._tab_showing(path)
        if existing is not None:
            window, tab = existing
            window.tab_widget.setCurrentWidget(tab)
            window.show()
            window.raise_()
            window.activateWindow()
            return True
        if self.tabs():  # 文書のあるウィンドウは触らず、新しいウィンドウで開く
            window = MainWindow()
            window.show()
            if window.open_path(path):
                return True
            window.close()
            return False
        doc = self._open_with_prompts(path)
        if doc is None:
            return False
        return self._attach(doc)

    def _open_with_prompts(self, path: Path) -> PdfDocument | None:
        """パスワードが要る間は尋ね直す。キャンセルとエラーは None。"""
        password: str | None = None
        asked: str | None = None  # 直前に尋ねたパスワードの種類
        self.password_retry = False
        try:
            while True:
                try:
                    return PdfDocument.open(path, password)
                except PasswordRequiredError:
                    kind = "user"
                except WrongPasswordError:
                    kind = asked or "user"  # 権限パスワードが違った時は、権限パスワードを尋ね直す
                except OwnerPasswordRequiredError:
                    kind = "owner"
                except PdfEditorError as err:
                    dialogs.show_error(self, err)
                    return None
                # 同じ種類を尋ね直す時だけ「違う」を添える。開くパスワードが合っていて、
                # 次に権限パスワードを尋ねる時は添えない
                self.password_retry = asked == kind
                asked = kind
                password = dialogs.ask_password(self, kind)
                if password is None:
                    return None
        finally:
            self.password_retry = False

    def _attach(self, doc: PdfDocument) -> bool:
        """開いた文書を新しいタブで表示する。表示の準備で失敗したら（壊れたページなど）
        タブは作らず（文書は DocumentTab が閉じる）、知らせる。"""
        try:
            tab = DocumentTab(doc)
        except Exception:  # noqa: BLE001 - どんな失敗でもアプリは落とさない
            log.exception("could not show the opened document")
            name = doc.path.name if doc.path else messages.APP_NAME
            dialogs.show_error(self, NotAPdfError(messages.OPEN_SHOW_FAILED.format(name=name)))
            return False
        self._add_tab(tab)
        self.document_loaded.emit()
        if doc.is_signed:
            dialogs.show_info(self, messages.SIGNED_NOTICE)
        return True

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
        """未保存のタブを左から順に、そのタブに切り替えて尋ねる。閉じてよいタブはその場で閉じ、
        「キャンセル」（または保存の失敗）ならそこで止めて、残りのタブとウィンドウは開いたまま。"""
        if self._saving:  # 保存の最中は閉じない
            event.ignore()
            return
        for tab in self.tabs():
            if tab.is_modified():
                self.tab_widget.setCurrentWidget(tab)
                if not tab.confirm_close():
                    event.ignore()
                    return
                self._remove_tab(tab)
        for tab in self.tabs():
            self._remove_tab(tab)
        if self in MainWindow.instances:
            MainWindow.instances.remove(self)
        self._closed = True
        super().closeEvent(event)
