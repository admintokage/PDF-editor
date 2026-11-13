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


def snapshot(doc) -> tuple:
    """(ラベル列, 各ページの回転, 各ページの大きさ(整数))。元に戻すの検証用。"""
    with doc.lock:
        n = doc.fitz.page_count
        rotations = tuple(doc.page_rotation(i) for i in range(n))
        sizes = tuple(tuple(round(v) for v in doc.page_size(i)) for i in range(n))
    return (tuple(labels(doc)), rotations, sizes)
