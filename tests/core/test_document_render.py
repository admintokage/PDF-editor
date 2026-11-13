import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument


def test_page_size_reflects_rotation(mixed_size_pdf):
    d = PdfDocument.open(mixed_size_pdf)
    w, h = d.page_size(0); assert (round(w), round(h)) == (595, 842)
    w, h = d.page_size(1); assert (round(w), round(h)) == (842, 595)


def test_render_dimensions_follow_zoom(simple_pdf):
    d = PdfDocument.open(simple_pdf)
    r = d.render(0, 2.0)
    assert (r.width, r.height) == (1190, 1684) and len(r.samples) == r.stride * r.height


def test_render_out_of_range_raises(simple_pdf):
    with pytest.raises(IndexError): PdfDocument.open(simple_pdf).render(3, 1.0)


def test_render_is_not_blank(simple_pdf):
    r = PdfDocument.open(simple_pdf).render(0, 1.0)
    assert len(set(r.samples[::997])) > 1


@pytest.mark.parametrize("index", [-1, 3])
def test_page_info_out_of_range_raises(simple_pdf, index):
    d = PdfDocument.open(simple_pdf)
    for call in (d.page_size, d.page_rotation, lambda i: d.render(i, 1.0)):
        with pytest.raises(IndexError):
            call(index)


def test_rotated_page_reports_swapped_size_and_renders_swapped(tmp_path):
    src = pymupdf.open()
    src.new_page(width=595, height=842).set_rotation(90)
    path = tmp_path / "rot.pdf"
    src.save(path)
    src.close()
    d = PdfDocument.open(path)
    assert d.page_rotation(0) == 90
    w, h = d.page_size(0)
    assert (round(w), round(h)) == (842, 595)
    r = d.render(0, 1.0)
    assert (r.width, r.height) == (842, 595)


def test_unrotated_page_rotation_is_zero(simple_pdf):
    assert PdfDocument.open(simple_pdf).page_rotation(0) == 0
