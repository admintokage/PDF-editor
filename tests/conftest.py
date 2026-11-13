"""全テスト共通の PDF fixture。すべて pymupdf でその場で生成する。"""
from pathlib import Path

import pymupdf
import pytest


def _text_page(doc: pymupdf.Document, number: int, width=595, height=842) -> pymupdf.Page:
    page = doc.new_page(width=width, height=height)
    page.insert_text((72, 100), f"Page {number}", fontsize=24)
    return page


def make_big(path: Path, pages: int) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        _text_page(doc, i + 1)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def simple_pdf(tmp_path) -> Path:
    return make_big(tmp_path / "simple.pdf", 3)


@pytest.fixture
def mixed_size_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    _text_page(doc, 1, 595, 842)   # A4 縦
    _text_page(doc, 2, 842, 595)   # A4 横
    _text_page(doc, 3, 516, 729)   # B5
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def toc_links_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    for i in range(5):
        _text_page(doc, i + 1)
    doc.set_toc([[1, "第1章", 1], [2, "第1.1節", 2], [1, "第2章", 4]])
    doc[1].insert_link({
        "kind": pymupdf.LINK_GOTO,
        "from": pymupdf.Rect(72, 150, 200, 180),
        "page": 3,
        "to": pymupdf.Point(0, 0),
    })
    doc[2].add_text_annot(pymupdf.Point(300, 300), "注釈")
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = "name"
    widget.field_value = "入力値"
    widget.rect = pymupdf.Rect(72, 200, 300, 230)
    doc[0].add_widget(widget)
    path = tmp_path / "toc_links.pdf"
    doc.save(path)
    doc.close()
    return path


def _add_text_field(page: pymupdf.Page, name: str, value: str, y: float = 200) -> int:
    widget = pymupdf.Widget()
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.field_name = name
    widget.field_value = value
    widget.rect = pymupdf.Rect(72, y, 300, y + 30)
    return page.add_widget(widget).xref


def make_form(path: Path) -> Path:
    """5 ページ（"Page N"）のフォーム PDF。実際の PDF に近づけるため AcroForm は間接参照にする。
    - 1 ページ目: テキスト "name"="val-name"、注釈とリンク（URI）
    - 2 ページ目: チェックボックス "chk"=オン
    - 3 ページ目と 5 ページ目: 1 つのフィールド "shared"="共有" のウィジェット 2 つ（親と Kids）
    - 4 ページ目: テキスト "other"="val-other"
    """
    doc = pymupdf.open()
    for i in range(5):
        _text_page(doc, i + 1)
    _add_text_field(doc[0], "name", "val-name")
    doc[0].add_text_annot(pymupdf.Point(400, 300), "注釈")
    doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 10, 50, 50),
                        "uri": "https://example.com/"})
    box = pymupdf.Widget()
    box.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    box.field_name = "chk"
    box.field_value = True
    box.rect = pymupdf.Rect(72, 300, 90, 318)
    doc[1].add_widget(box)
    kid_a = _add_text_field(doc[2], "shared_a", "共有")
    kid_b = _add_text_field(doc[4], "shared_b", "共有", y=400)
    _add_text_field(doc[3], "other", "val-other")

    # shared_a / shared_b を、親 "shared" の Kids にまとめる
    cat = doc.pdf_catalog()
    fields = doc.xref_get_key(cat, "AcroForm/Fields")[1]
    parent = doc.get_new_xref()
    doc.update_object(parent, f"<</T(shared)/FT/Tx/V(共有)/Kids[{kid_a} 0 R {kid_b} 0 R]>>")
    for kid in (kid_a, kid_b):
        doc.xref_set_key(kid, "Parent", f"{parent} 0 R")
        doc.xref_set_key(kid, "T", "null")
        doc.xref_set_key(kid, "FT", "null")
        doc.xref_set_key(kid, "V", "null")
    fields = fields.replace(f"{kid_a} 0 R", f"{parent} 0 R").replace(f" {kid_b} 0 R", "")
    acro = doc.get_new_xref()
    doc.update_object(acro, f"<</Fields{fields}/DA(/Helv 0 Tf 0 g)>>")
    doc.xref_set_key(cat, "AcroForm", f"{acro} 0 R")
    doc.save(path)
    doc.close()
    return path


def _write_raw_pdf(path: Path, objs: dict[int, str | bytes], root: int = 1) -> Path:
    """オブジェクト番号 → 中身（str は辞書など、bytes はストリームの中身）から PDF を手で組む。"""
    out = b"%PDF-1.7\n"
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        body = objs[num]
        if isinstance(body, bytes):
            out += (f"{num} 0 obj\n<</Length {len(body)}>>stream\n".encode()
                    + body + b"\nendstream\nendobj\n")
        else:
            out += f"{num} 0 obj\n{body}\nendobj\n".encode()
    size = max(objs) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for num in range(1, size):
        out += (f"{offsets[num]:010d} 00000 n \n" if num in offsets else "0000000000 65535 f \n").encode()
    out += f"trailer\n<</Size {size}/Root {root} 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


def make_nested_tree(path: Path) -> Path:
    """中間の /Pages ノードから属性を受け継ぐページだけでできた 6 ページの PDF。
    ページ自身は MediaBox・Rotate・Resources・CropBox を持たない。
      ルート (MediaBox 595x842, Helvetica)
        ├ A: Rotate 90, MediaBox 300x200, CropBox 10 10 290 190 → ページ 1, 2
        ├ B: MediaBox 400x600, Courier → ページ 3, 4
        │    └ (B の中の C: Rotate 180) → ページ 5
        └ ページ 6（ルートから受け継ぐ）
    """
    helv = "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"
    cour = "<</Type/Font/Subtype/Type1/BaseFont/Courier>>"
    objs: dict[int, str | bytes] = {
        1: "<</Type/Catalog/Pages 2 0 R>>",
        2: f"<</Type/Pages/Kids[3 0 R 4 0 R 15 0 R]/Count 6/MediaBox[0 0 595 842]"
           f"/Resources<</Font<</F1 {helv}>>>>>>",
        3: f"<</Type/Pages/Parent 2 0 R/Kids[10 0 R 11 0 R]/Count 2/Rotate 90"
           f"/MediaBox[0 0 300 200]/CropBox[10 10 290 190]/Resources<</Font<</F1 {helv}>>>>>>",
        4: f"<</Type/Pages/Parent 2 0 R/Kids[12 0 R 13 0 R 5 0 R]/Count 3/MediaBox[0 0 400 600]"
           f"/Resources<</Font<</F1 {cour}>>>>>>",
        5: "<</Type/Pages/Parent 4 0 R/Kids[14 0 R]/Count 1/Rotate 180>>",
    }
    parents = {10: 3, 11: 3, 12: 4, 13: 4, 14: 5, 15: 2}
    for n, (page, parent) in enumerate(parents.items(), start=1):
        objs[page] = f"<</Type/Page/Parent {parent} 0 R/Contents {page + 10} 0 R>>"
        objs[page + 10] = (f"BT /F1 24 Tf 20 40 Td (Page {n}) Tj ET "
                           f"0 0 1 rg {5 * n} 5 30 30 re f").encode()
    return _write_raw_pdf(path, objs)


@pytest.fixture
def nested_pdf(tmp_path) -> Path:
    return make_nested_tree(tmp_path / "nested.pdf")


@pytest.fixture
def form_pdf(tmp_path) -> Path:
    return make_form(tmp_path / "form.pdf")


@pytest.fixture
def user_pw_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    _text_page(doc, 1)
    path = tmp_path / "user_pw.pdf"
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.close()
    return path


@pytest.fixture
def owner_pw_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    _text_page(doc, 1)
    path = tmp_path / "owner_pw.pdf"
    doc.save(
        path,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="",
        owner_pw="owner",
        permissions=pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT,
    )
    doc.close()
    return path


@pytest.fixture
def signed_flag_pdf(tmp_path) -> Path:
    doc = pymupdf.open()
    _text_page(doc, 1)
    # AcroForm を作り SigFlags=3 を立てる（実際の署名は不要）
    root = doc.pdf_catalog()
    doc.xref_set_key(root, "AcroForm", "<</Fields[]/SigFlags 3>>")
    path = tmp_path / "signed_flag.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def sigflags_1_pdf(tmp_path) -> Path:
    """SigFlags=1（SignaturesExist だけ）。署名済みとして扱う。"""
    doc = pymupdf.open()
    _text_page(doc, 1)
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm", "<</Fields[]/SigFlags 1>>")
    path = tmp_path / "sigflags_1.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def big_pdf(tmp_path):
    def factory(pages: int) -> Path:
        return make_big(tmp_path / f"big_{pages}.pdf", pages)
    return factory


@pytest.fixture(scope="session")
def big_pdf_20mb(tmp_path_factory) -> Path:
    """100 ページ、各ページに乱数のノイズ画像を貼った約 20MB の PDF（圧縮が効かない）。"""
    import random

    rng = random.Random(20)
    side = 256  # 256x256 RGB = 196,608 バイト × 100 ページ ≒ 20MB
    doc = pymupdf.open()
    for i in range(100):
        page = _text_page(doc, i + 1)
        pix = pymupdf.Pixmap(pymupdf.csRGB, side, side, rng.randbytes(side * side * 3), False)
        page.insert_image(pymupdf.Rect(72, 150, 523, 601), pixmap=pix)
    path = tmp_path_factory.mktemp("big20") / "big_20mb.pdf"
    doc.save(path)
    doc.close()
    return path
