import pytest

from pdfeditor.core import errors
from pdfeditor.core.document import PdfDocument
from tests.helpers import lock_file


def test_save_as_roundtrip(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf); out = tmp_path / "out.pdf"; d.save(out)
    assert PdfDocument.open(out).page_count == 3 and d.path == out


def test_first_overwrite_creates_backup_once(simple_pdf):
    original = simple_pdf.read_bytes()
    d = PdfDocument.open(simple_pdf); d.save(); bak = simple_pdf.with_name(simple_pdf.stem + ".bak.pdf")
    assert bak.read_bytes() == original
    bak.write_bytes(b"marker"); d.save(); assert bak.read_bytes() == b"marker"


def test_save_as_makes_no_backup(simple_pdf, tmp_path):
    PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert not list(tmp_path.glob("*.bak.pdf"))


def test_locked_target_raises_and_keeps_original(simple_pdf):
    original = simple_pdf.read_bytes(); d = PdfDocument.open(simple_pdf)
    with lock_file(simple_pdf):
        with pytest.raises(errors.SaveFailedError) as e: d.save()
    assert e.value.reason == "locked" and "他のソフト" in e.value.user_message
    assert simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_verify_failure_keeps_original(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()
    monkeypatch.setattr(PdfDocument, "to_bytes", lambda self, raw=None: b"not a pdf")
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save()
    assert e.value.reason == "verify_failed" and simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_readonly_folder_reports_permission(simple_pdf, tmp_path, monkeypatch):
    def deny(*a, **k): raise PermissionError(13, "denied")
    monkeypatch.setattr("pathlib.Path.write_bytes", deny)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "permission"


def test_disk_full_reported(simple_pdf, tmp_path, monkeypatch):
    def full(*a, **k): raise OSError(28, "No space left")
    monkeypatch.setattr("pathlib.Path.write_bytes", full)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save(tmp_path / "o.pdf")
    assert e.value.reason == "disk_full"


def test_toc_links_annots_widgets_survive(toc_links_pdf, tmp_path):
    import pymupdf
    out = tmp_path / "o.pdf"; PdfDocument.open(toc_links_pdf).save(out); f = pymupdf.open(out)
    assert len(f.get_toc()) == 3 and len(f[1].get_links()) == 1
    assert len(list(f[2].annots())) == 1 and next(f[0].widgets()).field_value == "入力値"


def test_backup_failure_keeps_original_and_cleans_tmp(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()

    def boom(*a, **k): raise PermissionError(13, "denied")
    monkeypatch.setattr("shutil.copy2", boom)
    d = PdfDocument.open(simple_pdf)
    with pytest.raises(errors.SaveFailedError) as e: d.save()
    assert e.value.reason == "locked" and simple_pdf.read_bytes() == original
    assert not d.backup_done
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_save_as_then_overwrite_new_path_backs_up_once(simple_pdf, tmp_path):
    d = PdfDocument.open(simple_pdf); out = tmp_path / "out.pdf"
    d.save(out); assert not d.backup_done
    d.save(); assert d.backup_done and (tmp_path / "out.bak.pdf").exists()


def test_failed_backup_copy_keeps_existing_backup(simple_pdf, monkeypatch):
    import shutil
    bak = simple_pdf.with_name(simple_pdf.stem + ".bak.pdf")
    bak.write_bytes(b"good old backup")
    original = simple_pdf.read_bytes()

    def partial(src, dst, *a, **k):
        with open(dst, "wb") as f: f.write(b"par")
        raise OSError(5, "io error")
    monkeypatch.setattr(shutil, "copy2", partial)
    with pytest.raises(errors.SaveFailedError):
        PdfDocument.open(simple_pdf).save()
    assert bak.read_bytes() == b"good old backup" and simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*")) and not list(simple_pdf.parent.glob("*.baktmp-*"))


def test_page_count_mismatch_is_verify_failed(simple_pdf, monkeypatch):
    import pymupdf
    one = pymupdf.open(); one.new_page(); data = one.tobytes(); one.close()
    original = simple_pdf.read_bytes()
    monkeypatch.setattr(PdfDocument, "to_bytes", lambda self, raw=None: data)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save()
    assert e.value.reason == "verify_failed" and simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def _flaky_replace(monkeypatch, failures: int):
    """最初の failures 回は PermissionError を投げる os.replace。呼ばれた回数と待ち時間を返す。"""
    import os
    from pdfeditor.core import safe_save

    real = os.replace
    calls, sleeps = [], []

    def fake(src, dst):
        calls.append((src, dst))
        if len(calls) <= failures:
            raise PermissionError(13, "in use by another process")
        real(src, dst)
    monkeypatch.setattr(os, "replace", fake)
    monkeypatch.setattr(safe_save, "_sleep", sleeps.append)
    return calls, sleeps


def test_transient_lock_on_replace_is_retried(simple_pdf, monkeypatch):
    calls, sleeps = _flaky_replace(monkeypatch, failures=2)
    d = PdfDocument.open(simple_pdf); d.rotate_by([0], 90); d.save(simple_pdf.with_name("o.pdf"))
    assert len(calls) == 3 and sleeps == [0.1, 0.1]
    assert PdfDocument.open(simple_pdf.with_name("o.pdf")).page_rotation(0) == 90


def test_transient_lock_on_backup_replace_is_retried(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()
    calls, _ = _flaky_replace(monkeypatch, failures=2)  # 最初の置き換えはバックアップ
    d = PdfDocument.open(simple_pdf); d.save()
    assert d.backup_done and len(calls) == 4
    assert simple_pdf.with_name(simple_pdf.stem + ".bak.pdf").read_bytes() == original


def test_persistent_lock_gives_up_as_locked(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()
    calls, sleeps = _flaky_replace(monkeypatch, failures=10**6)
    with pytest.raises(errors.SaveFailedError) as e:
        PdfDocument.open(simple_pdf).save(simple_pdf.with_name("o.pdf"))
    assert e.value.reason == "locked" and len(calls) == 5 and len(sleeps) == 4
    assert simple_pdf.read_bytes() == original and not simple_pdf.with_name("o.pdf").exists()
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))


def test_relative_path_survives_working_directory_change(simple_pdf, tmp_path, monkeypatch):
    """相対パスで開いても絶対パスで覚える。作業フォルダが変わっても同じファイルに上書きする。"""
    monkeypatch.chdir(simple_pdf.parent)
    d = PdfDocument.open(simple_pdf.name)
    assert d.path == simple_pdf.resolve() and d.path.is_absolute()
    other = tmp_path / "elsewhere"; other.mkdir(); monkeypatch.chdir(other)
    d.rotate_by([0], 90); d.save()
    assert PdfDocument.open(simple_pdf).page_rotation(0) == 90 and not (other / "simple.pdf").exists()
    assert d.backup_done  # 上書きとして扱われ、バックアップができた


def test_save_as_relative_path_then_overwrite_is_same_file(simple_pdf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = PdfDocument.open(simple_pdf); d.save("copy.pdf")
    assert d.path == (tmp_path / "copy.pdf").resolve()
    d.save(str(tmp_path / "copy.pdf"))  # 同じファイルを絶対パスで指定: 上書き扱い
    assert d.backup_done


def test_save_holds_lock_only_for_the_snapshot(big_pdf, tmp_path, monkeypatch):
    """写しの掃除と書き出しの間は lock を手放している（描画スレッドを止めない）。"""
    import threading

    import pymupdf
    d = PdfDocument.open(big_pdf(5)); d.delete_pages([1])
    real_tobytes = pymupdf.Document.tobytes
    free_during_compaction = []

    def watch(self, *a, **k):
        if k.get("garbage"):  # 写しの書き出し
            got = []

            def try_lock():
                ok = d.lock.acquire(timeout=2)
                got.append(ok)
                if ok:
                    d.lock.release()
            t = threading.Thread(target=try_lock)
            t.start(); t.join()
            free_during_compaction.append(got[0])
        return real_tobytes(self, *a, **k)
    monkeypatch.setattr(pymupdf.Document, "tobytes", watch)
    d.save(tmp_path / "o.pdf")
    assert free_during_compaction == [True]
    assert PdfDocument.open(tmp_path / "o.pdf").page_count == 4


def test_close_forgets_password(user_pw_pdf):
    d = PdfDocument.open(user_pw_pdf, "user")
    assert d._password == "user" and "'user'" not in repr(d)
    d.close()
    assert d._password is None


def test_fsync_failure_keeps_original(simple_pdf, monkeypatch):
    original = simple_pdf.read_bytes()

    def bad(fd): raise OSError(5, "io error")
    monkeypatch.setattr("os.fsync", bad)
    with pytest.raises(errors.SaveFailedError) as e: PdfDocument.open(simple_pdf).save()
    assert e.value.reason == "other" and simple_pdf.read_bytes() == original
    assert not list(simple_pdf.parent.glob("*.tmp-*.pdf"))
