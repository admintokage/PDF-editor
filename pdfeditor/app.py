"""アプリの入口。"""
import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from pdfeditor import __version__
from pdfeditor.ui import messages

LOG_NAME = "pdf-editor.log"
_LOG_BYTES = 1_000_000
_LOG_GENERATIONS = 3

_logger = logging.getLogger("pdfeditor")
_handler: logging.Handler | None = None


def default_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "PDF-editor" / "logs"


def _show_fatal(message: str) -> None:
    if QApplication.instance() is None:
        return
    QMessageBox.critical(None, messages.FATAL_TITLE, message)


def install_excepthook(log_dir: Path) -> None:
    global _handler
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_NAME
    if _handler is not None:
        _logger.removeHandler(_handler)
        _handler.close()
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_LOG_BYTES, backupCount=_LOG_GENERATIONS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _handler = handler
    _logger.addHandler(handler)
    _logger.setLevel(logging.ERROR)
    _logger.propagate = False

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _logger.error("".join(traceback.format_exception(exc_type, exc, tb)))
        handler.flush()
        _show_fatal(messages.FATAL_TEXT.format(log_path=log_path))

    sys.excepthook = hook


def main(argv: list[str]) -> int:
    if "--version" in argv[1:]:
        print(__version__)
        return 0
    app = QApplication(argv)
    app.setApplicationName(messages.APP_NAME)
    install_excepthook(default_log_dir())

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.open_path(Path(argv[1]))
    return app.exec()
