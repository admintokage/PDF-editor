"""Step 3: remove only one paragraph's text (keep graphics, keep neighbours)."""
import pymupdf
from common import SAMPLE, OUT, face_bytes
from s2_paragraphs import paragraphs

DPI = 100


def pix(page):
    return page.get_pixmap(dpi=DPI, alpha=False)


def diff_outside(p0, p1, keep: pymupdf.Rect, pad=1):
    """count differing pixels outside keep rect (in page coords), and inside."""
    s = DPI / 72
    kx0, ky0, kx1, ky1 = (keep.x0 * s - pad, keep.y0 * s - pad, keep.x1 * s + pad, keep.y1 * s + pad)
    a, b = p0.samples, p1.samples
    w, n = p0.width, p0.n
    out = inside = 0
    first = None
    for i in range(0, len(a), n):
        if a[i:i + n] != b[i:i + n]:
            x, y = (i // n) % w, (i // n) // w
            if kx0 <= x <= kx1 and ky0 <= y <= ky1:
                inside += 1
            else:
                out += 1
                first = first or (round(x / s, 1), round(y / s, 1))
    return out, inside, first


def texts_by_para(page):
    return ["".join(l["text"] for l in p) for p in paragraphs(page)]


def para_rect(para):
    r = pymupdf.Rect()
    for l in para:
        r |= l["bbox"]
    return r


def band_rects(page, rect):
    """thin band through the middle of each char's em-box, computed from baseline origin and font size."""
    res = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                for c in s["chars"]:
                    r = pymupdf.Rect(c["bbox"])
                    oy = c["origin"][1]
                    my = oy - 0.38 * s["size"]
                    if rect.contains(pymupdf.Point((r.x0 + r.x1) / 2, my)):
                        res.append(pymupdf.Rect(r.x0 + 0.2, my - 0.3, r.x1 - 0.2, my + 0.3))
    return res


def char_rects(page, rect, shrink=0.25):
    """per-character rects of chars whose centre is inside rect, shrunk vertically."""
    res = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                for c in s["chars"]:
                    r = pymupdf.Rect(c["bbox"])
                    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                    if rect.contains(pymupdf.Point(cx, cy)):
                        h = r.height
                        res.append(pymupdf.Rect(r.x0 + 0.1, r.y0 + h * shrink, r.x1 - 0.1, r.y1 - h * shrink))
    return res


def run(target_idx, mode, fill, tag):
    doc = pymupdf.open(SAMPLE)
    page = doc[0]
    before = texts_by_para(page)
    paras = paragraphs(page)
    tr = para_rect(paras[target_idx])
    p0 = pix(page)
    if mode == "bbox":
        page.add_redact_annot(tr, fill=fill)
    else:
        for r in char_rects(page, tr):
            page.add_redact_annot(r, fill=fill)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    after_all = page.get_text()
    p1 = pix(page)
    out, inside, first = diff_outside(p0, p1, tr)
    remaining_in_target = page.get_text(clip=tr).strip()
    others_before = [t for i, t in enumerate(before) if i != target_idx]
    lost = [t[:20] for t in others_before if t not in after_all.replace("\n", "")]
    # shading check: pixel in the target bbox but between lines/right margin
    doc.save(OUT / f"s3_{tag}.pdf", garbage=3, deflate=True)
    p1.save(OUT / f"s3_{tag}.png")
    print(f"[{tag}] target=P{target_idx} mode={mode} fill={fill} rect={tuple(round(v, 1) for v in tr)}")
    print(f"   text left inside target: {remaining_in_target!r:.40}")
    print(f"   other paragraphs damaged: {lost}")
    print(f"   pixels changed outside target(+1px): {out}  inside: {inside}  first-outside-at: {first}")
    return page, tr


def bg_color_at(path, pt):
    d = pymupdf.open(path)
    p = d[0].get_pixmap(dpi=72, clip=pymupdf.Rect(pt, pt[0] + 1, pt[1] + 1))
    return p.pixel(0, 0)


if __name__ == "__main__":
    run(1, "bbox", False, "p1_bbox_nofill")
    run(2, "bbox", False, "p2_bbox_nofill")
    run(2, "bbox", None, "p2_bbox_defaultfill")
    run(2, "chars", False, "p2_chars_nofill")
    for t in ("p2_bbox_nofill", "p2_bbox_defaultfill"):
        print(f"   {t}: colour at shaded area (500,205) =", bg_color_at(OUT / f"s3_{t}.pdf", (500, 205)))

    # tight line pitch case: two paragraphs, 10.5pt Yu Mincho, pitch 12pt (Word 'fixed 12pt')
    print("\n--- tight pitch (12pt) test")
    for fontfile, pitch, mode in [(f, p, m) for f in ("yumin.ttf", "meiryo.ttc") for p in (12, 10.5, 9) for m in ("bbox", "chars", "band")]:
        d = pymupdf.open()
        pg = d.new_page(width=300, height=200)
        pg.insert_font(fontname="YM", fontbuffer=face_bytes(fontfile))
        y = 40
        for t in ["あいうえおかきくけこさしすせそたちつてと", "なにぬねのはひふへほ。",
                  "まみむめもやゆよらりるれろわをんアイウエ", "カキクケコ。"]:
            pg.insert_text((20, y), t, fontname="YM", fontsize=10.5)
            y += pitch
        lines = pg.get_text("dict")["blocks"]
        bbs = [pymupdf.Rect(l["bbox"]) for b in lines for l in b["lines"]]
        print("   line bboxes:", [(round(b.y0, 1), round(b.y1, 1)) for b in bbs])
        target = bbs[0] | bbs[1]
        if mode == "bbox":
            pg.add_redact_annot(target, fill=False)
        else:
            for r in (char_rects if mode == "chars" else band_rects)(pg, target):
                pg.add_redact_annot(r, fill=False)
        pg.apply_redactions(images=0, graphics=0)
        print(f"   {fontfile} pitch={pitch} mode={mode}: text after =", pg.get_text().split())
