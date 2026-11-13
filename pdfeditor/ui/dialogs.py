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
from pdfeditor.core.errors import PdfEditorError
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
