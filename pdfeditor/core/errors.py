"""エラーの型。user_message は日本語で、画面にそのまま出す。"""
from typing import Literal


class PdfEditorError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class NotAPdfError(PdfEditorError):
    """PDF として開けない（形式が違う、空、壊れている）。"""


class PasswordRequiredError(PdfEditorError):
    """開くためのパスワードが必要。"""


class WrongPasswordError(PdfEditorError):
    """パスワードが違う。"""


class OwnerPasswordRequiredError(PdfEditorError):
    """編集の制限があり、権限パスワードが必要。"""


class SaveFailedError(PdfEditorError):
    def __init__(
        self,
        user_message: str,
        reason: Literal["locked", "permission", "disk_full", "verify_failed", "other"],
    ):
        super().__init__(user_message)
        self.reason = reason


class PageOperationError(PdfEditorError):
    """ページ操作を実行できない。"""
