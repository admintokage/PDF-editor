"""メインウィンドウ。文書のタブ・メニュー・開く流れ・閉じる流れ・ドロップを受け持つ。
1 文書ぶんの部品と操作は DocumentTab にあり、メニューは表示中のタブに働く。"""
import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QUndoGroup
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QStackedWidget, QTabWidget

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.errors import (
    NotAPdfError,
    OwnerPasswordRequiredError,
    PasswordRequiredError,
    PdfEditorError,
    WrongPasswordError,
)
from pdfeditor.ui import dialogs, messages, page_clipboard
from pdfeditor.ui.document_tab import DocumentTab, same_file
from pdfeditor.ui.window_actions import build_menus

log = logging.getLogger(__name__)


def _current(name: str) -> property:
    """表示中のタブの属性（タブがなければ None）。既存の呼び出しとテストのため。"""
    return property(lambda self: getattr(self.current_tab(), name, None))


def _on_current_tab(name: str):
    """表示中のタブの同じ名前のメソッドを呼ぶ。タブがない・保存中なら何もせず False。"""
    def call(self, *args):
        tab = self.current_tab()
        if tab is None or self._saving:
            return False
        return bool(getattr(tab, name)(*args))
    call.__name__ = name
    return call


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
        build_menus(self)
        self.tab_widget.currentChanged.connect(self._on_current_changed)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        page_clipboard.clipboard().changed.connect(self._update_page_actions)
        self._on_current_changed()
        MainWindow.instances.append(self)

    # --- メニュー ---------------------------------------------------------
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
        """文書があれば追加・挿入、選択があれば切り取り・コピー・削除・回転、クリップボードに
        ページがあれば貼り付けを使える。保存中はどれも使えない。"""
        tab = self.current_tab()
        has_doc = tab is not None and not self._saving
        has_selection = has_doc and bool(tab.thumbnails.selected_pages())
        self.insert_blank_action.setEnabled(has_doc)
        self.insert_pdf_action.setEnabled(has_doc)
        self.paste_action.setEnabled(has_doc and page_clipboard.clipboard().count() > 0)
        for action in (self.cut_action, self.copy_action, self.delete_pages_action,
                       self.rotate_right_action, self.rotate_left_action):
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
        tab.is_open_in_other_tab = (
            lambda path, _tab=tab: self._tab_showing(path, exclude=_tab) is not None)
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
        for tab in self.tabs():
            tab.set_blocked(saving)
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
        self._update_actions()  # 表示中でないタブを閉じた時は currentChanged が来ないことがある
        self._update_title()

    # --- 表示中のタブへの操作（メニューと既存の呼び出しから） ---------------
    delete_selected_pages = _on_current_tab("delete_selected_pages")
    insert_blank_page = _on_current_tab("insert_blank_page")
    insert_pdf_pages = _on_current_tab("insert_pdf_pages")
    rotate_selected = _on_current_tab("rotate_selected")
    copy_selected_pages = _on_current_tab("copy_selected_pages")
    cut_selected_pages = _on_current_tab("cut_selected_pages")
    paste_pages = _on_current_tab("paste_pages")
    save = _on_current_tab("save")
    save_as = _on_current_tab("save_as")

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
    def _tab_showing(cls, path: Path, exclude: DocumentTab | None = None
                     ) -> "tuple[MainWindow, DocumentTab] | None":
        for window in cls.instances:
            for tab in window.tabs():
                if tab is not exclude and tab.doc.path is not None and same_file(tab.doc.path, path):
                    return window, tab
        return None

    def open_path(self, path: Path) -> bool:
        """新しいタブで開いて、そのタブを表示する。すでに開いているファイルは、そのタブ
        （別のウィンドウにあればそのウィンドウ）に切り替えるだけ。"""
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
        if not self._closed:  # アプリに 1 つのクリップボードから、閉じたウィンドウを切り離す
            self._closed = True
            page_clipboard.clipboard().changed.disconnect(self._update_page_actions)
        super().closeEvent(event)
