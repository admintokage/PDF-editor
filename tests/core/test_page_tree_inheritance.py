"""中間の /Pages ノードから属性（MediaBox・CropBox・Rotate・Resources）を受け継ぐページが、
並べ替え・削除の取り消し・保存のあとも、元と同じ大きさ・向き・見た目のままであること。"""
import hashlib
import itertools

import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument, page_order_after_move
from tests.helpers import labels


def _look(doc: PdfDocument) -> dict[int, tuple]:
    """ラベル → (回転, 表示上の大きさ, CropBox, 画像のハッシュ)。"""
    out = {}
    with doc.lock:
        for label, i in zip(labels(doc), range(doc.fitz.page_count)):
            page = doc.fitz[i]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), alpha=False)
            out[label] = (page.rotation, tuple(round(v) for v in page.rect),
                          tuple(round(v) for v in page.cropbox),
                          hashlib.md5(pix.samples).hexdigest())
    return out


def _check(doc: PdfDocument, original: dict, tmp_path, name="o.pdf") -> None:
    now = _look(doc)
    assert now == {k: original[k] for k in now}
    out = tmp_path / name
    doc.save(out)
    again = PdfDocument.open(out)
    assert _look(again) == now and labels(again) == labels(doc)
    again.close()


def test_fixture_really_inherits(nested_pdf):
    d = PdfDocument.open(nested_pdf); look = _look(d)
    assert look[1][0] == 90 and look[3][1] == (0, 0, 400, 600) and look[5][0] == 180
    assert look[6][1] == (0, 0, 595, 842)
    assert len({v[3] for v in look.values()}) == 6


@pytest.mark.parametrize("op", [
    lambda d: d.move_pages([3], 0),
    lambda d: d.move_pages([0], 6),
    lambda d: d.move_pages([0, 4], 2),
    lambda d: d.move_pages([5, 2], 1),
    lambda d: d.reorder([5, 4, 3, 2, 1, 0]),
    lambda d: d.reorder([2, 5, 0, 3, 1, 4]),
    lambda d: (d.move_pages([1], 5), d.reorder([0, 4, 1, 2, 3, 5])),  # 移動と、その取り消し
])
def test_reordering_keeps_each_page_look(nested_pdf, tmp_path, op):
    d = PdfDocument.open(nested_pdf); original = _look(d)
    op(d)
    _check(d, original, tmp_path)


def test_every_single_move_keeps_order_and_look(nested_pdf):
    d0 = PdfDocument.open(nested_pdf); original = _look(d0); d0.close()
    for i, to in itertools.product(range(6), range(7)):
        d = PdfDocument.open(nested_pdf)
        d.move_pages([i], to)
        assert labels(d) == [n + 1 for n in page_order_after_move(6, [i], to)]
        assert _look(d) == original, (i, to)
        d.close()


@pytest.mark.parametrize("indices", [[0], [1], [2], [4], [5], [1, 2], [0, 3, 4], [1, 4, 5]])
def test_delete_restore_keeps_each_page_look(nested_pdf, tmp_path, indices):
    d = PdfDocument.open(nested_pdf); original = _look(d)
    detached = d.delete_pages(indices)
    _check(d, original, tmp_path, "deleted.pdf")
    d.restore_pages(detached)
    assert labels(d) == [1, 2, 3, 4, 5, 6]
    _check(d, original, tmp_path)


def test_move_then_delete_then_restore(nested_pdf, tmp_path):
    d = PdfDocument.open(nested_pdf); original = _look(d)
    d.move_pages([4], 0)
    detached = d.delete_pages([1, 3])
    d.restore_pages(detached)
    d.reorder([1, 2, 3, 4, 0, 5])
    assert labels(d) == [1, 2, 3, 4, 5, 6]
    _check(d, original, tmp_path)


def test_new_pages_do_not_inherit_from_their_new_node(nested_pdf, tmp_path):
    """空白ページや挿入したページが、置かれた先のノードの Rotate などを受け継がない。"""
    d = PdfDocument.open(nested_pdf); original = _look(d)
    d.insert_blank(1, 595, 842)  # A ノード（Rotate 90）の中へ
    assert d.page_rotation(1) == 0 and tuple(round(v) for v in d.page_size(1)) == (595, 842)
    src = pymupdf.open(); src.new_page(width=200, height=100); data = src.tobytes(); src.close()
    d.insert_from_bytes(data, 5)  # B の中の C（Rotate 180）の隣へ
    assert d.page_rotation(5) == 0 and tuple(round(v) for v in d.page_size(5)) == (200, 100)
    out = tmp_path / "o.pdf"; d.save(out); r = PdfDocument.open(out)
    assert r.page_rotation(1) == 0 and r.page_rotation(5) == 0
    assert {k: v for k, v in _look(r).items() if k} == {k: original[k] for k in range(1, 7)}
