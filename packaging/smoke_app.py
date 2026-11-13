"""起動確認用の最小アプリ。

管理された PC で PySide6 と PyMuPDF が読み込めるかを確かめるためだけのもの。
`--self-test` を付けるとウィンドウを出さずに同じ処理を行い、成功すれば終了コード 0 で終わる。
"""
import os
import platform
import sys

import pymupdf

TITLE = "PDF-editor 起動確認"
MESSAGE = "この画面が出れば起動できています"


def render_sample_page() -> tuple[bytes, int, int]:
    """1 ページの PDF をメモリ上に作って描画し、(RGB バイト列, 幅, 高さ) を返す。"""
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=200)
        page.insert_text((30, 100), "PDF OK", fontsize=36)
        pix = page.get_pixmap(alpha=False)
        return pix.samples, pix.width, pix.height
    finally:
        doc.close()


def pymupdf_summary() -> str:
    return (pymupdf.__doc__ or "").strip().splitlines()[0]


def run_folder() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main(argv: list[str]) -> int:
    samples, width, height = render_sample_page()
    if width <= 0 or height <= 0 or not samples:
        return 1
    if "--self-test" in argv:
        return 0

    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication(argv)
    window = QWidget()
    window.setWindowTitle(TITLE)
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel(f"<h2>{TITLE}</h2><p>{MESSAGE}</p>"))
    layout.addWidget(QLabel(pymupdf_summary()))
    layout.addWidget(QLabel(f"Windows: {platform.platform()}"))
    layout.addWidget(QLabel(f"実行フォルダ: {run_folder()}"))
    image = QImage(samples, width, height, width * 3, QImage.Format.Format_RGB888)
    picture = QLabel()
    picture.setPixmap(QPixmap.fromImage(image.copy()))
    layout.addWidget(picture)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
