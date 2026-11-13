import subprocess, pathlib, pytest
DIST = pathlib.Path("dist")

@pytest.mark.dist
def test_smoke_exe_self_test_exits_zero():
    exe = DIST / "PDF-editor-smoke" / "PDF-editor-smoke.exe"
    assert subprocess.run([exe, "--self-test"], timeout=60).returncode == 0

@pytest.mark.dist
def test_smoke_runs_from_japanese_path_with_space(tmp_path):
    import shutil
    dst = tmp_path / "日本語 フォルダ" / "PDF-editor-smoke"
    shutil.copytree(DIST / "PDF-editor-smoke", dst)
    assert subprocess.run([dst / "PDF-editor-smoke.exe", "--self-test"], timeout=60).returncode == 0

@pytest.mark.dist
def test_smoke_zip_under_150mb():
    assert (DIST / "PDF-editor-smoke.zip").stat().st_size < 150 * 1024 * 1024


# ---- 本体（PDF-editor）の配布物 ----
import os
import shutil
import zipfile

APP_DIR = DIST / "PDF-editor"
APP_EXE = APP_DIR / "PDF-editor.exe"


def _run_app(exe, *args, result_file):
    """画面を出さないよう offscreen で動かし、--result-file の中身を返す。"""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([exe, *map(str, args), "--result-file", str(result_file)],
                          env=env, timeout=60)
    text = result_file.read_text(encoding="utf-8") if result_file.exists() else ""
    return proc.returncode, text


@pytest.mark.dist
def test_app_self_test(simple_pdf, tmp_path):
    code, text = _run_app(APP_EXE, "--self-test", simple_pdf, result_file=tmp_path / "r.txt")
    assert (code, text) == (0, "OK")


@pytest.mark.dist
def test_app_self_test_reports_failure_without_dialog(tmp_path):
    missing = tmp_path / "nothing.pdf"
    code, text = _run_app(APP_EXE, "--self-test", missing, result_file=tmp_path / "r.txt")
    assert code != 0
    assert text.startswith("ERROR")


@pytest.mark.dist
def test_app_runs_from_japanese_path_with_space(tmp_path, simple_pdf):  # Q-04
    dst = tmp_path / "日本語 フォルダ" / "PDF-editor"
    shutil.copytree(APP_DIR, dst)
    code, text = _run_app(dst / "PDF-editor.exe", "--self-test", simple_pdf,
                          result_file=tmp_path / "r.txt")
    assert (code, text) == (0, "OK")


@pytest.mark.dist
def test_second_startup_under_3s(tmp_path):  # Q-03
    _run_app(APP_EXE, "--startup-benchmark", result_file=tmp_path / "first.txt")
    code, text = _run_app(APP_EXE, "--startup-benchmark", result_file=tmp_path / "second.txt")
    assert code == 0
    print(f"startup (offscreen, second run): {text} ms")
    assert int(text.strip()) < 3000


@pytest.mark.dist
def test_zip_under_150mb():
    assert (DIST / "PDF-editor.zip").stat().st_size < 150 * 1024 * 1024


@pytest.mark.dist
def test_no_network_modules_bundled():
    names = {p.name.lower() for p in APP_DIR.rglob("*")}
    assert not any(n.startswith(("qt6network", "qt6webengine", "qtnetwork")) for n in names)


@pytest.mark.dist
def test_offscreen_and_windows_platform_plugins_bundled():
    plugins = {p.name.lower() for p in (APP_DIR / "_internal" / "PySide6" / "plugins" / "platforms").glob("*")}
    assert {"qwindows.dll", "qoffscreen.dll"} <= plugins
    assert "qjpeg.dll" in {p.name.lower() for p in APP_DIR.rglob("qjpeg.dll")}


@pytest.mark.dist
def test_license_and_readme_at_top_of_folder():
    assert (APP_DIR / "LICENSE").is_file()
    assert (APP_DIR / "README.txt").is_file()


@pytest.mark.dist
def test_zip_entries_use_forward_slashes_under_one_folder():
    with zipfile.ZipFile(DIST / "PDF-editor.zip") as z:
        names = z.namelist()
    assert names
    assert not any("\\" in n for n in names)
    assert all(n.startswith("PDF-editor/") for n in names)
