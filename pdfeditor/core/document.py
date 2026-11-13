"""PDF 文書の読み込み。Qt には依存しない。"""
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdfeditor.core import errors, page_detach, resource_prune, safe_save, scrub
from pdfeditor.core.page_detach import DetachedPages

log = logging.getLogger(__name__)

_PERM_OWNER_BIT = 4  # authenticate() の戻り値で、権限パスワード一致を示すビット
_SCRUB_FAILED = ("削除したページの一部をファイルから取り除けなかったため、保存を中止しました。"
                 "元のファイルはそのままです。")
_WRITE_FAILED = "保存できませんでした。元のファイルはそのままです。別の名前で保存してみてください。"
_EXPORT_FAILED = "ページをコピーできませんでした。PDF が壊れている可能性があります。"


@dataclass(frozen=True)
class _Snapshot:
    """保存する瞬間の文書。data は garbage なしのバイト列（オブジェクト番号は文書と同じ）。"""
    data: bytes
    detached_pages: frozenset[int]       # このセッションで外していて、まだ戻していないページ
    untargeted_outline: frozenset[int]   # 削除で行き先を外したしおりの項目
    originals: dict[int, str]            # 削除で PyMuPDF が書き換えたオブジェクトの、削除前の形



def _originals(detached: list[DetachedPages]) -> dict[int, str]:
    """外しているページの控えにある「削除前の形」を、古い削除のものを優先して重ねる。"""
    out: dict[int, str] = {}
    for d in detached:
        for x, text in d.changed_objects.items():
            out.setdefault(x, text)
    return out


PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A4 縦": (595, 842), "A4 横": (842, 595),
    "A3 縦": (842, 1191), "A3 横": (1191, 842),
    "B5 縦": (516, 729), "B5 横": (729, 516),
}


def _not_a_pdf(name: str) -> errors.NotAPdfError:
    return errors.NotAPdfError(f"{name}を開けません。PDF ファイルではないか、壊れています。")


def _open_authenticated(
    data: bytes, name: str, password: str | None, *, require_edit: bool
) -> pymupdf.Document:
    """バイト列を PDF として開き、パスワードを確認する。失敗時は閉じて例外を投げる。
    require_edit=False なら、編集が制限された PDF も開く（読み取るだけの用途）。
    PyMuPDF のどんな失敗（ページ数が読めない壊れた PDF、メモリ不足など）も NotAPdfError にする。"""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # MemoryError も含む
        raise _not_a_pdf(name) from exc

    try:
        return _check_opened(doc, name, password, require_edit=require_edit)
    except errors.PdfEditorError:
        doc.close()
        raise
    except Exception as exc:
        doc.close()
        raise _not_a_pdf(name) from exc
    except BaseException:
        doc.close()
        raise


def _check_opened(
    doc: pymupdf.Document, name: str, password: str | None, *, require_edit: bool
) -> pymupdf.Document:
    owner_ok = False
    if doc.needs_pass:
        if password is None:
            raise errors.PasswordRequiredError(f"{name}を開くにはパスワードが必要です。")
        result = doc.authenticate(password)
        if not result:
            raise errors.WrongPasswordError("パスワードが違います。")
        owner_ok = bool(result & _PERM_OWNER_BIT)
    elif password is not None:
        owner_ok = bool(doc.authenticate(password) & _PERM_OWNER_BIT)

    if doc.page_count == 0:
        raise errors.NotAPdfError(f"{name}を開けません。PDF ファイルが壊れています。")
    if require_edit and not owner_ok and not doc.permissions & pymupdf.PDF_PERM_MODIFY:
        raise errors.OwnerPasswordRequiredError(
            f"{name}は編集が制限されています。"
            "権限パスワード（編集用のパスワード）を入力してください。"
        )
    return doc


def page_order_after_move(count: int, indices: list[int], to: int) -> list[int]:
    """indices のページを元の相対順で取り出し、取り出す前の番号で数えた位置 to の直前に置いた
    新しい並びを、旧ページ番号の列で返す。"""
    moved = sorted(indices)
    taken = set(moved)
    rest = [i for i in range(count) if i not in taken]
    pos = to - sum(1 for i in moved if i < to)
    return rest[:pos] + moved + rest[pos:]


@dataclass(frozen=True)
class RenderedPage:
    """描画結果。samples は RGB（アルファなし）で 1 行 stride バイト。"""
    width: int
    height: int
    stride: int
    samples: bytes


def absolute_path(path: Path) -> Path:
    """作業フォルダが変わっても同じファイルを指すよう、絶対パスにする。Path.resolve を使うので
    シンボリックリンクはたどり、ネットワークドライブの割り当て（Z: など）は UNC パス
    （\\\\server\\share\\...）になることがある。resolve が失敗した時は absolute() で済ませる。"""
    path = Path(path)
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


class PdfDocument:
    def __init__(self, fitz_doc: pymupdf.Document, path: Path | None,
                 password: str | None = None):
        self.fitz = fitz_doc
        self.path = absolute_path(path) if path is not None else None
        self.lock = threading.RLock()
        self.backup_done = False  # この文書で上書き用バックアップを作ったか
        # 開いた時のパスワード。保存時に、暗号化を保ったまま写しを作り直すのに使う。
        # 記録やログには出さない。
        self._password = password
        self._tree_flat = False  # ページツリーの受け継ぎ属性をページに書き写したか
        # このセッションで外していて、まだ戻していないページ。保存の写しでこれだけを掃除する
        self._detached: list[DetachedPages] = []
        # 共有の Resources から削除ページの資源を外した結果の控え（次の保存で、変わっていない
        # ページの読み直しと描き比べを省く）
        self._prune_memo = resource_prune.Memo()

    def _ensure_flat_tree(self) -> None:
        """ページの構成を変える前に一度だけ、受け継ぎ属性を各ページに書き写す（lock を保持して呼ぶ）。
        見た目は変わらない。500 ページで約 16 ms。"""
        if not self._tree_flat:
            page_detach.flatten_page_tree(self.fitz)
            self._tree_flat = True

    def __repr__(self) -> str:  # パスワードを表示しない
        return f"PdfDocument(path={self.path!r})"

    @classmethod
    def open(cls, path: Path, password: str | None = None) -> "PdfDocument":
        path = absolute_path(path)
        try:
            data = path.read_bytes()  # ファイルを掴みっぱなしにしない
        except OSError as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。ファイルが見つからないか、読み取れません。"
            ) from exc
        doc = _open_authenticated(data, f"「{path.name}」", password, require_edit=True)
        return cls(doc, path, password)

    @property
    def page_count(self) -> int:
        with self.lock:
            return self.fitz.page_count

    @property
    def is_signed(self) -> bool:
        """SigFlags の SignaturesExist（ビット 1）が立っていれば署名済みとみなす。"""
        with self.lock:
            flags = self.fitz.get_sigflags()  # AcroForm がなければ -1
        return flags > 0 and bool(flags & 1)

    def _page(self, index: int) -> pymupdf.Page:
        """呼び出し側が self.lock を保持していること。Page は保持せず毎回取得する。"""
        if not 0 <= index < self.fitz.page_count:
            raise IndexError(f"ページ番号が範囲外です: {index}")
        return self.fitz[index]

    def page_size(self, index: int) -> tuple[float, float]:
        """回転を反映した表示上の (幅, 高さ)。単位はポイント。"""
        with self.lock:
            rect = self._page(index).rect
            return rect.width, rect.height

    def page_rotation(self, index: int) -> int:
        with self.lock:
            return self._page(index).rotation

    def render(self, index: int, zoom: float) -> RenderedPage:
        """zoom 1.0 = 72dpi。"""
        with self.lock:
            pix = self._page(index).get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return RenderedPage(pix.width, pix.height, pix.stride, bytes(pix.samples))

    # ---- ページ操作。すべて lock の中で実行し、検証してから変更する ----
    # pymupdf の Page は追加・削除・並べ替えの後に無効になるので、保持せず毎回 _page で取り直す。

    def _check_indices(self, indices: list[int]) -> None:
        """範囲外は IndexError、重複は ValueError。lock を保持して呼ぶ。"""
        for i in indices:
            if not 0 <= i < self.fitz.page_count:
                raise IndexError(f"ページ番号が範囲外です: {i}")
        if len(set(indices)) != len(indices):
            raise ValueError("ページ番号が重複しています。")

    def _check_position(self, at: int) -> None:
        if not 0 <= at <= self.fitz.page_count:
            raise IndexError(f"挿入位置が範囲外です: {at}")

    def _permute(self, order: list[int]) -> None:
        """ページを order（旧ページ番号の順列）の並びにする。lock を保持して呼ぶ。
        Document.select は AcroForm（フォーム）を消すので使わない。ページの参照を
        ページツリーの中で動かすだけの move_page なら、フォーム・しおり・リンクの行き先は
        ページオブジェクトについたまま残る。"""
        current = list(range(self.fitz.page_count))
        for k, wanted in enumerate(order):
            j = current.index(wanted, k)
            if j != k:
                self.fitz.move_page(j, k)  # j にあるページを k の前へ
                current.insert(k, current.pop(j))

    def move_pages(self, indices: list[int], to: int) -> None:
        with self.lock:
            self._check_indices(indices)
            self._check_position(to)
            if not indices:
                return
            self._ensure_flat_tree()
            self._permute(page_order_after_move(self.fitz.page_count, indices, to))

    def reorder(self, order: list[int]) -> None:
        """order は旧ページ番号の順列。"""
        with self.lock:
            if sorted(order) != list(range(self.fitz.page_count)):
                raise ValueError("並べ替えの指定がページ番号の順列になっていません。")
            self._ensure_flat_tree()
            self._permute(order)

    def delete_pages(self, indices: list[int]) -> DetachedPages | None:
        """ページを削除し、元に戻すための控え（restore_pages に渡す）を返す。空なら None。
        ページはページツリーから外すだけで、オブジェクトは文書に残る（保存したファイルには
        入らない）。外すページのフォーム部品は AcroForm から外す。"""
        with self.lock:
            self._check_indices(indices)
            if not indices:
                return None
            if len(indices) >= self.fitz.page_count:
                raise errors.PageOperationError("最後の 1 ページは削除できません。")
            self._ensure_flat_tree()  # 戻した先のノードから属性を受け継がないように
            order = sorted(indices)
            doomed = set(order)
            keep = [i for i in range(self.fitz.page_count) if i not in doomed]
            page_xrefs = [self._page(i).xref for i in order]
            still_shown = {self._page(i).xref for i in keep}  # 同じページオブジェクトが 2 回ある
            widgets = [
                xref for i in order if self._page(i).xref not in still_shown
                for xref, kind, _ in self._page(i).annot_xrefs()
                if kind == pymupdf.PDF_ANNOT_WIDGET
            ]
            outline = set(self.fitz.get_outline_xrefs())
            before = page_detach.objects_touched_by_delete(self.fitz, keep)
            labels = page_detach.page_labels_state(self.fitz)
            removals: list = []
            detached = False
            try:
                page_detach.remove_form_fields(self.fitz, widgets, removals)
                self.fitz.delete_pages(order)
                detached = True
                page_detach.drop_shadowed_labels(self.fitz)
                changed = page_detach.only_changed(self.fitz, before)
                result = DetachedPages(order, page_xrefs, removals, changed,
                                       frozenset(outline.intersection(changed)), labels)
                self._detached.append(result)
                return result
            except BaseException:
                # 途中で失敗したら、文書を削除前の状態に戻してから知らせる
                if detached:
                    page_detach.insert_page_objects(self.fitz, order, page_xrefs)
                page_detach.restore_form_fields(self.fitz, removals)
                page_detach.restore_objects(self.fitz, before)
                page_detach.restore_page_labels(self.fitz, labels)
                raise

    def restore_pages(self, detached: DetachedPages) -> None:
        """delete_pages で外したページを、同じオブジェクトのまま元の位置に戻す。フォーム部品、
        ほかのページから削除ページへのリンク、しおりの行き先、ページラベルも削除前の形に戻す。
        delete_pages の後に行った変更をすべて元に戻してから呼ぶこと（元に戻すの順序）。"""
        with self.lock:
            page_detach.insert_page_objects(self.fitz, detached.indices, detached.page_xrefs)
            page_detach.restore_form_fields(self.fitz, detached.removals)
            page_detach.restore_objects(self.fitz, detached.changed_objects)
            page_detach.restore_page_labels(self.fitz, detached.page_labels)
            self._detached = [d for d in self._detached if d is not detached]

    def insert_blank(self, at: int, width: float, height: float) -> None:
        with self.lock:
            self._check_position(at)
            self._ensure_flat_tree()  # 置いた先のノードの向きや切り抜きを受け継がないように
            unlabelled = page_detach.page_labels_state(self.fitz) is None
            self.fitz.new_page(pno=at, width=width, height=height)
            self._keep_unlabelled(unlabelled)

    def _keep_unlabelled(self, unlabelled: bool) -> None:
        """ページラベルのない文書の先頭にページを挿入すると、MuPDF がラベルを作る
        （1, 1, 2, ... と表示される）。もともとなければ消す（lock を保持して呼ぶ）。"""
        if unlabelled:
            page_detach.restore_page_labels(self.fitz, None)

    def default_blank_size(self, at: int) -> tuple[float, float]:
        """at-1 のページ（なければ at）の大きさ。"""
        with self.lock:
            return self.page_size(at - 1 if at > 0 else min(at, self.fitz.page_count - 1))

    def insert_from_bytes(self, data: bytes, at: int, password: str | None = None) -> int:
        """挿入したページ数を返す。挿入元は読み取るだけなので、編集制限があっても挿入できる。"""
        with self.lock:
            self._check_position(at)
            src = _open_authenticated(data, "挿入する PDF", password, require_edit=False)
            try:
                self._ensure_flat_tree()
                count = src.page_count
                before = self.fitz.page_count
                unlabelled = page_detach.page_labels_state(self.fitz) is None
                try:
                    self.fitz.insert_pdf(src, start_at=at)
                    self._keep_unlabelled(unlabelled)
                except Exception as exc:
                    # 途中まで入ったページがあれば取り除き、文書を挿入前に戻す
                    added = self.fitz.page_count - before
                    if added > 0:
                        self.delete_pages(list(range(at, at + added)))
                    raise errors.PageOperationError(
                        "PDF を挿入できませんでした。挿入する PDF が壊れている可能性があります。"
                    ) from exc
            finally:
                src.close()
            return count

    def insert_from_file(self, path: Path, at: int, password: str | None = None) -> int:
        path = Path(path)
        try:
            data = path.read_bytes()  # 先にバイト列で読むので、自分自身の挿入も同じ経路で動く
        except OSError as exc:
            raise errors.NotAPdfError(
                f"「{path.name}」を開けません。ファイルが見つからないか、読み取れません。"
            ) from exc
        return self.insert_from_bytes(data, at, password)

    def export_pages(self, indices: list[int]) -> bytes:
        """ページ indices だけを番号の小さい順に含む、新しい PDF のバイト列（暗号化なし）。
        コピー・切り取りでアプリ内のクリップボードに置く（メモリの中だけで使い、ファイルに書かない）。

        PyMuPDF の insert_pdf は取り込み元を書き換える（フォームの /Fields を空にする、注釈の
        /P・/Popup を外すなど。2 回目の取り込みではフォームの部品が落ちる）ので、生きている文書
        ではなく、その写しから 1 回で取り込む。写しは lock の中でバイト列にし、残りは lock の外で
        行う。写しからは指定していないページを、ページ削除と同じくフォーム部品を AcroForm から
        外してから取り除く（ほかのページの部品と親を共有する部品から、取り除いたページへ
        たどらないように）。ページが Resources を共有している（または /Pages から受け継いでいる）と、
        選ばなかったページの画像・フォントなども載っているので、選んだページが使わない項目を外す
        （resource_prune。見た目が変わればやめる）。受け継がれた MediaBox・Rotate・Resources は
        insert_pdf がページに書き写す。範囲外は IndexError、空・重複は ValueError。"""
        with self.lock:
            self._check_indices(indices)
            if not indices:
                raise ValueError("ページが指定されていません。")
            try:
                # 暗号は元のまま書く。暗号化なし（PDF_ENCRYPT_NONE）で書くと、生きている文書の
                # 暗号の状態が変わり、その後の保存で中身の読めないファイルができる（実測）
                data = self.fitz.tobytes(encryption=pymupdf.PDF_ENCRYPT_KEEP)
            except Exception as exc:  # noqa: BLE001
                raise errors.PageOperationError(_EXPORT_FAILED) from exc
            password = self._password
            detached = {x for d in self._detached for x in d.page_xrefs}  # 削除して控えているページ
        keep = set(indices)
        src = out = None
        try:
            src = pymupdf.open(stream=data, filetype="pdf")
            if src.needs_pass:
                src.authenticate(password or "")
            others = [i for i in range(src.page_count) if i not in keep]
            if others:
                widgets = [xref for i in others for xref, kind, _ in src[i].annot_xrefs()
                           if kind == pymupdf.PDF_ANNOT_WIDGET]
                removed = {src[i].xref for i in others} | detached
                page_detach.remove_form_fields(src, widgets, [])
                src.delete_pages(others)
                # 選んだページの（共有・受け継いだ）Resources から、選んだページが使わない資源を
                # 外す。見た目が変わればやめる（resource_prune）
                resource_prune.prune_unused_resources(src, None, removed)
            out = pymupdf.open()
            out.insert_pdf(src)
            return out.tobytes(garbage=3, deflate=True)
        except Exception as exc:  # noqa: BLE001
            raise errors.PageOperationError(_EXPORT_FAILED) from exc
        finally:
            for d in (out, src):
                if d is not None:
                    d.close()

    @staticmethod
    def _check_multiple_of_90(value: int) -> None:
        if value % 90 != 0:
            raise ValueError(f"回転角は 90 の倍数で指定してください: {value}")

    def set_rotation(self, indices: list[int], rotation: int) -> None:
        with self.lock:
            self._check_multiple_of_90(rotation)
            self._check_indices(indices)
            for i in indices:
                self._page(i).set_rotation(rotation % 360)

    def rotate_by(self, indices: list[int], delta: int) -> None:
        with self.lock:
            self._check_multiple_of_90(delta)
            self._check_indices(indices)
            for i in indices:
                page = self._page(i)
                page.set_rotation((page.rotation + delta) % 360)

    def _snapshot(self) -> _Snapshot:
        """今の文書をそのままバイト列にし、外しているページの控えと組にする（lock を保持して呼ぶ）。
        garbage なしなので文書の中身は変わらず（削除を元に戻すために残してあるページも残る）、
        オブジェクトの番号も変わらない。暗号化は元のまま。"""
        return _Snapshot(
            self.fitz.tobytes(encryption=pymupdf.PDF_ENCRYPT_KEEP),
            frozenset(x for d in self._detached for x in d.page_xrefs),
            frozenset(x for d in self._detached for x in d.untargeted_outline),
            _originals(self._detached),
        )

    def to_bytes(self, snapshot: _Snapshot | None = None) -> bytes:
        """保存するバイト列。パスワードと編集制限は元のまま残す（PDF_ENCRYPT_KEEP）。

        写し（snapshot から開いたもの）の上で、このセッションで外したページにだけ属するものを
        取り除き（scrub）、不要オブジェクトを掃除（garbage）する。生きている文書で掃除すると、
        元に戻すために残してあるページが消えるため。snapshot を渡せば lock は要らない
        （写しは文書と独立している）。失敗はすべて SaveFailedError（記録に残す）。"""
        if snapshot is None:
            with self.lock:
                snapshot = self._snapshot()
        try:
            copy = pymupdf.open(stream=snapshot.data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            log.exception("could not reopen the snapshot for saving")
            raise errors.SaveFailedError(_WRITE_FAILED, "other") from exc
        try:
            if copy.needs_pass:
                copy.authenticate(self._password or "")
            try:
                revived = page_detach.revive_chained_actions(copy, snapshot.originals)                     if snapshot.detached_pages else set()
                scrub.scrub_detached_pages(copy, snapshot.detached_pages,
                                           snapshot.untargeted_outline - revived, self._prune_memo)
            except Exception as exc:  # noqa: BLE001 - 取り除けたと言えなければ保存しない
                log.exception("scrubbing deleted pages failed; save refused")
                raise errors.SaveFailedError(_SCRUB_FAILED, "verify_failed") from exc
            try:
                return copy.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            except Exception as exc:  # noqa: BLE001
                log.exception("writing the saved copy failed")
                raise errors.SaveFailedError(_WRITE_FAILED, "other") from exc
        finally:
            copy.close()

    def save(self, target: Path | None = None) -> None:
        """target が None または現在のパスと同じなら上書き（初回のみバックアップ）。
        失敗時は SaveFailedError で、元のファイルには触れない。成功後は self.path が保存先になる。"""
        with self.lock:  # バイト列・外したページ・ページ数を同じ瞬間の状態で取る。ここだけ描画を止める
            snapshot = self._snapshot()
            pages = self.fitz.page_count
        data = self.to_bytes(snapshot)  # 写しの掃除と書き出しは lock の外で
        dest = absolute_path(target) if target is not None else self.path
        if dest is None:
            raise errors.SaveFailedError("保存先が指定されていません。", "other")
        overwrite = self.path is not None and dest == self.path
        made = safe_save.write_atomically(
            data, dest, backup=overwrite and not self.backup_done, expected_pages=pages
        )
        if made:
            self.backup_done = True
        self.path = dest

    def close(self) -> None:
        with self.lock:
            self._password = None
            if not self.fitz.is_closed:
                self.fitz.close()
