"""UI テストはウィンドウを出さずに動かす。Qt を読み込む前に設定する。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_unsaved_prompt(monkeypatch):
    """後片付けで閉じる時に本物の確認ダイアログを出さない。個別のテストは上書きできる。"""
    monkeypatch.setattr("pdfeditor.ui.dialogs.ask_unsaved", lambda parent, name: "discard")
    # 本物のモーダルを開くと無人の実行が止まるので、他は失敗にする。使うテストが差し替える
    for name in ("show_save_error", "show_error", "show_info", "ask_password", "ask_blank_page"):
        monkeypatch.setattr(
            f"pdfeditor.ui.dialogs.{name}",
            lambda *a, _n=name, **k: pytest.fail(f"unexpected real dialog: dialogs.{_n}"))


@pytest.fixture(autouse=True)
def _empty_page_clipboard():
    """ページ用クリップボードはアプリに 1 つなので、テストごとに空にする。"""
    yield
    from pdfeditor.ui import page_clipboard

    page_clipboard.clipboard().clear()


@pytest.fixture(autouse=True)
def _close_all_windows():
    yield
    from pdfeditor.ui.main_window import MainWindow

    for window in list(MainWindow.instances):
        window.close()
    MainWindow.instances.clear()
