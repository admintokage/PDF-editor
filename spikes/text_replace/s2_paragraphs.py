"""Step 2: paragraph estimation from page.get_text('dict')."""
import sys
import statistics
import pymupdf
from common import SAMPLE

path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".pdf") else str(SAMPLE)
doc = pymupdf.open(path)


def raw_lines(page):
    d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    out = []
    for b in d["blocks"]:
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            out.append({"block": b["number"], "bbox": pymupdf.Rect(ln["bbox"]), "spans": spans,
                        "text": "".join(s["text"] for s in ln["spans"])})
    return out


def table_rects(page):
    rects = []
    try:
        for t in page.find_tables().tables:
            rects.append(pymupdf.Rect(t.bbox))
    except Exception as e:  # noqa
        print("find_tables failed:", e)
    return rects


def paragraphs(page):
    lines = raw_lines(page)
    tabs = table_rects(page)
    body = []
    for ln in lines:
        if any(t.contains(ln["bbox"]) or t.intersects(ln["bbox"]) and (t & ln["bbox"]).get_area() > 0.5 * ln["bbox"].get_area() for t in tabs):
            continue
        body.append(ln)
    # merge lines on the same baseline (e.g. bullet glyph + text are separate lines in MuPDF)
    body.sort(key=lambda l: (round(l["bbox"].y1, 0), l["bbox"].x0))
    merged = []
    for ln in body:
        if merged and abs(merged[-1]["bbox"].y1 - ln["bbox"].y1) < 2 and ln["bbox"].x0 - merged[-1]["bbox"].x1 < 30:
            m = merged[-1]
            m["bbox"] |= ln["bbox"]
            m["spans"] += ln["spans"]
            m["text"] += ln["text"]
            m["parts"] = m.get("parts", 1) + 1
        else:
            merged.append(dict(ln))
    if not merged:
        return []
    col_left = min(l["bbox"].x0 for l in merged)
    col_right = max(l["bbox"].x1 for l in merged)
    paras = [[merged[0]]]
    for prev, ln in zip(merged, merged[1:]):
        size = statistics.median(s["size"] for s in ln["spans"])
        pitch_prev = prev["bbox"].y1 - (paras[-1][-2]["bbox"].y1 if len(paras[-1]) > 1 else prev["bbox"].y1 - 1.8 * size)
        gap = ln["bbox"].y1 - prev["bbox"].y1
        font_prev = prev["spans"][0]["font"]
        font_cur = ln["spans"][0]["font"]
        reasons = []
        if gap > 1.4 * max(pitch_prev, 1.2 * size):
            reasons.append("gap")
        if prev["bbox"].x1 < col_right - 1.5 * size:
            reasons.append("short-prev")
        if ln["bbox"].x0 > prev["bbox"].x0 + 0.6 * size and ln["bbox"].x0 - col_left < 2.5 * size:
            reasons.append("indent")
        if ln["text"].lstrip()[:1] in "・●■◆-–•" or ln.get("parts", 1) > 1 and ln["spans"][0]["text"].strip() in "・●■◆•":
            reasons.append("bullet")
        if font_prev.split("+")[-1] != font_cur.split("+")[-1] and abs(gap - pitch_prev) > 0.5:
            reasons.append("font+pitch")
        if reasons:
            ln["why"] = reasons
            paras.append([ln])
        else:
            paras[-1].append(ln)
    return paras


if __name__ == "__main__":
  for page in doc:
      print(f"=== page {page.number + 1}")
      d = page.get_text("dict")
      print("MuPDF blocks:", [(b["number"], len(b.get("lines", []))) for b in d["blocks"] if b["type"] == 0])
      print("tables:", table_rects(page))
      for i, para in enumerate(paragraphs(page)):
          r = pymupdf.Rect()
          for l in para:
              r |= l["bbox"]
          fonts = sorted({s["font"] for l in para for s in l["spans"]})
          colors = sorted({s["color"] for l in para for s in l["spans"]})
          txt = "".join(l["text"] for l in para)
          print(f" P{i}: lines={len(para)} bbox={tuple(round(v, 1) for v in r)} why={para[0].get('why')} fonts={fonts} colors={[hex(c) for c in colors]}")
          print("     ", txt[:60] + ("..." if len(txt) > 60 else ""))
