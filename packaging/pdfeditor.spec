# PyInstaller spec。環境変数 PDFEDITOR_TARGET (smoke | app) で配布物を切り替える。
# build.ps1 から呼ばれる想定。直接実行する場合は pyinstaller を使う前に環境変数を設定すること。
import os
from pathlib import Path

target = os.environ.get("PDFEDITOR_TARGET", "app")
root = Path(SPECPATH).parent

if target == "smoke":
    name = "PDF-editor-smoke"
    entry = root / "packaging" / "smoke_app.py"
elif target == "app":
    name = "PDF-editor"
    entry = root / "pdfeditor" / "__main__.py"
else:
    raise SystemExit(f"PDFEDITOR_TARGET が不正です: {target}")

if not entry.exists():
    raise SystemExit(f"エントリポイントがありません: {entry}")

a = Analysis(
    [str(entry)],
    pathex=[str(root)],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=name,
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name=name,
)
