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
ACTION_ZOOM_IN = "拡大(&I)"
ACTION_ZOOM_OUT = "縮小(&O)"
ACTION_FIT_WIDTH = "幅に合わせる(&W)"
ACTION_FIT_PAGE = "ページ全体(&P)"
ACTION_DELETE_PAGES = "削除(&D)"
ACTION_INSERT_BLANK = "空白ページを追加(&B)..."
ACTION_INSERT_PDF = "PDF から挿入(&I)..."
ACTION_ROTATE_RIGHT = "右に回転(&R)"
ACTION_ROTATE_LEFT = "左に回転(&L)"
UNDO_PREFIX = "元に戻す"
REDO_PREFIX = "やり直し"

# 開く
OPEN_DIALOG_TITLE = "PDF を開く"
OPEN_DIALOG_FILTER = "PDF ファイル (*.pdf)"
EMPTY_HINT = "ここに PDF ファイルをドロップするか、ファイルメニューから開いてください"
SIGNED_NOTICE = "この PDF には電子署名があります。編集して保存すると署名は無効になります。"

# ページ一覧と挿入
PAGES_DOCK_TITLE = "ページ"
INSERT_DIALOG_TITLE = "挿入する PDF を選ぶ"
INSERT_READ_FAILED = "「{name}」を開けません。ファイルが見つからないか、読み取れません。"
BLANK_DIALOG_TITLE = "空白ページを追加"
BLANK_SIZE_LABEL = "ページの大きさ:"
BLANK_SIZE_MATCH = "前後のページに合わせる"
BUTTON_OK = "OK"
BUTTON_CANCEL = "キャンセル"

# パスワード
PASSWORD_TITLE = "パスワードの入力"
PASSWORD_PROMPT_USER = "この PDF を開くにはパスワードが必要です。"
PASSWORD_PROMPT_OWNER = "この PDF は編集が制限されています。権限パスワードを入力してください。"
PASSWORD_WRONG = "パスワードが違います。もう一度入力してください。"

# 保存
SAVE_DIALOG_TITLE = "名前を付けて保存"
SAVING_TEXT = "保存しています…"
SAVE_UNEXPECTED = "保存できませんでした。予期しないエラーが発生しました。"
UNSAVED_TITLE = "変更の保存"
UNSAVED_TEXT = "「{name}」への変更を保存しますか？"
BUTTON_SAVE = "保存"
BUTTON_DISCARD = "保存しない"
BUTTON_SAVE_ELSEWHERE = "別の場所に保存"

# エラー
ERROR_TITLE = "エラー"
INFO_TITLE = "お知らせ"
FATAL_TITLE = "予期しないエラー"
FATAL_TEXT = "予期しないエラーが発生しました。\n詳細はログに記録されました:\n{log_path}"
