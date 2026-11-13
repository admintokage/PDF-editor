"""アプリ内のページ用クリップボードと、ページのコピー・切り取り・貼り付け（F-86）。"""
from pdfeditor.ui import page_clipboard


def test_clipboard_set_and_clear(qtbot):
    board = page_clipboard.clipboard()
    assert board is page_clipboard.clipboard()  # アプリに 1 つ
    assert board.data() is None and board.count() == 0
    with qtbot.waitSignal(board.changed):
        board.set_pages(b"%PDF-data", 2)
    assert board.data() == b"%PDF-data" and board.count() == 2
    with qtbot.waitSignal(board.changed):
        board.clear()
    assert board.data() is None and board.count() == 0


def test_clipboard_does_not_show_its_bytes(qtbot):
    board = page_clipboard.clipboard()
    board.set_pages(b"SECRET-BYTES", 1)
    assert "SECRET" not in repr(board)
