"""Step 4: reflow Japanese text into a box with insert_htmlbox; kinsoku, mixed scripts, justify,
line spacing, bold/colour runs, predictability. Then a manual breaker + TextWriter."""
import time
import pymupdf
from common import OUT, face_bytes

SIZE = 10.5
EM = SIZE
W = 20 * EM + 3  # htmlbox has ~1pt inner inset each side: 213pt holds exactly 20 full-width chars (measured)

arch = pymupdf.Archive()
arch.add(face_bytes("yumin.ttf"), "ym.ttf")
arch.add(face_bytes("yumindb.ttf"), "ymb.ttf")
arch.add(face_bytes("YuGothR.ttc", 0), "yg.ttf")
CSS = """
@font-face {font-family: YM; src: url(ym.ttf);}
@font-face {font-family: YM; src: url(ymb.ttf); font-weight: bold;}
@font-face {font-family: YG; src: url(yg.ttf);}
* {font-family: YM; font-size: %spt; margin: 0; padding: 0;}
""" % SIZE

ZEN = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろ"
F = pymupdf.Font(fontbuffer=face_bytes("yumin.ttf"))


def lines_in(page, clip=None):
    res = []
    for b in page.get_text("dict", clip=clip)["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"])
            if t.strip():
                res.append((round(l["spans"][0]["origin"][1], 2), round(l["bbox"][0], 2), round(l["bbox"][2], 2), t))
    res.sort()
    # merge same baseline
    out = []
    for y, x0, x1, t in res:
        if out and abs(out[-1][0] - y) < 1:
            out[-1] = (out[-1][0], min(out[-1][1], x0), max(out[-1][2], x1), out[-1][3] + t)
        else:
            out.append((y, x0, x1, t))
    return out


def htmlbox(html, width=W, css_extra="", height=400, page=None, rect=None):
    doc = None
    if page is None:
        doc = pymupdf.open()
        page = doc.new_page(width=width + 100, height=height + 100)
        rect = pymupdf.Rect(50, 50, 50 + width, 50 + height)
    t0 = time.perf_counter()
    rc = page.insert_htmlbox(rect, html, css=CSS + css_extra, archive=arch, scale_low=1)
    dt = time.perf_counter() - t0
    return page, rc, dt, lines_in(page, rect + (-1, -1, 1, 1)), doc


def show(title, lines, rc=None, dt=None):
    print(f"--- {title}  rc={rc} t={dt * 1000 if dt else 0:.1f}ms")
    prev = None
    for y, x0, x1, t in lines:
        pitch = f"pitch={y - prev:.2f}" if prev else ""
        print(f"   y={y:7.2f} x0={x0:6.2f} x1={x1:6.2f} {pitch:12s} |{t}|")
        prev = y


BAD_START = "、。，．）」』】〕・ー々ぁぃぅぇぉっゃゅょ！？"
BAD_END = "（「『【〔"


def check(lines):
    bad = []
    for i, (_, _, _, t) in enumerate(lines):
        if i > 0 and t[:1] in BAD_START:
            bad.append(f"line{i + 1} starts with {t[0]}")
        if i < len(lines) - 1 and t.rstrip()[-1:] in BAD_END:
            bad.append(f"line{i + 1} ends with {t.rstrip()[-1]}")
    return bad or ["OK"]


if __name__ == "__main__":
    results = {}
    # 1. kinsoku: 20 full-width chars then the prohibited char at position 21 (line start)
    for ch in "、。）」":
        txt = ZEN[:20] + ch + ZEN[20:35]
        if ch in "）」":
            txt = ZEN[:2] + ("（" if ch == "）" else "「") + ZEN[2:20] + ch + ZEN[20:35]
            txt = txt[:20] + ch + ZEN[20:35] if False else ZEN[:19] + ("（" if ch == "）" else "「") + ZEN[19:20] + ch + ZEN[20:35]
        page, rc, dt, ls, d = htmlbox(f"<p>{txt}</p>")
        show(f"kinsoku start {ch}", ls, rc, dt)
        results[f"start{ch}"] = check(ls)
        print("   ->", results[f"start{ch}"])
    for ch in "（「":
        txt = ZEN[:19] + ch + ZEN[19:24] + ("）" if ch == "（" else "」") + ZEN[24:40]
        page, rc, dt, ls, d = htmlbox(f"<p>{txt}</p>")
        show(f"kinsoku end {ch}", ls, rc, dt)
        results[f"end{ch}"] = check(ls)
        print("   ->", results[f"end{ch}"])

    # 2. mixed latin/japanese
    txt = "これは試験の文章でありまして、Microsoft Word からPDFへ書き出したファイルを、PyMuPDF で編集できるかどうか確かめるためのものです。URL https://example.com/very/long/path/name も含みます。"
    page, rc, dt, ls, d = htmlbox(f"<p>{txt}</p>")
    show("mixed latin/japanese", ls, rc, dt)
    print("   ->", check(ls))

    # 3. justify
    page, rc, dt, ls, d = htmlbox(f"<p>{txt}</p>", css_extra="p {text-align: justify;}")
    show("justify", ls, rc, dt)
    print("   right edges:", [x1 for _, _, x1, _ in ls], " box right =", 50 + W)

    # 4. line-height
    for lh in ("18.2pt", "1.0", "200%"):
        page, rc, dt, ls, d = htmlbox(f"<p>{ZEN * 2}</p>", css_extra=f"p {{line-height: {lh};}}")
        pitches = [round(b[0] - a[0], 2) for a, b in zip(ls, ls[1:])]
        print(f"--- line-height {lh}: baseline pitches {pitches}  first baseline y={ls[0][0]} (box top 50)")

    # 5. bold / colour runs, mixed fonts
    html = f"<p>普通の文字と<b>太字の語句</b>と<span style='color:#d90000'>赤い語句</span>と<span style='font-family:YG'>ゴシックの語句</span>を混ぜます。{ZEN[:20]}</p>"
    page, rc, dt, ls, d = htmlbox(html)
    spans = [(s["font"], hex(s["color"]), s["text"]) for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    print("--- runs:", spans)
    print("   fonts in pdf:", [f[3] for f in page.get_fonts()])
    d.save(OUT / "_s4_runs_nosubset.pdf", garbage=3, deflate=True)
    nos = (OUT / "_s4_runs_nosubset.pdf").stat().st_size
    (OUT / "_s4_runs_nosubset.pdf").unlink()
    d.subset_fonts()
    d.save(OUT / "s4_runs.pdf", garbage=3, deflate=True)
    print(f"   file size without subset_fonts={nos} bytes, with subset_fonts={(OUT / 's4_runs.pdf').stat().st_size} bytes")

    # 6. overflow / scale behaviour
    long = ZEN * 6
    for sl in (1, 0):
        dd = pymupdf.open()
        pg = dd.new_page()
        rc = pg.insert_htmlbox(pymupdf.Rect(50, 50, 50 + W, 90), f"<p>{long}</p>", css=CSS, archive=arch, scale_low=sl)
        print(f"--- overflow scale_low={sl}: rc={rc} lines placed={len(lines_in(pg))}")

    # 7. predictability: same html laid out in a scratch doc vs in the real page at another position
    html = f"<p>{txt}{ZEN}</p>"
    _, _, _, ls_a, _ = htmlbox(html)
    real = pymupdf.open(OUT.parent / "out" / "sample.pdf")
    pg = real[1]
    r = pymupdf.Rect(72, 300, 72 + W, 600)
    _, _, _, ls_b, _ = htmlbox(html, page=pg, rect=r)
    same = [t for *_, t in ls_a] == [t for *_, t in ls_b]
    print("--- predictability: same line breaks scratch vs real page:", same)
    # own prediction using Font.text_length on the htmlbox line texts
    for y, x0, x1, t in ls_a[:3]:
        print(f"   line width via get_text={x1 - x0:.2f}  via Font.text_length={F.text_length(t, SIZE):.2f}")
    t0 = time.perf_counter()
    for _ in range(20):
        htmlbox(html)
    print(f"   scratch layout cost: {(time.perf_counter() - t0) / 20 * 1000:.1f} ms per paragraph (incl. new doc)")
