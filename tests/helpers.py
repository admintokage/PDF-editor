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
