"""ページを「外して」「同じオブジェクトのまま戻す」ための低レベル処理。Qt には依存しない。

ページ削除の取り消しを、ページの写しではなく元のオブジェクトで行うためのもの。
写しで戻すと、フォームの部品が別物になり名前が変わる・値が消えるなど、利用者が触っていない
部分が変わってしまう（要件 7 章）。そこで削除では
  - ページをページツリーから外すだけにして、オブジェクトは文書の中に残す
  - 外すページのフォーム部品を AcroForm（Fields / 親の Kids / CO）から外し、外した位置を控える
  - PyMuPDF が書き換える、残るページの /Annots としおりの項目を、書き換え前の形で控える
を行い、取り消しではその逆をする。

前提: 外したオブジェクトが戻すまで文書に残っていること。生きている文書で不要オブジェクトの
掃除（tobytes の garbage）をすると消えてしまうので、保存では掃除を写しの側で行う
（PdfDocument.to_bytes）。

PyMuPDF の公開 API にない操作（配列の途中への出し入れ、既存ページオブジェクトのページツリー
への挿入）は、PyMuPDF 同梱の mupdf 低レベル API で行う。PyMuPDF の内部関数
_as_pdf_document と _reset_page_refs はこのモジュールでだけ使う。
"""
from dataclasses import dataclass, field

import pymupdf
from pymupdf import mupdf

_FIELDS = 0  # 外した配列の持ち主: AcroForm/Fields
_CO = -1     # 外した配列の持ち主: AcroForm/CO（計算順）


def _pdf(doc: pymupdf.Document):
    return pymupdf._as_pdf_document(doc)


@dataclass(frozen=True)
class _Removal:
    owner: int  # _FIELDS / _CO / 親フィールドの xref（その /Kids から外した）
    index: int
    xref: int
    gen: int


@dataclass
class DetachedPages:
    """外したページの控え。PdfDocument.delete_pages が返し、restore_pages が使う。"""
    indices: list[int]                      # 削除前のページ番号（昇順）
    page_xrefs: list[int]                   # indices と同じ順のページオブジェクト
    removals: list[_Removal] = field(default_factory=list)   # 外した順
    changed_objects: dict[int, str] = field(default_factory=dict)  # 書き換え前のオブジェクト


def _array(pdf, owner: int):
    acro = mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/AcroForm")
    if owner == _FIELDS:
        return mupdf.pdf_dict_gets(acro, "Fields")
    if owner == _CO:
        return mupdf.pdf_dict_gets(acro, "CO")
    return mupdf.pdf_dict_gets(mupdf.pdf_load_object(pdf, owner), "Kids")


def _find(array, xref: int) -> int:
    for i in range(mupdf.pdf_array_len(array)):
        if mupdf.pdf_to_num(mupdf.pdf_array_get(array, i)) == xref:
            return i
    return -1


def _take(pdf, owner: int, xref: int, out: list[_Removal]) -> bool:
    array = _array(pdf, owner)
    if not mupdf.pdf_is_array(array):
        return False
    i = _find(array, xref)
    if i < 0:
        return False
    out.append(_Removal(owner, i, xref, mupdf.pdf_to_gen(mupdf.pdf_array_get(array, i))))
    mupdf.pdf_array_delete(array, i)
    return True


def remove_form_fields(doc: pymupdf.Document, widget_xrefs: list[int],
                       removals: list[_Removal]) -> list[_Removal]:
    """部品 widget_xrefs を AcroForm から外す。親の Kids が空になったら親も外す
    （他のページに残る部品がある親は残す）。計算順 /CO からも外す。外した位置を removals に
    外した順に足していく（途中で失敗しても、そこまでを restore_form_fields で戻せる）。"""
    pdf = _pdf(doc)
    for node in widget_xrefs:
        while True:
            parent = mupdf.pdf_dict_gets(mupdf.pdf_load_object(pdf, node), "Parent")
            owner = mupdf.pdf_to_num(parent) if mupdf.pdf_is_indirect(parent) else _FIELDS
            if not _take(pdf, owner, node, removals):
                break
            if owner == _FIELDS or mupdf.pdf_array_len(_array(pdf, owner)) > 0:
                break
            node = owner  # 親の Kids が空になった: 親も外す
    removed = {r.xref for r in removals}
    co = _array(pdf, _CO)
    if mupdf.pdf_is_array(co):
        for i in reversed(range(mupdf.pdf_array_len(co))):
            if mupdf.pdf_to_num(mupdf.pdf_array_get(co, i)) in removed:
                _take(pdf, _CO, mupdf.pdf_to_num(mupdf.pdf_array_get(co, i)), removals)
    return removals


def restore_form_fields(doc: pymupdf.Document, removals: list[_Removal]) -> None:
    pdf = _pdf(doc)
    for r in reversed(removals):
        mupdf.pdf_array_insert(_array(pdf, r.owner), mupdf.pdf_new_indirect(pdf, r.xref, r.gen),
                               r.index)


def objects_touched_by_delete(doc: pymupdf.Document, keep: list[int]) -> dict[int, str]:
    """ページ削除で PyMuPDF が書き換えうるオブジェクト（残るページと、その間接参照の /Annots
    配列、しおりの項目）を、今の形で返す。"""
    xrefs = list(doc.get_outline_xrefs())
    for i in keep:
        page_xref = doc[i].xref
        xrefs.append(page_xref)
        kind, value = doc.xref_get_key(page_xref, "Annots")
        if kind == "xref":
            xrefs.append(int(value.split()[0]))
    return {x: doc.xref_object(x, compressed=True) for x in xrefs}


def only_changed(doc: pymupdf.Document, before: dict[int, str]) -> dict[int, str]:
    return {x: text for x, text in before.items() if doc.xref_object(x, compressed=True) != text}


def restore_objects(doc: pymupdf.Document, saved: dict[int, str]) -> None:
    for xref, text in saved.items():
        doc.update_object(xref, text)


INHERITABLE_KEYS = ("Resources", "MediaBox", "CropBox", "Rotate")


def flatten_page_tree(doc: pymupdf.Document) -> None:
    """ページが中間の /Pages ノードから受け継いでいる属性（Resources・MediaBox・CropBox・Rotate）を
    各ページに書き写し、ノードからは消す。

    PyMuPDF の move_page と MuPDF の pdf_insert_page は、ページを別のノードへ移すとき属性を
    書き写さないので、移った先のノードの大きさ・向き・フォントを受け継いでしまう。どのページも
    自分の属性だけで描かれるようにしておけば、どこへ移しても見た目は変わらない。
    ノードから消すのは、新しく足すページ（空白ページ・挿入）が置かれた先の属性を受け継がない
    ようにするため。どのページの見た目も変えない（テストで確かめている）。"""
    pdf = _pdf(doc)
    for i in range(doc.page_count):
        mupdf.pdf_flatten_inheritable_page_items(mupdf.pdf_lookup_page_obj(pdf, i))
    pages_name = mupdf.pdf_new_name("Pages")
    stack = [mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/Pages")]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        num = mupdf.pdf_to_num(node)
        if num in seen:  # 壊れた PDF のループに備える
            continue
        seen.add(num)
        for key in INHERITABLE_KEYS:
            mupdf.pdf_dict_dels(node, key)
        kids = mupdf.pdf_dict_gets(node, "Kids")
        for i in range(mupdf.pdf_array_len(kids)):
            kid = mupdf.pdf_array_get(kids, i)
            if mupdf.pdf_name_eq(mupdf.pdf_dict_gets(kid, "Type"), pages_name):
                stack.append(kid)
    doc._reset_page_refs()


def insert_page_objects(doc: pymupdf.Document, indices: list[int], page_xrefs: list[int]) -> None:
    """外したページオブジェクトを、元のページ番号 indices（昇順）に戻す。"""
    pdf = _pdf(doc)
    for index, xref in zip(indices, page_xrefs):
        mupdf.pdf_insert_page(pdf, index, mupdf.pdf_new_indirect(pdf, xref, 0))
    if pdf.m_internal.rev_page_map:  # ページ番号の対応表は古くなった
        mupdf.ll_pdf_drop_page_tree(pdf.m_internal)
    doc._reset_page_refs()
