"""アプリの入口。"""
import logging
import logging.handlers
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QLibraryInfo, QObject, QTimer, QTranslator
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from pdfeditor import __version__
from pdfeditor import diagnostics
from pdfeditor.ui import messages

LOG_NAME = "pdf-editor.log"
_LOG_BYTES = 1_000_000
_LOG_GENERATIONS = 3
# Qt 自身の文言（標準ボタン、入力欄の右クリックメニューなど）の日本語訳
QT_TRANSLATIONS = ("qtbase_ja", "qt_ja")

_logger = logging.getLogger("pdfeditor")
_handler: logging.Handler | None = None
_log_path: Path | None = None


def default_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "PDF-editor" / "logs"


def fallback_log_dir() -> Path:
    return Path(tempfile.gettempdir()) / "PDF-editor" / "logs"


def _open_log(log_dir: Path) -> tuple[logging.Handler, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_NAME
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_LOG_BYTES, backupCount=_LOG_GENERATIONS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    return handler, log_path


def close_logging() -> None:
    global _handler, _log_path
    if _handler is not None:
        _logger.removeHandler(_handler)
        _handler.close()
    _handler, _log_path = None, None


def setup_logging(log_dir: Path) -> Path | None:
    """ログファイルを用意する。できる範囲で行い、失敗しても起動は止めない。
    log_dir → 一時フォルダの順に試し、どちらもだめならファイルには書かない。書く先を返す。"""
    global _handler, _log_path
    close_logging()
    for candidate in (log_dir, fallback_log_dir()):
        try:
            handler, log_path = _open_log(candidate)
        except OSError:
            continue
        _handler, _log_path = handler, log_path
        _logger.addHandler(handler)
        break
    _logger.setLevel(logging.ERROR)
    _logger.propagate = False
    return _log_path


def _log_error(text: str) -> None:
    try:
        _logger.error(text)
        if _handler is not None:
            _handler.flush()
    except Exception:  # noqa: BLE001 - 記録の失敗でさらに落ちない
        pass


def _fatal_text(first_line: str) -> str:
    """first_line に、ログの場所（書けていれば）を添える。"""
    if _log_path is None:
        return f"{first_line}\n{messages.FATAL_NO_LOG}"
    return f"{first_line}\n{messages.FATAL_LOG_AT.format(log_path=_log_path)}"


def _show_fatal(message: str) -> None:
    """日本語でエラーを知らせる。QApplication がなければ何もしない。ここでは決して例外を出さない。"""
    try:
        if QApplication.instance() is None:
            return
        box = QMessageBox(QMessageBox.Icon.Critical, messages.FATAL_TITLE, message)
        box.addButton(messages.BUTTON_OK, QMessageBox.ButtonRole.AcceptRole)
        box.exec()
    except Exception:  # noqa: BLE001
        _log_error("could not show the fatal error dialog:\n" + traceback.format_exc())


def install_excepthook(log_dir: Path) -> Path | None:
    """ログを用意し、拾われなかった例外を記録して日本語で知らせるようにする。"""
    log_path = setup_logging(log_dir)

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log_error("".join(traceback.format_exception(exc_type, exc, tb)))
        _show_fatal(_fatal_text(messages.FATAL_TEXT))

    sys.excepthook = hook
    return log_path


def install_translations(app: QApplication) -> list[str]:
    """Qt の日本語訳を読み込む。読み込めた訳の名前を返す（見つからなくても起動は続ける）。"""
    folders = [QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    try:
        import PySide6

        folders.append(str(Path(PySide6.__file__).parent / "translations"))
    except Exception:  # noqa: BLE001
        pass
    loaded = []
    for name in QT_TRANSLATIONS:
        for folder in folders:
            translator = QTranslator(app)
            if translator.load(name, folder):
                app.installTranslator(translator)
                loaded.append(name)
                break
    return loaded


def _make_app(argv: list[str]) -> QApplication:
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(messages.APP_NAME)
    install_translations(app)
    return app


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

    app = _make_app(argv[:1])
    window = MainWindow()
    out: list[int] = []

    def painted() -> None:
        out.append(diagnostics.elapsed_ms_since_process_start(entered))
        QTimer.singleShot(0, app.quit)

    watcher = _FirstPaint(painted)
    window.installEventFilter(watcher)
    window.show()
    app.exec()
    window.close()  # 文書を開いていないので、保存を尋ねない
    if not out:
        diagnostics.write_result(result_file, "ERROR: ウィンドウが描かれませんでした")
        return 1
    diagnostics.write_result(result_file, str(out[0]))
    return 0


def _run_translation_check(argv: list[str], result_file: Path | None) -> int:
    """配布物の確認用。標準の「キャンセル」ボタンの文言を書き出す（日本語訳が効いているか）。"""
    _make_app(argv[:1])
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    diagnostics.write_result(result_file, buttons.button(QDialogButtonBox.StandardButton.Cancel).text())
    return 0


def _diagnostic_mode(args: list[str]) -> str | None:
    for flag in ("--self-test", "--startup-benchmark", "--translation-check"):
        if flag in args:
            return flag
    return None


def main(argv: list[str]) -> int:
    """どんな起動時の失敗も、記録して（画面のある起動なら）日本語で知らせ、0 以外を返す。
    確認用の起動（--self-test など）ではダイアログを出さず、結果ファイルに書く。"""
    entered = time.time()
    args = argv[1:]
    if "--version" in args:
        print(__version__)
        return 0
    result_file = diagnostics.option_value(args, "--result-file")
    result_path = Path(result_file) if result_file else None
    mode = _diagnostic_mode(args)
    if mode is not None:
        if mode != "--startup-benchmark":  # 起動時間は実際の画面で測れるよう、そのままにする
            os.environ["QT_QPA_PLATFORM"] = "offscreen"  # 画面もダイアログも出さない
        try:
            return _run_diagnostic(mode, argv, args, result_path, entered)
        except Exception as exc:  # noqa: BLE001 - どんな失敗も結果として返す
            diagnostics.write_result(result_path, f"ERROR: {type(exc).__name__}: {exc}")
            return 1

    install_excepthook(default_log_dir())  # QApplication より先に。ログが書けなくても続ける
    try:
        return _run_app(argv)
    except Exception:  # noqa: BLE001 - 起動の失敗でも黙って消えない
        _log_error("startup failed:\n" + traceback.format_exc())
        try:
            if QApplication.instance() is None:
                QApplication(argv[:1])
        except Exception:  # noqa: BLE001 - 画面が使えないなら記録だけ
            return 1
        _show_fatal(_fatal_text(messages.STARTUP_FAILED_TEXT))
        return 1


def _run_diagnostic(mode: str, argv: list[str], args: list[str], result_path: Path | None,
                    entered: float) -> int:
    if mode == "--self-test":
        pdf = diagnostics.option_value(args, "--self-test")
        if pdf is None:
            raise RuntimeError("--self-test には PDF のパスが必要です")
        diagnostics.run_self_test(Path(pdf))
        diagnostics.write_result(result_path, "OK")
        return 0
    if mode == "--translation-check":
        return _run_translation_check(argv, result_path)
    return _run_startup_benchmark(argv, result_path, entered)


def _run_app(argv: list[str]) -> int:
    app = _make_app(argv)

    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    # 実行ファイルに複数のファイルをドロップすると、それぞれタブで開く。ウィンドウへの
    # ドロップと同じく、PDF 以外は無視する（--オプションはファイルではない）
    window.handle_dropped_paths([Path(a) for a in argv[1:] if not a.startswith("--")])
    return app.exec()
