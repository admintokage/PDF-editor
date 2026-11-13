"""アプリの入口（app.main）。画面は offscreen、イベントループは回さない。"""
import sys

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from pdfeditor import app
from pdfeditor.ui import dialogs, messages
from pdfeditor.ui.main_window import MainWindow

_REAL_SHOW_FATAL = app._show_fatal  # 下の fixture が差し替える前のもの

@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path, qapp):
    """ログは tmp_path の中へ。イベントループは回さずに戻る。本物のダイアログは出さない。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(app, "fallback_log_dir", lambda: tmp_path / "temp" / "PDF-editor" / "logs")
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    fatal = []
    monkeypatch.setattr(app, "_show_fatal", fatal.append)
    yield fatal
    app.close_logging()  # ログファイルを手放す


def _log_text(tmp_path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("pdf-editor.log*"))


def test_main_starts_and_opens_argv(simple_pdf, _isolated):
    assert app.main(["PDF-editor", str(simple_pdf)]) == 0
    assert [w.doc.page_count for w in MainWindow.instances] == [3] and _isolated == []


def test_main_with_broken_argv_file_shows_error_and_keeps_running(tmp_path, monkeypatch, _isolated):
    import pymupdf
    doc = pymupdf.open(); [doc.new_page() for _ in range(3)]; raw = doc.tobytes(); doc.close()
    bad = tmp_path / "badcount.pdf"; bad.write_bytes(raw.replace(b"/Count 3", b"/Count 9"))
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    assert app.main(["PDF-editor", str(bad)]) == 0
    assert len(shown) == 1 and "開けません" in shown[0].user_message
    assert len(MainWindow.instances) == 1 and MainWindow.instances[0].doc is None
    assert _isolated == []


def test_unwritable_log_folder_falls_back_to_temp(tmp_path, monkeypatch, _isolated):
    blocker = tmp_path / "local"; blocker.write_text("not a folder")  # ここにはフォルダを作れない
    assert app.main(["PDF-editor"]) == 0
    assert app._log_path == tmp_path / "temp" / "PDF-editor" / "logs" / "pdf-editor.log"


def test_no_writable_log_folder_still_starts(tmp_path, monkeypatch, _isolated):
    (tmp_path / "local").write_text("x"); (tmp_path / "temp").write_text("x")
    assert app.main(["PDF-editor"]) == 0
    assert app._log_path is None and MainWindow.instances


def test_exception_during_window_creation_is_logged_and_reported(tmp_path, monkeypatch, _isolated):
    def boom(self): raise RuntimeError("window boom")
    monkeypatch.setattr(MainWindow, "__init__", boom)
    assert app.main(["PDF-editor"]) != 0
    assert len(_isolated) == 1 and _isolated[0].startswith(messages.STARTUP_FAILED_TEXT)
    assert "window boom" in _log_text(tmp_path / "local")


def test_fatal_without_log_says_so(tmp_path, monkeypatch, _isolated):
    (tmp_path / "local").write_text("x"); (tmp_path / "temp").write_text("x")
    monkeypatch.setattr(MainWindow, "__init__", lambda self: (_ for _ in ()).throw(RuntimeError()))
    assert app.main(["PDF-editor"]) != 0
    assert _isolated == [f"{messages.STARTUP_FAILED_TEXT}\n{messages.FATAL_NO_LOG}"]


@pytest.mark.parametrize("flag", ["--startup-benchmark", "--translation-check"])
def test_diagnostic_modes_never_show_a_dialog(tmp_path, monkeypatch, _isolated, flag):
    monkeypatch.setattr(MainWindow, "__init__", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("pdfeditor.app.QDialogButtonBox",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    out = tmp_path / "r.txt"
    assert app.main(["PDF-editor", flag, "--result-file", str(out)]) == 1
    assert out.read_text(encoding="utf-8").startswith("ERROR") and _isolated == []


def test_show_fatal_never_raises(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    _REAL_SHOW_FATAL("テスト")  # 例外を出さない


def test_qt_japanese_translation_is_installed(qapp):
    loaded = app.install_translations(qapp)
    assert "qtbase_ja" in loaded
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
    assert box.button(QDialogButtonBox.StandardButton.Cancel).text() == "キャンセル"


def test_app_dialogs_use_japanese_button_texts(qapp):
    box = dialogs.message_box(None, QMessageBox.Icon.Warning, "t", "x")
    assert [b.text() for b in box.buttons()] == [messages.BUTTON_OK]
    pw = dialogs.password_dialog(None, "x")
    assert pw.okButtonText() == messages.BUTTON_OK and pw.cancelButtonText() == messages.BUTTON_CANCEL
    box.deleteLater(); pw.deleteLater()
