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

def test_close_twice_is_safe(simple_pdf):
    doc = PdfDocument.open(simple_pdf)
    doc.close()
    doc.close()
