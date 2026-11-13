"""テスト共通の補助関数。"""
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def lock_file(path: Path):
    """ファイルを開いたままにする。Windows では開いているファイルは置き換えられず、
    「Acrobat Reader で開いている」状態を再現できる。"""
    f = open(path, "rb")
    try:
        yield f
    finally:
        f.close()


def labels(doc) -> list[int]:
    """各ページの文字 "Page N" から N の列を返す。なければ 0。"""
    import re

    out = []
    with doc.lock:
        for i in range(doc.fitz.page_count):
            m = re.search(r"Page (\d+)", doc.fitz[i].get_text())
            out.append(int(m.group(1)) if m else 0)
    return out
