"""モーダルダイアログ。MainWindow からは dialogs.xxx(...) の形で呼ぶ（テストで差し替えるため）。"""
from typing import Literal

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from pdfeditor.core.document import PAGE_SIZES
from pdfeditor.core.errors import PdfEditorError, SaveFailedError
from pdfeditor.ui import messages


def ask_password(parent, kind: Literal["user", "owner"]) -> str | None:
    """パスワードを尋ねる。キャンセルは None。parent.password_retry が真なら「違う」と添える。"""
    prompt = messages.PASSWORD_PROMPT_USER if kind == "user" else messages.PASSWORD_PROMPT_OWNER
    if getattr(parent, "password_retry", False):
        prompt = f"{messages.PASSWORD_WRONG}\n\n{prompt}"
    text, ok = QInputDialog.getText(
        parent, messages.PASSWORD_TITLE, prompt, QLineEdit.EchoMode.Password
    )
    return text if ok else None


def show_error(parent, err: PdfEditorError) -> None:
    QMessageBox.warning(parent, messages.ERROR_TITLE, err.user_message)


def ask_unsaved(parent, name: str) -> Literal["save", "discard", "cancel"]:
    """閉じる前に、未保存の変更をどうするか尋ねる。"""
    box = QMessageBox(QMessageBox.Icon.Warning, messages.UNSAVED_TITLE,
                      messages.UNSAVED_TEXT.format(name=name), parent=parent)
    save = box.addButton(messages.BUTTON_SAVE, QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton(messages.BUTTON_DISCARD, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton(messages.BUTTON_CANCEL, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.setEscapeButton(cancel)
    try:
        box.exec()
        clicked = box.clickedButton()
    finally:
        box.deleteLater()
    if clicked is save:
        return "save"
    if clicked is discard:
        return "discard"
    return "cancel"


def show_save_error(parent, err: SaveFailedError) -> Literal["save_as", "cancel"]:
    """保存失敗を知らせ、別の場所に保存するか尋ねる。"""
    box = QMessageBox(QMessageBox.Icon.Warning, messages.ERROR_TITLE, err.user_message,
                      parent=parent)
    elsewhere = box.addButton(messages.BUTTON_SAVE_ELSEWHERE, QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(messages.BUTTON_CANCEL, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(elsewhere)
    box.setEscapeButton(cancel)
    try:
        box.exec()
        clicked = box.clickedButton()
    finally:
        box.deleteLater()
    return "save_as" if clicked is elsewhere else "cancel"


def show_info(parent, text: str) -> None:
    QMessageBox.information(parent, messages.INFO_TITLE, text)


class BlankPageDialog(QDialog):
    """空白ページの大きさを選ぶ。先頭は「前後のページに合わせる」（default_size）。"""

    def __init__(self, parent, default_size: tuple[float, float]) -> None:
        super().__init__(parent)
        self.setWindowTitle(messages.BLANK_DIALOG_TITLE)
        self._sizes = [tuple(default_size), *PAGE_SIZES.values()]
        self.combo = QComboBox()
        self.combo.addItem(messages.BLANK_SIZE_MATCH)
        self.combo.addItems(list(PAGE_SIZES))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(messages.BUTTON_OK)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(messages.BUTTON_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow(messages.BLANK_SIZE_LABEL, self.combo)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def chosen_size(self) -> tuple[float, float]:
        return self._sizes[self.combo.currentIndex()]


def ask_blank_page(parent, default_size: tuple[float, float]) -> tuple[float, float] | None:
    """空白ページの大きさを尋ねる。キャンセルは None。"""
    dialog = BlankPageDialog(parent, default_size)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen_size()
    finally:
        dialog.deleteLater()
