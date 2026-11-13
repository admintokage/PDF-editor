"""Step 5b: picking a face from .ttc, embedding, subset size, fsType behaviour, PDF font name -> installed font."""
import os
import re
import time
from pathlib import Path
import pymupdf
from common import FONTS, OUT, face_bytes, ttc_offsets, names, fstype, fstype_meaning

TXT = "フォント埋め込みの試験です。ABC abc 123 永東国。"


def used_font(page):
    return sorted({s["font"] for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]})


print("== which face does each API pick from a .ttc?")
for f, want in (("msmincho.ttc", 1), ("meiryo.ttc", 2), ("YuGothM.ttc", 1)):
    path = str(FONTS / f)
    print(f"-- {f}: want idx{want}")
    print("   Font(fontfile=ttc).name          ->", pymupdf.Font(fontfile=path).name)
    print("   Font(fontbuffer=extracted idx).name ->", pymupdf.Font(fontbuffer=face_bytes(f, want)).name)
    d = pymupdf.open(); p = d.new_page()
    p.insert_font(fontname="F0", fontfile=path)
    p.insert_text((50, 50), TXT, fontname="F0")
    print("   page.insert_font(fontfile=ttc)   ->", used_font(p))
    d = pymupdf.open(); p = d.new_page()
    try:
        p._insertFont("F1", None, path, None, False, want, 0, 0, 0, -1)
        p.insert_text((50, 50), TXT, fontname="F1")
        print(f"   private page._insertFont(idx={want}) ->", used_font(p), " (private API)")
    except Exception as e:
        print("   private _insertFont failed:", e)
    d = pymupdf.open(); p = d.new_page()
    a = pymupdf.Archive(); a.add(Path(path).read_bytes(), "x.ttc")
    p.insert_htmlbox((50, 50, 400, 200), f"<p style='font-family:X'>{TXT}</p>",
                     css="@font-face {font-family: X; src: url(x.ttc);}", archive=a)
    print("   insert_htmlbox url(x.ttc)        ->", used_font(p))

print("\n== embedding + subset size (one line of text per font, A4 page)")
for f, idx in (("yumin.ttf", 0), ("YuGothR.ttc", 0), ("msmincho.ttc", 0), ("msgothic.ttc", 0), ("meiryo.ttc", 0),
               ("BIZ-UDGothicR.ttc", 0), ("BIZ-UDMinchoM.ttc", 0)):
    d = pymupdf.open(); p = d.new_page()
    p.insert_font(fontname="F", fontbuffer=face_bytes(f, idx))
    p.insert_text((50, 80), TXT, fontname="F", fontsize=12)
    full = OUT / "_tmp_full.pdf"; sub = OUT / "_tmp_sub.pdf"
    d.save(full, garbage=3, deflate=True)
    t0 = time.perf_counter(); d.subset_fonts(); dt = time.perf_counter() - t0
    d.save(sub, garbage=3, deflate=True)
    d2 = pymupdf.open(sub)
    fonts = d2[0].get_fonts()
    emb = [(x[3], x[1], len(d2.extract_font(x[0])[3])) for x in fonts]
    print(f"   {f:20s} face={len(face_bytes(f, idx)) // 1024:6d}KB  pdf full={full.stat().st_size // 1024:6d}KB"
          f"  subset={sub.stat().st_size // 1024:4d}KB  subset_fonts()={dt * 1000:.0f}ms  embedded={emb}  text ok={d2[0].get_text().strip() == TXT} {d2[0].get_text().strip()[:30]!r}")
    d2.close()
    full.unlink(); sub.unlink()

print("\n== does pymupdf look at fsType? (BIZ UD = 0x0004 preview&print)")
fb = pymupdf.Font(fontbuffer=face_bytes("BIZ-UDGothicR.ttc", 0))
print("   Font.flags:", fb.flags)
print("   -> pymupdf embeds it without complaint (see table above); the app must read fsType itself")

print("\n== font index of installed fonts (for PDF name matching)")
t0 = time.perf_counter()
dirs = [FONTS, Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts"]
index = {}  # normalized name -> (file, idx, ps, fsType)


def norm(s):
    return re.sub(r"[\s\-_,]", "", s).lower()


nfiles = 0
for dr in dirs:
    if not dr.exists():
        continue
    for fp in dr.iterdir():
        if fp.suffix.lower() not in (".ttf", ".ttc", ".otf"):
            continue
        try:
            data = fp.read_bytes()
            nfiles += 1
            for i, off in enumerate(ttc_offsets(data)):
                n = names(data, off)
                fs = fstype(data, off)
                ps = next(iter(n.get(6, {}).values()), "?")
                for nid in (1, 4, 6):
                    for lang, s in n.get(nid, {}).items():
                        index.setdefault(norm(s), (fp.name, i, ps, fs))
                        if nid == 1:  # family + subfamily combos
                            for sub in n.get(2, {}).values():
                                index.setdefault(norm(s + sub), (fp.name, i, ps, fs))
        except Exception as e:  # noqa
            pass
print(f"   scanned {nfiles} files, {len(index)} name keys in {time.perf_counter() - t0:.2f}s (reads whole files; header-only read would be faster)")


def lookup(pdfname):
    base = pdfname.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", pdfname) else pdfname
    base = re.sub(r",(Bold|Italic|BoldItalic)$", r"\1", base)
    for cand in (base, base.replace("-", " "), re.sub(r"-?(Regular|Roman|Book)$", "", base)):
        hit = index.get(norm(cand))
        if hit:
            return hit
    return None


for nm in ["DGXZZG+Yu Mincho Regular", "ABCDEF+YuMincho-Regular", "ABCDEF+YuMincho-Demibold", "ABCDEF+YuGothic-Bold",
           "ABCDEF+YuGothic-Medium", "ABCDEF+MS-Mincho", "ABCDEF+MSMincho", "ABCDEF+MS-PGothic", "ABCDEF+Meiryo",
           "ABCDEF+Meiryo,Bold", "ABCDEF+游明朝", "ABCDEF+ＭＳ明朝", "ABCDEF+BIZ-UDGothic", "ABCDEF+Century",
           "ABCDEF+HiraginoSans-W3", "ABCDEF+KozMinPr6N-Regular", "ABCDEF+YuGothicUI-Regular", "#82l#82r#96#BE#92#A9"]:
    hit = lookup(nm)
    print(f"   {nm:32s} -> {hit[0] + ' idx' + str(hit[1]) + ' ' + hit[2] + ' ' + fstype_meaning(hit[3]) if hit else 'NOT FOUND'}")
