"""保存する写しから、削除したページへの参照を取り除く。Qt には依存しない。

ページを削除しても、名前付きの行き先・OpenAction・構造ツリー（タグ付き PDF の /Pg）・
注釈の /P などがそのページを指していると、不要オブジェクトの掃除（garbage）でもページは
消えず、削除したはずの本文や画像が保存したファイルに残ってしまう。

そこで保存の写しで（生きている文書は元に戻すために触らない）:
  1. ページツリーにない /Type/Page オブジェクト（＝削除したページ）を探す
  2. そのページに属する構造要素（/Pg がそのページ）も「消すもの」に加える
  3. 消すもの以外のすべてのオブジェクトから、消すものへの参照を取り除く
     （行き先の配列 [ページ /Fit] は丸ごと null、それ以外の参照は null、
      OpenAction が行き先を失ったらキーごと消す）
  4. トレーラーからたどれるオブジェクトに、消すものへの参照が残っていないことを確かめる
その後 garbage 付きで書けば、どこからも参照されないページは落ちる。
4 で残っていたら UnscrubbableError を投げ、保存をやめる（中身が残ったファイルは書かない）。
"""
import re

import pymupdf

_REF = re.compile(r"(?<![\w.])(\d+)\s+(\d+)\s+R\b")
# 行き先の配列: [ページ参照 /XYZ ...] [ページ参照 /Fit] など
_DEST = re.compile(r"\[\s*(\d+)\s+\d+\s+R\s*/(?:XYZ|Fit|FitH|FitV|FitR|FitB|FitBH|FitBV)\b[^\[\]]*\]")


class UnscrubbableError(Exception):
    """削除したページへの参照を取り除ききれなかった。"""


def _refs(text: str) -> set[int]:
    return {int(m.group(1)) for m in _REF.finditer(text)}


# 名前付きの行き先のうち、行き先が null になった組: (名前) null / <16進> null / /名前 null
_DEAD_NAME = re.compile(r"(\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]*>|/[^\s/\[\]<>()]+)\s+null\b")


def _clean(text: str, doomed: set[int], key: str | None = None) -> str:
    """消すものへの参照を null にする。名前付きの行き先（name tree の /Names、旧式の /Dests）
    では、行き先を失った名前の組ごと取り除く。"""
    text = _DEST.sub(lambda m: " null " if int(m.group(1)) in doomed else m.group(0), text)
    text = _REF.sub(lambda m: " null " if int(m.group(1)) in doomed else m.group(0), text)
    if key in ("Names", "Dests"):
        text = _DEAD_NAME.sub(" ", text)
    return text


def _get_key(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return doc.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001 - 読めないオブジェクトは無いものとして扱う
        return "null", "null"


def _object(doc: pymupdf.Document, xref: int) -> str:
    try:
        return doc.xref_object(xref, compressed=True)
    except Exception:  # noqa: BLE001
        return ""


def detached_pages(doc: pymupdf.Document) -> set[int]:
    in_tree = {doc[i].xref for i in range(doc.page_count)}
    return {
        x for x in range(1, doc.xref_length())
        if x not in in_tree and _get_key(doc, x, "Type") == ("name", "/Page")
    }


def _neutralise(doc: pymupdf.Document, xref: int, doomed: set[int]) -> None:
    text = _object(doc, xref)
    if not _refs(text) & doomed:
        return
    if text.lstrip().startswith("<<"):  # 辞書（ストリームの辞書を含む）はキーごとに直す
        for key in doc.xref_get_keys(xref):
            kind, value = _get_key(doc, xref, key)
            if not _refs(value) & doomed:
                continue
            value = _clean(value, doomed, key)
            if key == "OpenAction" and value.lstrip("[ ").startswith("null"):
                value = "null"  # 行き先を失った OpenAction は消す
            doc.xref_set_key(xref, key, value)
    else:  # 配列など
        doc.update_object(xref, _clean(text, doomed))


def _reachable(doc: pymupdf.Document, doomed: set[int]) -> set[int]:
    """トレーラーから参照をたどって届くオブジェクト（消すものの先へはたどらない）。"""
    todo = [x for x in _refs(doc.pdf_trailer(compressed=True)) if x not in doomed]
    seen: set[int] = set()
    while todo:
        x = todo.pop()
        if x in seen or not 0 < x < doc.xref_length():
            continue
        seen.add(x)
        todo.extend(r for r in _refs(_object(doc, x)) if r not in seen and r not in doomed)
    return seen


def scrub_detached_pages(doc: pymupdf.Document) -> int:
    """削除したページへの参照を写し doc から取り除く。取り除いたページ数を返す。"""
    doomed = detached_pages(doc)
    if not doomed:
        return 0
    pages = len(doomed)
    for x in range(1, doc.xref_length()):  # そのページに属する構造要素
        kind, value = _get_key(doc, x, "Pg")
        if kind == "xref" and _refs(value) & doomed and _get_key(doc, x, "S")[0] == "name":
            doomed.add(x)
    for x in range(1, doc.xref_length()):
        if x not in doomed:
            _neutralise(doc, x, doomed)
    leftover = [x for x in _reachable(doc, doomed) if _refs(_object(doc, x)) & doomed]
    if leftover:
        raise UnscrubbableError(f"references to deleted pages remain in objects {leftover}")
    return pages
