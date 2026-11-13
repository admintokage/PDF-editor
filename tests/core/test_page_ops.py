import pymupdf
import pytest

from pdfeditor.core import errors
from pdfeditor.core.document import PAGE_SIZES, PdfDocument, page_order_after_move
from tests.helpers import labels, page_labels


def test_page_order_after_move():
    assert page_order_after_move(5, [0], 3) == [1, 2, 0, 3, 4]
    assert page_order_after_move(5, [1, 3], 0) == [1, 3, 0, 2, 4]
    assert page_order_after_move(5, [4], 5) == [0, 1, 2, 3, 4]


def test_move_pages(big_pdf):
    d = PdfDocument.open(big_pdf(5)); d.move_pages([0], 3); assert labels(d) == [2, 3, 1, 4, 5]


def test_move_pages_unsorted_indices_keep_relative_order(big_pdf):
    d = PdfDocument.open(big_pdf(5)); d.move_pages([3, 1], 0); assert labels(d) == [2, 4, 1, 3, 5]


def test_reorder_keeps_toc_and_links(toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); d.reorder([4, 3, 2, 1, 0])
    assert len(d.fitz.get_toc()) == 3
    assert d.fitz[3].get_links()[0]["page"] == 1   # 旧 2→4 ページ目へのリンクが、新 4→2 ページ目を指す


def test_reorder_remaps_toc_targets(toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); d.reorder([4, 3, 2, 1, 0])
    assert [t[2] for t in d.fitz.get_toc()] == [5, 4, 2]


def test_delete_returns_restorable_pages(big_pdf):
    d = PdfDocument.open(big_pdf(5)); removed = d.delete_pages([1, 3]); assert labels(d) == [1, 3, 5]
    d.restore_pages(removed); assert labels(d) == [1, 2, 3, 4, 5]


def test_cannot_delete_all_pages(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.PageOperationError) as e: d.delete_pages([0, 1, 2])
    assert "最後の 1 ページ" in e.value.user_message and d.page_count == 3


def test_delete_empty_is_noop(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    assert d.delete_pages([]) is None and d.page_count == 3


def test_delete_bad_index_leaves_document_unchanged(big_pdf):
    d = PdfDocument.open(big_pdf(5))
    with pytest.raises(IndexError): d.delete_pages([1, 9])
    assert labels(d) == [1, 2, 3, 4, 5]


def test_delete_duplicate_indices_rejected(big_pdf):
    d = PdfDocument.open(big_pdf(5))
    with pytest.raises(ValueError): d.delete_pages([1, 1])
    assert d.page_count == 5


def test_insert_blank_default_size_follows_neighbor(mixed_size_pdf):
    d = PdfDocument.open(mixed_size_pdf); w, h = d.default_blank_size(2); d.insert_blank(2, w, h)
    assert d.page_count == 4 and tuple(round(x) for x in d.page_size(2)) == (842, 595)


def test_default_blank_size_at_start_uses_first_page(mixed_size_pdf):
    d = PdfDocument.open(mixed_size_pdf)
    assert tuple(round(x) for x in d.default_blank_size(0)) == (595, 842)


def test_page_sizes_constant():
    assert PAGE_SIZES["A4 縦"] == (595, 842) and len(PAGE_SIZES) == 6


def test_insert_from_file_at_position(simple_pdf, big_pdf):
    d = PdfDocument.open(big_pdf(5)); n = d.insert_from_file(simple_pdf, 2)
    assert n == 3 and labels(d) == [1, 2, 1, 2, 3, 3, 4, 5]


def test_insert_document_into_itself(simple_pdf):
    d = PdfDocument.open(simple_pdf); d.insert_from_file(simple_pdf, 3); assert labels(d) == [1, 2, 3, 1, 2, 3]


def test_insert_encrypted_requires_password(simple_pdf, user_pw_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.PasswordRequiredError): d.insert_from_file(user_pw_pdf, 0)
    assert d.page_count == 3


def test_insert_encrypted_wrong_and_right_password(simple_pdf, user_pw_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.WrongPasswordError): d.insert_from_file(user_pw_pdf, 0, "bad")
    assert d.page_count == 3
    assert d.insert_from_file(user_pw_pdf, 0, "user") == 1 and labels(d) == [1, 1, 2, 3]


def test_insert_owner_restricted_source_without_password(simple_pdf, owner_pw_pdf):
    d = PdfDocument.open(simple_pdf)
    assert d.insert_from_file(owner_pw_pdf, 3) == 1 and d.page_count == 4


def test_insert_non_pdf_rejected(simple_pdf, tmp_path):
    bad = tmp_path / "x.pdf"; bad.write_bytes(b"not a pdf")
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.NotAPdfError): d.insert_from_file(bad, 0)
    with pytest.raises(errors.NotAPdfError): d.insert_from_file(tmp_path / "missing.pdf", 0)
    assert d.page_count == 3


def test_insert_at_out_of_range(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    data = simple_pdf.read_bytes()
    for bad in (-1, 4):
        with pytest.raises(IndexError): d.insert_from_bytes(data, bad)
        with pytest.raises(IndexError): d.insert_blank(bad, 100, 100)
    assert d.page_count == 3


def test_rotate_by(simple_pdf):
    d = PdfDocument.open(simple_pdf); d.rotate_by([0], 90); d.rotate_by([0], 270)
    assert d.page_rotation(0) == 0
    d.rotate_by([1], -90); assert d.page_rotation(1) == 270


def test_set_rotation(simple_pdf):
    d = PdfDocument.open(simple_pdf); d.set_rotation([0, 2], 180)
    assert [d.page_rotation(i) for i in range(3)] == [180, 0, 180]


def test_rotation_validation_leaves_document_unchanged(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(ValueError): d.rotate_by([0], 45)
    with pytest.raises(ValueError): d.set_rotation([0], 45)
    with pytest.raises(IndexError): d.rotate_by([0, 7], 90)
    with pytest.raises(IndexError): d.set_rotation([0, 7], 90)
    assert [d.page_rotation(i) for i in range(3)] == [0, 0, 0]


def test_move_and_reorder_validation(big_pdf):
    d = PdfDocument.open(big_pdf(5))
    with pytest.raises(IndexError): d.move_pages([0], 6)
    with pytest.raises(IndexError): d.move_pages([0], -1)
    with pytest.raises(IndexError): d.move_pages([0, 5], 1)
    with pytest.raises(ValueError): d.move_pages([1, 1], 0)
    with pytest.raises(ValueError): d.reorder([0, 1, 2, 3])
    with pytest.raises(ValueError): d.reorder([0, 1, 2, 3, 3])
    with pytest.raises(ValueError): d.reorder([0, 1, 2, 3, 9])
    assert labels(d) == [1, 2, 3, 4, 5]


def test_empty_indices_are_noops(big_pdf):
    d = PdfDocument.open(big_pdf(3))
    d.move_pages([], 1); d.rotate_by([], 90); d.set_rotation([], 90)
    assert labels(d) == [1, 2, 3] and d.page_rotation(0) == 0


def test_ops_survive_save(big_pdf, tmp_path):
    d = PdfDocument.open(big_pdf(5)); d.move_pages([4], 0); d.delete_pages([1]); d.rotate_by([0], 90)
    out = tmp_path / "o.pdf"; d.save(out); r = PdfDocument.open(out)
    assert labels(r) == [5, 2, 3, 4] and r.page_rotation(0) == 90


# ---- ページラベル: 削除・移動・挿入を元に戻すと、ラベルも /PageLabels も元どおり ----

def _labelled(tmp_path, n: int = 8) -> PdfDocument:
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels",
                     "<</Nums[0<</S/r>>2<</S/D/P(ch-)>>5<</S/A/St 3>>]>>")
    doc.save(tmp_path / "labels.pdf")
    doc.close()
    return PdfDocument.open(tmp_path / "labels.pdf")


@pytest.mark.parametrize("indices", [[2], [0, 4], [0], [7], [1, 2, 3, 4, 5, 6]],
                         ids=["single", "multi", "first", "last", "most"])
def test_delete_undo_restores_page_labels(tmp_path, indices):
    d = _labelled(tmp_path); before = page_labels(d.fitz)
    detached = d.delete_pages(indices)
    after_delete = page_labels(d.fitz)
    d.restore_pages(detached); assert page_labels(d.fitz) == before
    d.restore_pages(d.delete_pages(indices)); assert page_labels(d.fitz) == before  # 何度でも
    assert d.delete_pages(indices) and page_labels(d.fitz) == after_delete       # やり直しも同じ


def test_move_undo_keeps_page_labels(tmp_path):
    d = _labelled(tmp_path); before = page_labels(d.fitz)
    order = page_order_after_move(8, [1], 6)
    d.move_pages([1], 6)
    d.reorder([order.index(i) for i in range(8)])
    assert labels(d) == list(range(1, 9)) and page_labels(d.fitz) == before


@pytest.mark.parametrize("at", [0, 3, 8])
def test_insert_undo_restores_page_labels(tmp_path, simple_pdf, at):
    d = _labelled(tmp_path); before = page_labels(d.fitz)
    d.insert_blank(at, 100, 100); d.delete_pages([at])
    assert page_labels(d.fitz) == before
    n = d.insert_from_file(simple_pdf, at); d.delete_pages(list(range(at, at + n)))
    assert page_labels(d.fitz) == before
