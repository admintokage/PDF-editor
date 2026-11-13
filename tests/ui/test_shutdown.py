"""アプリ終了時の後片付けで例外が出ないこと。

未保存の変更を「保存しない」で閉じると、終了時に元に戻す履歴が破棄され、
その通知が破棄済みのウィンドウに届いて「予期しないエラー」になっていた。
プロセスの終了時にしか起きないので、別プロセスで確かめる。
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from pdfeditor.ui import dialogs
    from pdfeditor.ui.main_window import MainWindow

    sys.excepthook = lambda t, v, tb: print("HOOK:", t.__name__, v, flush=True)
    app = QApplication([])
    dialogs.ask_unsaved = lambda parent, name: sys.argv[2]
    w = MainWindow()
    w.show()
    assert w.open_path(Path(sys.argv[1]))
    w.thumbnails.select_pages([0])
    w.delete_selected_pages()
    assert w.isWindowModified()
    print("closed:", w.close(), flush=True)
    """
)


@pytest.mark.parametrize("answer", ["discard", "save"])
def test_no_error_at_exit_after_closing_modified_window(simple_pdf, tmp_path, answer):
    script = tmp_path / "close_modified.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(REPO))
    result = subprocess.run(
        [sys.executable, str(script), str(simple_pdf), answer],
        capture_output=True, text=True, env=env, timeout=60,
    )
    output = result.stdout + result.stderr
    assert "closed: True" in output
    assert "HOOK:" not in output
    assert "RuntimeError" not in output
    assert result.returncode == 0


def test_closing_twice_is_harmless(qtbot, simple_pdf):
    from pdfeditor.ui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    assert w.open_path(simple_pdf)
    w.thumbnails.select_pages([0])
    w.rotate_selected(90)
    assert w.close()
    assert w.close()
    assert not w.isWindowModified()
