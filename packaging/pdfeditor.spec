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

# 使わない Qt のモジュール。ネットワーク系は配布物に入れない（外部通信をしない方針）。
UNUSED_QT_MODULES = [
    "Network", "Qml", "Quick", "QuickWidgets", "QuickControls2", "WebEngineCore", "WebEngineWidgets",
    "WebEngineQuick", "WebChannel", "WebSockets", "Multimedia", "MultimediaWidgets", "Sql", "Test",
    "Pdf", "PdfWidgets", "Svg", "SvgWidgets", "OpenGL", "OpenGLWidgets", "Designer", "UiTools",
    "Help", "Xml", "Concurrent", "PrintSupport", "Bluetooth", "Nfc", "Positioning", "Sensors",
    "SerialPort", "RemoteObjects", "Scxml", "StateMachine", "TextToSpeech", "3DCore",
]
a = Analysis(
    [str(entry)],
    pathex=[str(root)],
    excludes=["tkinter"] + [f"PySide6.Qt{m}" for m in UNUSED_QT_MODULES],
)

# 本体（app）は PNG / JPEG の画像だけ使う。Qt が自動で拾う不要な DLL・プラグイン・翻訳を外す。
# 画面は qwindows、テストと --self-test 用に qoffscreen は残す。
DROP_BINARIES = (
    "opengl32sw.dll", "qt6network.dll", "qt6svg.dll", "qt6opengl.dll", "qt6pdf.dll",
    "qdirect2d.dll", "qminimal.dll", "qsvg.dll", "qsvgicon.dll", "qicns.dll", "qico.dll", "qtga.dll",
    "qtiff.dll", "qwbmp.dll", "qwebp.dll", "qtuiotouchplugin.dll", "qnetworklistmanager.dll",
    "qcertonlybackend.dll", "qopensslbackend.dll", "qschannelbackend.dll",
)
KEEP_TRANSLATIONS = ("qt_ja.qm", "qtbase_ja.qm")


def _keep_binary(entry):
    return os.path.basename(entry[0]).lower() not in DROP_BINARIES


def _keep_data(entry):
    name = entry[0].replace("\\", "/")
    if "/translations/" in name or name.startswith("PySide6/translations"):
        return os.path.basename(name) in KEEP_TRANSLATIONS
    return os.path.basename(name).lower() not in DROP_BINARIES


if target == "app":
    a.binaries = [b for b in a.binaries if _keep_binary(b)]
    a.datas = [d for d in a.datas if _keep_data(d)]
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
