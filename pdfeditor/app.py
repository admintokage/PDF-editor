"""アプリの入口。"""
import logging
import logging.handlers
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfeditor import __version__
from pdfeditor import diagnostics
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


class _FirstPaint(QObject):
    """ウィンドウが最初に描かれた時に callback を呼ぶ（1 回だけ）。"""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback
        self._done = False

    def eventFilter(self, obj, event) -> bool:
        if not self._done and event.type() == QEvent.Type.Paint:
            self._done = True
            self._callback()
        return False


def _run_startup_benchmark(argv: list[str], result_file: Path | None, entered: float) -> int:
    from pdfeditor.ui.main_window import MainWindow

    app = QApplication(argv[:1])
    window = MainWindow()
    out: list[int] = []

    def painted() -> None:
        out.append(diagnostics.elapsed_ms_since_process_start(entered))
        QTimer.singleShot(0, app.quit)

    watcher = _FirstPaint(painted)
    window.installEventFilter(watcher)
    window.show()
    app.exec()
    window.undo_stack.setClean()
    window.close()
    if not out:
        diagnostics.write_result(result_file, "ERROR: ウィンドウが描かれませんでした")
        return 1
    diagnostics.write_result(result_file, str(out[0]))
    return 0


def main(argv: list[str]) -> int:
    entered = time.time()
    args = argv[1:]
    if "--version" in args:
        print(__version__)
        return 0
    result_file = diagnostics.option_value(args, "--result-file")
    result_path = Path(result_file) if result_file else None
    if "--self-test" in args:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 何があっても画面は出さない。ダイアログも出さない
        pdf = diagnostics.option_value(args, "--self-test")
        try:
            if pdf is None:
                raise RuntimeError("--self-test には PDF のパスが必要です")
            diagnostics.run_self_test(Path(pdf))
        except Exception as exc:  # noqa: BLE001 - どんな失敗も結果として返す
            diagnostics.write_result(result_path, f"ERROR: {type(exc).__name__}: {exc}")
            return 1
        diagnostics.write_result(result_path, "OK")
        return 0
    if "--startup-benchmark" in args:
        return _run_startup_benchmark(argv, result_path, entered)
    app = QApplication(argv)
    app.setApplicationName(messages.APP_NAME)
    install_excepthook(default_log_dir())

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.open_path(Path(argv[1]))
    return app.exec()
