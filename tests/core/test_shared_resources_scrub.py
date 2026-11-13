"""削除したページの、共有された Resources に載った資源（要件 7 章: 個人情報の残り）。

ページが 1 つの Resources を共有していたり /Pages から受け継いでいたりすると、削除したページの
画像・フォント・フォーム・パターン・ExtGState・シェーディング・色空間・レイヤーが、残るページの
Resources に名前つきで残る。保存では、残るページが使っていない項目を取り除く。
- 残るページが使うもの（フォームやパターンの中から使うものも）は残り、見た目と文字は変わらない
- 取り除いて見た目が変わったら、取り除くのをやめる（安全網）。字句解析の失敗も同じ
- 切り取って同じ文書に貼り付けたページ（写し）は掃除されず、元と同じに描かれる
ファイルの中身は、全オブジェクトと展開した全ストリームを生のまま調べる。"""
import logging

import pymupdf
import pytest

from pdfeditor.core import resource_prune
from pdfeditor.core.document import PdfDocument
from tests.helpers import (
    IMP_COLOR, IMS_COLOR, PAGE1_MARKS, PAGE3_MARKS, SHARED_LAYOUTS, SHARED_PW, font_chunk,
    make_shared_resources_pdf, open_fitz, page_looks, page_texts, pixel_bytes,
    raw_count,
)

ENCRYPT = pytest.mark.parametrize("encrypt", [False, True], ids=["plain", "encrypted"])
LAYOUT = pytest.mark.parametrize("layout", SHARED_LAYOUTS)
COLORS = [(30, 200, 11), (70, 170, 61), (201, 13, 77), (150, 110, 161), (190, 80, 211)]
KEPT = [0, 1, 3, 4]


def _open(src, encrypt):
    return PdfDocument.open(src, SHARED_PW if encrypt else None)


def _warnings_after_full_load(path) -> str:
    pymupdf.TOOLS.mupdf_warnings()
    d = open_fitz(path)
    for p in d:
        p.get_pixmap(matrix=pymupdf.Matrix(.3, .3))
        p.get_text()
    d.close()
    return pymupdf.TOOLS.mupdf_warnings()


def _assert_page3_gone(out):
    assert {m: raw_count(out, m) for m in PAGE3_MARKS} == {m: 0 for m in PAGE3_MARKS}
    assert raw_count(out, pixel_bytes(COLORS[2])) == 0   # 画像のデータ
    assert raw_count(out, font_chunk()) == 0             # フォントのプログラム


def _assert_kept_resources_stay(out):
    assert all(raw_count(out, m) >= 1 for m in PAGE1_MARKS)
    for color in [COLORS[i] for i in KEPT] + [IMS_COLOR, IMP_COLOR]:
        assert raw_count(out, pixel_bytes(color)) >= 1


@ENCRYPT
@LAYOUT
def test_deleted_page_resources_leave_nothing(tmp_path, layout, encrypt):
    src = make_shared_resources_pdf(tmp_path / "src.pdf", layout, encrypt)
    assert raw_count(src, pixel_bytes(COLORS[2])) == 1 and raw_count(src, font_chunk()) == 1
    looks, texts = page_looks(src), page_texts(src)

    d = _open(src, encrypt)
    detached = d.delete_pages([2])
    out = tmp_path / "out.pdf"
    d.save(out)
    _assert_page3_gone(out)
    _assert_kept_resources_stay(out)
    assert page_looks(out) == [looks[i] for i in KEPT]
    assert page_texts(out) == [texts[i] for i in KEPT]
    assert _warnings_after_full_load(out) == ""
    r = open_fitz(out)
    for page, i in zip(r, KEPT):  # 残るページは自分の画像を持ち、削除したページのものは持たない
        names = {img[7] for img in page.get_images()}
        assert f"Im{i}" in names and "Im2" not in names
        assert "Fx3" not in {x[1] for x in page.get_xobjects()}
        assert "F3" not in {f[4] for f in page.get_fonts()}
    assert {"ImS", "ImP"} <= {img[7] for img in r[0].get_images()}
    assert "Fx" in {x[1] for x in r[0].get_xobjects()}
    assert [v["name"] for v in r.get_ocgs().values()] == ["MKoc0"]
    if encrypt:
        assert pymupdf.open(out).needs_pass
    r.close()

    # 保存の後でも元に戻せる（生きている文書は掃除していない）
    d.restore_pages(detached)
    back = tmp_path / "back.pdf"
    d.save(back)
    d.close()
    assert page_looks(back) == looks and page_texts(back) == texts
    assert raw_count(back, pixel_bytes(COLORS[2])) == 1 and raw_count(back, font_chunk()) == 1
    assert all(raw_count(back, m) == 1 for m in PAGE3_MARKS)


@ENCRYPT
@pytest.mark.parametrize("layout", ["shared", "inherited_root"])
def test_cut_and_paste_into_the_same_document_keeps_the_copy(tmp_path, layout, encrypt):
    """切り取り（書き出し + 削除）して同じ文書の最後に貼り付ける。外した元のページは掃除され、
    貼り付けた写しは元と同じに描かれ、ちょうど 1 つずつ資源を持つ。"""
    src = make_shared_resources_pdf(tmp_path / "src.pdf", layout, encrypt)
    looks, texts = page_looks(src), page_texts(src)
    d = _open(src, encrypt)
    data = d.export_pages([2])
    d.delete_pages([2])
    d.insert_from_bytes(data, 4)
    out = tmp_path / "out.pdf"
    d.save(out)
    d.close()
    order = [0, 1, 3, 4, 2]
    assert page_looks(out) == [looks[i] for i in order]
    assert page_texts(out) == [texts[i] for i in order]
    assert {m: raw_count(out, m) for m in PAGE3_MARKS} == {m: 1 for m in PAGE3_MARKS}
    assert raw_count(out, pixel_bytes(COLORS[2])) == 1 and raw_count(out, font_chunk()) == 1
    _assert_kept_resources_stay(out)
    assert _warnings_after_full_load(out) == ""


def test_resources_used_elsewhere_are_not_pruned(tmp_path):
    """共有の Resources をフォームの /DR も参照している: どこで名前が使われるか分からないので
    触らない（残る方が、壊すよりまし）。保存はできる。"""
    src = make_shared_resources_pdf(tmp_path / "src.pdf", "shared")
    doc = pymupdf.open(src)
    rx = int(doc.xref_get_key(doc[0].xref, "Resources")[1].split()[0])
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<</Fields[]/DR {rx} 0 R>>")
    doc.save(tmp_path / "dr.pdf")
    doc.close()
    looks = page_looks(tmp_path / "dr.pdf")
    d = PdfDocument.open(tmp_path / "dr.pdf")
    d.delete_pages([2])
    out = tmp_path / "out.pdf"
    d.save(out)
    d.close()
    assert raw_count(out, pixel_bytes(COLORS[2])) == 1
    assert page_looks(out) == [looks[i] for i in KEPT]


# ---------------------------------------------------------------------------
# 安全網: 字句解析の失敗・誤った取り除き。保存はでき、残るページは変わらない

@pytest.mark.parametrize("layout", ["shared", "inherited_root"])
def test_tokenizer_failure_keeps_resources(tmp_path, monkeypatch, caplog, layout):
    src = make_shared_resources_pdf(tmp_path / "src.pdf", layout)
    looks, texts = page_looks(src), page_texts(src)

    def broken(*a, **k):
        raise resource_prune.ContentError("injected")
    monkeypatch.setattr(resource_prune, "names_in", broken)
    d = PdfDocument.open(src)
    d.delete_pages([2])
    out = tmp_path / "out.pdf"
    with caplog.at_level(logging.WARNING):
        d.save(out)
    d.close()
    assert page_looks(out) == [looks[i] for i in KEPT]
    assert page_texts(out) == [texts[i] for i in KEPT]
    assert raw_count(out, pixel_bytes(COLORS[2])) == 1  # 取り除けなかった（残る）
    assert raw_count(out, "MKbody3") == 0                # ページそのものは消えている
    assert any("cannot read the content" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("wrong", ["nothing_used", "form_missed"])
@pytest.mark.parametrize("layout", ["shared", "inherited_root"])
def test_wrong_pruning_is_detected_and_undone(tmp_path, monkeypatch, caplog, layout, wrong):
    """使っている名前を取りこぼした（ように細工した）取り除きは、描き比べで見つかって戻る。"""
    src = make_shared_resources_pdf(tmp_path / "src.pdf", layout)
    looks, texts = page_looks(src), page_texts(src)
    real = resource_prune.names_in

    def lossy(pdf, stream):
        names = real(pdf, stream)
        if wrong == "nothing_used":
            return []
        return [(c, n) for c, n in names if n != "Fx"]
    monkeypatch.setattr(resource_prune, "names_in", lossy)
    d = PdfDocument.open(src)
    d.delete_pages([2])
    out = tmp_path / "out.pdf"
    with caplog.at_level(logging.WARNING):
        d.save(out)
    d.close()
    assert page_looks(out) == [looks[i] for i in KEPT]
    assert page_texts(out) == [texts[i] for i in KEPT]
    assert raw_count(out, pixel_bytes(IMS_COLOR)) == 1
    assert any("changed how" in r.getMessage() for r in caplog.records)
    assert _warnings_after_full_load(out) == ""



# ---------------------------------------------------------------------------
# 安全網が見分けること: 取りこぼした名前の、見た目のごく小さな違いでも戻す

SUBTLE = {
    "tiny_image": ("XObject", "q 0.05 0 0 0.05 100 100 cm /SUB Do Q", "{img}"),
    "small_text": ("Font", "BT /SUB 0.5 Tf 100 100 Td (tiny) Tj ET",
                   "<</Type/Font/Subtype/Type1/BaseFont/Courier/Encoding/WinAnsiEncoding>>"),
    "alpha_099": ("ExtGState", "q /SUB gs 1 0 0 rg 0 0 100 100 re f Q", "<</Type/ExtGState/ca 0.99>>"),
    "calrgb": ("ColorSpace", "q /SUB cs 0.2 0.5 0.7 sc 0 0 100 100 re f Q",
               "[/CalRGB<</WhitePoint[0.9505 1 1.089]/Gamma[2.2 2.2 2.2]>>]"),
    "print_only_layer": ("Properties", "/OC /SUB BDC 0 0 1 rg 0 0 100 100 re f EMC", "{ocg}"),
}


@pytest.mark.parametrize("kind", SUBTLE)
def test_safety_net_sees_subtle_changes(tmp_path, monkeypatch, caplog, kind):
    cat, content, value = SUBTLE[kind]
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pix.set_rect(pix.irect, (250, 0, 0))
    doc[0].insert_image(pymupdf.Rect(0, 0, 1, 1), pixmap=pix)
    img = doc[0].get_images()[0][0]

    def obj(text):
        x = doc.get_new_xref()
        doc.update_object(x, text)
        return x
    ocg = obj("<</Type/OCG/Name(print)/Usage<</Print<</PrintState/ON>>/View<</ViewState/OFF>>>>>>")
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties",
                     f"<</OCGs[{ocg} 0 R]/D<</OFF[{ocg} 0 R]/AS[<</Event/Print/Category[/Print]"
                     f"/OCGs[{ocg} 0 R]>>]>>>>")
    sub = {"{img}": img, "{ocg}": ocg}.get(value) or obj(value)
    dele = obj(f"<</Type/XObject/Subtype/Image/Width 1/Height 1/ColorSpace/DeviceRGB"
               f"/BitsPerComponent 8/Length 3>>")
    doc.update_stream(dele, b"\x01\x02\x03")
    rx = obj(f"<</{cat}<</SUB {sub} 0 R>>/XObject<</SUB {sub} 0 R/ImD {dele} 0 R>>>>"
             if cat == "XObject" else f"<</{cat}<</SUB {sub} 0 R>>/XObject<</ImD {dele} 0 R>>>>")
    for i, c in enumerate(["q 50 0 0 50 0 0 cm /ImD Do Q", content, ""]):
        cx = obj("<<>>")
        doc.update_stream(cx, c.encode())
        doc.xref_set_key(doc[i].xref, "Contents", f"{cx} 0 R")
        doc.xref_set_key(doc[i].xref, "Resources", f"{rx} 0 R")
    doc.save(tmp_path / "src.pdf", garbage=3)
    doc.close()
    looks = page_looks(tmp_path / "src.pdf", zoom=200 / 72)
    real = resource_prune.names_in
    monkeypatch.setattr(resource_prune, "names_in",
                        lambda pdf, s: [(c, n) for c, n in real(pdf, s) if n != "SUB"])
    d = PdfDocument.open(tmp_path / "src.pdf")
    d.delete_pages([0])
    with caplog.at_level(logging.WARNING):
        d.save(tmp_path / "out.pdf")
    d.close()
    assert page_looks(tmp_path / "out.pdf", zoom=200 / 72) == looks[1:]
    assert any("changed how" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 保存を繰り返すとき: 変わっていないページは読み直さず、描き比べもしない

def test_repeated_saves_reuse_verified_pages(tmp_path, monkeypatch):
    src = make_shared_resources_pdf(tmp_path / "src.pdf", "shared")
    looks = page_looks(src)
    calls = []
    real = resource_prune._fingerprint
    monkeypatch.setattr(resource_prune, "_fingerprint", lambda doc, i: calls.append(i) or real(doc, i))
    d = PdfDocument.open(src)
    d.delete_pages([2])
    d.save(tmp_path / "a.pdf")
    assert len(calls) == 2 * 4  # 残る 4 ページを、取り除く前と後で
    calls.clear()
    d.rotate_by([1], 90)
    d.save(tmp_path / "b.pdf")
    assert sorted(calls) == [1, 1]  # 回したページだけ
    calls.clear()
    d.save(tmp_path / "c.pdf")
    assert calls == []
    d.delete_pages([0])  # 外すページが増えた: 取り除くものが変わるので確かめ直す
    d.save(tmp_path / "d.pdf")
    assert len(calls) == 2 * 3
    d.close()
    _assert_page3_gone(tmp_path / "d.pdf")
    b = page_looks(tmp_path / "b.pdf")  # 2 ページ目は回してある
    assert b[0] == looks[0] and b[2:] == [looks[3], looks[4]]
    assert page_looks(tmp_path / "d.pdf") == [b[1], looks[3], looks[4]]
