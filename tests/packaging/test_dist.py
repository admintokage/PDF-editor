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
