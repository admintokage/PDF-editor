"""Shared helpers for the text-replace spike (throwaway code)."""
import struct
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
FONTS = Path(r"C:\Windows\Fonts")
SAMPLE = OUT / "sample.pdf"


def ttc_offsets(data: bytes):
    if data[:4] != b"ttcf":
        return [0]
    n = struct.unpack(">I", data[8:12])[0]
    return list(struct.unpack(">%dI" % n, data[12:12 + 4 * n]))


def tables(data: bytes, off: int):
    num = struct.unpack(">H", data[off + 4:off + 6])[0]
    res = {}
    for i in range(num):
        rec = off + 12 + 16 * i
        tag, chk, toff, tlen = struct.unpack(">4sIII", data[rec:rec + 16])
        res[tag.decode("latin1")] = (chk, toff, tlen)
    return data[off:off + 4], res


def extract_face(data: bytes, index: int) -> bytes:
    """Cut one face out of a .ttc into a standalone .ttf/.otf byte string."""
    off = ttc_offsets(data)[index]
    sfver, tbl = tables(data, off)
    tags = sorted(tbl)
    n = len(tags)
    es = 1
    while (1 << (es + 1)) <= n:
        es += 1
    sr = (1 << es) * 16
    head = sfver + struct.pack(">HHHH", n, sr, es, n * 16 - sr)
    body = b""
    recs = b""
    pos = 12 + 16 * n
    for t in tags:
        chk, toff, tlen = tbl[t]
        blob = data[toff:toff + tlen]
        recs += struct.pack(">4sIII", t.encode("latin1"), chk, pos + len(body), tlen)
        body += blob + b"\0" * ((4 - tlen % 4) % 4)
    return head + recs + body


def names(data: bytes, off: int):
    _, tbl = tables(data, off)
    _, noff, _ = tbl["name"]
    fmt, count, soff = struct.unpack(">HHH", data[noff:noff + 6])
    out = {}
    for i in range(count):
        p, e, lang, nid, ln, o = struct.unpack(">HHHHHH", data[noff + 6 + 12 * i: noff + 18 + 12 * i])
        if p == 3 and e in (1, 10):
            s = data[noff + soff + o: noff + soff + o + ln].decode("utf-16-be", "replace")
            out.setdefault(nid, {})[lang] = s
    return out


def fstype(data: bytes, off: int) -> int:
    _, tbl = tables(data, off)
    _, ooff, _ = tbl["OS/2"]
    return struct.unpack(">H", data[ooff + 8:ooff + 10])[0]


def fstype_meaning(v: int) -> str:
    low = v & 0x000F
    if low == 0:
        s = "0=インストール可能（制限なし）"
    elif low & 0x2 and not (low & 0xC):
        s = "2=埋め込み不可(Restricted)"
    elif low & 0x4:
        s = "4=印刷・プレビューのみ"
    elif low & 0x8:
        s = "8=編集可能"
    else:
        s = f"{low}=?"
    if v & 0x0100:
        s += " +部分埋め込み禁止(NoSubsetting)"
    if v & 0x0200:
        s += " +ビットマップのみ"
    return f"0x{v:04X} " + s


_cache = {}


def face_bytes(fname: str, index: int = 0) -> bytes:
    key = (fname, index)
    if key not in _cache:
        data = (FONTS / fname).read_bytes()
        _cache[key] = extract_face(data, index) if data[:4] == b"ttcf" else data
    return _cache[key]
