"""Step 4c: own line breaker (kinsoku push-out + Japanese justify) + TextWriter, per-run fonts/colours."""
import re
import time
import pymupdf
from common import OUT, face_bytes

BAD_START = set("、。，．）」』】〕・ー々ぁぃぅぇぉっゃゅょ！？")
BAD_END = set("（「『【〔")
FONTS = {"YM": pymupdf.Font(fontbuffer=face_bytes("yumin.ttf")),
         "YMB": pymupdf.Font(fontbuffer=face_bytes("yumindb.ttf"))}


def units(runs):
    """runs: [(text, fontkey, color)] -> units [(text, fontkey, color)] ; latin words kept whole."""
    out = []
    for text, fk, col in runs:
        for m in re.finditer(r"[A-Za-z0-9.,:/\-_%]+|\s|.", text):
            out.append((m.group(0), fk, col))
    return out


def width(u, size):
    return FONTS[u[1]].text_length(u[0], size)


def break_lines(runs, box_w, size, indent=0.0):
    us = units(runs)
    lines, cur, x = [], [], indent
    i = 0
    while i < len(us):
        u = us[i]
        w = width(u, size)
        if cur and x + w > box_w + 0.01:
            # kinsoku: push-out (oidashi) while next line would start badly or this line ends badly
            nxt = [u]
            while cur and (nxt[0][0][0] in BAD_START or cur[-1][0][-1] in BAD_END):
                nxt.insert(0, cur.pop())
            if not cur:  # cannot satisfy -> give up, force break
                cur, nxt = nxt[:-1], [nxt[-1]]
            lines.append(cur)
            cur = [n for n in nxt if n[0] != " " or n is not nxt[0]]
            x = sum(width(n, size) for n in cur)
            i += 1
            continue
        cur.append(u)
        x += w
        i += 1
    if cur:
        lines.append(cur)
    return lines


def place(page, rect, runs, size, pitch, indent=0.0, justify=True):
    lines = break_lines(runs, rect.width, size, indent)
    segs = []  # (color, TextWriter) in reading order, new writer whenever colour changes
    y = rect.y0 + size * 0.88  # baseline of first line (ascent approx)
    for li, ln in enumerate(lines):
        x0 = rect.x0 + (indent if li == 0 else 0)
        widths = [width(u, size) for u in ln]
        extra = 0.0
        if justify and li < len(lines) - 1 and len(ln) > 1:
            extra = (rect.x1 - x0 - sum(widths)) / (len(ln) - 1)
        x = x0
        for u, w in zip(ln, widths):
            if not segs or segs[-1][0] != u[2]:
                segs.append((u[2], pymupdf.TextWriter(page.rect)))
            tw = segs[-1][1]
            tw.append((x, y), u[0], font=FONTS[u[1]], fontsize=size)
            x += w + extra
        y += pitch
    for col, tw in segs:
        tw.write_text(page, color=col)
    return lines, y - pitch


if __name__ == "__main__":
    ZEN = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろ"
    txt = ZEN[:20] + "、" + ZEN[20:39] + "（" + ZEN[:5] + "）" + "。PDF と Word を混ぜた文で、Microsoft の名前も入れます。"
    runs = [(txt[:30], "YM", (0, 0, 0)), ("太字の語句", "YMB", (0, 0, 0)), ("赤い語句", "YM", (0.85, 0, 0)), (txt[30:], "YM", (0, 0, 0))]
    doc = pymupdf.open()
    page = doc.new_page()
    rect = pymupdf.Rect(50, 50, 50 + 210, 300)
    t0 = time.perf_counter()
    lines, lasty = place(page, rect, runs, 10.5, 18.2)
    dt = time.perf_counter() - t0
    print(f"layout+write {dt * 1000:.1f} ms, lines={len(lines)}")
    t0 = time.perf_counter()
    for _ in range(50):
        break_lines(runs, 210, 10.5)
    print(f"line breaking only (for UI preview): {(time.perf_counter() - t0) / 50 * 1000:.2f} ms")
    for b in page.get_text("dict")["blocks"]:
        for l in b["lines"]:
            t = "".join(s["text"] for s in l["spans"])
            print(f"   x0={l['bbox'][0]:.1f} x1={l['bbox'][2]:.1f} |{t}|")
    page.get_pixmap(dpi=110, clip=rect + (-5, -5, 5, -150)).save(OUT / "s4c_manual.png")
    doc.subset_fonts()
    doc.save(OUT / "s4c_manual.pdf", garbage=3, deflate=True)
    print("get_text joined ok:", page.get_text().replace("\n", "") == "".join(r[0] for r in runs).replace(" ", "") or page.get_text().replace("\n", "")[:80])
