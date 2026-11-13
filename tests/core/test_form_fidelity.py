"""フォーム（AcroForm）と暗号化が、ページ操作・元に戻す・保存のあとも元のまま残ること。
メモリ上の状態だけでなく、保存して開き直したファイルで確かめる。"""
import pymupdf
import pytest

from pdfeditor.core import errors
from pdfeditor.core.document import PdfDocument
from tests.helpers import form_tree_problems, labels, page_fields, snapshot


def _reopen(doc: PdfDocument, tmp_path, name="out.pdf", password=None) -> PdfDocument:
    out = tmp_path / name
    doc.save(out)
    return PdfDocument.open(out, password)


def _form_ok(doc: PdfDocument) -> None:
    assert doc.fitz.is_form_pdf == 4
    assert form_tree_problems(doc.fitz) == []


def _page_objects(fitz_doc) -> int:
    """ファイル内の /Type/Page オブジェクトの数（削除したページが残っていないかを見る）。"""
    return sum(
        1 for x in range(1, fitz_doc.xref_length())
        if fitz_doc.xref_get_key(x, "Type") == ("name", "/Page")
    )


# ---- C1: 並べ替えでフォームが消えない ----

@pytest.mark.parametrize("op", [
    lambda d: d.move_pages([4], 0),
    lambda d: d.move_pages([0, 2], 5),
    lambda d: d.reorder([4, 3, 2, 1, 0]),
    lambda d: d.reorder([0, 1, 2, 3, 4]),
])
def test_reordering_keeps_form_fields_and_values(form_pdf, tmp_path, op):
    d = PdfDocument.open(form_pdf)
    before = dict(zip(labels(d), page_fields(d.fitz)))
    op(d)
    after = dict(zip(labels(d), page_fields(d.fitz)))
    assert after == before
    _form_ok(d)
    r = _reopen(d, tmp_path)
    assert dict(zip(labels(r), page_fields(r.fitz))) == before
    _form_ok(r)


def test_move_then_move_back_restores_form_exactly(form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf); before = snapshot(d)
    d.move_pages([4], 0); d.reorder([1, 2, 3, 4, 0])
    assert snapshot(d) == before
    assert snapshot(_reopen(d, tmp_path)) == before


# ---- I1: 削除して元に戻すと、フォームが元のまま戻る ----

@pytest.mark.parametrize("indices", [[0], [2], [4], [2, 4], [0, 1, 3], [1, 2, 3, 4]])
def test_delete_restore_is_faithful(form_pdf, tmp_path, indices):
    d = PdfDocument.open(form_pdf); before = snapshot(d)
    deleted = d.delete_pages(indices)
    assert form_tree_problems(d.fitz) == []  # 削除後も孤立した部品が残らない
    d.restore_pages(deleted)
    assert snapshot(d) == before and form_tree_problems(d.fitz) == []
    r = _reopen(d, tmp_path)
    assert snapshot(r) == before and form_tree_problems(r.fitz) == []


def test_delete_restore_cycles_are_stable(form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf); before = snapshot(d)
    for _ in range(3):
        deleted = d.delete_pages([0, 2])
        d.restore_pages(deleted)
    assert snapshot(d) == before and form_tree_problems(d.fitz) == []
    assert snapshot(_reopen(d, tmp_path)) == before


def test_save_between_delete_and_restore(form_pdf, tmp_path):
    """保存（不要オブジェクトの掃除を含む）をはさんでも、削除を元に戻せる。"""
    d = PdfDocument.open(form_pdf); before = snapshot(d)
    deleted = d.delete_pages([0, 4])
    mid = _reopen(d, tmp_path, "mid.pdf")
    assert labels(mid) == [2, 3, 4] and form_tree_problems(mid.fitz) == []
    assert _page_objects(mid.fitz) == 3  # 削除したページはファイルに残らない
    names = {f[0] for page in page_fields(mid.fitz) for f in page}
    assert "name" not in names
    d.restore_pages(deleted)
    assert snapshot(d) == before
    assert snapshot(_reopen(d, tmp_path, "after.pdf")) == before


def test_delete_after_move_keeps_fields(form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf)
    d.move_pages([4], 0); before = snapshot(d)
    deleted = d.delete_pages([1])  # 元の 1 ページ目（"name"）
    d.restore_pages(deleted)
    assert snapshot(d) == before
    assert snapshot(_reopen(d, tmp_path)) == before


def test_restore_keeps_annotations_and_links_of_deleted_page(form_pdf):
    d = PdfDocument.open(form_pdf)
    annots = [a.info["content"] for a in d.fitz[0].annots()]
    links = [(l["kind"], l.get("uri")) for l in d.fitz[0].get_links()]
    d.restore_pages(d.delete_pages([0]))
    assert [a.info["content"] for a in d.fitz[0].annots()] == annots == ["注釈"]
    assert [(l["kind"], l.get("uri")) for l in d.fitz[0].get_links()] == links


def test_deleted_pages_are_not_in_saved_file(form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf); d.delete_pages([1, 3])
    r = _reopen(d, tmp_path)
    assert labels(r) == [1, 3, 5] and _page_objects(r.fitz) == 3
    assert r.fitz.is_form_pdf == 2 and form_tree_problems(r.fitz) == []


# ---- C2: 保存してもパスワードと編集制限が残る ----

def test_user_password_survives_save(user_pw_pdf, tmp_path):
    d = PdfDocument.open(user_pw_pdf, "user"); d.rotate_by([0], 90); d.save()
    with pytest.raises(errors.PasswordRequiredError):
        PdfDocument.open(user_pw_pdf)
    r = PdfDocument.open(user_pw_pdf, "user")
    assert r.page_rotation(0) == 90
    raw = pymupdf.open(user_pw_pdf)
    assert raw.needs_pass and raw.authenticate("owner") & 4  # 権限パスワードも元のまま


def test_user_password_opened_with_owner_survives_save_as(user_pw_pdf, tmp_path):
    d = PdfDocument.open(user_pw_pdf, "owner"); d.rotate_by([0], 90)
    out = tmp_path / "out.pdf"; d.save(out)
    with pytest.raises(errors.PasswordRequiredError):
        PdfDocument.open(out)
    assert PdfDocument.open(out, "user").page_rotation(0) == 90


def test_edit_restriction_survives_save(owner_pw_pdf, tmp_path):
    original = pymupdf.open(owner_pw_pdf).permissions
    d = PdfDocument.open(owner_pw_pdf, "owner"); d.rotate_by([0], 90)
    out = tmp_path / "out.pdf"; d.save(out)
    with pytest.raises(errors.OwnerPasswordRequiredError):
        PdfDocument.open(out)
    raw = pymupdf.open(out)
    assert raw.permissions == original and not raw.permissions & pymupdf.PDF_PERM_MODIFY
    assert PdfDocument.open(out, "owner").page_rotation(0) == 90


# ---- I5: 署名の印 ----

def test_sigflags_1_counts_as_signed(sigflags_1_pdf, signed_flag_pdf, simple_pdf):
    assert PdfDocument.open(sigflags_1_pdf).is_signed is True
    assert PdfDocument.open(signed_flag_pdf).is_signed is True
    assert PdfDocument.open(simple_pdf).is_signed is False
