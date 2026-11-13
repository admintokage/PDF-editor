"""ページの Resources から、残すページが使っていない項目を取り除く。Qt には依存しない。

ページが 1 つの Resources 辞書を共有している（または /Pages から受け継いでいる）と、削除した
ページや選ばなかったページの画像・フォント・フォーム XObject などが、残すページの Resources に
名前つきで載ったまま残る。どこからも参照されているので不要オブジェクトの掃除（garbage）でも
消えず、page.get_images() などで普通に取り出せてしまう。

そこで写しの上で（生きている文書には触らない）:
  1. 取り除く対象（candidates。削除したページの Resources の項目が参照するもの。None ならすべて）
     を参照するものが、残る文書からたどれなければ何もしない（共有されていない）。
  2. 使っている名前: 残すページをすべて、内容ストリームを MuPDF の字句解析器（pdf_lex）で読み、
     演算子（Do・Tf・gs・cs/CS・scn/SCN・sh・BDC/DP・インライン画像の /CS）が使う名前を、
     名前を引いた分類の辞書（XObject・Font・ExtGState・ColorSpace・Pattern・Shading・Properties）
     ごとに集める。フォーム XObject・Type3 フォントの CharProcs・タイリングパターン・ExtGState の
     SMask・注釈の外観（/N・/R・/D とその状態のすべて）の奥までたどる（自分の Resources が
     なければ、たどってきた辞書の名前として数える）。読めないページがあれば、そのページから
     届きうる辞書には触らない。
  3. どの残すページも使わない名前のうち、対象を参照する項目を辞書から外す。外した後でも、
     辞書（またはそれを持つ Resources）を、たどっていないのに残る文書からたどれるもの（フォームの
     /DR など）が参照していれば、その辞書は戻す。
  4. 安全網（必須）: 外した辞書に届くすべてのページを、外す前と後で比べる。描画命令の記録
     （画像・図形・文字の字形とフォント・色空間・透明度・変換行列）と、低い解像度のピクセル。
     違いがあれば、そのページの届く辞書を戻す（残る方が、見た目を壊すよりまし）。最後に、
     どのページも使う名前が引けることを確かめる。どれも記録に残す。
Memo を渡せば、前の保存から形の変わっていないページは、読み直しも描き比べもしない。
何が起きても例外は外へ出さず、変えたものは元に戻す（保存・書き出しを失敗させない）。
"""
import contextlib
import hashlib
import logging
import re
import threading
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

import pymupdf
from pymupdf import mupdf

log = logging.getLogger(__name__)

CATEGORIES = ("XObject", "Font", "ExtGState", "ColorSpace", "Pattern", "Shading", "Properties")
_ZOOM = 0.5          # 安全網で描く倍率（36dpi。細かい違いは描画命令の記録で見る）
_MAX_NEST = 32       # フォームなどの入れ子の上限
_REF_TEXT = re.compile(r"(?<![\w.])(\d+)\s+\d+\s+R\b")
_WS = frozenset(b" \t\r\n\f\0")
_DELIM = frozenset(b"()<>[]{}/%")

# 演算子 → (分類, 名前の位置)。位置は演算子の前に並ぶ（入れ子でない）オペランドの添字
_OPERATORS = {
    "Do": ("XObject", -1), "Tf": ("Font", 0), "gs": ("ExtGState", -1),
    "cs": ("ColorSpace", -1), "CS": ("ColorSpace", -1),
    "scn": ("Pattern", -1), "SCN": ("Pattern", -1), "sh": ("Shading", -1),
    "BDC": ("Properties", 1), "DP": ("Properties", 1),
}


class ContentError(Exception):
    """内容ストリームを読み切れなかった。"""


def reference_index(doc: pymupdf.Document) -> dict[int, set[int]]:
    """各オブジェクトの文字表現にある参照らしき番号。文字列の中の「8 0 R」にも当たるので、
    多めに拾う（絞り込みと「ほかに参照しているものがないか」の確認に、安全側で使う）。"""
    out: dict[int, set[int]] = {}
    for x in range(1, doc.xref_length()):
        try:
            text = doc.xref_object(x, compressed=True)
        except Exception:  # noqa: BLE001 - 読めないものは参照なし
            continue
        refs = {int(m) for m in _REF_TEXT.findall(text)}
        if refs:
            out[x] = refs
    return out


def _num(obj) -> int:
    return mupdf.pdf_to_num(obj) if mupdf.pdf_is_indirect(obj) else 0


def _refs(value) -> set[int]:
    """値（とその直接の中身）にある間接参照の番号。"""
    out, stack = set(), [value]
    while stack:
        o = stack.pop()
        if mupdf.pdf_is_indirect(o):
            out.add(mupdf.pdf_to_num(o))
        elif mupdf.pdf_is_dict(o):
            stack.extend(mupdf.pdf_dict_get_val(o, i) for i in range(mupdf.pdf_dict_len(o)))
        elif mupdf.pdf_is_array(o):
            stack.extend(mupdf.pdf_array_get(o, i) for i in range(mupdf.pdf_array_len(o)))
    return out


def _entries(d) -> list[tuple[str, object]]:
    return [(mupdf.pdf_to_name(mupdf.pdf_dict_get_key(d, i)), mupdf.pdf_dict_get_val(d, i))
            for i in range(mupdf.pdf_dict_len(d))]


# ---- 内容ストリームの字句解析 ----

def _skip_inline_data(stm) -> None:
    """ID の後ろの画像データを、空白 + EI + 空白（か終わり）まで読み飛ばす。"""
    prev = 32
    while True:
        c = mupdf.fz_read_byte(stm)
        if c < 0:
            return
        if c == 0x45 and prev in _WS:  # 'E'
            c2 = mupdf.fz_read_byte(stm)
            if c2 < 0:
                return
            if c2 == 0x49:  # 'I'
                nxt = mupdf.fz_peek_byte(stm)
                if nxt < 0 or nxt in _WS or nxt in _DELIM:
                    return
            prev = c2
            continue
        prev = c


def names_in(pdf, stream_obj) -> list[tuple[str, str]]:
    """内容ストリーム（配列なら続けたもの）が演算子で使う (分類, 名前) の列。読めなければ
    ContentError。"""
    try:
        stm = mupdf.pdf_open_contents_stream(pdf, stream_obj)
    except Exception as exc:  # noqa: BLE001
        raise ContentError("cannot open content stream") from exc
    buf = mupdf.PdfLexbuf(mupdf.PDF_LEXBUF_SMALL)
    raw = buf.m_internal
    out: list[tuple[str, str]] = []
    ops: list[str | None] = []
    depth = 0
    lex = mupdf.pdf_lex
    t_eof, t_err = mupdf.PDF_TOK_EOF, mupdf.PDF_TOK_ERROR
    t_name, t_kw = mupdf.PDF_TOK_NAME, mupdf.PDF_TOK_KEYWORD
    opens = (mupdf.PDF_TOK_OPEN_ARRAY, mupdf.PDF_TOK_OPEN_DICT)
    closes = (mupdf.PDF_TOK_CLOSE_ARRAY, mupdf.PDF_TOK_CLOSE_DICT)
    inline_key: str | None = None
    in_inline = False
    try:
        while True:
            tok = lex(stm, buf)
            if tok == t_eof:
                break
            if tok == t_err:
                raise ContentError("lexer error")
            if in_inline:  # BI ... ID の辞書: /CS・/ColorSpace の名前は ColorSpace 資源
                if tok == t_kw and raw.scratch == "ID":
                    in_inline = False
                    _skip_inline_data(stm)
                elif tok == t_name:
                    if inline_key in ("CS", "ColorSpace"):
                        out.append(("ColorSpace", raw.scratch))
                        inline_key = None
                    else:
                        inline_key = raw.scratch
                else:
                    inline_key = None
                continue
            if tok in opens:
                if depth == 0:
                    ops.append(None)
                depth += 1
            elif tok in closes:
                depth = max(depth - 1, 0)
            elif depth:
                continue
            elif tok == t_name:
                ops.append(raw.scratch)
            elif tok == t_kw:
                op = raw.scratch
                if op == "BI":
                    in_inline, inline_key = True, None
                else:
                    use = _OPERATORS.get(op)
                    if use and ops:
                        cat, pos = use
                        if -len(ops) <= pos < len(ops) and ops[pos] is not None:
                            out.append((cat, ops[pos]))
                ops.clear()
            else:
                ops.append(None)
    except ContentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ContentError("cannot read content stream") from exc
    if in_inline:
        raise ContentError("unterminated inline image")
    return out


# ---- 使っている名前を集める ----

def _page_resources(page_obj) -> tuple[object, int]:
    """ページの（受け継いだものも含む）Resources と、それを持っているオブジェクトの番号。"""
    node, seen = page_obj, set()
    while mupdf.pdf_is_dict(node) and _num(node) not in seen:
        seen.add(_num(node))
        res = mupdf.pdf_dict_gets(node, "Resources")
        if mupdf.pdf_is_dict(res):
            return res, _num(node)
        node = mupdf.pdf_dict_gets(node, "Parent")
    return mupdf.PdfObj(), 0


def _rkey(res, owner: int) -> tuple:
    """Resources 辞書の名前: 間接オブジェクトなら ("i", 番号)、そうでなければ持ち主の
    オブジェクト（ページ・/Pages・ストリーム）の番号で ("o", 番号)。"""
    return ("i", mupdf.pdf_to_num(res)) if mupdf.pdf_is_indirect(res) else ("o", owner)


def _dkey(res, rkey: tuple, cat: str):
    """分類の辞書（Resources の /XObject など）の名前と、その辞書。"""
    d = mupdf.pdf_dict_gets(res, cat)
    if not mupdf.pdf_is_dict(d):
        return None, d
    return (("i", mupdf.pdf_to_num(d)) if mupdf.pdf_is_indirect(d) else ("d", rkey, cat)), d


def _resolve(pdf, key: tuple):
    """_dkey の名前から、今の分類の辞書を引く。"""
    if key[0] == "i":
        return mupdf.pdf_load_object(pdf, key[1])
    _, (kind, num), cat = key
    holder = mupdf.pdf_load_object(pdf, num)
    res = holder if kind == "i" else mupdf.pdf_dict_gets(holder, "Resources")
    return mupdf.pdf_dict_gets(res, cat)


def _holders(key: tuple) -> tuple[int, int]:
    """分類の辞書を参照している（または中に持つ）間接オブジェクトの番号: (辞書, Resources か持ち主)。"""
    if key[0] == "i":
        return key[1], 0
    return 0, key[1][1]


@dataclass
class _PageUse:
    """1 ページ（内容・注釈の外観すべて）からたどった結果。"""
    used: dict[tuple, set[str]] = field(default_factory=lambda: defaultdict(set))
    keys: set[tuple] = field(default_factory=set)      # 名前を引いた Resources の分類の辞書
    walked: set[int] = field(default_factory=set)      # たどったオブジェクト（ページを含む）
    contexts: set[int] = field(default_factory=set)    # 名前を引いた、間接オブジェクトの Resources
    failed: bool = False
    signature: tuple = ()


class _Walker:
    """1 ページの内容と注釈の外観から、使う名前を、分類の辞書ごとに集める。"""

    def __init__(self, pdf, use: _PageUse):
        self.pdf = pdf
        self.use = use
        self.memo: set[tuple] = set()

    def context(self, res, rkey: tuple) -> None:
        if mupdf.pdf_is_indirect(res):
            self.use.contexts.add(mupdf.pdf_to_num(res))
        for cat in CATEGORIES:
            key, d = _dkey(res, rkey, cat)
            if key is not None:
                self.use.keys.add(key)

    def page(self, page_obj, page_num: int) -> None:
        self.use.walked.add(page_num)
        res, owner = _page_resources(page_obj)
        rkey = _rkey(res, owner or page_num)
        contents = mupdf.pdf_dict_gets(page_obj, "Contents")
        for c in ([mupdf.pdf_array_get(contents, i) for i in range(mupdf.pdf_array_len(contents))]
                  if mupdf.pdf_is_array(contents) else [contents]):
            self.use.walked.add(_num(c))
        self.context(res, rkey)  # 内容が空でも、ページの Resources は数える
        self.stream(contents, res, rkey, 0)
        annots = mupdf.pdf_dict_gets(page_obj, "Annots")
        self.use.walked.add(_num(annots))
        for i in range(mupdf.pdf_array_len(annots) if mupdf.pdf_is_array(annots) else 0):
            annot = mupdf.pdf_array_get(annots, i)
            self.use.walked.add(_num(annot))
            ap = mupdf.pdf_dict_gets(annot, "AP")
            if not mupdf.pdf_is_dict(ap):
                continue
            self.use.walked.add(_num(ap))
            for _, appearance in _entries(ap):  # /N・/R・/D、それぞれ 1 つか、状態ごとの辞書
                self.use.walked.add(_num(appearance))
                states = [appearance] if mupdf.pdf_is_stream(appearance) else \
                    [v for _, v in _entries(appearance)] if mupdf.pdf_is_dict(appearance) else []
                for s in states:
                    if mupdf.pdf_is_stream(s):
                        self.nested(s, res, rkey, 0)

    def nested(self, obj, res, rkey: tuple, depth: int) -> None:
        """自分の Resources があればそれで、なければ受け継いで、obj の内容をたどる。"""
        self.use.walked.add(_num(obj))
        own = mupdf.pdf_dict_gets(obj, "Resources")
        if mupdf.pdf_is_dict(own):
            res, rkey = own, _rkey(own, _num(obj))
        self.stream(obj, res, rkey, depth + 1)

    def stream(self, contents, res, rkey: tuple, depth: int) -> None:
        if depth > _MAX_NEST:
            raise ContentError("content nested too deeply")
        if mupdf.pdf_is_null(contents):
            return
        memo = (_num(contents), rkey)
        if memo[0] and memo in self.memo:
            return
        self.memo.add(memo)
        self.context(res, rkey)
        for cat, name in names_in(self.pdf, contents):
            key, d = _dkey(res, rkey, cat)
            if key is None:
                continue
            self.use.used[key].add(name)
            self.follow(cat, mupdf.pdf_dict_gets(d, name), res, rkey, depth)

    def follow(self, cat: str, obj, res, rkey: tuple, depth: int) -> None:
        if mupdf.pdf_is_null(obj):
            return
        self.use.walked.add(_num(obj))
        if cat == "XObject" and mupdf.pdf_is_stream(obj) and \
                mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Subtype")) == "Form":
            self.nested(obj, res, rkey, depth)
        elif cat == "Font" and mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Subtype")) == "Type3":
            own = mupdf.pdf_dict_gets(obj, "Resources")
            r, k = (own, _rkey(own, _num(obj))) if mupdf.pdf_is_dict(own) else (res, rkey)
            procs = mupdf.pdf_dict_gets(obj, "CharProcs")
            self.use.walked.add(_num(procs))
            for _, proc in (_entries(procs) if mupdf.pdf_is_dict(procs) else []):
                if mupdf.pdf_is_stream(proc):
                    self.use.walked.add(_num(proc))
                    self.stream(proc, r, k, depth + 1)
        elif cat == "Pattern" and mupdf.pdf_is_stream(obj):  # タイリングパターン
            self.nested(obj, res, rkey, depth)
        elif cat == "ExtGState" and mupdf.pdf_is_dict(obj):
            smask = mupdf.pdf_dict_gets(obj, "SMask")
            self.use.walked.add(_num(smask))
            group = mupdf.pdf_dict_gets(smask, "G") if mupdf.pdf_is_dict(smask) else smask
            if mupdf.pdf_is_stream(group):
                self.nested(group, res, rkey, depth)


def _inputs(use: _PageUse) -> set[int]:
    """ページの名前の使い方を決めるオブジェクト（たどったもの・Resources・分類の辞書）。"""
    return use.walked | use.contexts | {k[1] for k in use.keys if k[0] == "i"} \
        | {k[1][1] for k in use.keys if k[0] == "d"}


def _signature(doc: pymupdf.Document, walked: set[int]) -> tuple:
    """たどったオブジェクトの今の形（ストリームは中身の短い要約も）。変わっていなければ、前に
    たどった結果と確かめた結果をそのまま使える。"""
    out = []
    for x in sorted(walked):
        if not x:
            continue
        try:
            text = doc.xref_object(x, compressed=True)
            raw = doc.xref_stream_raw(x) if doc.xref_is_stream(x) else b""
        except Exception:  # noqa: BLE001 - 読めないものは毎回たどり直す
            return ()
        out.append((x, text, zlib.crc32(raw or b""), len(raw or b"")))
    return tuple(out)


def _closure(pdf, start: int) -> set[int]:
    """start からたどれる間接オブジェクト（/Parent・/P と、ほかのページ・ページツリー・カタログの
    先へはたどらない）。読めなかったページから届きうるものを、多めに求める。"""
    seen, todo = set(), [start]
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        obj = mupdf.pdf_load_object(pdf, x)
        typ = mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Type")) if mupdf.pdf_is_dict(obj) else ""
        if x != start and typ in ("Page", "Pages", "Catalog"):
            continue
        stack = [obj]
        while stack:
            o = stack.pop()
            if mupdf.pdf_is_indirect(o) and mupdf.pdf_to_num(o) != x:
                todo.append(mupdf.pdf_to_num(o))
            elif mupdf.pdf_is_dict(o):
                for k, v in _entries(o):
                    if k not in ("Parent", "P"):
                        stack.append(v)
            elif mupdf.pdf_is_array(o):
                stack.extend(mupdf.pdf_array_get(o, i) for i in range(mupdf.pdf_array_len(o)))
    return seen


class Memo:
    """生きている文書ごとの、前の保存でたどった結果と確かめた結果（保存を繰り返しても、変わって
    いないページは字句解析も描き比べもしない）。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.uses: dict[int, _PageUse] = {}                      # ページ → たどった結果
        self.verified: dict[int, tuple] = {}                      # ページ → (形, 取り除いた項目)


# ---- 取り除く・確かめる ----

def _fingerprint(doc: pymupdf.Document, index: int) -> tuple[bytes, bytes]:
    """ページ（注釈を含む）の描画命令の記録（画像・図形・文字の字形・色空間・透明度・変換行列）と、
    低い解像度のピクセルの要約。記録の方が、ピクセルより細かい違い（0.05pt の画像、0.5pt の文字、
    透明度 0.99、CalRGB の色空間など）まで見分ける。"""
    page = doc.load_page(index)
    try:
        dl = page.get_displaylist()
        buf = mupdf.fz_new_buffer(4096)
        out = mupdf.FzOutput(buf)
        dev = mupdf.fz_new_trace_device(out)
        mupdf.fz_run_display_list(dl.this, dev, mupdf.FzMatrix(), mupdf.FzRect(mupdf.FzRect.Fixed_INFINITE),
                                  mupdf.FzCookie())
        mupdf.fz_close_device(dev)
        mupdf.fz_close_output(out)
        trace = hashlib.sha256(mupdf.fz_buffer_extract(buf)).digest()
        pix = dl.get_pixmap(matrix=pymupdf.Matrix(_ZOOM, _ZOOM), alpha=False)
        look = hashlib.sha256(pix.samples_mv).digest()
        del pix
    finally:
        del page
    return trace, look


class _Pruner:
    def __init__(self, doc: pymupdf.Document, candidates: set[int] | None,
                 blocked: set[int], refs: dict[int, set[int]], memo: Memo | None):
        self.doc = doc
        self.pdf = pymupdf._as_pdf_document(doc)
        self.candidates = candidates
        self.blocked = blocked
        self.refs = refs
        self.memo = memo
        self.referrers: dict[int, set[int]] | None = None
        self.pages: dict[int, int] = {}            # ページ番号 → xref（同じオブジェクトは 1 回）
        self.uses: dict[int, _PageUse] = {}        # xref → たどった結果
        self.failed: set[tuple] = set()
        self.removed: dict[tuple, list[tuple[str, object]]] = {}

    # -- 下ごしらえ --

    def inverse(self) -> dict[int, set[int]]:
        if self.referrers is None:
            self.referrers = defaultdict(set)
            for x, targets in self.refs.items():
                for t in targets:
                    self.referrers[t].add(x)
        return self.referrers

    def reachable(self) -> set[int]:
        """トレーラーからたどれるオブジェクト（外したページの先へはたどらない。参照は文字表現で
        多めに拾う）。"""
        trailer = _refs(mupdf.pdf_trailer(self.pdf))
        seen, todo = set(), [x for x in trailer if x not in self.blocked]
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(r for r in self.refs.get(x, ()) if r not in seen and r not in self.blocked)
        return seen

    def worth_walking(self) -> bool:
        """取り除く対象を参照しているものが、残る文書からたどれるか（たどれなければ、共有されて
        いないので何もしなくてよい）。"""
        if self.candidates is None:
            return True
        inv = self.inverse()
        holders = set().union(*(inv.get(c, set()) for c in self.candidates)) - self.blocked
        return not holders.isdisjoint(self.reachable())

    def walk(self) -> None:
        """残すページをすべて、内容と注釈の外観の奥までたどる（前と同じ形のページは前の結果）。"""
        for i in range(mupdf.pdf_count_pages(self.pdf)):
            obj = mupdf.pdf_lookup_page_obj(self.pdf, i)
            x = mupdf.pdf_to_num(obj)
            if x in self.uses:
                continue
            self.pages[i] = x
            old = self.memo.uses.get(x) if self.memo else None
            if old is not None and old.signature and old.signature == _signature(self.doc, _inputs(old)):
                self.uses[x] = old
                continue
            use = _PageUse()
            try:
                _Walker(self.pdf, use).page(obj, x)
            except Exception:  # noqa: BLE001 - 読めないページから届く辞書には触らない
                log.warning("resource pruning: cannot read the content of page %d; resources it can "
                            "reach are kept", i, exc_info=True)
                use.failed = True
                use.walked |= _closure(self.pdf, x)
            use.signature = () if use.failed else _signature(self.doc, _inputs(use))
            self.uses[x] = use

    # -- 計画 --

    def plan(self) -> dict[tuple, set[str]]:
        used: dict[tuple, set[str]] = defaultdict(set)
        keys: set[tuple] = set()
        for use in self.uses.values():
            keys |= use.keys
            for k, names in use.used.items():
                used[k] |= names
            if use.failed:  # 読めなかったページから届きうる辞書には触らない
                for k in self.all_keys():
                    a, b = _holders(k)
                    if (a and a in use.walked) or (b and b in use.walked):
                        self.failed.add(k)
                self.failed |= use.keys
        out = {}
        for key in keys - self.failed:
            d = _resolve(self.pdf, key)
            if not mupdf.pdf_is_dict(d):
                continue
            gone = {name for name, value in _entries(d)
                    if name not in used[key] and (self.candidates is None
                                                  or not _refs(value).isdisjoint(self.candidates))}
            if gone:
                out[key] = gone
        return out

    def all_keys(self) -> set[tuple]:
        return set().union(*(u.keys for u in self.uses.values())) if self.uses else set()

    def apply(self, key: tuple, names: set[str]) -> None:
        d = _resolve(self.pdf, key)
        kept = []
        for name in sorted(names):
            kept.append((name, mupdf.pdf_dict_gets(d, name)))
            mupdf.pdf_dict_dels(d, name)
        self.removed[key] = kept

    def restore(self, key: tuple) -> None:
        d = _resolve(self.pdf, key)
        for name, value in self.removed.pop(key, []):
            mupdf.pdf_dict_puts(d, name, value)

    def restore_all(self) -> None:
        for key in list(self.removed):
            self.restore(key)

    # -- ほかの道がないか --

    def unknown_users(self) -> set[tuple]:
        """取り除いた辞書に、たどっていない道から届くものがあれば、その辞書。
        - 間接オブジェクトの辞書（分類の辞書・Resources）: それを参照していて、残る文書からたどれる
          ものが、すべてたどったもの（ページ・内容・フォーム・注釈など）か、同じ条件を満たす
          Resources であること（フォームの /DR などが参照していれば駄目）
        - ページ・/Pages・ストリームが直接持つ辞書: 持ち主をたどったこと
        取り除いた後の形で調べるので、取り除いた項目からしかたどれないもの（削除ページの
        フォームなど）は数えない。"""
        for k in self.removed:  # 取り除いた後の参照
            for h in _holders(k):
                if h:
                    with contextlib.suppress(Exception):
                        self.refs[h] = {int(m) for m in _REF_TEXT.findall(
                            self.doc.xref_object(h, compressed=True))}
        self.referrers = None
        reach = self.reachable()
        good = [u for u in self.uses.values() if not u.failed]
        walked = set().union(*(u.walked for u in good)) if good else set()
        contexts = set().union(*(u.contexts for u in good)) if good else set()
        inv = self.inverse()
        memo: dict[int, bool] = {}

        def known(h: int) -> bool:
            if h not in memo:
                memo[h] = True  # 循環に備えて仮に
                memo[h] = all(r == h or r not in reach or r in walked or (r in contexts and known(r))
                              for r in inv.get(h, ()))
            return memo[h]

        bad = set()
        for key in self.removed:
            if key[0] == "i" or key[1][0] == "i":
                if not known(key[1] if key[0] == "i" else key[1][1]):
                    bad.add(key)
            else:
                owner = key[1][1]
                obj = mupdf.pdf_load_object(self.pdf, owner)
                if owner not in walked and mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Type")) != "Pages":
                    bad.add(key)
        return bad

    # -- 全体 --

    def affected_pages(self, keys) -> list[int]:
        return [i for i, x in self.pages.items() if not self.uses[x].keys.isdisjoint(keys)]

    def page_plan(self, i: int) -> tuple:
        use = self.uses[self.pages[i]]
        return tuple(sorted((k, tuple(n for n, _ in v)) for k, v in self.removed.items() if k in use.keys))

    def run(self) -> int:
        if not self.worth_walking():
            return 0
        self.walk()
        plan = self.plan()
        if not plan:
            return 0
        for key, names in plan.items():
            self.apply(key, names)
        while True:  # たどっていない道から届く辞書は戻す（戻すとまた届くものが増えうる）
            bad = self.unknown_users()
            if not bad:
                break
            log.info("resource pruning: %d resource dictionaries are used elsewhere; kept", len(bad))
            for key in bad:
                self.restore(key)
        if not self.removed:
            return 0
        pages = self.affected_pages(self.removed)
        todo = [i for i in pages if not self.already_verified(i)]
        if todo:  # 安全網: 取り除く前と後で描き比べる
            after = {i: _fingerprint(self.doc, i) for i in todo}
            kept = {k: self.removed.pop(k) for k in list(self.removed)}
            for key, items in kept.items():
                d = _resolve(self.pdf, key)
                for name, value in items:
                    mupdf.pdf_dict_puts(d, name, value)
            before = {i: _fingerprint(self.doc, i) for i in todo}
            for key in kept:
                self.apply(key, {n for n, _ in kept[key]})
            self.verify(todo, before, after)
        self.check_names()
        if self.memo is not None:
            self.remember()
        return sum(len(v) for v in self.removed.values())

    def verify(self, todo: list[int], before: dict, after: dict) -> None:
        bad = [i for i in todo if after[i] != before[i]]
        while bad:
            back = set()
            for i in bad:
                back |= self.uses[self.pages[i]].keys & self.removed.keys()
            log.warning("resource pruning changed how %d page(s) look; restored %d resource "
                        "dictionaries (their unused entries stay in the file)", len(bad), len(back))
            for key in back:
                self.restore(key)
            again = [i for i in todo if not self.uses[self.pages[i]].keys.isdisjoint(back)
                     and not self.uses[self.pages[i]].keys.isdisjoint(self.removed)]
            bad = [i for i in again if _fingerprint(self.doc, i) != before[i]]

    def check_names(self) -> None:
        """構造の確認: どのページも、使う名前で、取り除く前に引けたものは今も引ける。"""
        for key, items in list(self.removed.items()):
            gone = {n for n, _ in items}
            if any(not gone.isdisjoint(u.used.get(key, ())) for u in self.uses.values()):
                log.warning("resource pruning: a used name would be removed; restored")
                self.restore(key)

    def already_verified(self, i: int) -> bool:
        if self.memo is None:
            return False
        x = self.pages[i]
        rec = self.memo.verified.get(x)
        return rec is not None and rec == (self.uses[x].signature, self.page_plan(i))

    def remember(self) -> None:
        self.memo.uses = {x: u for x, u in self.uses.items() if not u.failed}
        self.memo.verified = {self.pages[i]: (self.uses[self.pages[i]].signature, self.page_plan(i))
                              for i in self.affected_pages(self.removed)
                              if self.uses[self.pages[i]].signature}


def prune_unused_resources(doc: pymupdf.Document, candidates: set[int] | None = None,
                           blocked: set[int] = frozenset(),
                           refs: dict[int, set[int]] | None = None, memo: Memo | None = None) -> int:
    """写し doc の、ページツリーにあるページから使われていない Resources の項目を取り除き、
    取り除いた項目の数を返す。candidates を渡せば、それ（オブジェクト番号）を参照する項目だけが
    対象。blocked はページツリーから外したページとその注釈（その先は、残る文書からたどれるものに
    数えない）。refs は reference_index(doc)（なければ作る）。memo を渡せば、前の結果を使い、
    今回の結果を残す。失敗しても例外は出さず、変えたものを戻す。"""
    if candidates is not None and not candidates:
        return 0
    pruner = None
    lock = memo.lock if memo is not None else contextlib.nullcontext()
    with lock:
        try:
            pruner = _Pruner(doc, candidates, set(blocked),
                             dict(refs) if refs is not None else reference_index(doc), memo)
            return pruner.run()
        except Exception:  # noqa: BLE001 - 取り除けなくても保存・書き出しは続ける
            log.warning("resource pruning failed; resources kept unchanged", exc_info=True)
            if pruner is not None:
                try:
                    pruner.restore_all()
                except Exception:  # noqa: BLE001
                    log.exception("could not restore pruned resources")
                    raise
            return 0
