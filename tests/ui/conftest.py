"""UI テストはウィンドウを出さずに動かす。Qt を読み込む前に設定する。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
