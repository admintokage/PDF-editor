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
