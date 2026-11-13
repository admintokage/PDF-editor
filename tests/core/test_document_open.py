import pytest
from pdfeditor.core.document import PdfDocument
from pdfeditor.core import errors

def test_open_reads_page_count(simple_pdf):
    assert PdfDocument.open(simple_pdf).page_count == 3

def test_open_does_not_lock_file(simple_pdf):
    doc = PdfDocument.open(simple_pdf)
    simple_pdf.rename(simple_pdf.with_name("renamed.pdf"))  # ロックされていれば PermissionError

def test_open_japanese_path(simple_pdf, tmp_path):
    p = tmp_path / "日本語 フォルダ" / "書類 １.pdf"; p.parent.mkdir(); p.write_bytes(simple_pdf.read_bytes())
    assert PdfDocument.open(p).page_count == 3

@pytest.mark.parametrize("content", [b"", b"hello", b"%PDF-1.7\n1 0 obj\n<<"])
def test_open_rejects_non_pdf(tmp_path, content):
    p = tmp_path / "x.pdf"; p.write_bytes(content)
    with pytest.raises(errors.NotAPdfError) as e:
        PdfDocument.open(p)
    assert "開けません" in e.value.user_message

def test_user_password_flow(user_pw_pdf):
    with pytest.raises(errors.PasswordRequiredError): PdfDocument.open(user_pw_pdf)
    with pytest.raises(errors.WrongPasswordError): PdfDocument.open(user_pw_pdf, "bad")
    assert PdfDocument.open(user_pw_pdf, "user").page_count >= 1

def test_owner_only_restriction_requires_owner_password(owner_pw_pdf):
    with pytest.raises(errors.OwnerPasswordRequiredError): PdfDocument.open(owner_pw_pdf)
    assert PdfDocument.open(owner_pw_pdf, "owner").page_count >= 1

def test_is_signed(signed_flag_pdf, simple_pdf):
    assert PdfDocument.open(signed_flag_pdf).is_signed is True
    assert PdfDocument.open(simple_pdf).is_signed is False

def _bad_count_pdf(tmp_path):
    import pymupdf
    doc = pymupdf.open(); [doc.new_page() for _ in range(3)]; raw = doc.tobytes(); doc.close()
    p = tmp_path / "badcount.pdf"; p.write_bytes(raw.replace(b"/Count 3", b"/Count 9"))
    return p


def test_wrong_page_tree_count_is_reported_as_not_a_pdf(tmp_path):
    """ページツリーの /Count が壊れた PDF で PyMuPDF が RuntimeError を投げても、日本語で知らせる。"""
    with pytest.raises(errors.NotAPdfError) as e:
        PdfDocument.open(_bad_count_pdf(tmp_path))
    assert "「badcount.pdf」を開けません" in e.value.user_message


def test_memory_error_while_checking_is_reported_as_not_a_pdf(simple_pdf, monkeypatch):
    import pymupdf
    closed = []
    real_close = pymupdf.Document.close

    def boom(self, *a, **k): raise MemoryError()
    monkeypatch.setattr(pymupdf.Document, "authenticate", boom)
    monkeypatch.setattr(pymupdf.Document, "close",
                        lambda self: (closed.append(1), real_close(self))[1])
    with pytest.raises(errors.NotAPdfError):
        PdfDocument.open(simple_pdf, "x")
    assert closed  # 開きかけの文書は閉じた


def test_insert_broken_pdf_is_not_a_pdf(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.NotAPdfError): d.insert_from_file(_bad_count_pdf(tmp_path), 0)
    assert d.page_count == 3


def test_close_twice_is_safe(simple_pdf):
    doc = PdfDocument.open(simple_pdf)
    doc.close()
    doc.close()
