import pytest

from pdfeditor import diagnostics


def test_self_test_passes_on_valid_pdf(simple_pdf):
    diagnostics.run_self_test(simple_pdf)


def test_self_test_raises_on_missing_file(tmp_path):
    with pytest.raises(Exception):
        diagnostics.run_self_test(tmp_path / "none.pdf")


def test_option_value_and_result_file(tmp_path):
    assert diagnostics.option_value(["--a", "x"], "--a") == "x"
    assert diagnostics.option_value(["--a"], "--a") is None
    out = tmp_path / "r.txt"
    diagnostics.write_result(out, "OK")
    assert out.read_text(encoding="utf-8") == "OK"
