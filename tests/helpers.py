"""テスト共通の補助関数。"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def lock_file(path: Path):
    """ファイルを開いたままにする。Windows では開いているファイルは置き換えられず、
    「Acrobat Reader で開いている」状態を再現できる。"""
    f = open(path, "rb")
    try:
        yield f
    finally:
        f.close()


def labels(doc) -> list[int]:
    """各ページの文字 "Page N" から N の列を返す。なければ 0。"""
    import re

    out = []
    with doc.lock:
        for i in range(doc.fitz.page_count):
            m = re.search(r"Page (\d+)", doc.fitz[i].get_text())
            out.append(int(m.group(1)) if m else 0)
    return out


def make_view(qtbot, pdf_path: Path, size=(800, 1000)):
    """文書を開いて PageView に載せ、表示されるまで待つ。後始末は描画サービスを止めてから
    文書を閉じる（RenderService の約束）。"""
    from pdfeditor.core.document import PdfDocument
    from pdfeditor.ui.page_view import PageView
    from pdfeditor.ui.render_service import RenderService

    doc = PdfDocument.open(pdf_path)
    service = RenderService(doc)
    view = PageView()

    def cleanup(_view) -> None:
        view.set_document(None, None)
        service.shutdown()
        doc.close()

    qtbot.addWidget(view, before_close_func=cleanup)
    view.resize(*size)
    view.set_document(doc, service)
    view.show()
    qtbot.waitExposed(view)
    return view


def open_window(qtbot, pdf_path: Path, *more: Path):
    """MainWindow を出して（offscreen）文書を開き、表示されるまで待つ。more を渡すと、同じ
    ウィンドウの別のタブで開く（最後に開いたものが表示中）。後始末ではすべてのタブの変更を
    保存済み扱いにしてから閉じる（閉じる時に保存を尋ねないように）。"""
    from pdfeditor.ui.main_window import MainWindow

    window = MainWindow()

    def cleanup(_window) -> None:
        for tab in window.tabs():
            tab.undo_stack.setClean()

    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    for path in (pdf_path, *more):
        assert window.open_path(path)
    qtbot.waitExposed(window)
    return window


def select(window, indices: list[int]) -> None:
    """サムネイル一覧でページ indices を選ぶ（それ以外の選択は外す）。"""
    window.thumbnails.select_pages(indices)


def page_fields(fitz_doc) -> tuple:
    """各ページのフォーム部品を (フィールド名, 種類, 値) の並びで返す。"""
    return tuple(
        tuple((w.field_name, w.field_type_string, w.field_value) for w in fitz_doc[i].widgets())
        for i in range(fitz_doc.page_count)
    )


def _refs(text: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"(\d+)\s+\d+\s+R", text)]


def form_tree_problems(fitz_doc) -> list[str]:
    """AcroForm/Fields からたどれる部品と、ページ上の部品が一致しなければ、その説明を返す。
    孤立した（どのページにもない）部品・重複・Fields から外れた部品を見つける。"""
    def get(xref: int, key: str) -> tuple[str, str]:
        kind, value = fitz_doc.xref_get_key(xref, key)
        if kind == "xref":  # 間接参照の配列・辞書は中身を読む
            return "resolved", fitz_doc.xref_object(_refs(value)[0], compressed=True)
        return kind, value

    cat = fitz_doc.pdf_catalog()
    kind, acro = fitz_doc.xref_get_key(cat, "AcroForm")
    on_pages = [w.xref for i in range(fitz_doc.page_count) for w in fitz_doc[i].widgets()]
    if kind == "null":
        return [] if not on_pages else ["AcroForm がないのにページに部品がある"]
    acro_xref = _refs(acro)[0] if kind == "xref" else cat
    key = "Fields" if kind == "xref" else "AcroForm/Fields"
    leaves: list[int] = []

    def walk(xref: int) -> None:
        k, kids = get(xref, "Kids")
        if k in ("array", "resolved") and _refs(kids):
            for kid in _refs(kids):
                walk(kid)
        else:
            leaves.append(xref)

    for top in _refs(get(acro_xref, key)[1]):
        walk(top)
    problems = []
    if len(leaves) != len(set(leaves)):
        problems.append(f"Fields に重複: {leaves}")
    if set(leaves) - set(on_pages):
        problems.append(f"どのページにもない部品: {sorted(set(leaves) - set(on_pages))}")
    if set(on_pages) - set(leaves):
        problems.append(f"Fields から外れた部品: {sorted(set(on_pages) - set(leaves))}")
    return problems


def page_labels(fitz_doc) -> tuple:
    """(各ページのページラベル, カタログの /PageLabels 数値ツリーを展開した文字表現)。"""
    import re

    def expand(text: str, depth: int = 0) -> str:  # 間接参照の先を中身で置き換える
        if depth > 20:
            return text
        return re.sub(r"(\d+) 0 R", lambda m: expand(
            fitz_doc.xref_object(int(m.group(1)), compressed=True), depth + 1), text)

    kind, value = fitz_doc.xref_get_key(fitz_doc.pdf_catalog(), "PageLabels")
    tree = expand(value) if kind != "null" else ""
    return tuple(fitz_doc[i].get_label() for i in range(fitz_doc.page_count)), tree


def snapshot(doc) -> tuple:
    """(ラベル列, 各ページの回転, 各ページの大きさ(整数), 各ページのフォーム部品, is_form_pdf,
    ページラベル)。元に戻すの検証用。"""
    with doc.lock:
        n = doc.fitz.page_count
        rotations = tuple(doc.page_rotation(i) for i in range(n))
        sizes = tuple(tuple(round(v) for v in doc.page_size(i)) for i in range(n))
        fields = page_fields(doc.fitz)
        form = doc.fitz.is_form_pdf
        page_label = page_labels(doc.fitz)
    return (tuple(labels(doc)), rotations, sizes, fields, form, page_label)


# ---- ページが Resources を共有する PDF（削除・書き出しで、ほかのページの資源が残らないか）----

SHARED_PW = "user"
SHARED_LAYOUTS = ["shared", "inherited_root", "inherited_mid"]
_COLORS = [(30, 200, 11), (70, 170, 61), (201, 13, 77), (150, 110, 161), (190, 80, 211)]
IMS_COLOR, IMP_COLOR = (5, 250, 5), (250, 250, 5)
# 削除する 3 ページ目（index 2）だけが使う資源の印と、1 ページ目（残す）だけが使う資源の印
PAGE3_MARKS = ["MKform3", "MKpat3", "MKgs3", "MKsh3", "MKoc3", "DEADBE00EF01", "MKbody3"]
PAGE1_MARKS = ["MKform0", "MKpat0", "MKgs0", "MKsh0", "MKoc0", "C0FFEE00AA11"]


def pixel_bytes(color: tuple) -> bytes:
    """その色だけの画像の、展開したストリームに必ず現れるバイト列。"""
    return bytes(color) * 8


def font_chunk() -> bytes:
    """3 ページ目だけが使う埋め込みフォント（Tiro）のプログラムの一部。"""
    import pymupdf

    buf = pymupdf.Font("tiro").buffer
    return bytes(buf[len(buf) // 2:len(buf) // 2 + 64])


def _page_content(i: int) -> bytes:
    base = f"q 100 0 0 100 50 600 cm /Im{i} Do Q BT /F0 20 Tf 50 750 Td (Page {i + 1}) Tj ET\n"
    if i not in (0, 2):
        return base.encode()
    k = "0" if i == 0 else "3"
    form = "Fx" if i == 0 else "Fx3"
    extra = (f"q 1 0 0 1 400 600 cm /{form} Do Q\n"
             f"q /Pattern cs /P{k} scn 300 300 80 80 re f Q\n"
             f"q /GS{k} gs 0 0 1 rg 400 100 50 50 re f Q\n"
             f"q 50 150 100 100 re W n 1 0 0 1 50 150 cm /Sh{k} sh Q\n"
             f"/OC /oc{k} BDC 0 1 0 rg 450 450 40 40 re f EMC\n"
             f"q 20 0 0 20 500 300 cm BI /W 2 /H 1 /CS /CS{k} /BPC 8 ID \n").encode()
    extra += b"\x00\x01 EI Q\n"
    if i == 2:
        extra += b"BT /F0 12 Tf 50 680 Td (MKbody3) Tj ET\nBT /F3 14 Tf 50 650 Td <0024002500260027> Tj ET\n"
    return base.encode() + extra


def make_shared_resources_pdf(path: Path, layout: str, encrypt: bool = False) -> Path:
    """5 ページ。各ページが自分の画像 Im<i> を描き、3 ページ目（index 2）だけがフォーム・
    パターン・ExtGState・シェーディング・レイヤー・色空間（インライン画像）・埋め込みフォントを
    使う。1 ページ目も同じ種類の自分用の資源を使い、フォーム（自分の Resources なし）経由で ImS、
    パターン経由で ImP を描く。資源は layout により:
      shared: 1 つの間接の Resources をすべてのページが参照
      inherited_root: ルートの /Pages の（直接の）Resources を受け継ぐ
      inherited_mid: 中間の /Pages ノード 2 つがそれぞれ同じ中身の Resources を持つ"""
    import pymupdf

    doc = pymupdf.open()
    images = {}
    for i in range(5):
        page = doc.new_page()
        for name, color in [(f"Im{i}", _COLORS[i])] + (
                [("ImS", IMS_COLOR), ("ImP", IMP_COLOR)] if i == 0 else []):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
            pix.set_rect(pix.irect, color)
            images[name] = page.insert_image(pymupdf.Rect(0, 0, 1, 1), pixmap=pix)
    doc[2].insert_font(fontname="F3", fontbuffer=pymupdf.Font("tiro").buffer)
    f3 = [f[0] for f in doc[2].get_fonts() if f[4] == "F3"][0]

    def obj(text: str, stream: bytes | None = None) -> int:
        x = doc.get_new_xref()
        doc.update_object(x, text)
        if stream is not None:
            doc.update_stream(x, stream)
        return x

    helv = obj("<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    fx = obj("<</Type/XObject/Subtype/Form/BBox[0 0 60 60]/MK(MKform0)>>", b"q 60 0 0 60 0 0 cm /ImS Do Q")
    fx3 = obj("<</Type/XObject/Subtype/Form/BBox[0 0 200 60]>>",
              b"BT /F0 12 Tf 0 10 Td (MKform3) Tj ET")
    tiling = "/Type/Pattern/PatternType 1/PaintType 1/TilingType 1/BBox[0 0 20 20]/XStep 20/YStep 20"
    p0 = obj(f"<<{tiling}/MK(MKpat0)>>", b"q 20 0 0 20 0 0 cm /ImP Do Q")
    p3 = obj(f"<<{tiling}/MK(MKpat3)>>", b"1 0 0 rg 0 0 10 10 re f")
    gs0, gs3 = (obj(f"<</Type/ExtGState/ca {a}/MK(MKgs{k})>>") for k, a in (("0", .5), ("3", .3)))
    sh0, sh3 = (obj(f"<</ShadingType 2/ColorSpace/DeviceRGB/Coords[0 0 100 0]/MK(MKsh{k})"
                    f"/Function<</FunctionType 2/Domain[0 1]/C0[{c}]/C1[0 0 1]/N 1>>>>")
                for k, c in (("0", "1 0 0"), ("3", "0 1 0")))
    oc0, oc3 = (obj(f"<</Type/OCG/Name(MKoc{k})>>") for k in ("0", "3"))
    cs0 = obj("[/Indexed/DeviceRGB 1<c0ffee00aa11>]")
    cs3 = obj("[/Indexed/DeviceRGB 1<deadbe00ef01>]")
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties",
                     f"<</OCGs[{oc0} 0 R {oc3} 0 R]/D<</ON[{oc0} 0 R {oc3} 0 R]"
                     f"/Order[{oc0} 0 R {oc3} 0 R]>>>>")
    xobjects = " ".join(f"/{n} {x} 0 R" for n, x in images.items())
    res = (f"<</Font<</F0 {helv} 0 R/F3 {f3} 0 R>>/XObject<<{xobjects}/Fx {fx} 0 R/Fx3 {fx3} 0 R>>"
           f"/Pattern<</P0 {p0} 0 R/P3 {p3} 0 R>>/ExtGState<</GS0 {gs0} 0 R/GS3 {gs3} 0 R>>"
           f"/Shading<</Sh0 {sh0} 0 R/Sh3 {sh3} 0 R>>/ColorSpace<</CS0 {cs0} 0 R/CS3 {cs3} 0 R>>"
           f"/Properties<</oc0 {oc0} 0 R/oc3 {oc3} 0 R>>/ProcSet[/PDF/Text/ImageC/ImageI]>>")
    pages = [doc[i].xref for i in range(5)]
    for i, px in enumerate(pages):
        doc.xref_set_key(px, "Contents", f"{obj('<<>>', _page_content(i))} 0 R")
        doc.xref_set_key(px, "Resources", "null")
    root = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    if layout == "shared":
        rx = obj(res)
        for px in pages:
            doc.xref_set_key(px, "Resources", f"{rx} 0 R")
    elif layout == "inherited_root":
        doc.xref_set_key(root, "Resources", res)
    else:
        kids = []
        for group in (pages[:3], pages[3:]):
            node = obj(f"<</Type/Pages/Parent {root} 0 R/Count {len(group)}/Resources{res}"
                       f"/Kids[{' '.join(f'{p} 0 R' for p in group)}]>>")
            for px in group:
                doc.xref_set_key(px, "Parent", f"{node} 0 R")
            kids.append(node)
        doc.update_object(root, f"<</Type/Pages/Count 5/Kids[{kids[0]} 0 R {kids[1]} 0 R]>>")
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    doc = pymupdf.open(stream=data, filetype="pdf")
    kw = {}
    if encrypt:
        kw = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=SHARED_PW, owner_pw="owner")
    doc.save(path, **kw)
    doc.close()
    return path


def open_fitz(path_or_bytes, password: str | None = SHARED_PW):
    import pymupdf

    d = pymupdf.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, bytes) \
        else pymupdf.open(path_or_bytes)
    if d.needs_pass:
        assert d.authenticate(password)
    return d


def raw_count(path_or_bytes, needle: bytes | str) -> int:
    """全オブジェクトの文字表現と展開した全ストリームのうち、needle（文字列なら、そのまま・
    16 進の小文字と大文字）を含むものの数。"""
    import binascii

    if isinstance(needle, str):
        raw = needle.encode()
        forms = [raw, binascii.hexlify(raw), binascii.hexlify(raw).upper()]
    else:
        forms = [needle]
    d = open_fitz(path_or_bytes)
    n = 0
    for x in range(1, d.xref_length()):
        data = d.xref_object(x, compressed=True).encode("latin-1", "replace")
        if d.xref_is_stream(x):
            data += d.xref_stream(x) or b""
        n += any(f in data for f in forms)
    d.close()
    return n


def page_looks(path_or_bytes, zoom: float = 1.0) -> list[str]:
    import hashlib

    import pymupdf

    d = open_fitz(path_or_bytes)
    out = [hashlib.md5(p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).samples)
           .hexdigest() for p in d]
    d.close()
    return out


def page_texts(path_or_bytes) -> list[str]:
    d = open_fitz(path_or_bytes)
    out = [p.get_text() for p in d]
    d.close()
    return out


def make_shared_big(path: Path, pages: int = 500, side: int = 120) -> Path:
    """pages ページ（500 で約 20 MB）。各ページが自分の画像（乱数の画素、圧縮が効かない）と
    40 行の文字を描き、すべてのページが 1 つの間接の Resources を参照する。"""
    import random

    import pymupdf

    rnd = random.Random(4)
    doc = pymupdf.open()
    names = []
    for i in range(pages):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, side, side, rnd.randbytes(side * side * 3), False)
        names.append((f"Im{i}", page.insert_image(pymupdf.Rect(0, 0, 1, 1), stream=pix.tobytes("png"))))
    helv = doc.get_new_xref()
    doc.update_object(helv, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    rx = doc.get_new_xref()
    doc.update_object(rx, "<</Font<</F0 %d 0 R>>/XObject<<%s>>>>" % (
        helv, "".join(f"/{n} {x} 0 R" for n, x in names)))
    for i in range(pages):
        body = " ".join(f"(word{j} of page {i}) Tj T*" for j in range(40))
        cx = doc.get_new_xref()
        doc.update_object(cx, "<<>>")
        doc.update_stream(cx, f"q 200 0 0 200 50 550 cm /Im{i} Do Q BT /F0 9 Tf 12 TL 50 500 Td "
                              f"{body} ET".encode())
        doc.xref_set_key(doc[i].xref, "Contents", f"{cx} 0 R")
        doc.xref_set_key(doc[i].xref, "Resources", f"{rx} 0 R")
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path
