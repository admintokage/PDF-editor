import pytest

from pdfeditor.core import errors
from pdfeditor.core.document import PdfDocument
from tests.helpers import lock_file


def test_save_as_roundtrip(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf); out = tmp_path / "out.pdf"; d.save(out)
    assert PdfDocument.open(out).page_count == 3 and d.path == out


def test_first_overwrite_creates_backup_once(simple_pdf):
    original = simple_pdf.read_bytes()
    d = PdfDocument.open(simple_pdf); d.save(); bak = simple_pdf.with_name(simple_pdf.stem + ".bak.pdf")
    assert bak.read_bytes() == original
    bak.write_bytes(b"marker"); d.save(); assert bak.read_bytes() == b"marker"


def test_save_as_makes_no_backup(simple_pdf, tmp_path):
    PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert not list(tmp_path.glob("*.bak.pdf"))


def test_locked_target_raises_and_keeps_original(simple_pdf):
    original = simple_pdf.read_bytes(); d = PdfDocument.open(simple_pdf)
    with lock_file(simple_pdf):
        with pytest.raises(errors.SaveFailedError) as e: d.save()
    assert e.value.reason == "locked" and "他のソフト" in e.value.user_message
    assert simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_verify_failure_keeps_original(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()
    monkeypatch.setattr(PdfDocument, "to_bytes", lambda self: b"not a pdf")
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save()
    assert e.value.reason == "verify_failed" and simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_readonly_folder_reports_permission(simple_pdf, tmp_path, monkeypatch):
    def deny(*a, **k): raise PermissionError(13, "denied")
    monkeypatch.setattr("pathlib.Path.write_bytes", deny)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "permission"


def test_disk_full_reported(simple_pdf, tmp_path, monkeypatch):
    def full(*a, **k): raise OSError(28, "No space left")
    monkeypatch.setattr("pathlib.Path.write_bytes", full)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "disk_full"


def test_toc_links_annots_widgets_survive(toc_links_pdf, tmp_path):
    import pymupdf
    out = tmp_path / "o.pdf"; PdfDocument.open(toc_links_pdf).save(out); f = pymupdf.open(out)
    assert len(f.get_toc()) == 3 and len(f[1].get_links()) == 1
    assert len(list(f[2].annots())) == 1 and next(f[0].widgets()).field_value == "入力値"


def test_backup_failure_keeps_original_and_cleans_tmp(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()

    def boom(*a, **k): raise PermissionError(13, "denied")
    monkeypatch.setattr("shutil.copy2", boom)
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.SaveFailedError) as e: d.save()
    assert e.value.reason == "locked" and simple_pdf.read_bytes() == original
    assert not d.backup_done
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_save_as_then_overwrite_new_path_backs_up_once(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf); out = tmp_path / "out.pdf"
    d.save(out); assert not d.backup_done
    d.save(); assert d.backup_done and (tmp_path / "out.bak.pdf").exists()
