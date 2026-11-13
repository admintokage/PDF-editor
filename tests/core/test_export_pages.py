"""ページの書き出し（コピー・切り取りでアプリ内のクリップボードに置くバイト列）。"""
import binascii
import hashlib

import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument
from tests.helpers import form_tree_problems, labels, page_fields, snapshot


def _objects(fitz_doc) -> str:
    """全オブジェクトの中身（ストリームは展開後）を 1 つにまとめたもの。"""
    out = []
    for x in range(1, fitz_doc.xref_length()):
        out.append(fitz_doc.xref_object(x, compressed=True))
        if fitz_doc.xref_is_stream(x):
            out.append((fitz_doc.xref_stream(x) or b"").decode("latin-1"))
    return "\n".join(out)


def _hits(fitz_doc, texts: list[str]) -> list[str]:
    everything = _objects(fitz_doc)
    hits = []
    for text in texts:
        raw = text.encode()
        needles = [text, binascii.hexlify(raw).decode(), binascii.hexlify(raw).decode().upper()]
        hits += [n for n in needles if n in everything]
    return hits


def _page_objects(fitz_doc) -> int:
    return sum(1 for x in range(1, fitz_doc.xref_length())
               if fitz_doc.xref_get_key(x, "Type") == ("name", "/Page"))


def _look(fitz_doc, i: int) -> tuple:
    page = fitz_doc[i]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), alpha=False)
    return (page.rotation, tuple(round(v) for v in page.rect),
            tuple(round(v) for v in page.cropbox), hashlib.md5(pix.samples).hexdigest())


def _open(data: bytes) -> PdfDocument:
    return PdfDocument(pymupdf.open(stream=data, filetype="pdf"), None)


def test_exports_selected_pages_in_page_order_without_changing_source(big_pdf):
    d = PdfDocument.open(big_pdf(5)); before = snapshot(d)
    out = _open(d.export_pages([3, 1]))
    assert out.page_count == 2 and labels(out) == [2, 4]
    assert d.page_count == 5 and snapshot(d) == before


def test_export_contains_nothing_of_other_pages(big_pdf):
    d = PdfDocument.open(big_pdf(5))
    out = pymupdf.open(stream=d.export_pages([3, 1]), filetype="pdf")
    assert _hits(out, ["Page 1", "Page 3", "Page 5"]) == []
    assert _hits(out, ["Page 2", "Page 4"]) != []  # 調べ方が効いていること
    assert _page_objects(out) == 2


def test_export_from_nested_tree_keeps_each_page_look(nested_pdf):
    d = PdfDocument.open(nested_pdf)
    for i in range(6):
        out = pymupdf.open(stream=d.export_pages([i]), filetype="pdf")
        with d.lock:
            assert _look(out, 0) == _look(d.fitz, i), i


def test_export_from_password_pdf_is_unencrypted(user_pw_pdf):
    d = PdfDocument.open(user_pw_pdf, "user")
    out = pymupdf.open(stream=d.export_pages([0]), filetype="pdf")
    assert not out.needs_pass and not out.is_encrypted
    assert "Page 1" in out[0].get_text()


def test_export_keeps_rotation_and_annotations(toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf)
    d.set_rotation([1], 90)
    out = pymupdf.open(stream=d.export_pages([1, 2]), filetype="pdf")
    assert [p.rotation for p in out] == [90, 0]
    assert [a.type[1] for a in out[1].annots()] == ["Text"]
    assert out.get_toc() == []  # しおりは移さない


def test_export_keeps_form_fields_and_leaks_no_other_page(form_pdf):
    """3 ページ目の部品は 5 ページ目の部品と親を共有している。親から 5 ページ目をたどらない。"""
    d = PdfDocument.open(form_pdf); before = snapshot(d)
    with d.lock:
        before_objects = {x: d.fitz.xref_object(x, compressed=True)
                          for x in range(1, d.fitz.xref_length())}
    for indices, texts in (([0], ["name"]), ([1, 3], ["chk", "other"]), ([2], ["shared"])):
        out = pymupdf.open(stream=d.export_pages(indices), filetype="pdf")
        names = [w.field_name for p in out for w in p.widgets()]
        assert names == texts
        assert form_tree_problems(out) == []
        others = [f"Page {n + 1}" for n in range(5) if n not in indices]
        assert _hits(out, others) == [], indices
        assert _page_objects(out) == len(indices)
    # 元の文書はオブジェクトの中身まで変わらない（フォームの /Fields や部品の /P を含めて）
    with d.lock:
        assert {x: d.fitz.xref_object(x, compressed=True)
                for x in range(1, d.fitz.xref_length())} == before_objects
    assert snapshot(d) == before and form_tree_problems(d.fitz) == []
    assert page_fields(d.fitz)[0] == (("name", "Text", "val-name"),)


def test_export_does_not_carry_deleted_pages(big_pdf):
    """削除（元に戻すために文書に残してある）ページの中身も入らない。"""
    d = PdfDocument.open(big_pdf(5))
    d.delete_pages([2])
    out = pymupdf.open(stream=d.export_pages([0, 1]), filetype="pdf")
    assert _hits(out, ["Page 3", "Page 4", "Page 5"]) == [] and out.page_count == 2


@pytest.mark.parametrize("indices,error", [([], ValueError), ([0, 0], ValueError),
                                           ([9], IndexError), ([-1], IndexError)])
def test_export_rejects_bad_indices(big_pdf, indices, error):
    d = PdfDocument.open(big_pdf(5))
    with pytest.raises(error):
        d.export_pages(indices)
