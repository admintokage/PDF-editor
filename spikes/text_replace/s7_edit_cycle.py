"""Step 7: time for redact+reinsert of one paragraph on the 2-page sample, and stability over 5 consecutive edits."""
import re
import time
import pymupdf
from common import SAMPLE, OUT, face_bytes
from s2_paragraphs import paragraphs
from s3_redact_text import band_rects, para_rect, pix, diff_outside

arch = pymupdf.Archive()
arch.add(face_bytes("yumin.ttf"), "ym.ttf")
arch.add(face_bytes("yumindb.ttf"), "ymb.ttf")
CSS = """@font-face {font-family: YM; src: url(ym.ttf);}
@font-face {font-family: YM; src: url(ymb.ttf); font-weight: bold;}
* {font-family: YM; font-size: 10.5pt; margin: 0; padding: 0; line-height: 18.2pt;}
p {text-indent: 1em;}"""
PROBE = (100, 130)  # a point inside sample P1 on page 1


def find_para(page):
    for para in paragraphs(page):
        if para_rect(para).contains(PROBE):
            return para


def to_html(para, extra):
    out = []
    for l in para:
        for s in l["spans"]:
            t = s["text"].replace("\xa0", " ")
            if "Demibold" in s["font"]:
                t = f"<b>{t}</b>"
            if s["color"] != 0:
                t = f"<span style='color:#{s['color']:06x}'>{t}</span>"
            out.append(t)
    body = "".join(out)
    if extra.startswith("REPLACE:"):
        return "<p>" + re.sub(r"新しく|第\d回", extra[8:], body, count=1) + "</p>"
    return "<p>" + body + extra + "</p>"


def first_baseline_offset(width):
    d = pymupdf.open(); p = d.new_page()
    p.insert_htmlbox(pymupdf.Rect(0, 0, width, 300), "<p>あ</p>", css=CSS, archive=arch)
    return p.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]["origin"][1]


def edit_once(doc, extra):
    page = doc[0]
    t = {}
    t0 = time.perf_counter()
    para = find_para(page)
    r = para_rect(para)
    first_base = para[0]["spans"][0]["origin"][1]
    html = to_html(para, extra)
    t["detect"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    for rr in band_rects(page, r):
        page.add_redact_annot(rr, fill=False)
    page.apply_redactions(images=0, graphics=0)
    t["redact"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    box = pymupdf.Rect(r.x0 - 1, 0, r.x1 + 3, 0)  # +inset (htmlbox keeps ~1pt each side)
    box.y0 = first_base - first_baseline_offset(box.width)
    box.y1 = box.y0 + 200
    # F-14 check: where would the new text end, and what is the next content below?
    below = [para_rect(p).y0 for p in paragraphs(page) if para_rect(p).y0 > r.y1 - 1]
    rc = page.insert_htmlbox(box, html, css=CSS, archive=arch, scale_low=1)
    t["insert"] = time.perf_counter() - t0
    new_lines = [l for b in page.get_text("dict", clip=box)["blocks"] for l in b.get("lines", [])
                 if l["spans"] and l["spans"][0]["font"].startswith("YuMincho")]
    new_bottom = max(l["bbox"][3] for l in new_lines)  # glyph box bottom, not the CSS line box (box.y1 - rc[0])
    nxt = min(below) if below else page.rect.y1
    print(f"   F-14 check: old bottom={r.y1:.1f} new text bottom={new_bottom:.1f} next content top={nxt:.1f} -> {'OVERLAP WARNING' if new_bottom > nxt else 'fits'}")
    return t, rc, first_base


def check(doc, label, base_pix, base_texts, target_rect):
    page = doc[0]
    texts = ["".join(l["text"] for l in p) for p in paragraphs(page)]
    others_ok = all(bt in texts for bt in base_texts[2:])  # P2.. unchanged
    p1 = pix(page)
    out, inside, first = diff_outside(base_pix, p1, target_rect + (0, 0, 0, 2))
    para = find_para(page)
    print(f"   {label}: paragraphs={len(texts)} others unchanged={others_ok} edited lines={len(para)} "
          f"px changed outside edit area={out} fonts on p1={len(page.get_fonts())} xrefs={doc.xref_length()}")


base = pymupdf.open(SAMPLE)
base_pix = pix(base[0])
base_texts = ["".join(l["text"] for l in p) for p in paragraphs(base[0])]
target_rect = para_rect(find_para(base[0]))
print("sample size", SAMPLE.stat().st_size)

print("== A: open -> edit -> save, 5 times in a row (each on the previous output)")
src = SAMPLE
for i in range(1, 6):
    doc = pymupdf.open(src)
    t, rc, fb = edit_once(doc, f"REPLACE:第{i}回")
    t0 = time.perf_counter()
    doc.subset_fonts()
    t["subset"] = time.perf_counter() - t0
    out = OUT / f"s7_edit{i}.pdf"
    t0 = time.perf_counter()
    doc.save(out, garbage=3, deflate=True)
    t["save"] = time.perf_counter() - t0
    print(f" edit {i}: rc={tuple(round(x, 1) for x in rc)} " + " ".join(f"{k}={v * 1000:.0f}ms" for k, v in t.items())
          + f" total={sum(t.values()) * 1000:.0f}ms size={out.stat().st_size}")
    d2 = pymupdf.open(out)
    check(d2, f"after edit {i}", base_pix, base_texts, target_rect)
    np = find_para(d2[0])
    print(f"   first baseline orig={fb:.2f} new={np[0]['spans'][0]['origin'][1]:.2f}  fonts={[f[3] for f in d2[0].get_fonts()]}")
    if i == 5:
        print("   text:", "".join(l["text"] for l in np)[-40:])
        d2[0].get_pixmap(dpi=80).save(OUT / "s7_edit5_p1.png")
    d2.close()
    src = out

print("== A2: text that grows by one line (expected F-14 warning)")
doc = pymupdf.open(SAMPLE)
edit_once(doc, "（ここに一行分ほどの追記を加えて、段落が一行増えるようにします。）")
print("== B: 5 edits in memory, single save")
doc = pymupdf.open(SAMPLE)
for i in range(1, 6):
    t, rc, fb = edit_once(doc, f"REPLACE:第{i}回")
print("   last edit timings:", {k: round(v * 1000) for k, v in t.items()})
doc.subset_fonts()
doc.save(OUT / "s7_mem5.pdf", garbage=3, deflate=True)
print("   size", (OUT / "s7_mem5.pdf").stat().st_size)
check(pymupdf.open(OUT / "s7_mem5.pdf"), "after 5 in-memory edits", base_pix, base_texts, target_rect)
