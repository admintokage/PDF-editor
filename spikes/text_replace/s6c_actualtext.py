"""Step 6c: marked-content /ActualText and StructTree /Alt (as in tagged Word exports) after redaction."""
import pymupdf
from common import OUT
doc = pymupdf.open(); pg = doc.new_page()
pg.insert_text((100, 100), "visible", fontname="helv")
xref = pg.get_contents()[0]
c = doc.xref_stream(xref)
doc.update_stream(xref, b"/Span <</ActualText (SECRETACTUAL)>> BDC\n" + c + b"\nEMC\n")
# minimal StructTreeRoot with an /Alt text
st = doc.get_new_xref(); doc.update_object(st, "<</Type/StructTreeRoot /K [<</S/Figure /Alt (SECRETALT)>>]>>")
doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{st} 0 R")
pg = doc.reload_page(pg)
print("before: get_text", repr(pg.get_text().strip()))
pg.add_redact_annot(pg.rect, fill=(0, 0, 0)); pg.apply_redactions()
out = OUT / "s6c.pdf"; doc.save(out, garbage=3)
d = pymupdf.open(out)
blob = b"".join(d.xref_object(x).encode("latin1", "replace") + ((d.xref_stream(x) or b"") if d.xref_is_stream(x) else b"") for x in range(1, d.xref_length()))
print("after: get_text", repr(d[0].get_text().strip()), "| ActualText left:", b"SECRETACTUAL" in blob, "| StructTree Alt left:", b"SECRETALT" in blob)
