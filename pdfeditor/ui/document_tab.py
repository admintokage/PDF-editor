"""1 つの PDF ぶんの部品（ページ表示・サムネイル・元に戻す履歴・描画サービス）と、
ページ操作・保存の流れ。MainWindow はこれをタブに並べ、メニューから表示中のタブを動かす。"""
import logging
import os
from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QProgressDialog, QSplitter, QVBoxLayout, QWidget

from pdfeditor.core.document import PdfDocument
from pdfeditor.core.errors import (
    NotAPdfError,
    PageOperationError,
    PasswordRequiredError,
    PdfEditorError,
    SaveFailedError,
    WrongPasswordError,
)
from pdfeditor.ui import dialogs, messages, page_clipboard
from pdfeditor.ui.page_commands import (
    CutPagesCommand,
    DeletePagesCommand,
    InsertBlankCommand,
    InsertPdfCommand,
    PastePagesCommand,
    RotatePagesCommand,
    make_undo_stack,
    push_command,
)
from pdfeditor.ui.page_view import PageView
from pdfeditor.ui.render_service import RenderService
from pdfeditor.ui.thumbnail_panel import ThumbnailPanel

log = logging.getLogger(__name__)

PROGRESS_DELAY_MS = 1000  # 保存がこれより長引いたら進行表示を出す


class _SaveSignals(QObject):
    done = Signal(object)  # None（成功）か SaveFailedError


class _SaveTask(QRunnable):
    """別スレッドで doc.save だけを行う。Qt のウィジェットには触れない。"""

    def __init__(self, doc: PdfDocument, target: Path | None, signals: _SaveSignals):
        super().__init__()
        self._doc, self._target, self._signals = doc, target, signals

    def run(self) -> None:
        result: SaveFailedError | None = None
        try:
            self._doc.save(self._target)
        except SaveFailedError as err:
            result = err
        except Exception:  # 想定外でもアプリを落とさず、編集内容は残す
            log.exception("unexpected error while saving")
            result = SaveFailedError(messages.SAVE_UNEXPECTED, "other")
        self._signals.done.emit(result)


class DocumentTab(QWidget):
    document_changed = Signal()  # ページ構成が変わった（文書は既に変わっている）
    modified_changed = Signal(bool)  # 未保存の変更の有無が変わった
    title_changed = Signal()  # 保存先（ファイル名）が変わった
    saving_changed = Signal(bool)  # 保存を始めた・終えた（ウィンドウ全体が操作を止める）
    selection_changed = Signal()  # サムネイルの選択が変わった

    def __init__(self, doc: PdfDocument, parent: QWidget | None = None):
        """表示の準備に失敗したら（壊れたページなど）片付けて文書を閉じ、例外を投げ直す。"""
        super().__init__(parent)
        self.doc = doc
        self.password_retry = False  # dialogs.ask_password が「違う」の注記に使う
        self._saving = False
        self._blocked = False
        self._shut_down = False
        self._save_result: SaveFailedError | None = None
        self._save_loop: QEventLoop | None = None
        self._save_signals: _SaveSignals | None = None
        self.undo_stack = make_undo_stack(self)
        self.render_service = RenderService(doc, parent=self)
        self.page_view = PageView()
        self.thumbnails = ThumbnailPanel()

        # 左にサムネイル、右にページ表示
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self.thumbnails)
        self._splitter.addWidget(self.page_view)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setCollapsible(0, False)
        self._splitter.setSizes([self.thumbnails.panel_width(), 1000])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)

        # 子の部品のシグナルは自分のメソッドで受ける（破棄の途中に届いても、shutdown で切り離す）
        self.document_changed.connect(self._on_document_changed)
        self.thumbnails.page_activated.connect(self.page_view.scroll_to_page)
        self.page_view.current_page_changed.connect(self.thumbnails.show_page)
        self.thumbnails.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)
        try:
            self.page_view.set_document(doc, self.render_service)
            self.thumbnails.set_document(doc, self.render_service, self.undo_stack,
                                         doc_changed=self.document_changed.emit)
            self.thumbnails.show_page(self.page_view.current_page())
        except BaseException:
            self.shutdown()
            raise

    # --- 状態 -------------------------------------------------------------
    def title(self) -> str:
        return self.doc.path.name if self.doc.path is not None else messages.APP_NAME

    def tool_tip(self) -> str:
        return str(self.doc.path) if self.doc.path is not None else ""

    def is_modified(self) -> bool:
        return not self.undo_stack.isClean()

    def set_blocked(self, blocked: bool) -> None:
        """ウィンドウの別のタブが保存中の間、このタブも操作を受け付けない。"""
        self._blocked = blocked

    def _busy(self) -> bool:
        return self._saving or self._blocked or self._shut_down

    def _on_clean_changed(self, clean: bool) -> None:
        self.modified_changed.emit(not clean)

    def _on_selection_changed(self, *_args) -> None:
        self.selection_changed.emit()

    def _on_document_changed(self) -> None:
        """文書は既に変わっている。古い画像を捨ててから並びを読み直す。"""
        if not self._shut_down:
            self.render_service.invalidate()
            self.page_view.reload_layout()
            self.thumbnails.refresh()

    def set_active(self, active: bool) -> None:
        """表示しなくなったら、ページ表示とサムネイルの画像を手放し、描画サービスの
        キャッシュを空にする（描画中・配送待ちの結果も捨てる）。描画スレッドは止めずに待たせる。
        表示に戻ったら、見えている分を頼み直す（F-85e）。"""
        if self._shut_down:
            return
        if active:
            self.page_view.request_images()
            self.thumbnails.request_images()
            return
        self.page_view.release_images()
        self.thumbnails.release_images()
        self.render_service.invalidate()  # 全消去。キャッシュと、遅れて届く結果を捨てる

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
        if self._busy() or not selected:
            return
        if self._run(DeletePagesCommand(self.doc, selected, self.document_changed.emit)):
            self.thumbnails.select_pages([min(selected[0], self.doc.page_count - 1)])

    def insert_blank_page(self) -> None:
        if self._busy():
            return
        at = self._insert_position()
        size = dialogs.ask_blank_page(self, self.doc.default_blank_size(at))
        if size is None:
            return
        width, height = size
        if self._run(InsertBlankCommand(self.doc, at, width, height, self.document_changed.emit)):
            self._show_inserted(at, 1)

    def insert_pdf_pages(self) -> None:
        if self._busy():
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
        if self._busy() or not selected:
            return
        self._run(RotatePagesCommand(self.doc, selected, delta, self.document_changed.emit))

    # --- コピー・切り取り・貼り付け（F-86） ---------------------------------
    def _export(self, selected: list[int]) -> bytes | None:
        try:
            return self.doc.export_pages(selected)
        except PdfEditorError as err:
            dialogs.show_error(self, err)
            return None

    def copy_selected_pages(self) -> bool:
        selected = self.thumbnails.selected_pages()
        if self._busy() or not selected:
            return False
        data = self._export(selected)
        if data is None:
            return False
        page_clipboard.clipboard().set_pages(data, len(selected))
        return True

    def cut_selected_pages(self) -> bool:
        """先に書き出し、次に（元に戻せる形で）取り除き、両方できた時だけクリップボードに置く。
        どちらかに失敗したら、文書もクリップボードも試す前のまま。"""
        selected = self.thumbnails.selected_pages()
        if self._busy() or not selected:
            return False
        if len(selected) >= self.doc.page_count:  # F-86e（削除と同じ扱いで、理由を表示する）
            dialogs.show_error(self, PageOperationError(messages.CUT_LAST_PAGE))
            return False
        data = self._export(selected)
        if data is None:
            return False
        if not self._run(CutPagesCommand(self.doc, selected, self.document_changed.emit)):
            return False
        page_clipboard.clipboard().set_pages(data, len(selected))
        self.thumbnails.select_pages([min(selected[0], self.doc.page_count - 1)])
        return True

    def paste_pages(self) -> bool:
        """選択中の最後のページの次（選択がなければ末尾）に貼り付け、貼り付けたページを選ぶ。"""
        data = page_clipboard.clipboard().data()
        if self._busy() or data is None:
            return False
        at = self._insert_position()
        before = self.doc.page_count
        if not self._run(PastePagesCommand(self.doc, data, at, self.document_changed.emit)):
            return False
        self._show_inserted(at, self.doc.page_count - before)
        return True

    # --- 保存 -------------------------------------------------------------
    def save(self) -> bool:
        """上書き保存。保存先がまだない（または親フォルダがない）ときは名前を付けて保存。"""
        if self._busy():
            return False
        path = self.doc.path
        if path is None or not (path.parent.is_dir() and os.access(path.parent, os.W_OK)):
            return self.save_as()
        return self._save_to(None)

    def save_as(self) -> bool:
        if self._busy():
            return False
        target = self._ask_save_path()
        return target is not None and self._save_to(target)

    def _ask_save_path(self) -> Path | None:
        path = self.doc.path
        name, _ = QFileDialog.getSaveFileName(
            self, messages.SAVE_DIALOG_TITLE, str(path) if path else "",
            messages.OPEN_DIALOG_FILTER)
        if not name:
            return None
        target = Path(name)
        if target.suffix.lower() != ".pdf":
            target = target.with_name(target.name + ".pdf")
        return target

    def _save_to(self, target: Path | None) -> bool:
        """成功するか、利用者がやめるまで続ける。成功した時だけ True。"""
        while True:
            err = self._save_in_background(target)
            if err is None:
                self.undo_stack.setClean()
                self.title_changed.emit()
                return True
            if dialogs.show_save_error(self, err) != "save_as":
                return False
            target = self._ask_save_path()
            if target is None:
                return False

    def _save_in_background(self, target: Path | None) -> SaveFailedError | None:
        """doc.save を別スレッドで走らせ、終わるまでイベントを回して待つ。"""
        self._set_saving(True)
        loop = QEventLoop()  # 親なし。終わったら deleteLater
        self._save_loop = loop
        self._save_result = None
        progress: QProgressDialog | None = None

        def show_progress() -> None:
            nonlocal progress
            progress = QProgressDialog(messages.SAVING_TEXT, "", 0, 0, self)
            progress.setCancelButton(None)  # 中断できない
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(show_progress)
        signals = _SaveSignals()
        self._save_signals = signals  # 作業スレッドが使い終わるまで保持する
        signals.done.connect(self._on_save_done, Qt.ConnectionType.QueuedConnection)
        try:
            timer.start(PROGRESS_DELAY_MS)
            QThreadPool.globalInstance().start(_SaveTask(self.doc, target, signals))
            loop.exec()
        finally:
            timer.stop()
            timer.deleteLater()
            if progress is not None:
                progress.close()
                progress.deleteLater()
            self._save_loop = None
            loop.deleteLater()
            self._set_saving(False)
        return self._save_result

    def _on_save_done(self, result) -> None:
        self._save_result = result
        if self._save_loop is not None:
            self._save_loop.quit()

    def _set_saving(self, saving: bool) -> None:
        """保存中はこのタブのページ操作を受け付けない。ウィンドウには saving_changed で知らせる。"""
        self._saving = saving
        self.thumbnails.setEnabled(not saving)  # 並べ替えのドラッグも止める
        self.saving_changed.emit(saving)

    # --- 閉じる -----------------------------------------------------------
    def confirm_close(self) -> bool:
        """閉じてよいか。未保存なら尋ねる。「保存」で保存に失敗、または「キャンセル」なら False。"""
        if self._saving:
            return False
        if not self.is_modified():
            return True
        answer = dialogs.ask_unsaved(self, self.title())
        if answer == "cancel":
            return False
        return answer != "save" or self.save()

    def shutdown(self) -> None:
        """受け口を切り離し、履歴を消し、描画スレッドを止めてから文書を閉じる。何度呼んでもよい。
        履歴はタブの子で、破棄される時に cleanChanged を出す。破棄済みのタブやウィンドウに
        届かないよう、先に切り離す（コミット 35fec82 と同じ扱い）。"""
        if self._shut_down:
            return
        self._shut_down = True
        self.undo_stack.cleanChanged.disconnect(self._on_clean_changed)
        self.thumbnails.selectionModel().selectionChanged.disconnect(self._on_selection_changed)
        self.undo_stack.clear()
        self.page_view.set_document(None, None)
        self.thumbnails.set_document(None, None, None)
        self.render_service.shutdown()
        self.doc.close()
