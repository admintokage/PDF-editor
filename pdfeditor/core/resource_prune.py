"""ページの Resources から、残すページが使っていない項目を取り除く。Qt には依存しない。

ページが 1 つの Resources 辞書を共有している（または /Pages から受け継いでいる）と、削除した
ページや選ばなかったページの画像・フォント・フォーム XObject などが、残すページの Resources に
名前つきで載ったまま残る。どこからも参照されているので不要オブジェクトの掃除（garbage）でも
消えず、page.get_images() などで普通に取り出せてしまう。

そこで写しの上で（生きている文書には触らない）:
  1. 候補: 残すページの Resources の 7 つの分類（XObject・Font・ExtGState・ColorSpace・Pattern・
     Shading・Properties）の項目のうち、取り除く対象（candidates。削除したページの Resources から
     たどれるもの。None ならすべて）を参照するもの。
  2. 使っている名前: 残すページの内容ストリームを MuPDF の字句解析器（pdf_lex）で読み、演算子
     （Do・Tf・gs・cs/CS・scn/SCN・sh・BDC/DP・インライン画像の /CS）が使う名前を集める。
     フォーム XObject・Type3 フォントの CharProcs・タイリングパターン・ExtGState の SMask・注釈の
     外観ストリームへもたどる（自分の Resources がなければ、たどってきた辞書の名前として数える）。
  3. 候補のうち、使われていない名前を辞書から外す。辞書を、残すページとたどったもの以外が
     参照していたら（フォームの /DR など）、その辞書には触らない。
  4. 安全網（必須）: 辞書を変えたページを、変える前と後で描き（同じ倍率でピクセル単位）、
     文字の抽出（フォント名・大きさ・位置つき）も比べる。違いがあれば、そのページの使う辞書を
     元に戻す（残る方が、見た目を壊すよりまし）。字句解析の失敗も、その辞書を変えない。
     どちらも記録に残す。
何が起きても例外は外へ出さず、変えたものは元に戻す（保存・書き出しを失敗させない）。
"""
import hashlib
import logging
import re
from collections import defaultdict

import pymupdf
from pymupdf import mupdf

log = logging.getLogger(__name__)

CATEGORIES = ("XObject", "Font", "ExtGState", "ColorSpace", "Pattern", "Shading", "Properties")
_ZOOM = 1.0          # 安全網で描く倍率（72dpi）
_MAX_NEST = 32       # フォームなどの入れ子の上限
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES
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
    return ("i", mupdf.pdf_to_num(res)) if mupdf.pdf_is_indirect(res) else ("o", owner)


def _dkey(res, rkey: tuple, cat: str):
    d = mupdf.pdf_dict_gets(res, cat)
    if not mupdf.pdf_is_dict(d):
        return None, d
    return (("i", mupdf.pdf_to_num(d)) if mupdf.pdf_is_indirect(d) else ("d", rkey, cat)), d


class _Usage:
    """残すページからたどれる内容が使う名前を、分類の辞書ごとに集める。"""

    def __init__(self, pdf):
        self.pdf = pdf
        self.used: dict[tuple, set[str]] = defaultdict(set)
        self.walked: set[int] = set()      # たどったストリーム・フォント・パターンなど
        self.contexts: set[int] = set()    # 名前を引いた、間接オブジェクトの Resources
        self.memo: set[tuple] = set()
        self.touched: set[tuple] = set()   # 今のページでたどった辞書

    def page(self, page_obj, page_num: int) -> set[tuple]:
        """ページ（内容と注釈の外観）をたどり、たどった辞書のキーを返す。失敗は ContentError。"""
        self.touched = set()
        res, owner = _page_resources(page_obj)
        rkey = _rkey(res, owner or page_num)
        self.stream(mupdf.pdf_dict_gets(page_obj, "Contents"), res, rkey, 0)
        annots = mupdf.pdf_dict_gets(page_obj, "Annots")
        for i in range(mupdf.pdf_array_len(annots) if mupdf.pdf_is_array(annots) else 0):
            ap = mupdf.pdf_dict_gets(mupdf.pdf_array_get(annots, i), "AP")
            if not mupdf.pdf_is_dict(ap):
                continue
            for _, appearance in _entries(ap):
                states = [appearance] if mupdf.pdf_is_stream(appearance) else \
                    [v for _, v in _entries(appearance)] if mupdf.pdf_is_dict(appearance) else []
                for s in states:
                    if mupdf.pdf_is_stream(s):
                        self.nested(s, res, rkey, 0)
        return self.touched

    def nested(self, obj, res, rkey: tuple, depth: int) -> None:
        """自分の Resources があればそれで、なければ受け継いで、obj の内容をたどる。"""
        self.walked.add(_num(obj))
        own = mupdf.pdf_dict_gets(obj, "Resources")
        if mupdf.pdf_is_dict(own):
            res, rkey = own, _rkey(own, _num(obj))
        self.stream(obj, res, rkey, depth + 1)

    def stream(self, contents, res, rkey: tuple, depth: int) -> None:
        if depth > _MAX_NEST:
            raise ContentError("content nested too deeply")
        if mupdf.pdf_is_null(contents):
            return
        memo = (_num(contents) or id(contents), rkey)
        if _num(contents) and memo in self.memo:
            return
        self.memo.add(memo)
        if mupdf.pdf_is_indirect(res):
            self.contexts.add(mupdf.pdf_to_num(res))
        for cat, name in names_in(self.pdf, contents):
            key, d = _dkey(res, rkey, cat)
            if key is None:
                continue
            self.used[key].add(name)
            self.touched.add(key)
            self.follow(cat, mupdf.pdf_dict_gets(d, name), res, rkey, depth)

    def follow(self, cat: str, obj, res, rkey: tuple, depth: int) -> None:
        if mupdf.pdf_is_null(obj):
            return
        if cat == "XObject" and mupdf.pdf_is_stream(obj) and \
                mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Subtype")) == "Form":
            self.nested(obj, res, rkey, depth)
        elif cat == "Font" and mupdf.pdf_to_name(mupdf.pdf_dict_gets(obj, "Subtype")) == "Type3":
            self.walked.add(_num(obj))
            own = mupdf.pdf_dict_gets(obj, "Resources")
            r, k = (own, _rkey(own, _num(obj))) if mupdf.pdf_is_dict(own) else (res, rkey)
            procs = mupdf.pdf_dict_gets(obj, "CharProcs")
            for _, proc in (_entries(procs) if mupdf.pdf_is_dict(procs) else []):
                if mupdf.pdf_is_stream(proc):
                    self.walked.add(_num(proc))
                    self.stream(proc, r, k, depth + 1)
        elif cat == "Pattern" and mupdf.pdf_is_stream(obj):  # タイリングパターン
            self.nested(obj, res, rkey, depth)
        elif cat == "ExtGState" and mupdf.pdf_is_dict(obj):
            self.walked.add(_num(obj))
            group = mupdf.pdf_dict_getp(obj, "SMask/G")
            if mupdf.pdf_is_stream(group):
                self.walked.add(_num(mupdf.pdf_dict_gets(obj, "SMask")))
                self.nested(group, res, rkey, depth)


# ---- 取り除く・確かめる ----

def _fingerprint(doc: pymupdf.Document, index: int) -> tuple[bytes, bytes]:
    """ページの見た目（ピクセル）と文字の抽出（フォント・大きさ・位置つき）の要約。"""
    page = doc.load_page(index)
    try:
        dl = page.get_displaylist()  # 内容を一度だけ解釈し、描画と文字の抽出の両方に使う
        pix = dl.get_pixmap(matrix=pymupdf.Matrix(_ZOOM, _ZOOM), alpha=False)
        look = hashlib.sha256(pix.samples_mv).digest()
        text = pymupdf.TextPage(dl.get_textpage(flags=_TEXT_FLAGS)).extractDICT()
        words = hashlib.sha256(repr(text["blocks"]).encode("utf-8", "surrogatepass")).digest()
    finally:
        del page
    return look, words


class _Pruner:
    def __init__(self, doc: pymupdf.Document, candidates: set[int] | None,
                 detached: set[int], refs: dict[int, set[int]]):
        self.doc = doc
        self.pdf = pymupdf._as_pdf_document(doc)
        self.candidates = candidates
        self.detached = detached
        self.refs = refs
        self.referrers: dict[int, set[int]] | None = None
        self.dicts: dict[tuple, object] = {}             # 分類の辞書
        self.prunable: dict[tuple, set[str]] = defaultdict(set)
        self.page_keys: dict[int, set[tuple]] = {}        # ページ番号 → 関わる辞書
        self.page_xref: dict[int, int] = {}
        self.failed: set[tuple] = set()
        self.removed: dict[tuple, list[tuple[str, object]]] = {}
        self.fallbacks = 0

    def find_candidates(self) -> None:
        seen_xrefs = set()
        for i in range(mupdf.pdf_count_pages(self.pdf)):
            obj = mupdf.pdf_lookup_page_obj(self.pdf, i)
            x = mupdf.pdf_to_num(obj)
            if x in seen_xrefs:  # 同じページオブジェクトが 2 回ページツリーにある
                continue
            seen_xrefs.add(x)
            res, owner = _page_resources(obj)
            if not mupdf.pdf_is_dict(res):
                continue
            rkey = _rkey(res, owner or x)
            keys = set()
            for cat in CATEGORIES:
                key, d = _dkey(res, rkey, cat)
                if key is None:
                    continue
                for name, value in _entries(d):
                    if self.candidates is None or not _refs(value).isdisjoint(self.candidates):
                        self.prunable[key].add(name)
                        self.dicts[key] = d
                        keys.add(key)
            if keys:
                self.page_keys[i] = keys
                self.page_xref[i] = x

    def collect_usage(self) -> _Usage:
        usage = _Usage(self.pdf)
        for i, x in self.page_xref.items():
            try:
                self.page_keys[i] |= usage.page(mupdf.pdf_lookup_page_obj(self.pdf, i), x)
            except Exception:  # noqa: BLE001 - 読めないページの辞書には触らない
                log.warning("resource pruning: cannot read the content of page %d; "
                            "its resources are kept", i, exc_info=True)
                self.failed |= self.page_keys[i] | usage.touched
                self.fallbacks += 1
        return usage

    def referrers_known(self, num: int, usage: _Usage, allowed: set[int], seen: set[int]) -> bool:
        """num を参照しているものが、残すページ・外したページ・たどったもの、またはそれだけから
        参照されている Resources だけか。"""
        if num in seen:
            return True
        seen.add(num)
        if self.referrers is None:
            self.referrers = defaultdict(set)
            for x, targets in self.refs.items():
                for t in targets:
                    self.referrers[t].add(x)
        for x in self.referrers.get(num, ()):
            if x not in allowed and x != num:
                if x not in usage.contexts or not self.referrers_known(x, usage, allowed, seen):
                    return False
        return True

    def removals(self, usage: _Usage) -> dict[tuple, set[str]]:
        allowed = set(self.page_xref.values()) | self.detached | usage.walked
        if self.candidates:
            allowed |= self.candidates
        out = {}
        for key, names in self.prunable.items():
            if key in self.failed:
                continue
            gone = names - usage.used.get(key, set())
            if not gone:
                continue
            if key[0] == "i":
                holder = key[1]
            elif key[1][0] == "i":
                holder = key[1][1]
            else:
                holder = 0  # ページ（か /Pages）が直接持つ辞書
            if holder and not self.referrers_known(holder, usage, allowed, set()):
                log.info("resource pruning: resources %s are used elsewhere; kept", key)
                continue
            out[key] = gone
        return out

    def apply(self, key: tuple, names: set[str]) -> None:
        d = self.dicts[key]
        kept = []
        for name in sorted(names):
            value = mupdf.pdf_dict_gets(d, name)
            kept.append((name, value))
            mupdf.pdf_dict_dels(d, name)
        self.removed[key] = kept

    def restore(self, key: tuple) -> None:
        d = self.dicts[key]
        for name, value in self.removed.pop(key, []):
            mupdf.pdf_dict_puts(d, name, value)

    def run(self) -> int:
        self.find_candidates()
        if not self.page_keys:
            return 0
        usage = self.collect_usage()
        plan = self.removals(usage)
        if not plan:
            return 0
        pages = [i for i, keys in self.page_keys.items() if not keys.isdisjoint(plan)]
        before = {i: _fingerprint(self.doc, i) for i in pages}
        for key, names in plan.items():
            self.apply(key, names)
        todo = pages
        while todo:
            bad = [i for i in todo if _fingerprint(self.doc, i) != before[i]]
            if not bad:
                break
            back = set()
            for i in bad:
                back |= self.page_keys[i] & self.removed.keys()
            log.warning("resource pruning changed how %d page(s) look; restored %d resource "
                        "dictionaries (their unused entries stay in the file)", len(bad), len(back))
            self.fallbacks += 1
            for key in back:
                self.restore(key)
            todo = [i for i in pages if not self.page_keys[i].isdisjoint(back)
                    and not self.page_keys[i].isdisjoint(self.removed)]
        return sum(len(v) for v in self.removed.values())

    def restore_all(self) -> None:
        for key in list(self.removed):
            self.restore(key)


def prune_unused_resources(doc: pymupdf.Document, candidates: set[int] | None = None,
                           detached: set[int] = frozenset(),
                           refs: dict[int, set[int]] | None = None) -> int:
    """写し doc の、ページツリーにあるページの Resources から、どのページも使っていない項目を
    取り除き、取り除いた項目の数を返す。candidates を渡せば、それ（オブジェクト番号）を参照する
    項目だけが対象。detached はページツリーから外したページ（Resources を参照していてもよい）。
    refs は reference_index(doc)（なければ作る）。失敗しても例外は出さず、変えたものを戻す。"""
    if candidates is not None and not candidates:
        return 0
    pruner = None
    try:
        pruner = _Pruner(doc, candidates, set(detached),
                         refs if refs is not None else reference_index(doc))
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
