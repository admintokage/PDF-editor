"""テスト共通の補助関数。"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def lock_file(path: Path):
    """ファイルを開いたままにする。Windows では開いているファイルは置き換えられず、
    「Acrobat Reader で開いている」状態を再現できる。"""
    f = open(path, "rb")
    try:
        yield f
    finally:
        f.close()


def labels(doc) -> list[int]:
    """各ページの文字 "Page N" から N の列を返す。なければ 0。"""
    import re

    out = []
    with doc.lock:
        for i in range(doc.fitz.page_count):
            m = re.search(r"Page (\d+)", doc.fitz[i].get_text())
            out.append(int(m.group(1)) if m else 0)
    return out


def make_view(qtbot, pdf_path: Path, size=(800, 1000)):
    """文書を開いて PageView に載せ、表示されるまで待つ。後始末は描画サービスを止めてから
    文書を閉じる（RenderService の約束）。"""
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView
    from pdfeditor.ui.render_service import RenderService

    doc = PdfDocument.open(pdf_path)
    service = RenderService(doc)
    view = PageView()

    def cleanup(_view) -> None:
        view.set_document(None, None)
        service.shutdown()
        doc.close()

    qtbot.addWidget(view, before_close_func=cleanup)
    view.resize(*size)
    view.set_document(doc, service)
    view.show()
    qtbot.waitExposed(view)
    return view


def open_window(qtbot, pdf_path: Path):
    """MainWindow を出して（offscreen）文書を開き、表示されるまで待つ。後始末では変更を
    保存済み扱いにしてから閉じる（閉じる時に保存を尋ねないように）。"""
    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow()

    def cleanup(_window) -> None:
        window.undo_stack.setClean()

    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    assert window.open_path(pdf_path)
    qtbot.waitExposed(window)
    return window


def select(window, indices: list[int]) -> None:
    """サムネイル一覧でページ indices を選ぶ（それ以外の選択は外す）。"""
    window.thumbnails.select_pages(indices)


def snapshot(doc) -> tuple:
    """(ラベル列, 各ページの回転, 各ページの大きさ(整数))。元に戻すの検証用。"""
    with doc.lock:
        n = doc.fitz.page_count
        rotations = tuple(doc.page_rotation(i) for i in range(n))
        sizes = tuple(tuple(round(v) for v in doc.page_size(i)) for i in range(n))
    return (tuple(labels(doc)), rotations, sizes)
