"""保存する写しから、削除したページにだけ属するものを取り除く。Qt には依存しない。

ページを削除しても、名前付きの行き先・OpenAction・構造ツリー（タグ付き PDF）・しおり・記事の
スレッド・レイヤー（OCG）・注釈の /P などがそのページやその注釈を指していると、不要オブジェクトの
掃除（garbage）でも消えず、削除したはずの本文・注釈・フォームの値・代替テキストなどが保存した
ファイルに残ってしまう。

そこで保存の写しで（生きている文書は元に戻すために触らない）、アプリが外したページ
（PdfDocument が控えているもの。テンプレートなど、もともとページツリーの外にあるページは対象外）
について:
  0. 残るページと共有している Resources から、残るページが使わない削除ページの資源（画像・
     フォントなど）の項目を外す（resource_prune。見た目が変わらないことを描いて確かめる）。
     同じページオブジェクトがページツリーにまだあれば（2 回あって 1 つだけ削除）、消さない。
  1. 消すもの: そのページ、その注釈（残るページと共有していないもの）、行き先がそのページの
     行き先・GoTo アクション（間接オブジェクトのもの）。行き先を失った名前付きの行き先の名前を
     名前で指すもの（/Dest・GoTo の /D）も行き先を失ったものとして扱う
  2. 構造ツリー: 中身（MCID・MCR・OBJR。/Pg は親から受け継ぐ）がすべて削除ページにある要素を、
     子孫ごと消す。残るページの中身がある要素は残し、削除ページの中身の子だけ外す。
     ParentTree からは、削除ページとその注釈の組を外す。
  3. しおり: 削除ページを指す項目は、残るページを指す（または行き先が削除ページでない）子孫が
     なければ子孫ごと消す。あれば項目は残して行き先だけ外す。
  4. 記事のスレッド: 削除ページのビーズを外してつなぎ直す。ビーズがなくなったスレッドは消す。
  5. レイヤー: 削除ページ（とその注釈）からしか使われていない OCG を /OCProperties から外す。
  6. 消すもの以外のすべてのオブジェクトから、消すものへの参照を取り除く（辞書ならキーごと、
     配列なら null。名前ツリー・数値ツリーでは組ごと。行き先を失った行き先・GoTo も同じ。
     /Next のある GoTo は、外して /Next のアクションで置き換える）。名前ツリー・数値ツリーの
     /Limits は残った鍵から数え直し、空になった節とツリーは外す。
  7. トレーラーからたどれるオブジェクトに、消すものへの参照が残っていないことを確かめる。
その後 garbage 付きで書けば、どこからも参照されないものは落ちる。
7 で残っていたら UnscrubbableError を投げ、保存をやめる（中身が残ったファイルは書かない）。

参照の書き換えはすべて mupdf のオブジェクト API で、辞書・配列・間接参照を型として扱って行う。
文字列とストリームの中身には触れない（文字列の中の「8 0 R」という文字は参照ではない）。
オブジェクトの文字表現（xref_object）は、調べるオブジェクトを絞り込むためにだけ使う。
"""
import re
from collections.abc import Iterable

import pymupdf
from pymupdf import mupdf

from pdfeditor.core import resource_prune

# 文字表現の中の参照らしきもの。文字列の中の文字にも当たるので、絞り込み（多めに拾う）にだけ使う。
_REF_TEXT = re.compile(r"(?<![\w.])(\d+)\s+\d+\s+R\b")
_NULL = mupdf.PdfObj()  # mupdf では null は NULL ポインタ
_MAX_DEPTH = 400        # 構造ツリー・しおりの深さの上限（壊れたファイルの無限の入れ子に備える）


class UnscrubbableError(Exception):
    """削除したページへの参照を取り除ききれなかった（または確かめられなかった）。"""


def _num(obj) -> int:
    return mupdf.pdf_to_num(obj) if mupdf.pdf_is_indirect(obj) else 0


def _name(obj) -> str:
    return mupdf.pdf_to_name(obj) if mupdf.pdf_is_name(obj) else ""


def _get(obj, key: str):
    return mupdf.pdf_dict_gets(obj, key)


def _items(obj) -> list:
    """配列なら要素の列、それ以外（単独の値）なら [値]、null なら []。"""
    if mupdf.pdf_is_array(obj):
        return [mupdf.pdf_array_get(obj, i) for i in range(mupdf.pdf_array_len(obj))]
    return [] if mupdf.pdf_is_null(obj) else [obj]


def _is_container(obj) -> bool:
    return not mupdf.pdf_is_indirect(obj) and (mupdf.pdf_is_dict(obj) or mupdf.pdf_is_array(obj))


def _direct_refs(obj, skip_key: str | None = None, top: bool = True) -> list[int]:
    """obj（の直接の中身）にある間接参照の番号。skip_key は一番外側の辞書でだけ飛ばす。
    top=False なら、obj そのものが間接参照ならその番号も入れる。"""
    out: list[int] = []
    stack = [(obj, top)]
    while stack:
        o, top = stack.pop()
        if mupdf.pdf_is_indirect(o) and not top:
            out.append(mupdf.pdf_to_num(o))
        elif mupdf.pdf_is_dict(o):
            for i in range(mupdf.pdf_dict_len(o)):
                if top and skip_key and _name(mupdf.pdf_dict_get_key(o, i)) == skip_key:
                    continue
                stack.append((mupdf.pdf_dict_get_val(o, i), False))
        elif mupdf.pdf_is_array(o):
            stack.extend((mupdf.pdf_array_get(o, i), False) for i in range(mupdf.pdf_array_len(o)))
    return out


class _Scrub:
    def __init__(self, doc: pymupdf.Document, pages: set[int], untargeted_outline: set[int],
                 memo: resource_prune.Memo | None = None):
        self.doc = doc
        self.memo = memo
        self.pdf = pymupdf._as_pdf_document(doc)
        self.size = doc.xref_length()
        self.pages = pages
        self.untargeted_outline = untargeted_outline
        self.removed: set[int] = set(pages)
        self.dead_dests: set[int] = set()   # 行き先を失った、間接オブジェクトの行き先・GoTo
        self.catalog = _num(_get(mupdf.pdf_trailer(self.pdf), "Root"))
        self.kept_pages: set[int] = set()
        self.kept_annots: set[int] = set()
        self.removed_annots: set[int] = set()
        self.text_refs: dict[int, set[int]] = {}
        self.seen_elems: set[int] = set()      # 構造ツリーで見た要素
        self.live_memo: dict[int, bool] = {}   # しおりの項目を残すか
        self.outline_seen: set[int] = set()    # しおりで組み直した項目
        self.layer_candidates: set[int] = set()  # 削除ページから使う OCG
        self.dead_names: set[str] = set()      # 行き先を失った名前付きの行き先の名前
        self.spliced: set[int] = set()         # 行き先を失い /Next を継ぐ、間接オブジェクトの GoTo
        self.dest_holders: set[int] = set()    # 名前で行き先を指しうるオブジェクト（絞り込み用）

    # ---- 下ごしらえ ----

    def load(self, xref: int):
        return mupdf.pdf_load_object(self.pdf, xref)

    def check_pages(self) -> None:
        for i in range(mupdf.pdf_count_pages(self.pdf)):
            self.kept_pages.add(mupdf.pdf_to_num(mupdf.pdf_lookup_page_obj(self.pdf, i)))
        for x in self.pages:
            if not 0 < x < self.size or _name(_get(self.load(x), "Type")) != "Page":
                raise UnscrubbableError(f"object {x} is not a detached page")
        # 同じページオブジェクトがページツリーに 2 回あり、その 1 つだけを削除した: まだ使っている
        self.pages -= self.kept_pages
        self.removed = set(self.pages)

    def read_text_refs(self) -> None:
        """各オブジェクトの文字表現にある参照らしき番号（絞り込み用）。"""
        for x in range(1, self.size):
            try:
                text = self.doc.xref_object(x, compressed=True)
            except Exception:  # noqa: BLE001 - 読めないものは参照なし（確かめる段で読み直す）
                continue
            refs = {int(m) for m in _REF_TEXT.findall(text)}
            if refs:
                self.text_refs[x] = refs
            if "/D" in text:
                self.dest_holders.add(x)

    def candidates(self, numbers: set[int]) -> list[int]:
        """numbers のどれかを参照しているかもしれない、消すもの以外のオブジェクト。"""
        return [x for x, refs in self.text_refs.items()
                if x not in self.removed and not refs.isdisjoint(numbers)]

    def collect_annotations(self) -> None:
        for x in self.kept_pages:
            self.kept_annots.update(_num(a) for a in _items(_get(self.load(x), "Annots")))
        for x in self.pages:
            for a in _items(_get(self.load(x), "Annots")):
                if _num(a) and _num(a) not in self.kept_annots:
                    self.removed_annots.add(_num(a))
        self.removed |= self.removed_annots

    # ---- 共有された Resources ----

    def note_layers(self) -> None:
        """削除ページ（とその注釈）から使う OCG を、Resources を取り除く前に控える
        （取り除いた後は、共有の Resources からはたどれなくなる）。"""
        props = _get(self.load(self.catalog), "OCProperties")
        self.layer_candidates = self.layers_used_by(self.pages | self.removed_annots)             if mupdf.pdf_is_dict(props) else set()

    def resource_candidates(self) -> set[int]:
        """削除ページの Resources（受け継いだものも含む）の各項目が参照するオブジェクト。"""
        out: set[int] = set()
        for x in self.pages:
            res = mupdf.pdf_dict_get_inheritable(self.load(x), mupdf.pdf_new_name("Resources"))
            for cat in resource_prune.CATEGORIES:
                d = _get(res, cat)
                if mupdf.pdf_is_dict(d):
                    for i in range(mupdf.pdf_dict_len(d)):
                        out.update(_direct_refs(mupdf.pdf_dict_get_val(d, i), None, top=False))
        return out - self.kept_pages

    def prune_shared_resources(self) -> None:
        """残るページと共有している Resources から、残るページが使っていない、削除ページの
        資源の項目を取り除く（resource_prune。見た目が変わらないことを確かめる）。"""
        resource_prune.prune_unused_resources(self.doc, self.resource_candidates(),
                                              self.pages | self.removed_annots, self.text_refs,
                                              self.memo)

    # ---- 行き先 ----

    def dead(self, value) -> bool:
        """value（辞書の値・配列の要素）が、消すものか、行き先を失った行き先・GoTo か。"""
        if mupdf.pdf_is_indirect(value):
            return mupdf.pdf_to_num(value) in self.removed
        if mupdf.pdf_is_array(value):
            return self.dead_dest_array(value)
        if mupdf.pdf_is_dict(value):
            return self.dead_goto(value)
        return False

    def dead_dest_value(self, dest) -> bool:
        """行き先（/Dest や GoTo の /D の値）が、削除ページか、なくなった名前付きの行き先を指すか。"""
        if mupdf.pdf_is_indirect(dest):
            num = mupdf.pdf_to_num(dest)
            if num in self.dead_dests or num in self.pages:
                return True
        if mupdf.pdf_is_array(dest):
            return self.dead_dest_array(dest)
        return self.dead_name(dest)

    def dead_name(self, value) -> bool:
        """value（文字列・名前）が、行き先を失った名前付きの行き先の名前か。"""
        if mupdf.pdf_is_string(value):
            return mupdf.pdf_to_text_string(value) in self.dead_names
        return mupdf.pdf_is_name(value) and mupdf.pdf_to_name(value) in self.dead_names

    def dead_dest_array(self, array) -> bool:
        """[削除ページ /Fit ...] の形の行き先。"""
        return (mupdf.pdf_array_len(array) >= 2
                and _num(mupdf.pdf_array_get(array, 0)) in self.pages
                and mupdf.pdf_is_name(mupdf.pdf_array_get(array, 1)))

    def dead_goto(self, d) -> bool:
        """/D が行き先を失った GoTo アクション、または名前付きの行き先の <</D ...>>。
        続くアクション（/Next）があるものは、/D だけ取り除く（ここでは False）。"""
        s = _get(d, "S")
        if (not mupdf.pdf_is_null(s) and _name(s) != "GoTo") or not mupdf.pdf_is_null(_get(d, "Next")):
            return False
        return self.dead_dest_value(_get(d, "D"))

    def spliced_goto(self, d) -> bool:
        """/D が行き先を失い、/Next に続くアクションがある GoTo（外して /Next で置き換える）。"""
        if _num(d) in self.spliced:
            return True
        return (mupdf.pdf_is_dict(d) and _name(_get(d, "S")) == "GoTo"
                and not mupdf.pdf_is_null(_get(d, "Next")) and self.dead_dest_value(_get(d, "D")))

    def splice(self, value, depth: int = 0) -> list | None:
        """value が行き先を失った GoTo で /Next があれば、その代わりに置くアクションの列
        （/Next のうち、さらに外すもの・継ぐものを処理したもの）。そうでなければ None。"""
        if depth > _MAX_DEPTH:
            raise UnscrubbableError("action chain too deep")
        if not self.spliced_goto(value):
            return None
        out = []
        for item in _items(_get(value, "Next")):
            if self.dead(item):
                continue
            sub = self.splice(item, depth + 1)
            out.extend(sub if sub is not None else [item])
        return out

    def single_action(self, actions: list):
        """アクションの列を、1 つしか置けない場所（/A・/OpenAction・/AA の項目）に置く形にする。
        2 つ目以降は 1 つ目の /Next の後ろへつなぐ（実行の順は変わらない）。"""
        if len(actions) == 1:
            return actions[0]
        first = mupdf.pdf_copy_dict(actions[0])
        chain = mupdf.pdf_new_array(self.pdf, len(actions))
        for item in _items(_get(first, "Next")) + actions[1:]:
            mupdf.pdf_array_push(chain, item)
        mupdf.pdf_dict_puts(first, "Next", chain)
        return first

    def note_dead_names(self) -> None:
        """行き先が削除ページの、名前付きの行き先（名前ツリー /Names/Dests と古い /Dests）の名前。"""
        root = mupdf.pdf_trailer(self.pdf)
        stack, seen = [mupdf.pdf_dict_getp(root, "Root/Names/Dests")], set()
        while stack:
            node = stack.pop()
            if not mupdf.pdf_is_dict(node) or (_num(node) and _num(node) in seen):
                continue
            seen.add(_num(node))
            names = _get(node, "Names")
            for i in range(0, mupdf.pdf_array_len(names) - 1 if mupdf.pdf_is_array(names) else 0, 2):
                if self.dead(mupdf.pdf_array_get(names, i + 1)):
                    self.dead_names.add(mupdf.pdf_to_text_string(mupdf.pdf_array_get(names, i)))
            stack.extend(_items(_get(node, "Kids")))
        old = mupdf.pdf_dict_getp(root, "Root/Dests")
        if mupdf.pdf_is_dict(old):
            for i in range(mupdf.pdf_dict_len(old)):
                if self.dead(mupdf.pdf_dict_get_val(old, i)):
                    self.dead_names.add(_name(mupdf.pdf_dict_get_key(old, i)))

    def mark_dead_destinations(self) -> None:
        """行き先が削除ページの、間接オブジェクトの行き先と GoTo を消すものに加える。"""
        while True:
            found, spliced = [], []
            self.note_dead_names()
            todo = set(self.candidates(self.pages | self.dead_dests | self.spliced))
            if self.dead_names:
                todo |= self.dest_holders - self.removed
            for x in todo - self.dead_dests - self.spliced:
                obj = self.load(x)
                if (mupdf.pdf_is_array(obj) and self.dead_dest_array(obj)) or \
                        (mupdf.pdf_is_dict(obj) and self.dead_goto(obj)):
                    found.append(x)
                elif mupdf.pdf_is_dict(obj) and self.spliced_goto(obj):
                    spliced.append(x)
            if not found and not spliced:
                return
            self.dead_dests.update(found)
            self.removed.update(found)
            self.spliced.update(spliced)

    # ---- 構造ツリー ----

    def prune_structure(self) -> None:
        root = mupdf.pdf_dict_getp(mupdf.pdf_trailer(self.pdf), "Root/StructTreeRoot")
        if not mupdf.pdf_is_dict(root):
            return
        self.seen_elems.add(_num(root))
        self.element(root, 0, 0, is_root=True)
        gone_keys = self.parent_tree_keys()
        tree = _get(root, "ParentTree")
        if gone_keys and mupdf.pdf_is_dict(tree):
            self.prune_number_tree(tree, gone_keys, set())

    def element(self, elem, pg: int, depth: int, is_root: bool = False) -> tuple[bool, bool]:
        """構造要素（とルート）の中身が (残るページにあるか, 削除ページにあるか)。
        残る要素からは、削除ページの中身の子を外す。"""
        if depth > _MAX_DEPTH:
            raise UnscrubbableError("structure tree too deep")
        own = _get(elem, "Pg")
        if mupdf.pdf_is_indirect(own):
            pg = mupdf.pdf_to_num(own)
        kept, det = False, _num(own) in self.pages
        k = _get(elem, "K")
        kids = _items(k)
        drop = []
        for i, kid in enumerate(kids):
            kid_kept, kid_det, remove = self.kid(kid, pg, depth)
            kept |= kid_kept
            det |= kid_det
            if remove:
                drop.append(i)
        if not is_root and det and not kept:
            return kept, det  # この要素ごと消える（呼び出し側で）
        if drop:
            for i in drop:
                self.remove_subtree(kids[i])
            if mupdf.pdf_is_array(k):
                for i in reversed(drop):
                    mupdf.pdf_array_delete(k, i)
            else:
                mupdf.pdf_dict_dels(elem, "K")
        return kept, det

    def kid(self, kid, pg: int, depth: int) -> tuple[bool, bool, bool]:
        """/K の子 1 つ: (残るページにあるか, 削除ページにあるか, 外すか)。"""
        if mupdf.pdf_is_int(kid):  # MCID: 要素の（受け継いだ）ページの中身
            return self.on_page(pg)
        if not mupdf.pdf_is_dict(kid):
            return False, False, False
        typ = _name(_get(kid, "Type"))
        own = _get(kid, "Pg")
        page = mupdf.pdf_to_num(own) if mupdf.pdf_is_indirect(own) else pg
        if typ == "MCR":
            return self.on_page(page)
        if typ == "OBJR":
            obj = _num(_get(kid, "Obj"))
            if obj in self.kept_annots or obj in self.kept_pages:
                return True, False, False
            if obj in self.removed:
                return False, True, True
            return self.on_page(page)
        if mupdf.pdf_is_null(_get(kid, "S")):
            return True, False, False  # 知らない形の子は残す
        num = _num(kid)
        if num:
            if num in self.seen_elems:  # 二度目（壊れたファイルの循環など）: 残す側に倒す
                return True, False, False
            self.seen_elems.add(num)
        kept, det = self.element(kid, pg, depth + 1)
        return kept, det, det and not kept

    def on_page(self, page: int) -> tuple[bool, bool, bool]:
        if page in self.pages:
            return False, True, True
        return True, False, False  # 残るページ、またはページが分からない（残す側に倒す）

    def remove_subtree(self, kid) -> None:
        """外す子が構造要素なら、その子孫の構造要素ごと消すものに加える。"""
        stack, seen = [kid], set()
        while stack:
            node = stack.pop()
            num = _num(node)
            if num:
                if num in seen or num in self.kept_pages:
                    continue
                seen.add(num)
            if not mupdf.pdf_is_dict(node) or mupdf.pdf_is_null(_get(node, "S")):
                continue
            if num:
                self.removed.add(num)
            stack.extend(_items(_get(node, "K")))

    def parent_tree_keys(self) -> set[int]:
        """ParentTree から外す番号: 削除ページの /StructParents と、消す注釈の /StructParent。"""
        def key(x: int, name: str) -> int | None:
            v = _get(self.load(x), name)
            return mupdf.pdf_to_int(v) if mupdf.pdf_is_int(v) else None
        gone = {key(x, "StructParents") for x in self.pages}
        gone |= {key(x, "StructParent") for x in self.removed_annots}
        kept = {key(x, "StructParents") for x in self.kept_pages}
        kept |= {key(x, "StructParent") for x in self.kept_annots}
        return gone - kept - {None}

    def prune_number_tree(self, node, keys: set[int], seen: set[int]) -> None:
        if _num(node) in seen:
            return
        seen.add(_num(node))
        nums = _get(node, "Nums")
        if mupdf.pdf_is_array(nums):
            for i in reversed(range(0, mupdf.pdf_array_len(nums) - 1, 2)):
                key = mupdf.pdf_array_get(nums, i)
                if mupdf.pdf_is_int(key) and mupdf.pdf_to_int(key) in keys:
                    mupdf.pdf_array_delete(nums, i + 1)
                    mupdf.pdf_array_delete(nums, i)
        for kid in _items(_get(node, "Kids")):
            self.prune_number_tree(kid, keys, seen)

    # ---- しおり ----

    def prune_outline(self) -> None:
        root = mupdf.pdf_dict_getp(mupdf.pdf_trailer(self.pdf), "Root/Outlines")
        if not mupdf.pdf_is_dict(root):
            return
        self.rebuild_outline(root, 0)

    def children(self, node) -> list:
        out, seen, item = [], set(), _get(node, "First")
        while mupdf.pdf_is_indirect(item) and mupdf.pdf_to_num(item) not in seen:
            seen.add(mupdf.pdf_to_num(item))
            out.append(item)
            item = _get(item, "Next")
        return out

    def target_dead(self, item) -> bool:
        return (_num(item) in self.untargeted_outline or self.dead(_get(item, "Dest"))
                or self.dead_name(_get(item, "Dest")) or self.dead(_get(item, "A")))

    def live(self, item, depth: int = 0) -> bool:
        """項目かその子孫に、削除ページ以外を指す（または行き先のない）項目があるか。"""
        if depth > _MAX_DEPTH:
            raise UnscrubbableError("outline too deep")
        num = _num(item)
        if num not in self.live_memo:
            self.live_memo[num] = True  # 循環に備えて仮に残す
            self.live_memo[num] = not self.target_dead(item) or any(
                self.live(c, depth + 1) for c in self.children(item))
        return self.live_memo[num]

    def rebuild_outline(self, node, depth: int) -> bool:
        """node の子を組み直す。子孫が変わったら True（/Count を数え直す）。"""
        if depth > _MAX_DEPTH or _num(node) in self.outline_seen:
            return False
        self.outline_seen.add(_num(node))
        kids = self.children(node)
        keep = [c for c in kids if self.live(c)]
        changed = len(keep) != len(kids)
        for c in kids:
            if not self.live(c):
                self.remove_outline_subtree(c)
        for c in keep:
            if self.target_dead(c):  # 残るページを指す子孫があるので、項目は残して行き先を外す
                for key in ("Dest", "A"):
                    if self.dead(_get(c, key)) or (key == "Dest" and self.dead_name(_get(c, key))):
                        mupdf.pdf_dict_dels(c, key)
            changed |= self.rebuild_outline(c, depth + 1)
        if len(keep) != len(kids):
            self.relink(node, keep)
        if changed:
            self.recount(node)
        return changed

    def remove_outline_subtree(self, item) -> None:
        stack = [item]
        while stack:
            node = stack.pop()
            if _num(node) in self.removed:
                continue
            self.removed.add(_num(node))
            stack.extend(self.children(node))

    def relink(self, node, keep: list) -> None:
        if not keep:
            for key in ("First", "Last", "Count"):
                mupdf.pdf_dict_dels(node, key)
            return
        mupdf.pdf_dict_puts(node, "First", keep[0])
        mupdf.pdf_dict_puts(node, "Last", keep[-1])
        for i, item in enumerate(keep):
            if i:
                mupdf.pdf_dict_puts(item, "Prev", keep[i - 1])
            else:
                mupdf.pdf_dict_dels(item, "Prev")
            if i + 1 < len(keep):
                mupdf.pdf_dict_puts(item, "Next", keep[i + 1])
            else:
                mupdf.pdf_dict_dels(item, "Next")

    def visible(self, node, depth: int = 0) -> int:
        """node を開いたときに見える子孫の数。"""
        n = 0
        for c in self.children(node):
            n += 1
            if mupdf.pdf_to_int(_get(c, "Count")) > 0 and depth < _MAX_DEPTH:
                n += self.visible(c, depth + 1)
        return n

    def recount(self, node) -> None:
        count = _get(node, "Count")
        if not mupdf.pdf_is_int(count):
            return
        if mupdf.pdf_is_null(_get(node, "First")):
            mupdf.pdf_dict_dels(node, "Count")
            return
        is_root = mupdf.pdf_is_null(_get(node, "Parent"))
        n = self.visible(node)
        mupdf.pdf_dict_puts(node, "Count", mupdf.pdf_new_int(
            n if is_root or mupdf.pdf_to_int(count) > 0 else -n))

    # ---- 記事のスレッド ----

    def prune_threads(self) -> None:
        threads = mupdf.pdf_dict_getp(mupdf.pdf_trailer(self.pdf), "Root/Threads")
        if not mupdf.pdf_is_array(threads):
            return
        for i in reversed(range(mupdf.pdf_array_len(threads))):
            thread = mupdf.pdf_array_get(threads, i)
            beads, seen, bead = [], set(), _get(thread, "F")
            while mupdf.pdf_is_indirect(bead) and mupdf.pdf_to_num(bead) not in seen:
                seen.add(mupdf.pdf_to_num(bead))
                beads.append(bead)
                bead = _get(bead, "N")
            keep = [b for b in beads if _num(_get(b, "P")) not in self.pages]
            if len(keep) == len(beads):
                continue
            self.removed.update(_num(b) for b in beads if _num(_get(b, "P")) in self.pages)
            if not keep:
                mupdf.pdf_array_delete(threads, i)
                if _num(thread):
                    self.removed.add(_num(thread))
                continue
            for j, b in enumerate(keep):
                mupdf.pdf_dict_puts(b, "N", keep[(j + 1) % len(keep)])
                mupdf.pdf_dict_puts(b, "V", keep[j - 1])
            mupdf.pdf_dict_puts(thread, "F", keep[0])
            if mupdf.pdf_is_indirect(thread):
                mupdf.pdf_dict_puts(keep[0], "T", thread)

    # ---- レイヤー（OCG）----

    def prune_layers(self) -> None:
        props = _get(self.load(self.catalog), "OCProperties")
        if not mupdf.pdf_is_dict(props):
            return
        used = self.layer_candidates
        if not used:
            return
        reachable, _ = self.reachable(skip_layers=True)
        gone = used - reachable
        if not gone:
            return
        self.removed.update(gone)
        self.drop_from_layer_lists(props, gone)

    def layers_used_by(self, roots: set[int]) -> set[int]:
        """roots（削除ページとその注釈など）からたどれる OCG。残るページ・ページツリー・
        カタログの先へはたどらない。"""
        found, seen, todo = set(), set(), list(roots)
        while todo:
            x = todo.pop()
            if x in seen or not 0 < x < self.size:
                continue
            seen.add(x)
            obj = self.load(x)
            typ = _name(_get(obj, "Type")) if mupdf.pdf_is_dict(obj) else ""
            if typ == "OCG":
                found.add(x)
                continue
            if x in self.kept_pages or x == self.catalog or typ in ("Pages", "Catalog"):
                continue
            todo.extend(_direct_refs(obj))
        return found

    def drop_from_layer_lists(self, props, gone: set[int]) -> None:
        """/OCProperties の中の配列（/OCGs・/ON・/OFF・/Order・/RBGroups・/Locked・/AS の /OCGs
        など）から、消す OCG を要素ごと外す。"""
        stack, seen = [props], set()
        while stack:
            o = stack.pop()
            num = _num(o)
            if num:
                if num in seen or num in gone:
                    continue
                seen.add(num)
                if _name(_get(o, "Type")) == "OCG":
                    continue
            if mupdf.pdf_is_array(o):
                for i in reversed(range(mupdf.pdf_array_len(o))):
                    item = mupdf.pdf_array_get(o, i)
                    if _num(item) in gone:
                        mupdf.pdf_array_delete(o, i)
                    else:
                        stack.append(item)
            elif mupdf.pdf_is_dict(o):
                for i in reversed(range(mupdf.pdf_dict_len(o))):
                    val = mupdf.pdf_dict_get_val(o, i)
                    if _num(val) in gone:
                        mupdf.pdf_dict_del(o, mupdf.pdf_dict_get_key(o, i))
                    else:
                        stack.append(val)

    # ---- 参照を取り除く・確かめる ----

    def clean(self, obj, key: str = "", depth: int = 0) -> None:
        """obj（の直接の中身）から、消すものへの参照を取り除く。行き先を失った GoTo は、/Next が
        あればそれで置き換える。"""
        if depth > _MAX_DEPTH:
            raise UnscrubbableError("object nested too deeply")
        if mupdf.pdf_is_dict(obj):
            for i in reversed(range(mupdf.pdf_dict_len(obj))):
                k, v = mupdf.pdf_dict_get_key(obj, i), mupdf.pdf_dict_get_val(obj, i)
                name = _name(k)
                if self.dead(v) or (name == "Dest" and self.dead_name(v)):
                    mupdf.pdf_dict_del(obj, k)  # null の値は「キーがない」と同じ
                    continue
                actions = self.splice(v)
                if actions is not None:
                    if not actions:
                        mupdf.pdf_dict_del(obj, k)
                        continue
                    if name == "Next":
                        v = mupdf.pdf_new_array(self.pdf, len(actions))
                        for a in actions:
                            mupdf.pdf_array_push(v, a)
                    else:
                        v = self.single_action(actions)
                    mupdf.pdf_dict_put(obj, k, v)
                    v = mupdf.pdf_dict_get(obj, k)
                if _is_container(v):
                    self.clean(v, name, depth + 1)
                    if name == "Next" and mupdf.pdf_is_array(v) and not mupdf.pdf_array_len(v):
                        mupdf.pdf_dict_del(obj, k)
        elif mupdf.pdf_is_array(obj) and key == "Next":  # アクションの列: 外す・継ぐ
            for i in reversed(range(mupdf.pdf_array_len(obj))):
                v = mupdf.pdf_array_get(obj, i)
                actions = [] if self.dead(v) else self.splice(v)
                if actions is not None:
                    mupdf.pdf_array_delete(obj, i)
                    for a in reversed(actions):
                        mupdf.pdf_array_insert(obj, a, i)
            for i in range(mupdf.pdf_array_len(obj)):
                v = mupdf.pdf_array_get(obj, i)
                if _is_container(v):
                    self.clean(v, "", depth + 1)
        elif mupdf.pdf_is_array(obj):
            n = mupdf.pdf_array_len(obj)
            pairs = key in ("Names", "Nums") and n % 2 == 0 and all(
                (mupdf.pdf_is_string if key == "Names" else mupdf.pdf_is_int)(
                    mupdf.pdf_array_get(obj, i)) for i in range(0, n, 2))
            if pairs:  # 名前ツリー・数値ツリーの葉: 値を失った組は丸ごと外す
                for i in reversed(range(0, n, 2)):
                    if self.dead(mupdf.pdf_array_get(obj, i + 1)):
                        mupdf.pdf_array_delete(obj, i + 1)
                        mupdf.pdf_array_delete(obj, i)
            for i in range(mupdf.pdf_array_len(obj)):
                v = mupdf.pdf_array_get(obj, i)
                if self.dead(v):
                    mupdf.pdf_array_put(obj, i, _NULL)
                elif _is_container(v):
                    self.clean(v, "", depth + 1)

    def reachable(self, skip_layers: bool = False) -> tuple[set[int], dict[int, set[int]]]:
        """トレーラーからたどれるオブジェクト（消すものの先へはたどらない）と、そのうち消すものを
        参照しているもの {xref: 参照している消すもの}。skip_layers ならカタログの /OCProperties を
        たどらない（レイヤーがほかから使われているかを見るため）。"""
        todo = [x for x in _direct_refs(mupdf.pdf_trailer(self.pdf)) if x not in self.removed]
        seen: set[int] = set()
        leftover: dict[int, set[int]] = {}
        while todo:
            x = todo.pop()
            if x in seen or not 0 < x < self.size:
                continue
            seen.add(x)
            skip = "OCProperties" if skip_layers and x == self.catalog else None
            refs = _direct_refs(self.load(x), skip)
            bad = self.removed.intersection(refs)
            if bad:
                leftover[x] = bad
            todo.extend(r for r in refs if r not in seen and r not in self.removed)
        return seen, leftover


def _remove_references(s: _Scrub) -> None:
    """消すもの以外のすべてのオブジェクトから、消すものへの参照を取り除く。行き先を失った名前付きの
    行き先を名前で指すものと、/Next を継ぐ GoTo（間接オブジェクト）を参照するものも直す。"""
    todo = set(s.candidates(s.removed | s.spliced))
    if s.dead_names:
        todo |= s.dest_holders - s.removed
    for x in sorted(todo - s.spliced):  # /Next を継ぐ GoTo そのものは触らない（参照がなくなり落ちる）
        s.clean(s.load(x))


def _limit(node, i: int):
    lim = _get(node, "Limits")
    return mupdf.pdf_array_get(lim, i) if mupdf.pdf_is_array(lim) and mupdf.pdf_array_len(lim) == 2 \
        else None


def _fix_tree(pdf, node, nums: bool, is_root: bool, depth: int, seen: set[int]) -> bool:
    """名前ツリー・数値ツリーの節 node の /Limits を、残っている組から数え直す（組は鍵の順に
    並んでいるので、最初と最後の鍵）。空になった子は /Kids から外す。空でなければ True。"""
    if not mupdf.pdf_is_dict(node):
        return False
    num = _num(node)
    if num:
        if num in seen:  # 壊れたファイルで同じ節に二度来た: そのまま残す
            return True
        seen.add(num)
    if depth > _MAX_DEPTH:
        return True
    key = "Nums" if nums else "Names"
    first = last = None
    leaf = _get(node, key)
    if mupdf.pdf_is_array(leaf):
        n = mupdf.pdf_array_len(leaf)
        if n >= 2:
            first, last = mupdf.pdf_array_get(leaf, 0), mupdf.pdf_array_get(leaf, (n - 2) // 2 * 2)
        else:
            mupdf.pdf_dict_dels(node, key)
    kids = _get(node, "Kids")
    if mupdf.pdf_is_array(kids):
        for i in reversed(range(mupdf.pdf_array_len(kids))):
            if not _fix_tree(pdf, mupdf.pdf_array_get(kids, i), nums, False, depth + 1, seen):
                mupdf.pdf_array_delete(kids, i)
        n = mupdf.pdf_array_len(kids)
        if not n:
            mupdf.pdf_dict_dels(node, "Kids")
        elif first is None:
            first, last = _limit(mupdf.pdf_array_get(kids, 0), 0), _limit(mupdf.pdf_array_get(kids, n - 1), 1)
            if first is None or last is None:  # 子の範囲が分からない: 節はそのまま残す
                return True
    if first is None:
        return False
    if not is_root:
        limits = mupdf.pdf_new_array(pdf, 2)
        mupdf.pdf_array_push(limits, mupdf.pdf_deep_copy_obj(first))
        mupdf.pdf_array_push(limits, mupdf.pdf_deep_copy_obj(last))
        mupdf.pdf_dict_puts(node, "Limits", limits)
    return True


def _fix_trees(s: _Scrub) -> None:
    """組を外したかもしれない名前ツリー（/Names の各ツリーと構造の IDTree）と数値ツリー
    （ParentTree）の /Limits を数え直し、空になった節とツリーを外す。"""
    root = mupdf.pdf_trailer(s.pdf)
    catalog = _get(root, "Root")
    names = _get(catalog, "Names")
    if mupdf.pdf_is_dict(names):
        for i in reversed(range(mupdf.pdf_dict_len(names))):
            if not _fix_tree(s.pdf, mupdf.pdf_dict_get_val(names, i), False, True, 0, set()):
                mupdf.pdf_dict_del(names, mupdf.pdf_dict_get_key(names, i))
        if not mupdf.pdf_dict_len(names):
            mupdf.pdf_dict_dels(catalog, "Names")
    struct = _get(catalog, "StructTreeRoot")
    if mupdf.pdf_is_dict(struct):
        for key, nums in (("IDTree", False), ("ParentTree", True)):
            tree = _get(struct, key)
            if mupdf.pdf_is_dict(tree) and not _fix_tree(s.pdf, tree, nums, True, 0, set()):
                mupdf.pdf_dict_dels(struct, key)


def scrub_detached_pages(doc: pymupdf.Document, pages: Iterable[int],
                         untargeted_outline: Iterable[int] = (),
                         memo: resource_prune.Memo | None = None) -> int:
    """写し doc から、外したページ pages（xref）にだけ属するものを取り除く。取り除いたページ数を
    返す。untargeted_outline は、削除のときに行き先を外したしおりの項目（xref）。
    取り除ききれなければ UnscrubbableError。memo は、生きている文書ごとの資源の取り除きの控え
    （resource_prune.Memo。前の保存の結果を使い回す）。"""
    pages = set(pages)
    if not pages:
        return 0
    s = _Scrub(doc, pages, set(untargeted_outline), memo)
    s.check_pages()
    if not s.pages:
        return 0
    s.read_text_refs()
    s.collect_annotations()
    s.note_layers()
    s.prune_shared_resources()
    s.mark_dead_destinations()
    s.prune_structure()
    s.prune_outline()
    s.prune_threads()
    s.prune_layers()
    _remove_references(s)
    _fix_trees(s)
    _, leftover = s.reachable()
    if leftover:
        raise UnscrubbableError(f"references to removed objects remain: {leftover}")
    return len(pages)
