"""削除して保存したページは、保存したファイルに何も残らないこと（要件 7 章: 個人情報の残り）。
名前付きの行き先・OpenAction・構造ツリー（タグ付き PDF）・注釈の /P から参照されていても、
平文でも暗号化された PDF でも。生きている文書は残すので、保存の後でも元に戻せること。"""
import binascii
import hashlib

import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument
from tests.helpers import labels

MARK = "SECRETMARK123"
FNAME = "secretfield"
FVAL = "FIELDSECRET456"
ALT = "ALTSECRET789"
CASES = ["plain", "named_dest", "old_dests", "open_action", "struct_tree", "annot_p", "all"]


def _make(path, case: str, encrypt: bool):
    """4 ページ。2 ページ目（index 1）が消すページで、本文・注釈・画像・フォーム部品に秘密の印。"""
    doc = pymupdf.open()
    for i in range(4):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    secret = doc[1]
    secret.insert_text((72, 120), MARK)
    cx = secret.get_contents()[0]
    doc.update_stream(cx, doc.xref_stream(cx) + f"\n% {MARK}\n".encode())
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 37, 41), 0)
    pix.set_rect(pix.irect, (200, 10, 30))
    secret.insert_image(pymupdf.Rect(100, 200, 200, 300), pixmap=pix)
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name, w.field_value, w.rect = FNAME, FVAL, pymupdf.Rect(72, 400, 300, 430)
    secret.add_widget(w)
    secret.add_text_annot((300, 300), MARK)
    keep = pymupdf.Widget(); keep.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    keep.field_name, keep.field_value, keep.rect = "keep", "keepval", pymupdf.Rect(72, 400, 300, 430)
    doc[0].add_widget(keep)
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 50),
                        "page": 2, "to": pymupdf.Point(0, 0)})   # 残るページへのリンク
    doc.set_toc([[1, "secret", 2], [1, "kept", 3]])
    cat, p2, p3 = doc.pdf_catalog(), doc[1].xref, doc[2].xref
    if case in ("named_dest", "all"):
        nd = doc.get_new_xref()
        doc.update_object(nd, f"<</Names[(gone)[{p2} 0 R/Fit](kept)[{p3} 0 R/Fit]]>>")
        doc.xref_set_key(cat, "Names", f"<</Dests {nd} 0 R>>")
    if case in ("old_dests", "all"):
        doc.xref_set_key(cat, "Dests", f"<</gone2[{p2} 0 R/Fit]/kept2[{p3} 0 R/XYZ 0 0 0]>>")
    if case in ("open_action", "all"):
        doc.xref_set_key(cat, "OpenAction", f"[{p2} 0 R/Fit]")
    if case in ("struct_tree", "all"):
        root, se_gone, se_kept, se_kid = (doc.get_new_xref() for _ in range(4))
        doc.update_object(se_kid, f"<</Type/StructElem/S/Span/P {se_gone} 0 R/K 1/ActualText({ALT})>>")
        doc.update_object(se_gone, f"<</Type/StructElem/S/Figure/P {root} 0 R/Pg {p2} 0 R"
                                   f"/K[0 {se_kid} 0 R]/Alt({ALT})>>")
        doc.update_object(se_kept, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {doc[0].xref} 0 R"
                                   f"/K[<</Type/MCR/Pg {p2} 0 R/MCID 3>> 0]/Alt(keep-alt)>>")
        doc.update_object(root, f"<</Type/StructTreeRoot/K[{se_kept} 0 R {se_gone} 0 R]"
                                f"/ParentTree<</Nums[0[{se_kept} 0 R]1[{se_gone} 0 R]]>>>>")
        doc.xref_set_key(cat, "StructTreeRoot", f"{root} 0 R")
        doc.xref_set_key(cat, "MarkInfo", "<</Marked true>>")
        doc.xref_set_key(doc[0].xref, "StructParents", "0")
        doc.xref_set_key(p2, "StructParents", "1")
    if case in ("annot_p", "all"):
        a = doc[0].add_text_annot((100, 100), "kept note")
        doc.xref_set_key(a.xref, "P", f"{p2} 0 R")  # 誤った /P が消すページを指す
    kw = {}
    if encrypt:
        kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.save(path, **kw)
    doc.close()
    return path


def _scan(path, password=None) -> dict:
    """ファイルの全オブジェクトと、展開した全ストリームを生のまま調べる。"""
    pymupdf.TOOLS.mupdf_warnings()  # ためていた警告を捨てる
    d = pymupdf.open(path)
    if d.needs_pass:
        assert d.authenticate(password)
    needles = [s.encode() for s in (MARK, FNAME, FVAL, ALT)]
    needles += [binascii.hexlify(n) for n in needles] + [binascii.hexlify(n).upper() for n in needles]
    hits, page_objs, images = [], 0, 0
    for x in range(1, d.xref_length()):
        obj = d.xref_object(x, compressed=True)
        data = obj.encode("utf-8", "replace")
        if d.xref_is_stream(x):
            data += d.xref_stream(x) or b""
        if d.xref_get_key(x, "Type") == ("name", "/Page"):
            page_objs += 1
        if "/Width 37" in obj and "/Height 41" in obj:
            images += 1
        hits += [(x, n) for n in needles if n in data]
    looks = {}
    for i, label in enumerate(_labels(d)):
        pix = d[i].get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), alpha=False)
        looks[label] = hashlib.md5(pix.samples).hexdigest()
    res = dict(page_count=d.page_count, page_objs=page_objs, images=images, hits=hits,
               looks=looks, toc=d.get_toc(), dests=d.resolve_names(),
               links=[l.get("page") for l in d[0].get_links()],
               fields=[(w.field_name, w.field_value) for p in d for w in p.widgets()])
    d.close()
    res["warnings"] = pymupdf.TOOLS.mupdf_warnings()
    return res


def _labels(d) -> list[int]:
    import re
    return [int(m.group(1)) if (m := re.search(r"Page (\d+)", p.get_text())) else 0 for p in d]


@pytest.mark.parametrize("encrypt", [False, True], ids=["plain", "encrypted"])
@pytest.mark.parametrize("case", CASES)
def test_deleted_page_leaves_nothing_in_saved_file(tmp_path, case, encrypt):
    pw = "user" if encrypt else None
    src = _make(tmp_path / "src.pdf", case, encrypt)
    original = _scan(src, pw)
    assert original["hits"] and original["images"] == 1  # 印は元のファイルにある

    d = PdfDocument.open(src, pw)
    detached = d.delete_pages([1])
    out = tmp_path / "out.pdf"; d.save(out)
    r = _scan(out, pw)
    assert r["page_count"] == r["page_objs"] == 3
    assert r["hits"] == [] and r["images"] == 0
    assert r["warnings"] == ""
    assert r["looks"] == {k: original["looks"][k] for k in (1, 3, 4)}  # 残るページの見た目は同じ
    assert r["fields"] == [("keep", "keepval")]
    assert r["toc"][-1][1:] == ["kept", 2] and r["links"] == [1]  # 残るページへの行き先は生きている
    if case in ("named_dest", "all"):
        assert r["dests"]["kept"]["page"] == 1 and "gone" not in r["dests"]
    if case in ("old_dests", "all"):
        assert r["dests"]["kept2"]["page"] == 1
    if encrypt:
        assert pymupdf.open(out).needs_pass

    # 保存の後でも元に戻せる。戻して保存すれば、ページはそっくり戻る
    d.restore_pages(detached)
    out2 = tmp_path / "out2.pdf"; d.save(out2)
    back = _scan(out2, pw)
    assert back["looks"] == original["looks"] and back["fields"] == original["fields"]
    assert back["page_objs"] == 4 and back["images"] == 1 and back["toc"] == original["toc"]
    assert back["warnings"] == ""
    if case in ("named_dest", "all"):
        assert back["dests"]["gone"]["page"] == 1
    d.close()


def test_page_deleted_in_an_earlier_session_needs_nothing(tmp_path):
    src = _make(tmp_path / "src.pdf", "all", False)
    d = PdfDocument.open(src); d.delete_pages([1]); first = tmp_path / "first.pdf"; d.save(first)
    d.close()
    again = PdfDocument.open(first); again.rotate_by([0], 90)
    out = tmp_path / "out.pdf"; again.save(out)
    r = _scan(out)
    assert r["page_objs"] == 3 and r["hits"] == [] and r["warnings"] == ""


def test_inserted_then_deleted_pages_leave_nothing(tmp_path, simple_pdf):
    secret = _make(tmp_path / "secret.pdf", "plain", False)
    d = PdfDocument.open(simple_pdf)
    d.insert_from_file(secret, 1)
    d.delete_pages([1, 2, 3, 4])
    assert labels(d) == [1, 2, 3]
    out = tmp_path / "out.pdf"; d.save(out)
    r = _scan(out)
    assert r["page_objs"] == 3 and r["hits"] == [] and r["images"] == 0 and r["warnings"] == ""


def test_undo_after_save_then_redo_save_again(tmp_path):
    src = _make(tmp_path / "src.pdf", "all", False)
    d = PdfDocument.open(src)
    det = d.delete_pages([1]); d.save(tmp_path / "a.pdf")
    d.restore_pages(det); d.save(tmp_path / "b.pdf")
    d.delete_pages([1]); d.save(tmp_path / "c.pdf")
    r = _scan(tmp_path / "c.pdf")
    assert r["page_objs"] == 3 and r["hits"] == [] and r["warnings"] == ""
