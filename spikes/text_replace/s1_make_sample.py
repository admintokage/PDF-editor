"""Step 1 (fallback): build a Word-like 2-page sample with pymupdf and Windows fonts.

Word COM export failed (see findings), so this imitates a Word export:
per-line text runs, embedded subset CID fonts, no paragraph gap, 1-char first-line indent,
mixed bold/colour runs, bullets, a grey shaded paragraph and a ruled table.
"""
import pymupdf
from common import SAMPLE, face_bytes

lines = (SAMPLE.parent.parent / "sample_text.txt").read_text(encoding="utf-8").splitlines()

FACES = {  # key: (file, ttc index)
    "YM": ("yumin.ttf", 0), "YMB": ("yumindb.ttf", 0),
    "YG": ("YuGothR.ttc", 0), "YGB": ("YuGothB.ttc", 0),
    "MM": ("msmincho.ttc", 0), "ME": ("meiryo.ttc", 0),
}
FONT = {k: pymupdf.Font(fontbuffer=face_bytes(*v)) for k, v in FACES.items()}
BLACK, RED, BLUE = (0, 0, 0), (0.85, 0, 0), (0, 0.2, 0.8)
SIZE = 10.5
LEFT, RIGHT = 72.0, 523.0  # A4 595 wide, ~25mm margins


def runs_for(text, base, marks):
    """split text into (str, fontkey, color) runs; marks: list of (substr, fontkey, color)."""
    runs = [(text, base, BLACK)]
    for sub, fk, col in marks:
        new = []
        for t, f, c in runs:
            if sub in t and f == base and c == BLACK:
                a, b = t.split(sub, 1)
                new += [(a, f, c), (sub, fk, col), (b, f, c)]
            else:
                new.append((t, f, c))
        runs = new
    return [r for r in runs if r[0]]


def layout(runs, width, indent):
    """naive char wrap (no kinsoku) -> list of lines, each list of (x_offset, str, font, color)."""
    out, cur, x = [], [], indent
    for t, f, c in runs:
        for ch in t:
            w = FONT[f].text_length(ch, SIZE)
            if x + w > width + 0.01:
                out.append(cur)
                cur, x = [], 0.0
            if cur and cur[-1][2] == f and cur[-1][3] == c:
                cur[-1][1] += ch
            else:
                cur.append([x, ch, f, c])
            x += w
    if cur:
        out.append(cur)
    return out


def put_para(page, y, runs, pitch, left=LEFT, right=RIGHT, indent=SIZE, shade=None):
    ls = layout(runs, right - left, indent)
    if shade:
        page.draw_rect(pymupdf.Rect(left - 2, y - SIZE - 3, right + 2, y + pitch * (len(ls) - 1) + 5),
                       color=None, fill=shade, overlay=False)
    for ln in ls:
        for x, s, f, c in ln:
            page.insert_text((left + x, y), s, fontname=f, fontsize=SIZE, color=c)
        y += pitch
    return y


doc = pymupdf.open()
p = doc.new_page(width=595, height=842)
for k, v in FACES.items():  # same resource names on both pages
    pass


def add_fonts(page):
    for k, v in FACES.items():
        page.insert_font(fontname=k, fontbuffer=face_bytes(*v))


add_fonts(p)
y = 90
# title
p.insert_text((LEFT, y), lines[0], fontname="YGB", fontsize=14)
y += 30
y = put_para(p, y, runs_for(lines[1], "YM", [(lines[12], "YMB", BLACK), (lines[13], "YM", RED)]), 18.2)
y = put_para(p, y, runs_for(lines[2], "YG", []), 18.2, shade=(0.85, 0.85, 0.85))
for i in (3, 4, 5):  # bullets
    p.insert_text((LEFT + 4, y), "・", fontname="ME", fontsize=SIZE)
    y = put_para(p, y, runs_for(lines[i], "ME", []), 20.0, left=LEFT + 21, indent=0)
y = put_para(p, y, runs_for(lines[6], "MM", []), 15.8)
# table 3x3 ruled
y += 6
cols = [LEFT, LEFT + 110, LEFT + 260, RIGHT]
rh = 22
for r in range(4):
    p.draw_line((LEFT, y + r * rh), (RIGHT, y + r * rh), color=BLACK, width=0.5)
for cx in cols:
    p.draw_line((cx, y), (cx, y + 3 * rh), color=BLACK, width=0.5)
for r in range(3):
    for c, cell in enumerate(lines[9 + r].split("\t")):
        p.insert_text((cols[c] + 5, y + r * rh + 15), cell, fontname="MM", fontsize=SIZE)
y += 3 * rh + 24

p2 = doc.new_page(width=595, height=842)
add_fonts(p2)
y = 90
y = put_para(p2, y, runs_for(lines[7], "ME", [(lines[14], "ME", BLUE)]), 20.0)
y = put_para(p2, y, runs_for(lines[8], "YM", []), 18.2)
doc.subset_fonts()
doc.ez_save(SAMPLE)
d = pymupdf.open(SAMPLE)
print("saved", SAMPLE, SAMPLE.stat().st_size, "bytes, pages", d.page_count)
for pg in d:
    print(pg.number, [(f[3], f[1], f[2]) for f in pg.get_fonts()])
for pg in d:
    pg.get_pixmap(dpi=80).save(SAMPLE.parent / f"sample_p{pg.number + 1}.png")
