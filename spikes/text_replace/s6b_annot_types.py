"""Step 6b: which annotation types does apply_redactions remove?"""
import pymupdf
doc = pymupdf.open(); pg = doc.new_page()
R = pymupdf.Rect(50, 50, 400, 400)
pg.insert_text((60, 70), "hello world", fontname="helv")
pg.add_text_annot((60, 100), "note")
pg.add_freetext_annot(pymupdf.Rect(60, 120, 200, 140), "freetext")
pg.add_highlight_annot(pg.search_for("hello")[0]).set_info(content="hl")
pg.add_rect_annot(pymupdf.Rect(60, 150, 100, 190)).set_info(content="square")
pg.add_ink_annot([[(60, 200), (90, 230)]]).set_info(content="ink")
pg.add_stamp_annot(pymupdf.Rect(120, 200, 220, 240), 0).set_info(content="stamp")
pg.add_line_annot((60, 260), (200, 260)).set_info(content="line")
pg.add_underline_annot(pg.search_for("world")[0]).set_info(content="ul")
pg.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(60, 300, 200, 320), "uri": "https://example.com/secret"})
pg.add_file_annot((300, 300), b"secret attachment", "a.txt")
pg = doc.reload_page(pg)
before = [a.type[1] for a in pg.annots()]
print("before:", before, "links:", len(pg.get_links()))
pg.add_redact_annot(R, fill=(0, 0, 0)); pg.apply_redactions()
doc.save("out/s6b.pdf", garbage=4); doc = pymupdf.open("out/s6b.pdf"); pg = doc[0]
print("raw uri left:", b"example.com/secret" in open("out/s6b.pdf","rb").read(), "attachment left:", b"secret attachment" in b"".join((doc.xref_stream(x) or b"") for x in range(1, doc.xref_length()) if doc.xref_is_stream(x)))
after = [a.type[1] for a in pg.annots()]
print("after :", after, "links:", len(pg.get_links()))
print("removed:", [t for t in before if t not in after])
