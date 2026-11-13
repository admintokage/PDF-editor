"""画面に出す日本語の文言。"""

CMD_MOVE_PAGES = "ページの移動"
CMD_DELETE_PAGES = "ページの削除"
CMD_INSERT_BLANK = "空白ページの追加"
CMD_INSERT_PDF = "PDF の挿入"
CMD_ROTATE_PAGES = "ページの回転"

APP_NAME = "PDF-editor"

# メニュー
MENU_FILE = "ファイル(&F)"
MENU_EDIT = "編集(&E)"
MENU_PAGE = "ページ(&P)"
MENU_VIEW = "表示(&V)"
ACTION_OPEN = "開く(&O)..."
ACTION_SAVE = "上書き保存(&S)"
ACTION_SAVE_AS = "名前を付けて保存(&A)..."
ACTION_QUIT = "終了(&X)"
UNDO_PREFIX = "元に戻す"
REDO_PREFIX = "やり直し"

# 開く
OPEN_DIALOG_TITLE = "PDF を開く"
OPEN_DIALOG_FILTER = "PDF ファイル (*.pdf)"
EMPTY_HINT = "ここに PDF ファイルをドロップするか、ファイルメニューから開いてください"
SIGNED_NOTICE = "この PDF には電子署名があります。編集して保存すると署名は無効になります。"

# パスワード
PASSWORD_TITLE = "パスワードの入力"
PASSWORD_PROMPT_USER = "この PDF を開くにはパスワードが必要です。"
PASSWORD_PROMPT_OWNER = "この PDF は編集が制限されています。権限パスワードを入力してください。"
PASSWORD_WRONG = "パスワードが違います。もう一度入力してください。"

# エラー
ERROR_TITLE = "エラー"
INFO_TITLE = "お知らせ"
FATAL_TITLE = "予期しないエラー"
FATAL_TEXT = "予期しないエラーが発生しました。\n詳細はログに記録されました:\n{log_path}"
