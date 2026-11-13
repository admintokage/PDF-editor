"""モーダルダイアログ。MainWindow からは dialogs.xxx(...) の形で呼ぶ（テストで差し替えるため）。"""
from typing import Literal

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

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
