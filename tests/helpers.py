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


def page_fields(fitz_doc) -> tuple:
    """各ページのフォーム部品を (フィールド名, 種類, 値) の並びで返す。"""
    return tuple(
        tuple((w.field_name, w.field_type_string, w.field_value) for w in fitz_doc[i].widgets())
        for i in range(fitz_doc.page_count)
    )


def _refs(text: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"(\d+)\s+\d+\s+R", text)]


def form_tree_problems(fitz_doc) -> list[str]:
    """AcroForm/Fields からたどれる部品と、ページ上の部品が一致しなければ、その説明を返す。
    孤立した（どのページにもない）部品・重複・Fields から外れた部品を見つける。"""
    def get(xref: int, key: str) -> tuple[str, str]:
        kind, value = fitz_doc.xref_get_key(xref, key)
        if kind == "xref":  # 間接参照の配列・辞書は中身を読む
            return "resolved", fitz_doc.xref_object(_refs(value)[0], compressed=True)
        return kind, value

    cat = fitz_doc.pdf_catalog()
    kind, acro = fitz_doc.xref_get_key(cat, "AcroForm")
    on_pages = [w.xref for i in range(fitz_doc.page_count) for w in fitz_doc[i].widgets()]
    if kind == "null":
        return [] if not on_pages else ["AcroForm がないのにページに部品がある"]
    acro_xref = _refs(acro)[0] if kind == "xref" else cat
    key = "Fields" if kind == "xref" else "AcroForm/Fields"
    leaves: list[int] = []

    def walk(xref: int) -> None:
        k, kids = get(xref, "Kids")
        if k in ("array", "resolved") and _refs(kids):
            for kid in _refs(kids):
                walk(kid)
        else:
            leaves.append(xref)

    for top in _refs(get(acro_xref, key)[1]):
        walk(top)
    problems = []
    if len(leaves) != len(set(leaves)):
        problems.append(f"Fields に重複: {leaves}")
    if set(leaves) - set(on_pages):
        problems.append(f"どのページにもない部品: {sorted(set(leaves) - set(on_pages))}")
    if set(on_pages) - set(leaves):
        problems.append(f"Fields から外れた部品: {sorted(set(on_pages) - set(leaves))}")
    return problems


def snapshot(doc) -> tuple:
    """(ラベル列, 各ページの回転, 各ページの大きさ(整数), 各ページのフォーム部品, is_form_pdf)。
    元に戻すの検証用。"""
    with doc.lock:
        n = doc.fitz.page_count
        rotations = tuple(doc.page_rotation(i) for i in range(n))
        sizes = tuple(tuple(round(v) for v in doc.page_size(i)) for i in range(n))
        fields = page_fields(doc.fitz)
        form = doc.fitz.is_form_pdf
    return (tuple(labels(doc)), rotations, sizes, fields, form)
