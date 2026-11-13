"""削除したページの掃除（scrub）の細部。要件 7 章（個人情報の残り）と、残すものを変えないこと。

- R1: 残す文字列に「<削除したページの番号> 0 R」という文字が入っていても、保存でき、文字列は変わらない
- R2: 削除したページにだけ属するもの（構造要素・注釈・しおり・レイヤー・記事のビーズ）は残らない
- R3: 削除したページを /Pg に持つ上位の構造要素でも、残るページの子がいれば残る
- R4: アプリが外していないページ（テンプレートなど、ページツリーの外のページ）は消さない
- R5: 掃除やその確認での失敗は、日本語の SaveFailedError になる（元のファイルはそのまま）
- R6: 保存の後で元に戻せば、リンク・しおり・名前付きの行き先・フォームが戻る
ファイルの中身は、全オブジェクトと展開した全ストリームを生のまま（平文・16 進）調べる。"""
import binascii
import hashlib
import logging
import re

import pymupdf
import pytest
from pymupdf import mupdf

from pdfeditor.core import errors, scrub
from pdfeditor.core.document import PdfDocument

ENCRYPT = pytest.mark.parametrize("encrypt", [False, True], ids=["plain", "encrypted"])
PW = "user"


def _base(n: int = 4) -> pymupdf.Document:
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    return doc


def _obj(doc, text: str) -> int:
    x = doc.get_new_xref()
    doc.update_object(x, text)
    return x


def _save(doc, path, encrypt: bool):
    kw = {}
    if encrypt:
        kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=PW, owner_pw="owner")
    doc.save(path, **kw)
    doc.close()
    return path


def _open(path, encrypt: bool) -> pymupdf.Document:
    d = pymupdf.open(path)
    if d.needs_pass:
        assert d.authenticate(PW)
    return d


def _forms(text: str) -> list[bytes]:
    """文字列の、ファイルの中に現れうる形: そのまま・16 進（小文字・大文字）・UTF-16BE の 16 進。"""
    raw = text.encode()
    utf16 = text.encode("utf-16-be")
    return [raw, binascii.hexlify(raw), binascii.hexlify(raw).upper(),
            binascii.hexlify(utf16), binascii.hexlify(utf16).upper()]


def raw_hits(path, encrypt: bool, needles: list[str]) -> list:
    """全オブジェクトの文字表現と、展開した全ストリームに needles が残っていれば (xref, 印) の列。"""
    d = _open(path, encrypt)
    hits = []
    for x in range(1, d.xref_length()):
        data = d.xref_object(x, compressed=True).encode("utf-8", "replace")
        if d.xref_is_stream(x):
            data += d.xref_stream(x) or b""
        hits += [(x, n) for n in needles for form in _forms(n) if form in data]
    d.close()
    return hits


def looks(path, encrypt: bool) -> list[str]:
    d = _open(path, encrypt)
    out = [hashlib.md5(p.get_pixmap(matrix=pymupdf.Matrix(.4, .4), alpha=False).samples).hexdigest()
           for p in d]
    d.close()
    return out


def delete_and_save(src, out, encrypt: bool, indices=(1,)) -> PdfDocument:
    d = PdfDocument.open(src, PW if encrypt else None)
    d.delete_pages(list(indices))
    d.save(out)
    return d


def warnings_after_full_load(path, encrypt: bool) -> str:
    """開き直して全ページを描き、しおり・リンク・構造ツリーを読む。その間の MuPDF の警告。"""
    pymupdf.TOOLS.mupdf_warnings()
    d = _open(path, encrypt)
    for p in d:
        p.get_pixmap(matrix=pymupdf.Matrix(.2, .2))
        p.get_links()
        list(p.annots())
        list(p.widgets())
    d.get_toc(False)
    d.resolve_names()
    d.close()
    return pymupdf.TOOLS.mupdf_warnings()


# ---------------------------------------------------------------------------
# R1: 文字列の中の「N 0 R」は参照ではない

def _r1_build(path, encrypt: bool):
    doc = _base()
    p2 = doc[1].xref
    ref = f"{p2} 0 R"
    note = doc[0].add_text_annot((100, 100), f"see {ref} in appendix")
    doc.xref_set_key(note.xref, "P", f"{p2} 0 R")  # 同じオブジェクトに本物の参照もある
    doc.set_metadata({"title": f"Report {ref}", "subject": "x"})
    js = _obj(doc, f"<</S/JavaScript/JS(var s = '{ref}';)>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</JavaScript<</Names[(a) {js} 0 R]>>>>")
    inline = doc[0].add_text_annot((200, 200), "inline")
    doc.xref_set_key(inline.xref, "A", f"<</S/JavaScript/JS(app.alert\\('Item {ref}'\\);)>>")
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name, w.field_value, w.rect = "kf", f"order {ref}", pymupdf.Rect(72, 400, 300, 430)
    doc[0].add_widget(w)
    doc.set_toc([[1, f"Ref {ref}", 3], [1, "other", 1], [1, "to deleted", 2]])
    return _save(doc, path, encrypt)


def _r1_strings(path, encrypt: bool) -> dict:
    d = _open(path, encrypt)
    cat = d.pdf_catalog()
    annots = list(d[0].annots())
    names = d.xref_get_key(cat, "Names/JavaScript/Names")[1]
    js_xref = int(re.search(r"(\d+) 0 R", names).group(1))
    info = int(d.xref_get_key(-1, "Info")[1].split()[0])
    out = {
        "annot_contents": [d.xref_get_key(a.xref, "Contents") for a in annots],
        "inline_js": [d.xref_get_key(a.xref, "A/JS") for a in annots],
        "info_title": d.xref_get_key(info, "Title"),
        "js": d.xref_get_key(js_xref, "JS"),
        "bookmark_titles": [d.xref_get_key(x, "Title") for x in d.get_outline_xrefs()],
        "field": [(w.field_name, w.field_value, d.xref_get_key(w.xref, "V")) for w in d[0].widgets()],
    }
    d.close()
    return out


@ENCRYPT
def test_reference_like_text_in_kept_strings_is_untouched(tmp_path, encrypt):
    src = _r1_build(tmp_path / "src.pdf", encrypt)
    before = _r1_strings(src, encrypt)
    out = tmp_path / "out.pdf"
    delete_and_save(src, out, encrypt).close()
    after = _r1_strings(out, encrypt)
    gone = before["bookmark_titles"].pop()  # 削除したページだけを指すしおりは消える
    assert gone == ("string", "to deleted")
    assert after == before
    assert warnings_after_full_load(out, encrypt) == ""


# ---------------------------------------------------------------------------
# R2: 削除したページにだけ属するものは残らない

def _tag(doc, root: int, kids: str, nums: str, extra: str = "") -> None:
    doc.update_object(root, f"<</Type/StructTreeRoot/K[{kids}]/ParentTree<</Nums[{nums}]>>{extra}>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<</Marked true>>")


def case_objr_widget(doc):
    """/Pg のない構造要素が OBJR で削除ページのフォーム部品を指す。"""
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name, w.field_value, w.rect = "MKAname", "MKAvalue", pymupdf.Rect(72, 400, 300, 430)
    wd = doc[1].add_widget(w)
    p1, p2 = doc[0].xref, doc[1].xref
    root, form, kept = (doc.get_new_xref() for _ in range(3))
    doc.update_object(form, f"<</Type/StructElem/S/Form/P {root} 0 R"
                            f"/K[<</Type/OBJR/Obj {wd.xref} 0 R/Pg {p2} 0 R>>]>>")
    doc.update_object(kept, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {p1} 0 R/K 0/ActualText(kept-se)>>")
    _tag(doc, root, f"{kept} 0 R {form} 0 R", f"0[{kept} 0 R]5 {form} 0 R")
    doc.xref_set_key(p1, "StructParents", "0")
    doc.xref_set_key(wd.xref, "StructParent", "5")
    return ["MKAname", "MKAvalue"]


def case_objr_link(doc):
    """/Pg のない構造要素が OBJR（/Pg なし）で削除ページの URI リンクを指す。"""
    doc[1].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 50, 50),
                        "uri": "https://MKALINK.example/private"})
    lx = [x for x, k, _ in doc[1].annot_xrefs()][0]
    p1 = doc[0].xref
    root, se, kept = (doc.get_new_xref() for _ in range(3))
    doc.update_object(se, f"<</Type/StructElem/S/Link/P {root} 0 R/K[<</Type/OBJR/Obj {lx} 0 R>>]>>")
    doc.update_object(kept, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {p1} 0 R/K 0/ActualText(kept-se)>>")
    _tag(doc, root, f"{kept} 0 R {se} 0 R", f"0[{kept} 0 R]7 {se} 0 R")
    doc.xref_set_key(p1, "StructParents", "0")
    doc.xref_set_key(lx, "StructParent", "7")
    return ["MKALINK"]


def case_inherited_pg(doc):
    """削除ページを /Pg に持つ要素の子（/Pg を受け継ぐ）が ParentTree に載っている。"""
    p1, p2 = doc[0].xref, doc[1].xref
    root, gone, kid, kept = (doc.get_new_xref() for _ in range(4))
    doc.update_object(kid, f"<</Type/StructElem/S/Span/P {gone} 0 R/K 0/ActualText(MKBinherit)>>")
    doc.update_object(gone, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {p2} 0 R/K[{kid} 0 R]>>")
    doc.update_object(kept, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {p1} 0 R/K 0/ActualText(kept-se)>>")
    _tag(doc, root, f"{kept} 0 R {gone} 0 R", f"0[{kept} 0 R]1[{kid} 0 R]", "/ParentTreeNextKey 2")
    doc.xref_set_key(p1, "StructParents", "0")
    doc.xref_set_key(p2, "StructParents", "1")
    return ["MKBinherit"]


def case_mcr_only(doc):
    """/Pg のない図の要素が、MCR だけで削除ページの内容を指す。"""
    p1, p2 = doc[0].xref, doc[1].xref
    root, fig, kept = (doc.get_new_xref() for _ in range(3))
    doc.update_object(fig, f"<</Type/StructElem/S/Figure/P {root} 0 R"
                           f"/K[<</Type/MCR/Pg {p2} 0 R/MCID 0>>]/Alt(MKCalt)/ActualText(MKCact)"
                           f"/E(MKCexp)/T(MKCtitle)>>")
    doc.update_object(kept, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {p1} 0 R/K 0/ActualText(kept-se)>>")
    _tag(doc, root, f"{kept} 0 R {fig} 0 R", f"0[{kept} 0 R]1[{fig} 0 R]")
    doc.xref_set_key(p1, "StructParents", "0")
    doc.xref_set_key(p2, "StructParents", "1")
    return ["MKCalt", "MKCact", "MKCexp", "MKCtitle"]


def case_bookmarks(doc):
    """削除ページを指すしおり（子も削除ページだけを指す）は消える。残るページを指す子があれば
    項目は残し、行き先だけ外す。"""
    doc.set_toc([
        [1, "MKDgone", 2],
        [1, "MKDparent", 2], [2, "MKDchild", 2],
        [1, "keep-parent", 2], [2, "keep-child", 3],
        [1, "kept", 4],
    ])
    return ["MKDgone", "MKDparent", "MKDchild"]


def case_layers(doc):
    """削除ページでだけ使うレイヤー（OCG）は名前ごと消える。残るページでも使うものは残る。"""
    only = doc.add_ocg("MKElayer", on=False)
    shared = doc.add_ocg("keep-layer", on=True)
    doc[1].insert_text((72, 200), "x", oc=only)
    doc[1].insert_text((72, 220), "y", oc=shared)
    doc[0].insert_text((72, 220), "y", oc=shared)
    return ["MKElayer"]


def case_threads(doc):
    """記事のスレッド: 削除ページのビーズは外し、ビーズがなくなったスレッドは消える。"""
    p1, p2, p3 = doc[0].xref, doc[1].xref, doc[2].xref
    t, b1, b2, b3, t2, c1 = (doc.get_new_xref() for _ in range(6))
    doc.update_object(t, f"<</Type/Thread/F {b1} 0 R/I<</Title(kept thread)>>>>")
    doc.update_object(b1, f"<</Type/Bead/T {t} 0 R/N {b2} 0 R/V {b3} 0 R/P {p1} 0 R/R[0 0 100 100]>>")
    doc.update_object(b2, f"<</Type/Bead/N {b3} 0 R/V {b1} 0 R/P {p2} 0 R/R[0 0 100 100]>>")
    doc.update_object(b3, f"<</Type/Bead/N {b1} 0 R/V {b2} 0 R/P {p3} 0 R/R[0 0 100 100]>>")
    doc.update_object(t2, f"<</Type/Thread/F {c1} 0 R/I<</Title(MKFthread)>>>>")
    doc.update_object(c1, f"<</Type/Bead/T {t2} 0 R/N {c1} 0 R/V {c1} 0 R/P {p2} 0 R/R[1 1 9 9]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Threads", f"[{t} 0 R {t2} 0 R]")
    doc.xref_set_key(p1, "B", f"[{b1} 0 R]")
    doc.xref_set_key(p2, "B", f"[{b2} 0 R {c1} 0 R]")
    doc.xref_set_key(p3, "B", f"[{b3} 0 R]")
    return ["MKFthread"]


R2_CASES = [case_objr_widget, case_objr_link, case_inherited_pg, case_mcr_only, case_bookmarks,
            case_layers, case_threads]


def _struct_kids(d) -> list:
    """構造ツリーの (S, ActualText) を木の順に。各要素の /P が親を指すことも確かめる。"""
    pdf = pymupdf._as_pdf_document(d)
    root = mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/StructTreeRoot")
    out = []

    def walk(node, parent_num):
        k = mupdf.pdf_dict_gets(node, "K")
        items = [mupdf.pdf_array_get(k, i) for i in range(mupdf.pdf_array_len(k))] \
            if mupdf.pdf_is_array(k) else [k]
        for kid in items:
            if mupdf.pdf_is_dict(kid) and mupdf.pdf_dict_gets(kid, "S").m_internal:
                assert mupdf.pdf_to_num(mupdf.pdf_dict_gets(kid, "P")) == parent_num
                out.append((mupdf.pdf_to_name(mupdf.pdf_dict_gets(kid, "S")),
                            mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(kid, "ActualText"))))
                walk(kid, mupdf.pdf_to_num(kid))
    walk(root, mupdf.pdf_to_num(root))
    return out


def _threads(d) -> list:
    index = {d[i].xref: i for i in range(d.page_count)}
    pdf = pymupdf._as_pdf_document(d)
    threads = mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/Threads")
    out = []
    for i in range(mupdf.pdf_array_len(threads)):
        t = mupdf.pdf_array_get(threads, i)
        first = mupdf.pdf_dict_gets(t, "F")
        beads, b = [], first
        while True:
            beads.append(index[mupdf.pdf_to_num(mupdf.pdf_dict_gets(b, "P"))])
            assert mupdf.pdf_to_num(mupdf.pdf_dict_gets(mupdf.pdf_dict_gets(b, "N"), "V")) == \
                mupdf.pdf_to_num(b)
            b = mupdf.pdf_dict_gets(b, "N")
            if mupdf.pdf_to_num(b) == mupdf.pdf_to_num(first) or len(beads) > 10:
                break
        title = mupdf.pdf_to_text_string(mupdf.pdf_dict_getp(t, "I/Title"))
        assert mupdf.pdf_to_num(mupdf.pdf_dict_gets(first, "T")) == mupdf.pdf_to_num(t)
        out.append((title, beads))
    return out


@ENCRYPT
@pytest.mark.parametrize("case", R2_CASES, ids=lambda c: c.__name__[5:])
def test_nothing_of_the_deleted_page_remains(tmp_path, case, encrypt):
    doc = _base()
    doc[1].insert_text((72, 120), "MKbody")
    needles = case(doc) + ["MKbody"]
    src = _save(doc, tmp_path / "src.pdf", encrypt)
    assert {n for _, n in raw_hits(src, encrypt, needles)} == set(needles)  # 元には全部ある
    before = looks(src, encrypt)

    out = tmp_path / "out.pdf"
    d = delete_and_save(src, out, encrypt)
    assert raw_hits(out, encrypt, needles) == []
    assert looks(out, encrypt) == [before[i] for i in (0, 2, 3)]  # 残るページの見た目は同じ
    assert warnings_after_full_load(out, encrypt) == ""

    r = _open(out, encrypt)
    if case.__name__ in ("case_objr_widget", "case_objr_link", "case_inherited_pg", "case_mcr_only"):
        assert _struct_kids(r) == [("P", "kept-se")]
        assert r.xref_get_key(r.pdf_catalog(), "StructTreeRoot/ParentTree/Nums")[1] \
            .startswith("[0[")  # 残るページの ParentTree の組は残る
    if case is case_bookmarks:
        assert [t[:3] for t in r.get_toc()] == [
            [1, "keep-parent", -1], [2, "keep-child", 2], [1, "kept", 3]]
    if case is case_layers:
        assert [v["name"] for v in r.get_ocgs().values()] == ["keep-layer"]
    if case is case_threads:
        assert _threads(r) == [("kept thread", [0, 1])]
    r.close()
    d.close()


# ---------------------------------------------------------------------------
# R3: 上位の構造要素の /Pg が削除ページでも、残るページの子がいれば残る

@ENCRYPT
def test_high_level_element_on_deleted_page_keeps_kept_children(tmp_path, encrypt):
    doc = _base(3)
    px = [doc[i].xref for i in range(3)]
    root, docse = doc.get_new_xref(), doc.get_new_xref()
    paras = [doc.get_new_xref() for _ in range(3)]
    for i, p in enumerate(paras):
        doc.update_object(p, f"<</Type/StructElem/S/P/P {docse} 0 R/Pg {px[i]} 0 R/K 0"
                             f"/ActualText(para{i + 1})>>")
    doc.update_object(docse, f"<</Type/StructElem/S/Document/P {root} 0 R/Pg {px[0]} 0 R"
                             f"/K[{' '.join(f'{p} 0 R' for p in paras)}]>>")
    _tag(doc, root, f"{docse} 0 R", " ".join(f"{i}[{p} 0 R]" for i, p in enumerate(paras)))
    for i in range(3):
        doc.xref_set_key(px[i], "StructParents", str(i))
    src = _save(doc, tmp_path / "src.pdf", encrypt)

    out = tmp_path / "out.pdf"
    delete_and_save(src, out, encrypt, indices=(0,)).close()
    r = _open(out, encrypt)
    assert _struct_kids(r) == [("Document", ""), ("P", "para2"), ("P", "para3")]
    # 残るページの ParentTree の組は、残るページの段落を指したまま
    pdf = pymupdf._as_pdf_document(r)
    for i in range(r.page_count):
        key = mupdf.pdf_to_int(mupdf.pdf_dict_gets(mupdf.pdf_lookup_page_obj(pdf, i), "StructParents"))
        tree = mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/StructTreeRoot/ParentTree")
        mapped = mupdf.pdf_lookup_number(tree, key)
        assert mupdf.pdf_to_text_string(
            mupdf.pdf_dict_gets(mupdf.pdf_array_get(mapped, 0), "ActualText")) == f"para{i + 2}"
    r.close()
    assert raw_hits(out, encrypt, ["para1"]) == []
    assert warnings_after_full_load(out, encrypt) == ""


# ---------------------------------------------------------------------------
# R4: アプリが外したページだけが対象。テンプレートのページは残る

@ENCRYPT
def test_template_page_survives_and_deleted_page_is_scrubbed(tmp_path, encrypt):
    doc = _base()
    doc[1].insert_text((72, 120), "MKbody")
    tpl_stream = b"BT /F1 12 Tf 10 10 Td (TPLKEEP) Tj ET"
    cs = _obj(doc, "<<>>")
    doc.update_stream(cs, tpl_stream)
    tpl = _obj(doc, f"<</Type/Page/MediaBox[0 0 100 100]/Contents {cs} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Templates<</Names[(tpl) {tpl} 0 R]>>>>")
    p2 = doc[1].xref
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"[{p2} 0 R/Fit]")  # 掃除が要る参照
    src = _save(doc, tmp_path / "src.pdf", encrypt)

    out = tmp_path / "out.pdf"
    delete_and_save(src, out, encrypt).close()
    r = _open(out, encrypt)
    names = r.xref_get_key(r.pdf_catalog(), "Names/Templates/Names")
    assert names[0] == "array" and names[1].startswith("[(tpl)")
    t = int(re.search(r"(\d+) 0 R", names[1]).group(1))
    assert r.xref_get_key(t, "Type") == ("name", "/Page")
    assert r.xref_get_key(t, "MediaBox") == ("array", "[0 0 100 100]")
    c = int(r.xref_get_key(t, "Contents")[1].split()[0])
    assert r.xref_stream(c) == tpl_stream
    assert r.xref_get_key(r.pdf_catalog(), "OpenAction")[0] == "null"
    r.close()
    assert raw_hits(out, encrypt, ["MKbody"]) == []


# ---------------------------------------------------------------------------
# R5: 掃除・確認の失敗は日本語の SaveFailedError（元のファイルはそのまま、編集は残る）

def _struct_file(tmp_path):
    doc = _base()
    case_inherited_pg(doc)
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"[{doc[1].xref} 0 R/Fit]")
    return _save(doc, tmp_path / "src.pdf", False)


def test_unverifiable_reference_refuses_to_save(tmp_path, monkeypatch, caplog):
    src = _struct_file(tmp_path)
    original = src.read_bytes()
    monkeypatch.setattr(scrub, "_remove_references", lambda *a, **k: None)  # 参照を残す
    d = PdfDocument.open(src)
    d.delete_pages([1])
    with caplog.at_level(logging.ERROR), pytest.raises(errors.SaveFailedError) as e:
        d.save()
    assert "削除したページ" in e.value.user_message and "保存を中止" in e.value.user_message
    assert src.read_bytes() == original and d.page_count == 3
    assert any(r.exc_info for r in caplog.records)
    assert not list(tmp_path.glob("*.tmp-*.pdf"))


def test_unexpected_error_in_scrub_is_save_failed(tmp_path, monkeypatch, caplog):
    src = _struct_file(tmp_path)
    original = src.read_bytes()

    def boom(*a, **k):
        raise mupdf.FzErrorSyntax(8, "invalid key in dict")
    monkeypatch.setattr(scrub, "_remove_references", boom)
    d = PdfDocument.open(src)
    d.delete_pages([1])
    with caplog.at_level(logging.ERROR), pytest.raises(errors.SaveFailedError) as e:
        d.save()
    assert "保存を中止" in e.value.user_message
    assert src.read_bytes() == original and d.page_count == 3
    assert any(r.exc_info for r in caplog.records)
    monkeypatch.undo()
    d.save()  # 編集は残っていて、もう一度保存できる
    assert pymupdf.open(src).page_count == 3


# ---------------------------------------------------------------------------
# R6: 保存の後で元に戻すと、ページはリンク・しおり・名前付きの行き先・フォームごと戻る

def _summary(path, encrypt: bool) -> dict:
    d = _open(path, encrypt)
    out = dict(
        links=[[(l["kind"], l.get("page"), l.get("uri")) for l in p.get_links()] for p in d],
        toc=[t[:3] for t in d.get_toc()],
        dests={k: v["page"] for k, v in d.resolve_names().items()},
        fields=[[(w.field_name, w.field_value) for w in p.widgets()] for p in d],
        text=[p.get_text() for p in d],
    )
    d.close()
    return out


@ENCRYPT
def test_undo_after_save_restores_everything(tmp_path, encrypt):
    doc = _base()
    doc[1].insert_text((72, 120), "MKbody")
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 50),
                        "page": 1, "to": pymupdf.Point(0, 0)})
    doc[1].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 50),
                        "page": 2, "to": pymupdf.Point(0, 0)})
    doc[1].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(60, 10, 90, 50),
                        "uri": "https://example.com/x"})
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name, w.field_value, w.rect = "secret", "value", pymupdf.Rect(72, 400, 300, 430)
    doc[1].add_widget(w)
    doc.set_toc([[1, "deleted", 2], [1, "kept", 3]])
    nd = _obj(doc, f"<</Names[(gone)[{doc[1].xref} 0 R/Fit](kept)[{doc[2].xref} 0 R/Fit]]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests {nd} 0 R>>")
    case_inherited_pg(doc)
    src = _save(doc, tmp_path / "src.pdf", encrypt)
    before = _summary(src, encrypt)

    d = PdfDocument.open(src, PW if encrypt else None)
    detached = d.delete_pages([1])
    d.save(tmp_path / "a.pdf")
    assert raw_hits(tmp_path / "a.pdf", encrypt, ["MKbody", "MKBinherit"]) == []
    d.restore_pages(detached)
    d.save(tmp_path / "b.pdf")
    d.close()
    assert _summary(tmp_path / "b.pdf", encrypt) == before
    assert {n for _, n in raw_hits(tmp_path / "b.pdf", encrypt, ["MKbody", "MKBinherit"])} == \
        {"MKbody", "MKBinherit"}
    assert warnings_after_full_load(tmp_path / "b.pdf", encrypt) == ""


# ---------------------------------------------------------------------------
# S2: 同じページオブジェクトがページツリーに 2 回ある（copy_page など）

def _dup_file(tmp_path):
    doc = _base(3)
    doc[0].insert_text((72, 120), "MKdup")
    doc.copy_page(0, -1)  # 4 ページ目は 1 ページ目と同じオブジェクト
    assert doc[3].xref == doc[0].xref
    return _save(doc, tmp_path / "dup.pdf", False)


def test_deleting_one_occurrence_of_a_repeated_page_keeps_it(tmp_path):
    src = _dup_file(tmp_path)
    before = looks(src, False)
    d = PdfDocument.open(src)
    detached = d.delete_pages([3])
    out = tmp_path / "out.pdf"
    d.save(out)
    assert looks(out, False) == before[:3]
    assert {n for _, n in raw_hits(out, False, ["MKdup"])} == {"MKdup"}
    d.restore_pages(detached)
    d.save(tmp_path / "back.pdf")
    d.close()
    assert looks(tmp_path / "back.pdf", False) == before


def test_deleting_every_occurrence_of_a_repeated_page_scrubs_it(tmp_path):
    src = _dup_file(tmp_path)
    before = looks(src, False)
    d = PdfDocument.open(src)
    d.delete_pages([0, 3])
    out = tmp_path / "out.pdf"
    d.save(out)
    d.close()
    assert looks(out, False) == before[1:3]
    assert raw_hits(out, False, ["MKdup"]) == []


# ---------------------------------------------------------------------------
# S3: 名前ツリー・数値ツリーから組を外したら /Limits を数え直し、空になった節を外す

def _name_tree_file(tmp_path, encrypt):
    """名前付きの行き先（Kids と Limits のある名前ツリー）と構造の IDTree。3 ページ目（index 2）を
    削除すると、ある葉の端の名前と、ある葉の全部がなくなる。残るページのリンクとしおりが、
    削除ページの名前付きの行き先を名前で指している。"""
    doc = _base(4)
    px = [doc[i].xref for i in range(4)]
    dest = {n: _obj(doc, f"[{px[i]} 0 R/XYZ 0 792 0]") for n, i in (
        ("a-keep", 0), ("b-keep", 1), ("c-MKgone", 2), ("d-MKgone2", 2), ("e-keep", 3))}
    l1 = _obj(doc, f"<</Limits[(a-keep)(c-MKgone)]/Names[(a-keep) {dest['a-keep']} 0 R"
                   f"(b-keep) {dest['b-keep']} 0 R(c-MKgone) {dest['c-MKgone']} 0 R]>>")
    l2 = _obj(doc, f"<</Limits[(d-MKgone2)(d-MKgone2)]/Names[(d-MKgone2) {dest['d-MKgone2']} 0 R]>>")
    l3 = _obj(doc, f"<</Limits[(e-keep)(e-keep)]/Names[(e-keep) {dest['e-keep']} 0 R]>>")
    mid = _obj(doc, f"<</Limits[(d-MKgone2)(e-keep)]/Kids[{l2} 0 R {l3} 0 R]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests<</Kids[{l1} 0 R {mid} 0 R]>>>>")
    root = doc.get_new_xref()
    ids = ["id-0", "id-1", "id-2-MKid", "id-3"]
    se = [_obj(doc, f"<</Type/StructElem/S/P/P {root} 0 R/Pg {px[i]} 0 R/K 0/ID({ids[i]})>>")
          for i in range(4)]
    i1 = _obj(doc, f"<</Limits[(id-0)(id-1)]/Names[(id-0) {se[0]} 0 R(id-1) {se[1]} 0 R]>>")
    i2 = _obj(doc, f"<</Limits[(id-2-MKid)(id-2-MKid)]/Names[(id-2-MKid) {se[2]} 0 R]>>")
    i3 = _obj(doc, f"<</Limits[(id-3)(id-3)]/Names[(id-3) {se[3]} 0 R]>>")
    _tag(doc, root, " ".join(f"{s} 0 R" for s in se),
         " ".join(f"{i}[{s} 0 R]" for i, s in enumerate(se)),
         f"/IDTree<</Kids[{i1} 0 R {i2} 0 R {i3} 0 R]>>")
    for i in range(4):
        doc.xref_set_key(px[i], "StructParents", str(i))
    links = [_obj(doc, f"<</Type/Annot/Subtype/Link/Rect[{k * 40} 10 {k * 40 + 30} 50]/P {px[0]} 0 R"
                       f"{a}>>")
             for k, a in enumerate(["/Dest(c-MKgone)", "/A<</S/GoTo/D(d-MKgone2)>>", "/Dest(e-keep)"])]
    doc.xref_set_key(px[0], "Annots", "[" + " ".join(f"{x} 0 R" for x in links) + "]")
    doc.set_toc([[1, "named gone", 1], [1, "kept", 1]])
    ox = doc.get_outline_xrefs()
    doc.xref_set_key(ox[0], "Dest", "(d-MKgone2)")
    doc.xref_set_key(ox[1], "Dest", "(b-keep)")
    return _save(doc, tmp_path / "names.pdf", encrypt)


def _tree_nodes(d, path: str) -> list:
    """名前ツリーの節（根から深さ優先）: (根か, Limits, 葉の名前) の列。"""
    pdf = pymupdf._as_pdf_document(d)
    out = []

    def walk(node, is_root):
        lim = mupdf.pdf_dict_gets(node, "Limits")
        lims = tuple(mupdf.pdf_to_text_string(mupdf.pdf_array_get(lim, i)) for i in range(2)) \
            if mupdf.pdf_is_array(lim) else None
        names = mupdf.pdf_dict_gets(node, "Names")
        keys = [mupdf.pdf_to_text_string(mupdf.pdf_array_get(names, i))
                for i in range(0, mupdf.pdf_array_len(names), 2)] if mupdf.pdf_is_array(names) else []
        out.append((is_root, lims, keys))
        kids = mupdf.pdf_dict_gets(node, "Kids")
        for i in range(mupdf.pdf_array_len(kids) if mupdf.pdf_is_array(kids) else 0):
            walk(mupdf.pdf_array_get(kids, i), False)
    walk(mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), path), True)
    return out


@ENCRYPT
def test_name_tree_limits_and_empty_nodes(tmp_path, encrypt):
    src = _name_tree_file(tmp_path, encrypt)
    out = tmp_path / "out.pdf"
    delete_and_save(src, out, encrypt, indices=(2,)).close()
    assert raw_hits(out, encrypt, ["MKgone", "MKid"]) == []
    assert warnings_after_full_load(out, encrypt) == ""
    r = _open(out, encrypt)
    assert {k: v["page"] for k, v in r.resolve_names().items()} == \
        {"a-keep": 0, "b-keep": 1, "e-keep": 2}
    assert _tree_nodes(r, "Root/Names/Dests") == [
        (True, None, []), (False, ("a-keep", "b-keep"), ["a-keep", "b-keep"]),
        (False, ("e-keep", "e-keep"), []), (False, ("e-keep", "e-keep"), ["e-keep"])]
    assert _tree_nodes(r, "Root/StructTreeRoot/IDTree") == [
        (True, None, []), (False, ("id-0", "id-1"), ["id-0", "id-1"]),
        (False, ("id-3", "id-3"), ["id-3"])]
    assert [l.get("page") for l in r[0].get_links()] == [2]  # 名前で残るページを指すリンクだけ
    assert [t[1:] for t in r.get_toc()] == [["kept", 2]]  # 消えた名前だけを指すしおりは消える
    r.close()


def test_whole_name_tree_removed_when_empty(tmp_path):
    doc = _base(3)
    d1 = _obj(doc, f"[{doc[1].xref} 0 R/Fit]")
    leaf = _obj(doc, f"<</Limits[(MKonly)(MKonly)]/Names[(MKonly) {d1} 0 R]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests<</Kids[{leaf} 0 R]>>>>")
    src = _save(doc, tmp_path / "src.pdf", False)
    out = tmp_path / "out.pdf"
    delete_and_save(src, out, False).close()
    assert raw_hits(out, False, ["MKonly"]) == []
    r = _open(out, False)
    assert r.xref_get_key(r.pdf_catalog(), "Names/Dests")[0] == "null"
    r.close()
    assert warnings_after_full_load(out, False) == ""


# ---------------------------------------------------------------------------
# S4: 行き先を失った GoTo は外し、/Next があればそれを継ぐ。/D のない GoTo は残さない

def _gotos_without_d(path, encrypt) -> list:
    d = _open(path, encrypt)
    pdf = pymupdf._as_pdf_document(d)
    bad, seen = [], set()

    def walk(o, depth=0):
        if depth > 50:
            return
        if mupdf.pdf_is_indirect(o):
            if mupdf.pdf_to_num(o) in seen:
                return
            seen.add(mupdf.pdf_to_num(o))
        if mupdf.pdf_is_dict(o):
            if mupdf.pdf_to_name(mupdf.pdf_dict_gets(o, "S")) == "GoTo" and \
                    mupdf.pdf_is_null(mupdf.pdf_dict_gets(o, "D")):
                bad.append(mupdf.pdf_to_num(o))
            for i in range(mupdf.pdf_dict_len(o)):
                walk(mupdf.pdf_dict_get_val(o, i), depth + 1)
        elif mupdf.pdf_is_array(o):
            for i in range(mupdf.pdf_array_len(o)):
                walk(mupdf.pdf_array_get(o, i), depth + 1)
    walk(mupdf.pdf_trailer(pdf))
    d.close()
    return bad


def _chain(a) -> list:
    """アクション（の配列）から /Next をたどった (S, URI か JS か行き先のページ番号の有無) の列。"""
    out = []
    for item in ([mupdf.pdf_array_get(a, i) for i in range(mupdf.pdf_array_len(a))]
                 if mupdf.pdf_is_array(a) else [a]):
        if not mupdf.pdf_is_dict(item):
            continue
        out.append((mupdf.pdf_to_name(mupdf.pdf_dict_gets(item, "S")),
                    mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(item, "URI"))
                    or mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(item, "JS"))))
        out += _chain(mupdf.pdf_dict_gets(item, "Next"))
    return out


@ENCRYPT
def test_dead_goto_is_spliced_out_of_action_chains(tmp_path, encrypt):
    doc = _base(4)
    p1, p2, p3 = doc[0].xref, doc[1].xref, doc[2].xref
    gone = f"/D[{p2} 0 R/Fit]"
    ind_goto = _obj(doc, f"<</S/GoTo{gone}/Next<</S/URI/URI(https://c.example)>>>>")
    chains = {
        "uri_next_goto": f"<</S/URI/URI(https://a.example)/Next<</S/GoTo{gone}>>>>",
        "goto_next_uri": f"<</S/GoTo{gone}/Next<</S/URI/URI(https://b.example)>>>>",
        "goto_next_array": f"<</S/GoTo{gone}/Next[<</S/JavaScript/JS(one)/Next<</S/JavaScript/JS(sub)>>>>"
                           f"<</S/GoTo{gone}>><</S/JavaScript/JS(two)>>]>>",
        "goto_next_kept_goto": f"<</S/GoTo{gone}/Next<</S/GoTo/D[{p3} 0 R/Fit]>>>>",
        "indirect": f"<</S/URI/URI(https://d.example)/Next {ind_goto} 0 R>>",
        "indirect_direct_a": f"{ind_goto} 0 R",
    }
    # PyMuPDF の削除は、削除ページへ行くリンク注釈を（/Next があっても）丸ごと外すので、
    # 消えない種類の注釈（四角形）の /A に置く
    annots = [_obj(doc, f"<</Type/Annot/Subtype/Square/Rect[{k * 20} 0 {k * 20 + 10} 10]/P {p1} 0 R"
                        f"/A {action}>>") for k, action in enumerate(chains.values())]
    doc.xref_set_key(p1, "Annots", "[" + " ".join(f"{x} 0 R" for x in annots) + "]")
    doc.xref_set_key(p1, "AA", f"<</O<</S/GoTo{gone}/Next<</S/JavaScript/JS(aa)>>>>>>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction",
                     f"<</S/GoTo{gone}/Next<</S/JavaScript/JS(open)>>>>")
    src = _save(doc, tmp_path / "src.pdf", encrypt)
    out = tmp_path / "out.pdf"
    delete_and_save(src, out, encrypt).close()
    assert _gotos_without_d(out, encrypt) == []
    r = _open(out, encrypt)
    pdf = pymupdf._as_pdf_document(r)
    got = {name: _chain(mupdf.pdf_dict_gets(mupdf.pdf_load_object(pdf, x), "A"))
           for name, (x, _, _) in zip(chains, r[0].annot_xrefs())}
    assert got == {
        "uri_next_goto": [("URI", "https://a.example")],
        "goto_next_uri": [("URI", "https://b.example")],
        "goto_next_array": [("JavaScript", "one"), ("JavaScript", "sub"), ("JavaScript", "two")],
        "goto_next_kept_goto": [("GoTo", "")],
        "indirect": [("URI", "https://d.example"), ("URI", "https://c.example")],
        "indirect_direct_a": [("URI", "https://c.example")],
    }
    page = mupdf.pdf_lookup_page_obj(pdf, 0)
    assert _chain(mupdf.pdf_dict_getp(page, "AA/O")) == [("JavaScript", "aa")]
    assert _chain(mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/OpenAction")) == \
        [("JavaScript", "open")]
    r.close()
    assert warnings_after_full_load(out, encrypt) == ""
