import pymupdf
import pytest

from pdfeditor.core import errors
from pdfeditor.core.document import PdfDocument
from pdfeditor.ui import messages
from pdfeditor.ui.page_commands import (
    DeletePagesCommand, InsertBlankCommand, InsertPdfCommand, MovePagesCommand,
    RotatePagesCommand, make_undo_stack, push_command,
)
from tests.helpers import form_tree_problems, labels, snapshot


@pytest.mark.parametrize("make", [
    lambda d: MovePagesCommand(d, [0, 2], 5),
    lambda d: DeletePagesCommand(d, [1, 3]),
    lambda d: InsertBlankCommand(d, 2, 595, 842),
    lambda d: RotatePagesCommand(d, [0, 4], 90),
])
def test_undo_restores_and_redo_reapplies(qtbot, big_pdf, make):
    d = PdfDocument.open(big_pdf(5)); before = snapshot(d)
    stack = make_undo_stack(None); stack.push(make(d)); after = snapshot(d); assert after != before
    stack.undo(); assert snapshot(d) == before
    stack.redo(); assert snapshot(d) == after


def test_insert_pdf_undo(qtbot, big_pdf, simple_pdf):
    d = PdfDocument.open(big_pdf(5)); stack = make_undo_stack(None)
    stack.push(InsertPdfCommand(d, simple_pdf.read_bytes(), 2, None)); assert d.page_count == 8
    stack.undo(); assert labels(d) == [1, 2, 3, 4, 5]
    stack.redo(); assert d.page_count == 8


def test_delete_undo_restores_toc(qtbot, toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); toc = d.fitz.get_toc(); stack = make_undo_stack(None)
    stack.push(DeletePagesCommand(d, [0])); stack.undo(); assert d.fitz.get_toc() == toc


def test_delete_noncontiguous_restores_positions_and_toc(qtbot, toc_links_pdf):
    d = PdfDocument.open(toc_links_pdf); toc = d.fitz.get_toc(); stack = make_undo_stack(None)
    stack.push(DeletePagesCommand(d, [3, 0, 2])); assert labels(d) == [2, 5]
    stack.undo(); assert labels(d) == [1, 2, 3, 4, 5]; assert d.fitz.get_toc() == toc
    stack.redo(); assert labels(d) == [2, 5]


def test_rotate_undo_restores_differing_rotations(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(4)); d.set_rotation([1], 90); d.set_rotation([2], 180)
    before = snapshot(d); stack = make_undo_stack(None)
    stack.push(RotatePagesCommand(d, [0, 1, 2], 90))
    assert [d.page_rotation(i) for i in range(3)] == [90, 180, 270]
    stack.undo(); assert snapshot(d) == before


def test_move_undo_nonadjacent_down(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(6)); stack = make_undo_stack(None)
    stack.push(MovePagesCommand(d, [4, 1], 0)); assert labels(d) == [2, 5, 1, 3, 4, 6]
    stack.undo(); assert labels(d) == [1, 2, 3, 4, 5, 6]


def test_doc_changed_called_on_redo_and_undo(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(3)); calls = []; stack = make_undo_stack(None)
    stack.push(RotatePagesCommand(d, [0], 90, lambda: calls.append(1))); assert len(calls) == 1
    stack.undo(); assert len(calls) == 2


def test_push_command_failure_leaves_nothing(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(3)); before = snapshot(d); stack = make_undo_stack(None)
    with pytest.raises(errors.PageOperationError):
        push_command(stack, DeletePagesCommand(d, [0, 1, 2]))
    assert stack.count() == 0 and snapshot(d) == before


def test_push_command_success_applies_once_and_undoes(qtbot, big_pdf):
    d = PdfDocument.open(big_pdf(3)); calls = []; stack = make_undo_stack(None)
    push_command(stack, RotatePagesCommand(d, [0], 90, lambda: calls.append(1)))
    assert stack.count() == 1 and d.page_rotation(0) == 90 and len(calls) == 1
    stack.undo(); assert d.page_rotation(0) == 0
    stack.redo(); assert d.page_rotation(0) == 90


def test_failed_direct_push_is_reported_and_history_cleared(qtbot, big_pdf, monkeypatch):
    """直接 push して redo が失敗しても、例外は外に漏れず、知らせて履歴を消す。
    （アプリは push_command を使うので、失敗した操作はそもそも積まれない。）"""
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    d = PdfDocument.open(big_pdf(3)); stack = make_undo_stack(None)
    stack.push(DeletePagesCommand(d, [0, 1, 2]))
    qtbot.waitUntil(lambda: bool(shown))
    assert isinstance(shown[0], errors.PageOperationError) and "最後の 1 ページ" in shown[0].user_message
    assert stack.count() == 0 and d.page_count == 3


def test_exception_in_undo_is_reported_and_history_cleared(qtbot, big_pdf, monkeypatch):
    """元に戻すの途中の例外を PySide に握りつぶさせない: 知らせて、履歴を消し、未保存のままにする。"""
    shown, changed = [], []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    d = PdfDocument.open(big_pdf(3)); stack = make_undo_stack(None)
    push_command(stack, RotatePagesCommand(d, [0], 90, lambda: changed.append(1)))
    push_command(stack, RotatePagesCommand(d, [1], 90, lambda: changed.append(1)))
    stack.setClean()
    cmd = stack.command(1)
    monkeypatch.setattr(cmd, "_revert", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    stack.undo()
    qtbot.waitUntil(lambda: bool(shown))
    assert shown[0].user_message == messages.UNDO_FAILED
    assert stack.count() == 0 and not stack.canUndo() and not stack.isClean()
    assert len(changed) == 3  # 失敗しても表示を読み直させる
    assert d.page_rotation(1) == 90  # 文書は失敗した時のまま（勝手に変えない）


def test_exception_in_redo_is_reported(qtbot, big_pdf, monkeypatch):
    shown = []
    monkeypatch.setattr("pdfeditor.ui.dialogs.show_error", lambda p, e: shown.append(e))
    d = PdfDocument.open(big_pdf(3)); stack = make_undo_stack(None)
    push_command(stack, RotatePagesCommand(d, [0], 90)); stack.undo()
    cmd = stack.command(0)
    monkeypatch.setattr(cmd, "_apply", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    stack.redo()
    qtbot.waitUntil(lambda: bool(shown))
    assert stack.count() == 0 and d.page_rotation(0) == 0


def test_undo_limit_is_50(qtbot): assert make_undo_stack(None).undoLimit() == 50


def test_clean_state_tracks_save(qtbot, simple_pdf):
    d = PdfDocument.open(simple_pdf); s = make_undo_stack(None); s.push(RotatePagesCommand(d, [0], 90))
    assert not s.isClean(); s.setClean(); assert s.isClean(); s.undo(); assert not s.isClean()


@pytest.mark.parametrize("make", [
    lambda d: MovePagesCommand(d, [4], 0),
    lambda d: MovePagesCommand(d, [0, 2], 5),
    lambda d: DeletePagesCommand(d, [0]),
    lambda d: DeletePagesCommand(d, [2, 4]),
    lambda d: DeletePagesCommand(d, [0, 1, 3]),
])
def test_form_survives_undo_redo_cycles_and_save(qtbot, form_pdf, tmp_path, make):
    """フォームの部品名・種類・値と AcroForm が、元に戻す/やり直し ×3 と保存のあとも元のまま。"""
    d = PdfDocument.open(form_pdf); before = snapshot(d); stack = make_undo_stack(None)
    push_command(stack, make(d)); after = snapshot(d)
    assert form_tree_problems(d.fitz) == []
    for _ in range(3):
        stack.undo(); assert snapshot(d) == before and form_tree_problems(d.fitz) == []
        stack.redo(); assert snapshot(d) == after and form_tree_problems(d.fitz) == []
    stack.undo()
    out = tmp_path / "o.pdf"; d.save(out); r = PdfDocument.open(out)
    assert snapshot(r) == before and form_tree_problems(r.fitz) == []


def test_move_then_delete_then_undo_all_keeps_form(qtbot, form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf); before = snapshot(d); stack = make_undo_stack(None)
    push_command(stack, MovePagesCommand(d, [4, 1], 0))
    push_command(stack, DeletePagesCommand(d, [0, 2]))
    d.save(tmp_path / "mid.pdf")  # 途中で保存しても元に戻せる
    while stack.canUndo():
        stack.undo()
    assert snapshot(d) == before
    out = tmp_path / "o.pdf"; d.save(out)
    assert snapshot(PdfDocument.open(out)) == before


def test_insert_pdf_undo_leaves_no_orphan_fields(qtbot, form_pdf, tmp_path):
    d = PdfDocument.open(form_pdf); before = snapshot(d); stack = make_undo_stack(None)
    push_command(stack, InsertPdfCommand(d, form_pdf.read_bytes(), 2, None))
    stack.undo(); assert snapshot(d) == before
    out = tmp_path / "o.pdf"; d.save(out); r = PdfDocument.open(out)
    assert snapshot(r) == before and form_tree_problems(r.fitz) == []


def _links(d):
    """各ページのリンクを (種類, 矩形, 行き先ページ) の並びで返す。"""
    out = []
    for i in range(d.page_count):
        out.append(sorted(
            (l["kind"], tuple(round(v, 2) for v in l["from"]), l.get("page", -1))
            for l in d.fitz[i].get_links()
        ))
    return out


# toc_links_pdf: ページ 2（index 1）にページ 4（index 3）へのリンクが 1 つ
@pytest.mark.parametrize("indices", [[3], [1], [1, 3], [0, 3], [3, 1, 4]])
def test_delete_undo_redo_undo_preserves_links(qtbot, toc_links_pdf, indices):
    d = PdfDocument.open(toc_links_pdf); stack = make_undo_stack(None)
    before_links, before = _links(d), snapshot(d)
    assert before_links[1] == [(pymupdf.LINK_GOTO, (72, 150, 200, 180), 3)]
    stack.push(DeletePagesCommand(d, indices)); deleted = snapshot(d)
    stack.undo(); assert _links(d) == before_links and snapshot(d) == before
    stack.redo(); assert snapshot(d) == deleted
    stack.undo(); assert _links(d) == before_links and snapshot(d) == before


@pytest.mark.parametrize("indices", [[3], [0], [3, 0], [4, 2]])
def test_delete_undo_preserves_links_from_deleted_pages_and_uri(qtbot, toc_links_pdf, indices):
    d = PdfDocument.open(toc_links_pdf)
    d.fitz[3].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 60, 40),
                           "page": 0, "to": pymupdf.Point(0, 0)})   # 削除ページ -> 残るページ
    d.fitz[4].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 50, 90, 70),
                           "uri": "https://example.com/"})
    d.fitz[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 80, 90, 100),
                           "page": 2, "to": pymupdf.Point(0, 0)})
    stack = make_undo_stack(None); before = _links(d)
    stack.push(DeletePagesCommand(d, indices)); stack.undo()
    assert _links(d) == before
    stack.redo(); stack.undo(); assert _links(d) == before
    assert [l.get("uri") for l in d.fitz[4].get_links()] == ["https://example.com/"]
