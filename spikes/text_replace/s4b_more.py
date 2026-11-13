"""Step 4b: more kinsoku cases and justify for pure Japanese."""
from s4_reflow import htmlbox, show, check, ZEN
cases = {
  "small kana っ at start": ZEN[:20] + "っ" + ZEN[20:30],
  "long vowel ー at start": ZEN[:20] + "ー" + ZEN[20:30],
  "！ at start": ZEN[:20] + "！" + ZEN[20:30],
  "。」 double at start": ZEN[:20] + "。」" + ZEN[20:30],
  "・ at start": ZEN[:20] + "・" + ZEN[20:30],
  "number 1,234 split": ZEN[:17] + "12,345円" + ZEN[20:30],
}
for k, t in cases.items():
    _, rc, dt, ls, _ = htmlbox(f"<p>{t}</p>")
    show(k, ls, rc, dt); print("   ->", check(ls))
t = ZEN[:20] + "、" + ZEN[20:44] + "。" + ZEN[:10]
_, rc, dt, ls, _ = htmlbox(f"<p>{t}</p>", css_extra="p {text-align: justify;}")
show("pure Japanese justify", ls, rc, dt); print("   right edges", [x1 for *_, x1, _ in [(a,b,c,d) for a,b,c,d in ls]])
_, rc, dt, ls, _ = htmlbox(f"<p style='text-indent:1em'>{t}</p>")
show("text-indent 1em", ls, rc, dt)
