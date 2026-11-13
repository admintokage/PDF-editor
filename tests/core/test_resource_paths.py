"""共有の Resources に、残すページが別の道から届く場合（要件 7 章と「残すものを壊さない」）。

削除する 1 ページ目と残す 2 ページ目が Resources を共有し、自分の Resources を持つ 3 ページ目は、
共有の辞書を /Resources に持つフォーム F に、注釈の外観・包みのフォーム・パターンなどを通って
届く。F は共有の辞書の /ImK を描く。資源を取り除いても:
- 3 ページ目は 200dpi でピクセルまで同じ、文字も同じ、MuPDF の警告なし（保存でも書き出しでも）
- 安全に取り除ける場合は、削除したページの画像（ImD）は保存したファイルに残らない
- 内容が読めない（字句解析の失敗）など安全でなければ、辞書には触らず、記録に残す
layout: shared = 1 つの間接の Resources を共有。inherited = /Pages の Resources を受け継ぎ、
その /XObject 辞書（間接）を F も使う。"""
import hashlib
import logging

import pymupdf
import pytest

from pdfeditor.core.document import PdfDocument
from tests.helpers import raw_count

PW = "user"
DEL_TAG, KEEP_TAG = b"DELONLY!", b"KEEPUSE!"
HELV = "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"
COUR = "<</Type/Font/Subtype/Type1/BaseFont/Courier/Encoding/WinAnsiEncoding>>"
TXT = "BT /FH 12 Tf 10 280 Td (kept text) Tj ET\n"


class _Doc:
    def __init__(self):
        self.doc = pymupdf.open()
        for _ in range(3):
            self.doc.new_page(width=300, height=300)
        self.px = [self.doc[i].xref for i in range(3)]

    def obj(self, text, stream: bytes | str | None = None) -> int:
        x = self.doc.get_new_xref()
        self.doc.update_object(x, text)
        if stream is not None:
            self.doc.update_stream(x, stream.encode("latin-1") if isinstance(stream, str) else stream,
                                   new=True)
        return x

    def image(self, tag: bytes, rgb) -> int:
        data = (tag + bytes(rgb) * 64)[:192]
        return self.obj("<</Type/XObject/Subtype/Image/Width 8/Height 8/ColorSpace/DeviceRGB"
                        "/BitsPerComponent 8>>", data)

    def form(self, content: str, res: str = "", bbox: str = "0 0 300 300") -> int:
        r = f"/Resources {res}" if res else ""
        return self.obj(f"<</Type/XObject/Subtype/Form/BBox[{bbox}]{r}>>", content)

    def content(self, page: int, text: str) -> None:
        self.doc.xref_set_key(self.px[page], "Contents", f"{self.obj('<<>>', text)} 0 R")


def _build(path, path_kind: str, layout: str, encrypt: bool):
    b = _Doc()
    imd, imk = b.image(DEL_TAG, (10, 200, 10)), b.image(KEEP_TAG, (200, 10, 10))
    helv, cour = b.obj(HELV), b.obj(COUR)
    shared = b.obj("<<>>")          # shared: 共有の Resources / inherited: 共有の /XObject 辞書
    f_res = f"{shared} 0 R" if layout == "shared" else f"<</XObject {shared} 0 R>>"
    f = b.form("q 50 0 0 50 0 0 cm /ImK Do Q", f_res, "0 0 50 50")
    xobjects = f"/ImD {imd} 0 R/ImK {imk} 0 R/Fm {f} 0 R"
    if layout == "shared":
        b.doc.update_object(shared, f"<</XObject<<{xobjects}>>/Font<</FH {helv} 0 R>>>>")
        for i in (0, 1):
            b.doc.xref_set_key(b.px[i], "Resources", f"{shared} 0 R")
    else:
        b.doc.update_object(shared, f"<<{xobjects}>>")
        root = int(b.doc.xref_get_key(b.doc.pdf_catalog(), "Pages")[1].split()[0])
        for i in (0, 1):
            b.doc.xref_set_key(b.px[i], "Resources", "null")
        b.doc.xref_set_key(root, "Resources", f"<</XObject {shared} 0 R/Font<</FH {helv} 0 R>>>>")
    b.content(0, "q 100 0 0 100 0 0 cm /ImD Do Q /Fm Do")
    b.content(1, TXT)
    own_x, page3 = "", "BT /FO 12 Tf 10 280 Td (page three) Tj ET\n"
    if path_kind.startswith("annot"):
        state = path_kind.split("_")[1]  # N・D・R
        blank = b.form("q Q", bbox="0 0 50 50")
        ap = f"/N {f} 0 R" if state == "N" else f"/N {blank} 0 R/{state}<</On {f} 0 R>>"
        an = b.obj(f"<</Type/Annot/Subtype/Stamp/Rect[100 100 150 150]/AP<<{ap}>>/F 4>>")
        b.doc.xref_set_key(b.px[2], "Annots", f"[{an} 0 R]")
    elif path_kind in ("wrapper", "wrapper_lexfail"):
        w = b.form("q 1 0 0 1 100 100 cm /Fm Do Q", f"<</XObject<</Fm {f} 0 R>>>>")
        own_x = f"/XObject<</W {w} 0 R>>"
        page3 += ("> " if path_kind == "wrapper_lexfail" else "") + "/W Do"
    elif path_kind == "pattern_in_wrapper":
        pat = b.obj(f"<</Type/Pattern/PatternType 1/PaintType 1/TilingType 1/BBox[0 0 50 50]"
                    f"/XStep 50/YStep 50/Resources {f_res}>>", "q 50 0 0 50 0 0 cm /ImK Do Q")
        w = b.form("/Pattern cs /P scn 0 0 120 120 re f", f"<</Pattern<</P {pat} 0 R>>>>")
        own_x = f"/XObject<</W {w} 0 R>>"
        page3 += "q 1 0 0 1 100 100 cm /W Do Q"
    b.content(2, page3)
    b.doc.xref_set_key(b.px[2], "Resources", f"<</Font<</FO {cour} 0 R>>{own_x}>>")
    data = b.doc.tobytes()
    b.doc.close()
    doc = pymupdf.open(stream=data, filetype="pdf")
    kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=PW, owner_pw="owner") if encrypt else {}
    doc.save(path, **kw)
    doc.close()
    return path


def _open(src):
    d = pymupdf.open(stream=src, filetype="pdf") if isinstance(src, bytes) else pymupdf.open(src)
    if d.needs_pass:
        assert d.authenticate(PW)
    return d


def _looks(src, pages=None) -> list:
    """200dpi のピクセルと、文字（フォント・大きさ・位置つき）、それに MuPDF の警告。"""
    pymupdf.TOOLS.mupdf_warnings()
    d = _open(src)
    out = []
    for i in (range(d.page_count) if pages is None else pages):
        p = d[i]
        pix = p.get_pixmap(dpi=200, alpha=False, annots=True)
        out.append((hashlib.sha256(pix.samples).hexdigest(),
                    repr(p.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"])))
    d.close()
    return out + [pymupdf.TOOLS.mupdf_warnings()]


def _f_still_has_imk(src) -> bool:
    """/ImK を描くストリーム（3 ページ目から届くフォーム・パターン。描かれない状態の外観も含む）が
    あり、そのどれからも /ImK が名前で引ける。"""
    d = _open(src)
    users = [x for x in range(1, d.xref_length())
             if d.xref_is_stream(x) and b"/ImK Do" in (d.xref_stream(x) or b"")]
    ok = bool(users) and all(d.xref_get_key(x, "Resources/XObject/ImK")[0] == "xref" for x in users)
    d.close()
    return ok


PATHS = ["annot_N", "annot_D", "annot_R", "wrapper", "pattern_in_wrapper"]
LAYOUTS = ["shared", "inherited"]
ENCRYPT = pytest.mark.parametrize("encrypt", [False, True], ids=["plain", "encrypted"])


@ENCRYPT
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("path_kind", PATHS)
def test_kept_page_reaching_shared_dict_another_way_is_intact(tmp_path, path_kind, layout, encrypt):
    src = _build(tmp_path / "src.pdf", path_kind, layout, encrypt)
    before = _looks(src)
    assert before[-1] == ""
    d = PdfDocument.open(src, PW if encrypt else None)
    d.delete_pages([0])
    out = tmp_path / "out.pdf"
    d.save(out)
    exported = d.export_pages([1])
    d.close()
    assert _looks(out) == before[1:]
    assert _f_still_has_imk(out)
    assert raw_count(out, DEL_TAG) == 0          # 安全に取り除けたので、削除ページの画像は残らない
    assert raw_count(out, KEEP_TAG) == 1
    assert _looks(exported) == [before[2], ""]
    assert raw_count(exported, DEL_TAG) == 0 and _f_still_has_imk(exported)


@ENCRYPT
@pytest.mark.parametrize("layout", LAYOUTS)
def test_unreadable_kept_content_leaves_reachable_dicts_alone(tmp_path, caplog, layout, encrypt):
    """3 ページ目の内容に字句解析できない字がある: そこから届く共有の辞書には触らない。"""
    src = _build(tmp_path / "src.pdf", "wrapper_lexfail", layout, encrypt)
    before = _looks(src)
    d = PdfDocument.open(src, PW if encrypt else None)
    d.delete_pages([0])
    out = tmp_path / "out.pdf"
    with caplog.at_level(logging.INFO):
        d.save(out)
        exported = d.export_pages([1])
    d.close()
    assert _looks(out)[:-1] == before[1:-1] and _looks(exported)[:-1] == [before[2]]
    assert _f_still_has_imk(out) and _f_still_has_imk(exported)
    assert raw_count(out, DEL_TAG) == 1           # 取り除けなかった（残る）
    assert any("cannot read" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_dictionary_reached_from_outside_the_pages_is_left_alone(tmp_path, caplog, layout):
    """共有の辞書を、ページの内容からはたどらない所（フォームの /DR）も参照している。"""
    src = _build(tmp_path / "src.pdf", "wrapper", layout, False)
    doc = pymupdf.open(src)
    shared = [x for x in range(1, doc.xref_length())
              if "/ImD" in doc.xref_object(x, compressed=True)][0]
    dr = f"{shared} 0 R" if layout == "shared" else f"<</XObject {shared} 0 R>>"
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<</Fields[]/DR {dr}>>")
    doc.save(tmp_path / "dr.pdf")
    doc.close()
    before = _looks(tmp_path / "dr.pdf")
    d = PdfDocument.open(tmp_path / "dr.pdf")
    d.delete_pages([0])
    out = tmp_path / "out.pdf"
    with caplog.at_level(logging.INFO):
        d.save(out)
    d.close()
    assert _looks(out) == before[1:]
    assert raw_count(out, DEL_TAG) == 1
    assert any("used elsewhere" in r.getMessage() for r in caplog.records)
