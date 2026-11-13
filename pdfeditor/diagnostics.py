"""配布物の確認用の機能（--self-test / --startup-benchmark）。画面は出さない。"""
import ctypes
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from pdfeditor.core.document import PdfDocument


def write_result(result_file: Path | None, text: str) -> None:
    """結果を標準出力と（指定があれば）ファイルへ書く。ウィンドウアプリでは標準出力が使えないことがある。"""
    try:
        print(text, flush=True)
    except (OSError, ValueError):
        pass
    if result_file is not None:
        Path(result_file).write_text(text, encoding="utf-8")


def option_value(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def run_self_test(pdf_path: Path) -> None:
    """開く、最初のページを描画する、1 ページ回転して一時フォルダへ保存する、保存物を開き直す。
    どれかが失敗したら例外を投げる。"""
    doc = PdfDocument.open(pdf_path)
    try:
        page = doc.render(0, 1.0)
        if page.width <= 0 or page.height <= 0 or not page.samples:
            raise RuntimeError("最初のページを描画できませんでした")
        count = doc.page_count
        doc.rotate_by([0], 90)
        with tempfile.TemporaryDirectory(prefix="pdf-editor-selftest-") as tmp:
            out = Path(tmp) / "out.pdf"
            doc.save(out)
            again = PdfDocument.open(out)
            try:
                if again.page_count != count or again.page_rotation(0) != 90:
                    raise RuntimeError("保存したファイルの内容が一致しません")
            finally:
                again.close()
    finally:
        doc.close()


def process_start_epoch() -> float | None:
    """このプロセスの開始時刻（time.time() と同じ基準の秒）。取れなければ None。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user),
        )
        if not ok:
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return (ticks - 116444736000000000) / 1e7
    except (AttributeError, OSError):
        return None


def elapsed_ms_since_process_start(fallback_start: float) -> int:
    """プロセス開始から今までのミリ秒。取れなければ fallback_start（time.time()）からの経過。"""
    start = process_start_epoch()
    if start is None:
        start = fallback_start
    return max(0, int((time.time() - start) * 1000))
