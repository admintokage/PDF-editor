"""UI テストはウィンドウを出さずに動かす。Qt を読み込む前に設定する。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_unsaved_prompt(monkeypatch):
    """後片付けで閉じる時に本物の確認ダイアログを出さない。個別のテストは上書きできる。"""
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda parent, name: "discard")


@pytest.fixture(autouse=True)
def _close_all_windows():
    yield
    from pdfeditor.ui.main_window import MainWindow

    for window in list(MainWindow.instances):
        window.close()
    MainWindow.instances.clear()
