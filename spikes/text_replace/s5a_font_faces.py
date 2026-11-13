"""Step 5a: list faces inside each font file, their names and OS/2 fsType (embedding permission)."""
from common import FONTS, ttc_offsets, names, fstype, fstype_meaning

FILES = ["yumin.ttf", "yumindb.ttf", "YuGothR.ttc", "YuGothM.ttc", "YuGothB.ttc", "msmincho.ttc",
         "msgothic.ttc", "meiryo.ttc", "meiryob.ttc", "BIZ-UDGothicR.ttc", "BIZ-UDGothicB.ttc",
         "BIZ-UDMinchoM.ttc"]

for f in FILES:
    data = (FONTS / f).read_bytes()
    for i, off in enumerate(ttc_offsets(data)):
        n = names(data, off)
        fam = n.get(1, {}).get(0x409, "?")
        famj = n.get(1, {}).get(0x411, "")
        sub = n.get(2, {}).get(0x409, "?")
        ps = n.get(6, {}).get(0x409, "?")
        print(f"{f:20s} idx{i} family={fam!r} ja={famj!r} style={sub!r} ps={ps!r} fsType={fstype_meaning(fstype(data, off))}")
