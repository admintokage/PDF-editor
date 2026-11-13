"""Step 6: redaction leaks. Text, image, partly covered vector lines, annotations, form field values,
text inside a Form XObject, invisible text. After apply_redactions + save, can the secret be recovered?"""
import re
import zlib
import pymupdf
from common import OUT, face_bytes

SECRETS = {"text": "SECRETTEXT1", "invis": "SECRETINVIS", "annot_ft": "SECRETFREETEXT", "annot_note": "SECRETNOTE",
           "field": "SECRETFIELD", "xobj": "SECRETXOBJ", "jp": "機密の日本語"}
R = pymupdf.Rect(80, 80, 400, 330)  # redaction area (whole test area)


def build(path):
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_font(fontname="YG", fontbuffer=face_bytes("YuGothR.ttc"))
    pg.insert_text((100, 100), SECRETS["text"], fontname="helv", fontsize=12)
    pg.insert_text((100, 120), SECRETS["invis"], fontname="helv", fontsize=12, render_mode=3)
    pg.insert_text((100, 140), SECRETS["jp"], fontname="YG", fontsize=12)
    # image 200x60 px with a gradient; placed so redaction covers its left half only
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 60), False)
    for x in range(200):
        for y in range(60):
            pix.set_pixel(x, y, (x % 256, (y * 4) % 256, 200))
    img_rect = pymupdf.Rect(300, 150, 500, 210)  # x 300..400 inside R, 400..500 outside
    xref_img = pg.insert_image(img_rect, pixmap=pix)
    # vector line crossing the edge of R, plus one fully inside, plus one outside
    pg.draw_line((300, 250), (550, 250), color=(0, 0, 1), width=2)   # partly covered
    pg.draw_line((100, 260), (200, 260), color=(1, 0, 0), width=2)   # fully covered
    pg.draw_line((100, 400), (500, 400), color=(0, 1, 0), width=2)   # outside
    # annotations
    pg.add_freetext_annot(pymupdf.Rect(100, 160, 250, 180), SECRETS["annot_ft"])
    pg.add_text_annot((110, 190), SECRETS["annot_note"])
    # form field
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "f1"
    w.rect = pymupdf.Rect(100, 270, 250, 290)
    w.field_value = SECRETS["field"]
    pg.add_widget(w)
    # Form XObject: text on another page shown via show_pdf_page
    src = pymupdf.open()
    sp = src.new_page(width=200, height=40)
    sp.insert_text((5, 25), SECRETS["xobj"], fontname="helv", fontsize=12)
    pg.show_pdf_page(pymupdf.Rect(100, 295, 300, 335), src, 0)
    # same image on page 2 (shared xref)
    p2 = doc.new_page()
    p2.insert_image(pymupdf.Rect(50, 50, 250, 110), pixmap=pix)
    doc.set_metadata({"title": "SECRETMETA"})
    doc.subset_fonts()
    doc.save(path, garbage=3, deflate=True)
    return xref_img


def raw_hits(path):
    """decompress every stream & object (like mutool clean -d) and search for the secrets as bytes."""
    doc = pymupdf.open(path)
    blob = b""
    for x in range(1, doc.xref_length()):
        try:
            blob += doc.xref_object(x, compressed=False).encode("latin1", "replace")
            if doc.xref_is_stream(x):
                blob += doc.xref_stream(x) or b""
        except Exception:
            pass
    # also raw file bytes + zlib-inflate any FlateDecode blobs found directly in the file (incl. stale objects of incremental saves)
    data = open(path, "rb").read()
    blob += data
    for m in re.finditer(rb"stream\r?\n", data):
        try:
            blob += zlib.decompressobj().decompress(data[m.end():m.end() + 2_000_000])
        except Exception:
            pass
    low = blob.lower()
    yg = pymupdf.Font(fontbuffer=face_bytes("YuGothR.ttc"))

    def found(k, v):
        if k == "jp":  # CID font: content stream holds glyph ids as 4-hex-digit codes
            pat = "".join("%04x" % yg.has_glyph(ord(c)) for c in v).encode()
            return pat in low
        return v.encode() in blob or v.encode("utf-16-be") in blob or v.encode().hex().encode() in low
    hits = {k: found(k, v) for k, v in SECRETS.items()}
    hits["meta"] = b"SECRETMETA" in blob
    doc.close()
    return hits


def text_hits(path):
    doc = pymupdf.open(path)
    t = "".join(p.get_text() for p in doc)
    for p in doc:
        for a in p.annots() or []:
            t += (a.info.get("content") or "")
        for w in p.widgets() or []:
            t += str(w.field_value)
    res = {k: v in t for k, v in SECRETS.items()}
    doc.close()
    return res


def state(path, xref_img):
    doc = pymupdf.open(path)
    pg = doc[0]
    annots = [(a.type[1], a.info.get("content")) for a in pg.annots()]
    widgets = [(w.field_name, w.field_value) for w in pg.widgets()]
    lines = [(tuple(round(v) for v in d["rect"]), d.get("color")) for d in pg.get_drawings() if d.get("color") and d["color"] != (0.0, 0.0, 0.0)]
    imgs = pg.get_images(full=True)
    info = {"annots": annots, "widgets": widgets, "coloured lines": lines, "page1 images": [i[0] for i in imgs]}
    # covered half of the image: sample a pixel at x=20 (inside R) and x=180 (outside)
    for im in imgs:
        pm = pymupdf.Pixmap(doc, im[0])
        info[f"img{im[0]} {pm.width}x{pm.height} px(20,30)/(180,30)"] = (pm.pixel(20, 30), pm.pixel(180, 30))
    p2 = doc[1].get_images()
    pm2 = pymupdf.Pixmap(doc, p2[0][0])
    info["page2 image px(20,30)"] = pm2.pixel(20, 30)
    doc.close()
    return info


src = OUT / "s6_src.pdf"
xref_img = build(src)
print("control (before redaction): text", text_hits(src))
print("control (before redaction): raw ", raw_hits(src))

for label, kw, save in [
    ("defaults(images=2 pixels, graphics=1 if-covered)", {}, "full"),
    ("graphics=2 (remove if touched)", {"graphics": pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED}, "full"),
    ("defaults, save garbage=0 (no cleanup)", {}, "nogc"),
    ("defaults, incremental save", {}, "incr"),
]:
    out = OUT / f"s6_{save}_{len(kw)}.pdf"
    if save == "incr":
        import shutil
        shutil.copy(src, out)
        doc = pymupdf.open(out)
    else:
        doc = pymupdf.open(src)
    pg = doc[0]
    pg.add_redact_annot(R, fill=(0, 0, 0))
    pg.apply_redactions(**kw)
    if save == "full":
        doc.save(out, garbage=4, deflate=True, clean=True)
    elif save == "nogc":
        doc.save(out, garbage=0)
    else:
        doc.saveIncr()
    doc.close()
    print(f"\n### {label} -> {out.name} ({out.stat().st_size} bytes)")
    print("  get_text / annots / widgets still contain:", {k: v for k, v in text_hits(out).items() if v} or "none")
    print("  raw decompressed search finds:", {k: v for k, v in raw_hits(out).items() if v} or "none")
    for k, v in state(out, xref_img).items():
        print(f"  {k}: {v}")

# supplementary: delete annots + widgets in the area ourselves, then redact
print("\n### manual: delete annots/widgets intersecting R, then redact + full save")
doc = pymupdf.open(src)
pg = doc[0]
for a in list(pg.annots()):
    if a.rect.intersects(R):
        pg.delete_annot(a)
for w in list(pg.widgets()):
    if w.rect.intersects(R):
        pg.delete_widget(w)
pg.add_redact_annot(R, fill=(0, 0, 0))
pg.apply_redactions()
out = OUT / "s6_manual.pdf"
doc.save(out, garbage=4, deflate=True, clean=True)
doc.close()
print("  get_text hits:", {k: v for k, v in text_hits(out).items() if v} or "none")
print("  raw hits:", {k: v for k, v in raw_hits(out).items() if v} or "none")
d = pymupdf.open(out)
print("  AcroForm fields left:", d.xref_get_key(d.pdf_catalog(), "AcroForm"), [x for x in d[0].widgets()])
pg = d[0]
pg.get_pixmap(dpi=60).save(OUT / "s6_manual.png")
