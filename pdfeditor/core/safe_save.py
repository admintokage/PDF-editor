"""元ファイルを壊さない保存。Qt には依存しない。

一時ファイルに書く → 開き直してページ数を確認 → （必要なら）バックアップ → 置き換え。
どの段階で失敗しても一時ファイルを消し、保存先には触れない。
"""
import errno
import os
import shutil
import uuid
from pathlib import Path

import pymupdf

from pdfeditor.core import errors

_WINERROR_DISK_FULL = 112

_MESSAGES = {
    "locked": "このファイルは他のソフトで開かれているため保存できません。"
              "そのソフトを閉じるか、別の名前で保存してください。",
    "permission": "このフォルダには保存できません。書き込みの許可がありません。"
                  "別のフォルダを選んで保存してください。",
    "disk_full": "ディスクの空き容量が足りないため保存できません。"
                 "不要なファイルを削除するか、別のドライブに保存してください。",
    "verify_failed": "保存したファイルの確認に失敗したため、元のファイルはそのままにしました。"
                     "別の名前で保存してみてください。",
    "other": "保存できませんでした。元のファイルはそのままです。"
             "別の名前で保存してみてください。",
}


def _fail(reason: str, cause: BaseException | None = None) -> errors.SaveFailedError:
    err = errors.SaveFailedError(_MESSAGES[reason], reason)
    err.__cause__ = cause
    return err


def backup_path(target: Path) -> Path:
    """報告.pdf -> 報告.bak.pdf"""
    return target.with_name(target.stem + ".bak" + target.suffix)


def _classify(exc: OSError, *, writing_tmp: bool) -> errors.SaveFailedError:
    if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == _WINERROR_DISK_FULL:
        return _fail("disk_full", exc)
    if isinstance(exc, PermissionError):
        return _fail("permission" if writing_tmp else "locked", exc)
    return _fail("other", exc)


def _page_count_of(data: bytes) -> int:
    """バイト列から開いてページ数を返す（ファイルを掴まない）。開けなければ verify_failed。"""
    doc = None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        return doc.page_count
    except Exception as exc:
        raise _fail("verify_failed", exc) from exc
    finally:
        if doc is not None:
            doc.close()


def write_atomically(
    data: bytes, target: Path, *, backup: bool, expected_pages: int | None = None
) -> bool:
    """data を target に安全に書く。バックアップを作ったら True を返す。

    expected_pages が None のときは data 自体のページ数と照合する。
    """
    target = Path(target)
    if expected_pages is None:
        expected_pages = _page_count_of(data)

    tmp = target.with_name(f"{target.stem}.tmp-{uuid.uuid4().hex[:8]}.pdf")
    try:
        try:
            tmp.write_bytes(data)
        except OSError as exc:
            raise _classify(exc, writing_tmp=True) from exc
        try:
            written = tmp.read_bytes()
        except OSError as exc:
            raise _classify(exc, writing_tmp=True) from exc
        if _page_count_of(written) != expected_pages:
            raise _fail("verify_failed")
        made_backup = False
        try:
            if backup and target.exists():
                shutil.copy2(target, backup_path(target))
                made_backup = True
            os.replace(tmp, target)
        except OSError as exc:
            raise _classify(exc, writing_tmp=False) from exc
        return made_backup
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
