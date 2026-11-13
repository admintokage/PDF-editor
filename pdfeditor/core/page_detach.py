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
import re
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
    # 削除で PyMuPDF が行き先を外したしおりの項目（保存の掃除で、削除ページを指していたと分かるように）
    untargeted_outline: frozenset[int] = frozenset()
    # 削除前のページラベル（MuPDF が削除で書き換えるので、元に戻すときにこの形へ戻す）
    page_labels: tuple | None = None


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


def _chained_goto(action) -> bool:
    """action が、/Next に続くアクションのある GoTo か。"""
    return (mupdf.pdf_is_dict(action) and mupdf.pdf_to_name(mupdf.pdf_dict_gets(action, "S")) == "GoTo"
            and not mupdf.pdf_is_null(mupdf.pdf_dict_gets(action, "Next")))


def _items(obj) -> list:
    if mupdf.pdf_is_array(obj):
        return [mupdf.pdf_array_get(obj, i) for i in range(mupdf.pdf_array_len(obj))]
    return []


def revive_chained_actions(copy: pymupdf.Document, originals: dict[int, str]) -> set[int]:
    """保存の写し copy で、ページ削除のときに PyMuPDF が外した「削除ページへの GoTo に /Next が
    続く」アクションを元に戻す（そのあと掃除で、GoTo を外して /Next を継がせる）。
      - しおりの項目: /A（と色 /C）を削除前の形に戻す
      - リンク注釈: 残るページの /Annots から外されたものを、元の位置に戻す
    originals は削除前のオブジェクトの文字表現（DetachedPages.changed_objects を古い順に重ねた
    もの）。戻したしおりの項目の xref を返す。生きている文書には使わない。"""
    pdf = _pdf(copy)
    parsed: dict[int, object] = {}
    scratch: list[int] = []

    def original(x: int):
        if x not in parsed:
            tmp = copy.get_new_xref()  # 文字表現を写しの中で読む（参照の番号は写しと同じ）
            copy.update_object(tmp, originals[x])
            scratch.append(tmp)
            parsed[x] = mupdf.pdf_load_object(pdf, tmp)
        return parsed[x]

    revived: set[int] = set()
    for x in list(originals):
        old, cur = original(x), mupdf.pdf_load_object(pdf, x)
        if not mupdf.pdf_is_dict(old) or not mupdf.pdf_is_dict(cur):
            continue
        if not mupdf.pdf_is_null(mupdf.pdf_dict_gets(old, "Title")):  # しおりの項目
            action = mupdf.pdf_dict_gets(old, "A")
            if _chained_goto(action) and mupdf.pdf_is_null(mupdf.pdf_dict_gets(cur, "A")):
                mupdf.pdf_dict_puts(cur, "A", mupdf.pdf_deep_copy_obj(
                    mupdf.pdf_dict_get(old, mupdf.pdf_new_name("A"))))
                color = mupdf.pdf_dict_gets(old, "C")
                if mupdf.pdf_is_null(color):
                    mupdf.pdf_dict_dels(cur, "C")
                else:
                    mupdf.pdf_dict_puts(cur, "C", mupdf.pdf_deep_copy_obj(color))
                revived.add(x)
        elif mupdf.pdf_to_name(mupdf.pdf_dict_gets(old, "Type")) == "Page":
            annots = mupdf.pdf_dict_get(old, mupdf.pdf_new_name("Annots"))
            if mupdf.pdf_is_indirect(annots) and mupdf.pdf_to_num(annots) in originals:
                annots = original(mupdf.pdf_to_num(annots))
            current = mupdf.pdf_dict_gets(cur, "Annots")
            now = {mupdf.pdf_to_num(a) for a in _items(current)}
            back = {mupdf.pdf_to_num(a) for a in _items(annots)
                    if mupdf.pdf_to_num(a) not in now
                    and mupdf.pdf_to_name(mupdf.pdf_dict_gets(a, "Subtype")) == "Link"
                    and _chained_goto(mupdf.pdf_dict_gets(a, "A"))}
            if not back:
                continue
            order = [mupdf.pdf_to_num(a) for a in _items(annots)
                     if mupdf.pdf_to_num(a) in now | back]
            order += [n for n in (mupdf.pdf_to_num(a) for a in _items(current)) if n not in order]
            if not mupdf.pdf_is_array(current):
                current = mupdf.pdf_new_array(pdf, len(order))
                mupdf.pdf_dict_puts(cur, "Annots", current)
            while mupdf.pdf_array_len(current):
                mupdf.pdf_array_delete(current, 0)
            for n in order:
                mupdf.pdf_array_push(current, mupdf.pdf_new_indirect(pdf, n, 0))
    for tmp in scratch:  # 読むためだけのオブジェクトは空にする（保存の掃除で落ちる）
        copy.update_object(tmp, "null")
    return revived


PageLabels = tuple[str, dict[int, str]] | None  # (カタログの値, 中の間接オブジェクト) / なし


def page_labels_state(doc: pymupdf.Document) -> PageLabels:
    """カタログの /PageLabels（数値ツリー）を、中の間接オブジェクトごと今の形で控える。
    MuPDF はページの削除・挿入でこのツリーを書き換える（範囲をずらす、なかったツリーを作る）。"""
    kind, value = doc.xref_get_key(doc.pdf_catalog(), "PageLabels")
    if kind == "null":
        return None
    objects: dict[int, str] = {}
    todo = [value]
    while todo and len(objects) < 10000:
        for m in re.finditer(r"(?<![\w.])(\d+)\s+\d+\s+R\b", todo.pop()):
            x = int(m.group(1))
            if x not in objects and 0 < x < doc.xref_length():
                objects[x] = doc.xref_object(x, compressed=True)
                todo.append(objects[x])
    return value, objects


def restore_page_labels(doc: pymupdf.Document, state: PageLabels) -> None:
    for xref, text in (state[1] if state else {}).items():
        doc.update_object(xref, text)
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", state[0] if state else "null")


def drop_shadowed_labels(doc: pymupdf.Document) -> None:
    """/PageLabels の（入れ子でない）/Nums で同じページ番号の範囲が重なったら、後のもの（MuPDF が
    使う方）だけを残す。挿入したページを削除すると MuPDF の書き換えで重なる。ラベルは変わらない。"""
    labels = mupdf.pdf_dict_getp(mupdf.pdf_trailer(_pdf(doc)), "Root/PageLabels/Nums")
    if not mupdf.pdf_is_array(labels):
        return
    n = mupdf.pdf_array_len(labels)
    for i in reversed(range(0, n - 3, 2)):
        a, b = mupdf.pdf_array_get(labels, i), mupdf.pdf_array_get(labels, i + 2)
        if mupdf.pdf_is_int(a) and mupdf.pdf_is_int(b) and mupdf.pdf_to_int(a) == mupdf.pdf_to_int(b):
            mupdf.pdf_array_delete(labels, i + 1)
            mupdf.pdf_array_delete(labels, i)


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
