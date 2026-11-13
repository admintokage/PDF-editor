"""性能テスト。pytest -m perf で実行する。"""
import time
from pathlib import Path

import psutil
import pymupdf
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
def test_500_pages_save_with_100_deleted_under_10s(big_pdf, tmp_path):
    """削除したページの掃除（scrub）を含む保存。"""
    d = PdfDocument.open(big_pdf(500))
    d.delete_pages(list(range(0, 500, 5)))
    t = time.perf_counter()
    d.save(tmp_path / "o.pdf")
    elapsed = time.perf_counter() - t
    d.close()
    print(f"500 pages save with 100 deleted: {elapsed:.3f}s")
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


@pytest.fixture(scope="session")
def shared_500(tmp_path_factory) -> Path:
    from tests.helpers import make_shared_big
    return make_shared_big(tmp_path_factory.mktemp("shared") / "shared500.pdf")


@pytest.mark.perf
def test_500_pages_sharing_resources_save_with_100_deleted_under_10s(shared_500, tmp_path):
    """共有の Resources から削除ページの画像を外し、残る 400 ページを描き比べる保存。"""
    d = PdfDocument.open(shared_500)
    d.delete_pages(list(range(0, 500, 5)))
    t = time.perf_counter()
    d.save(tmp_path / "o.pdf")
    elapsed = time.perf_counter() - t
    d.close()
    r = pymupdf.open(tmp_path / "o.pdf")
    assert r.page_count == 400 and len(r[0].get_images()) == 400  # 削除した 100 枚は外れた
    r.close()
    print(f"500 pages sharing Resources (~20MB), 100 deleted, save: {elapsed:.3f}s")
    assert elapsed < 10.0


@pytest.mark.perf
def test_export_from_500_pages_sharing_resources(shared_500):
    d = PdfDocument.open(shared_500)
    times = []
    for indices in ([7], list(range(0, 500, 2))):
        t = time.perf_counter()
        data = d.export_pages(indices)
        times.append(time.perf_counter() - t)
        out = pymupdf.open(stream=data, filetype="pdf")
        assert len(out[0].get_images()) == len(indices)  # 選んだページの画像だけ
        out.close()
    d.close()
    print(f"export 1 page: {times[0]:.3f}s, 250 pages: {times[1]:.3f}s")
    assert times[0] < 1.0 and times[1] < 10.0


@pytest.mark.perf
def test_long_article_thread_is_pruned_in_linear_time():
    """5000 ビーズのスレッド（1000 ページに 5 つずつ）から、5 ページごとに 1 ページを外す。"""
    from pdfeditor.core import scrub

    doc = pymupdf.open()
    for _ in range(1000):
        doc.new_page()
    pages = [doc[i].xref for i in range(1000)]
    thread = doc.get_new_xref()
    beads = [doc.get_new_xref() for _ in range(5000)]
    for i, b in enumerate(beads):
        t = f"/T {thread} 0 R" if i == 0 else ""
        doc.update_object(b, f"<</Type/Bead{t}/N {beads[(i + 1) % 5000]} 0 R/V {beads[i - 1]} 0 R"
                             f"/P {pages[i // 5]} 0 R/R[0 0 10 10]>>")
    doc.update_object(thread, f"<</Type/Thread/F {beads[0]} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "Threads", f"[{thread} 0 R]")
    gone = {pages[i] for i in range(0, 1000, 5)}
    doc.delete_pages(list(range(0, 1000, 5)))
    s = scrub._Scrub(doc, gone, set())
    t = time.perf_counter()
    s.prune_threads()
    elapsed = time.perf_counter() - t
    print(f"5000-bead thread, 200 of 1000 pages removed: {elapsed:.3f}s")
    assert len(s.removed - gone) == 1000 and elapsed < 0.5
