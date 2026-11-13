"""性能テスト。pytest -m perf で実行する。"""
import time

import psutil
import pytest

from pdfeditor.core.document import PdfDocument
from tests.helpers import make_view


@pytest.mark.perf
def test_big_pdf_is_about_20mb(big_pdf_20mb):
    assert 15 * 1024 * 1024 < big_pdf_20mb.stat().st_size < 30 * 1024 * 1024


@pytest.mark.perf
def test_first_page_of_100_pages_20mb_under_2s(big_pdf_20mb):
    t = time.perf_counter()
    d = PdfDocument.open(big_pdf_20mb)
    d.render(0, 1.5)
    elapsed = time.perf_counter() - t
    d.close()
    print(f"first page of 100 pages 20MB: {elapsed:.3f}s")
    assert elapsed < 2.0


@pytest.mark.perf
def test_500_pages_open_and_reorder(big_pdf):
    p = big_pdf(500)
    t = time.perf_counter()
    d = PdfDocument.open(p)
    d.move_pages([499], 0)
    d.render(0, 1.0)
    elapsed = time.perf_counter() - t
    d.close()
    print(f"500 pages open+reorder+render: {elapsed:.3f}s")
    assert elapsed < 3.0


@pytest.mark.perf
def test_500_pages_save_under_10s(big_pdf, tmp_path):
    d = PdfDocument.open(big_pdf(500))
    t = time.perf_counter()
    d.save(tmp_path / "o.pdf")
    elapsed = time.perf_counter() - t
    d.close()
    print(f"500 pages save: {elapsed:.3f}s")
    assert elapsed < 10.0


@pytest.mark.perf
def test_memory_stable_while_scrolling_500_pages(qtbot, big_pdf):
    proc = psutil.Process()
    v = make_view(qtbot, big_pdf(500))
    for i in range(0, 500, 5):
        v.scroll_to_page(i)
        qtbot.wait(20)
    mid = proc.memory_info().rss
    for i in range(499, 0, -5):
        v.scroll_to_page(i)
        qtbot.wait(20)
    growth = proc.memory_info().rss - mid
    print(f"rss growth on second pass: {growth / 1024 / 1024:.1f}MB (mid {mid / 1024 / 1024:.0f}MB)")
    assert growth < 100 * 1024 * 1024
