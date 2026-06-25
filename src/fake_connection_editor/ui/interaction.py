"""ポインタ操作の状態機械（ドラッグ接続/切断・横断切断・値コピー）。

EditorWindow から操作系の責務を切り出したコントローラ（master §5.1〜§5.5）。
EditorWindow は「アプリ全体 eventFilter の主体」と薄い転送を保ち（境界での掴みを
拾う意図的な設計・PROGRESS の `8baa6f0` を維持）、実際の分岐とドラッグ/スラッシュの
一時状態・値メニューはこのコントローラが持つ。

EditorWindow からは ``handle_event`` に左押下/移動/離す/コンテキストメニューを
転送し、消費したか（True/False）を返す。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger

from ..scene_access.interface import PlugId
from ..viewmodel import LEFT, RIGHT, EditorViewModel
from ..viewmodel.editor import ConnectBlock, CopyReason
from .connection_overlay import ConnectionOverlay
from .errors import error_handler
from .icons import themed_cursor
from .qt_compat import Qt, QtCore, QtGui, QtWidgets
from .tree_model import AttributeTreeModel

logger = getLogger(__name__)

# 接続/leaf 接続が成立しない理由（ConnectBlock）→ ユーザー向け文言（§5.5）。
# 未知キー / None は ``.get`` のフォールバック（"Cannot connect."）に委ねる。
_CONNECT_MESSAGES = {
    ConnectBlock.TYPE_INCOMPATIBLE: "The attribute types are not compatible.",
    ConnectBlock.NO_DIRECTION: (
        "No valid direction: the source must be readable and the destination writable."
    ),
    ConnectBlock.DST_LOCKED: (
        "The destination is locked. Enable Force connect to override."
    ),
    ConnectBlock.LEAF_COUNT_MISMATCH: (
        "Leaf connect needs the same number of child attributes on both sides."
    ),
    ConnectBlock.LEAF_NON_SCALAR: (
        "Leaf connect requires all child attributes to be scalar."
    ),
    ConnectBlock.LEAF_CHILD_INCOMPATIBLE: (
        "The child attribute types are not compatible."
    ),
}


def _apply_direction(
    left: PlugId | None, right: PlugId | None, l2r: bool
) -> tuple[PlugId | None, PlugId | None]:
    """方向トグルに従って (src, dst) を決める（純粋関数・Qt 非依存）。

    ボタン操作（Connect / Connect Leaf / Copy Value）は左右どちらかを src・他方を
    dst として扱う。方向トグルが L2R（左→右）なら左が src、R2L なら右が src になる。

    Args:
        left: 左ツリーで選択中の plug（未選択は None）。
        right: 右ツリーで選択中の plug（未選択は None）。
        l2r: True なら左→右、False なら右→左。

    Returns:
        ``(src, dst)`` のタプル。
    """
    return (left, right) if l2r else (right, left)


class InteractionController:
    """ドラッグ接続/切断・横断切断・値コピーを司る操作系。

    状態（ドラッグ/スラッシュ/グラブ）を保持し、EditorWindow から転送される入力
    イベントを処理する。判定は ViewModel に一元化し（``can_drag_connect`` 等）、
    描画反映は overlay に委ねる。
    """

    # ポート押下→リリースがこの距離（px・manhattan）以内なら「ドラッグせず
    # クリック」とみなし、接続/切断ではなくその行の選択に倒す（ポートが行の
    # 中央側の端にあり、クリックで行選択できないと体感されるため）。
    CLICK_TOL = 4

    def __init__(
        self,
        window: QtWidgets.QWidget,
        vm: EditorViewModel,
        overlay: ConnectionOverlay,
        trees: dict[str, QtWidgets.QTreeView],
        models: dict[str, AttributeTreeModel],
        gap: QtWidgets.QWidget,
        force_on: Callable[[], bool],
        force_disconnect_on: Callable[[], bool],
        direction_on: Callable[[], bool],
    ) -> None:
        """コントローラを生成する。

        Args:
            window: 親ウィンドウ（QMenu/QMessageBox の親）。
            vm: 共有 ViewModel。
            overlay: 接続線オーバーレイ（描画反映・当たり判定）。
            trees: 左右の ``QTreeView``。
            models: 左右の ``AttributeTreeModel``。
            gap: 中央帯ウィジェット（値メニュー対象判定）。
            force_on: force connect トグルの状態を返す callable。
            force_disconnect_on: force disconnect トグルの状態を返す callable。
            direction_on: 方向トグルが L2R（左→右）なら True を返す callable。
        """
        self._window = window
        self._vm = vm
        self._overlay = overlay
        self._trees = trees
        self._models = models
        self._gap = gap
        self._force_on = force_on
        self._force_disconnect_on = force_disconnect_on
        self._direction_on = direction_on
        # ドラッグ状態。{"mode": "new"|"reconnect", "anchor": PlugId,
        #   "old_dst": PlugId|None, "grab_side": str, "grabbed": PlugId,
        #   "press": QPoint}。None はドラッグ中でない。
        self._drag: dict | None = None
        self._grabber: QtWidgets.QWidget | None = None
        # 横断切断のスラッシュ操作（Alt+Shift ドラッグ・§5.1 優先度3）。
        self._slash: dict | None = None

    # ---- EditorWindow からの転送 ----
    def handle_event(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """転送入力の処理を行い、操作中の例外を捕捉・通知する境界（master §5.5）。

        実処理は ``_dispatch`` に委ね、ここでは例外を握って通知する。ドラッグ/横断/
        Delete の確定（vm 書き込み＝実機 cmds はエラーを投げ得る）が例外を投げても、
        各 ``_end_*`` の ``finally`` で状態は後始末済みなので、通知して消費扱い
        （``True``）で返す（Maya の赤帯に出す＝ScriptEditor 止まりを解消）。

        Args:
            obj: イベントを受けたオブジェクト。
            event: 入力イベント。

        Returns:
            消費したら True。例外を捕捉した場合も True（消費扱い）。
        """
        try:
            return self._dispatch(obj, event)
        except Exception as exc:  # noqa: BLE001  操作境界で全例外を握り通知する
            logger.exception("%s", exc)
            self._report_error(exc)
            return True

    def _report_error(self, exc: BaseException) -> None:
        """捕捉した例外をウィンドウ経由で reporter へ通知する。"""
        report = getattr(self._window, "_report_error", None)
        if callable(report):
            report(exc)

    def _warn(self, message: str) -> None:
        """予測できる失敗（実行時拒否・§5.5）を warning レベルで通知する。

        Maya では黄帯（``displayWarning``）に出す。注入されていなければログのみ。
        """
        notify = getattr(self._window, "_notify_user", None)
        if callable(notify):
            notify("warning", message)

    def _dispatch(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """転送された入力からポート操作・線選択・横断切断を処理する。

        左押下の分岐（最初にヒットしたものを消費・それ以外は素通り）:
            1. ポート上（``port_at``）→ つなぎ/つなぎ替え/切断ドラッグ（従来）。
            2. Alt+Shift → 横断切断のスラッシュ開始（§5.1 優先度3）。
            3. それ以外 → 素通り（コラプス開閉のみ消費しうる）。

        Args:
            obj: イベントを受けたオブジェクト。
            event: 入力イベント。

        Returns:
            このコントローラが消費したら True（呼び出し側は ``True`` を返す）。
            消費しなければ False（呼び出し側は既定処理へ素通り）。
        """
        etype = event.type()
        # QWidget のイベントだけ見る（QWindow との二重発火を避ける）。
        if not isinstance(obj, QtWidgets.QWidget):
            return False

        if etype == QtCore.QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._busy():
                return False
            return self._on_left_press(obj, event)
        elif etype == QtCore.QEvent.MouseMove:
            if self._drag is not None:
                self._update_drag(event.globalPos())
                return True
            if self._slash is not None:
                self._update_slash(event.globalPos())
                return True
        elif (
            etype == QtCore.QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            if self._drag is not None:
                self._end_drag(event.globalPos())
                return True
            if self._slash is not None:
                self._end_slash(event.globalPos())
                return True
        elif (
            etype == QtCore.QEvent.ContextMenu
            and not self._busy()
            and self._is_tree_area(obj)
        ):
            self._show_value_menu(obj, event.pos(), event.globalPos())
            return True
        return False

    def _busy(self) -> bool:
        """ドラッグ/スラッシュのいずれか進行中か返す。"""
        return self._drag is not None or self._slash is not None

    def is_interacting(self) -> bool:
        """ユーザー操作（ドラッグ/スラッシュ）が進行中か返す。

        外部変更のライブ同期（watcher）が、操作の最中に再読込でモデルを作り直して
        足元を崩さないよう、フラッシュを延期するか判断するのに使う。
        """
        return self._busy()

    def _on_left_press(self, obj: QtWidgets.QWidget, event: QtCore.QEvent) -> bool:
        """左押下の分岐を判定して処理する（handle_event から呼ぶ）。"""
        global_pos = event.globalPos()
        hit = self._overlay.port_at(global_pos)
        if hit is not None:
            self._begin_drag(obj, hit, global_pos)
            return True  # ポート上はドラッグで消費
        mods = event.modifiers()
        if (mods & Qt.AltModifier) and (mods & Qt.ShiftModifier):
            self._begin_slash(obj, global_pos)  # 横断切断（§5.1 優先度3）
            return True
        if self._maybe_toggle_branch(obj, global_pos):
            return True  # コラプス開閉で消費（当たり判定を広げる）
        return False

    def _maybe_toggle_branch(
        self, obj: QtWidgets.QWidget, global_pos: QtCore.QPoint
    ) -> bool:
        """コラプス矢印の当たり判定を広げて開閉する（矢印が小さく狙いにくい対策）。

        Qt 標準の branch スロット（テキスト左 1 段ぶん）は細く狙いにくいので、
        ここで広げる:
            - **セクション行**: 行のどこでも開閉（ノード名クリックで開閉できる）。
            - **属性行**: テキスト左（インデント/矢印領域全体）で開閉。
        子を持たない行・テキスト上のクリックは対象外（素通りして選択に委ねる）。
        ポートは中央寄りなので ``port_at`` の判定が先に消費し、競合しない。

        Args:
            obj: 押下を受けたウィジェット（ツリーの viewport を期待）。
            global_pos: 押下のグローバル座標。

        Returns:
            開閉したら True（呼び出し側は消費）。対象外なら False。
        """
        for side in (LEFT, RIGHT):
            tree = self._trees[side]
            if tree.viewport() is not obj:
                continue
            pos = tree.viewport().mapFromGlobal(global_pos)
            idx = tree.indexAt(pos)
            if not idx.isValid() or self._models[side].rowCount(idx) == 0:
                return False  # 無効行/子なし（leaf）は開閉対象外
            is_section = self._models[side].is_section(idx)
            if is_section or pos.x() < tree.visualRect(idx).left():
                tree.setExpanded(idx, not tree.isExpanded(idx))
                return True
            return False
        return False

    def _is_tree_area(self, obj: QtCore.QObject) -> bool:
        """左右ツリーの viewport か中央帯（値メニュー対象領域）かを返す。"""
        if obj is self._gap:
            return True
        return any(obj is self._trees[s].viewport() for s in (LEFT, RIGHT))

    # ---- 値コピー（右クリックメニュー・master §5.3） ----
    def _select_under_cursor(
        self, obj: QtCore.QObject, pos: QtCore.QPoint
    ) -> tuple[str | None, PlugId | None]:
        """右クリックした行をそのツリーの選択にし、その (side, plug) を返す。

        右クリックでは Qt 標準は選択を変えないため、ここでクリック直下の属性行を
        currentIndex に反映して「左クリックで選ぶ」手間を 1 回減らす。セクション行や
        空白上・中央帯では選択を変えず ``(None, None)`` を返す（master §5.3）。

        Args:
            obj: ContextMenu を受けた viewport（左右どちらかのツリー）。
            pos: viewport ローカルのクリック座標。

        Returns:
            右クリック直下の ``(side, plug)``。属性行でなければ ``(None, None)``。
        """
        for side in (LEFT, RIGHT):
            tree = self._trees[side]
            if tree.viewport() is obj:
                idx = tree.indexAt(pos)
                plug = self._models[side].plug_at(idx) if idx.isValid() else None
                if plug is not None:
                    tree.setCurrentIndex(idx)
                    return side, plug
                return None, None
        return None, None

    def _current_plug(self, side: str) -> PlugId | None:
        """その側ツリーで現在選択中の属性 plug を返す（セクション行は None）。"""
        tree = self._trees[side]
        return self._models[side].plug_at(tree.currentIndex())

    def _selected_src_dst(self) -> tuple[PlugId | None, PlugId | None]:
        """方向トグルに従って、選択中の (src, dst) plug を返す。"""
        return _apply_direction(
            self._current_plug(LEFT), self._current_plug(RIGHT), self._direction_on()
        )

    def _require_pair(self, src: PlugId | None, dst: PlugId | None) -> bool:
        """src/dst が揃っているか検証し、欠けていれば警告して False を返す。"""
        if src is None or dst is None:
            self._warn("Select one attribute on each side.")
            return False
        return True

    @error_handler
    def connect_selected(self) -> None:
        """選択ペアを方向トグルに従って接続する（Connect ボタン・master §5.2）。

        左右で 1 つずつ選択し、方向に従い src→dst へ接続する。選択不足や接続不可は
        実行時に警告する（実行時拒否・master §5.5）。
        """
        src, dst = self._selected_src_dst()
        if not self._require_pair(src, dst):
            return
        force = self._force_on()
        if not self._vm.try_connect(src, dst, force=force):
            block = self._vm.connect_blocker(src, dst, force=force)
            self._warn(_CONNECT_MESSAGES.get(block, "Cannot connect."))

    @error_handler
    def connect_leaf_selected(self) -> None:
        """選択ペアを子属性ごとに接続する（Connect Leaf ボタン・master §5.2）。

        親同士を選び、子をばらして接続する（tx→tx, ty→ty, tz→tz）。成立しない
        組み合わせは実行時に理由付きで警告する。
        """
        src, dst = self._selected_src_dst()
        if not self._require_pair(src, dst):
            return
        force = self._force_on()
        if not self._vm.connect_leaf(src, dst, force=force):
            block = self._vm.leaf_blocker(src, dst, force=force)
            self._warn(_CONNECT_MESSAGES.get(block, "Cannot connect."))

    @error_handler
    def copy_value_selected(self, *, leaf: bool = False) -> None:
        """選択ペアの値を方向トグルに従ってコピーする（master §5.3）。

        Args:
            leaf: True なら子属性ごとに値をコピーする。
        """
        src, dst = self._selected_src_dst()
        if not self._require_pair(src, dst):
            return
        force = self._force_on()
        if leaf:
            result = self._vm.copy_value_leaf(src, dst, force=force)
        else:
            result = self._vm.copy_value(src, dst, force=force)
        if not result.ok:
            self._warn_copy(result.reason)

    def _show_value_menu(
        self, obj: QtCore.QObject, pos: QtCore.QPoint, global_pos: QtCore.QPoint
    ) -> None:
        """右クリックメニューを出す（接続たどり・値コピー・master §5.3）。

        右クリック直下の属性（起点 plug）を基点に、接続をたどる項目（Load/Add
        Connected）と現在値コピー（Copy Attribute Value）を出し、続けて従来の左右
        ペア値コピー（Copy Value / Copy Value (Leaf)）を出す。起点ベースの項目は
        条件を満たさなければグレーアウトし、ペア値コピーは事前にグレーアウトせず
        実行時に評価する（実行時拒否・master §5.5）。

        Args:
            obj: ContextMenu を受けた viewport（左右ツリー or 中央帯）。
            pos: viewport ローカルのクリック座標。
            global_pos: メニューを出すグローバル座標。
        """
        side, plug = self._select_under_cursor(obj, pos)

        menu = QtWidgets.QMenu(self._window)
        has_conn = plug is not None and self._vm.is_connected(plug)
        can_copy_attr = plug is not None and self._vm.can_copy_value(plug)
        act_load = menu.addAction("Load Connected")
        act_add = menu.addAction("Add Connected")
        act_copy_attr = menu.addAction("Copy Attribute Value")
        act_load.setEnabled(has_conn)
        act_add.setEnabled(has_conn)
        act_copy_attr.setEnabled(can_copy_attr)
        menu.addSeparator()
        act_value = menu.addAction("Copy Value")
        act_leaf = menu.addAction("Copy Value (Leaf)")

        chosen = menu.exec_(global_pos)
        if chosen is act_value:
            self.copy_value_selected(leaf=False)
        elif chosen is act_leaf:
            self.copy_value_selected(leaf=True)
        elif chosen is act_load:
            self.load_connected(side, plug, add=False)
        elif chosen is act_add:
            self.load_connected(side, plug, add=True)
        elif chosen is act_copy_attr:
            self.copy_attribute_value(plug)

    @error_handler
    def load_connected(self, side: str, plug: PlugId, *, add: bool) -> None:
        """起点 plug の接続相手ノード群を反対側ツリーへロードする（master §3.2）。

        Args:
            side: 起点 plug が属する側（``LEFT`` / ``RIGHT``）。
            plug: 右クリックした起点 plug。
            add: True なら反対側へ追加（Add）、False なら置換（Load）。
        """
        nodes = self._vm.connected_nodes(plug)
        opposite = RIGHT if side == LEFT else LEFT
        if add:
            self._vm.add_nodes(opposite, nodes)
        else:
            self._vm.set_nodes(opposite, nodes)

    @error_handler
    def copy_attribute_value(self, plug: PlugId) -> None:
        """起点 plug の現在値をクリップボードへコピーする（master §5.3）。

        コピー可能なのは NUMERIC / MATRIX のみ。メニューで事前にグレーアウト済みだが、
        保険として実行時にも不可なら警告して中止する。

        Args:
            plug: 右クリックした起点 plug。
        """
        text = self._vm.read_value_text(plug)
        if text is None:
            self._warn("This attribute type has no copyable value.")
            return
        QtWidgets.QApplication.clipboard().setText(text)

    def _warn_copy(self, reason) -> None:
        """値コピー失敗/警告を warning レベルで知らせる（master §5.3）。"""
        messages = {
            CopyReason.DST_CONNECTED: (
                "The destination has an input connection; "
                "the set value is ignored while connected."
            ),
            CopyReason.DST_LOCKED: (
                "The destination is locked. Turn on Force connect to overwrite."
            ),
            CopyReason.INCOMPATIBLE: (
                "The types are incompatible; cannot copy the value."
            ),
        }
        text = messages.get(reason, "Cannot copy the value.")
        self._warn(text)

    # ---- ドラッグ接続/切断・つなぎ替え（master §5.1） ----
    def _begin_drag(
        self, viewport: QtWidgets.QWidget, hit: tuple, global_pos: QtCore.QPoint
    ) -> None:
        """ポート押下でドラッグを開始する（master §5.1）。

        入力（destination）ポートを掴んだら既存線が外れて付いてくる（つなぎ替え）。
        出力/未接続ポートなら新規の線を伸ばす。仮線は常に「固定端（anchor）→
        カーソル」のベジェで描く。
        """
        side, plug = hit
        sources = self._vm.get_connections(plug).sources
        if sources:
            # 入力を掴んだ = 既存線を掴んで外す。anchor は反対端の source。
            src = sources[0]
            self._drag = {
                "mode": "reconnect",
                "anchor": src,
                "old_dst": plug,
                "grab_side": side,
                "grabbed": plug,
                "press": global_pos,
            }
            self._overlay.set_suppressed(src, plug)
        else:
            # 出力/未接続を掴んだ = 新規の線。anchor は掴んだポート自身。
            self._drag = {
                "mode": "new",
                "anchor": plug,
                "old_dst": None,
                "grab_side": side,
                "grabbed": plug,
                "press": global_pos,
            }

        self._grabber = viewport
        viewport.grabMouse()
        self._apply_dim(self._drag["anchor"], plug)
        self._update_drag(global_pos)

    def _apply_dim(self, anchor: PlugId, grabbed: PlugId) -> None:
        """ドラッグ開始時に1回だけ候補を評価し、接続不可ポートを沈める（§5.5）。

        判定は ViewModel に一元化（``can_drag_connect``）し、ここは可視ポートを
        渡して結果を overlay へ反映するだけ。掴んだポート自身とつなぎ替え元の dst
        （anchor / grabbed）は沈めない（落とし直せるように）。

        Args:
            anchor: 互換判定の基準 plug（新規=掴んだポート / つなぎ替え=source）。
            grabbed: 実際に掴んだポート plug（つなぎ替え時は外した dst）。
        """
        keep = {anchor, grabbed}
        dimmed = {
            plug
            for plug in self._overlay.visible_plugs()
            if plug not in keep
            and not self._vm.can_drag_connect(
                anchor, plug, leaf=False, force=self._force_on()
            )
        }
        self._overlay.set_dimmed(dimmed)

    def _update_drag(self, global_pos: QtCore.QPoint) -> None:
        """ドラッグ中の仮線（anchor→カーソル）をベジェで追従させる。"""
        found = self._overlay.find_port(self._drag["anchor"])
        start = found[1] if found is not None else None
        self._overlay.set_temp_line(start, self._overlay.mapFromGlobal(global_pos))

    def _end_drag(self, global_pos: QtCore.QPoint) -> None:
        """ドロップ位置で接続/切断/つなぎ替えを確定する（master §5.1）。

        接続系の書き込みが例外を投げても（実機 cmds はエラーを投げ得る）、
        グラブ解放と仮線/沈め状態のクリアを ``finally`` で必ず行う（問題6）。

        押下からほとんど動かずに離した場合は「ドラッグせずクリック」とみなし、
        接続/切断は一切行わず掴んだポートの行を選択する（ポートは行の中央側の
        端にあり、ここをクリックしても行選択できないと体感されるための対策）。
        """
        mode = self._drag["mode"]
        anchor = self._drag["anchor"]
        old_dst = self._drag["old_dst"]
        grab_side = self._drag["grab_side"]
        grabbed = self._drag["grabbed"]
        is_click = (
            global_pos - self._drag["press"]
        ).manhattanLength() <= self.CLICK_TOL
        target = self._overlay.port_at(global_pos)
        # 掴んだ側と同じ側へのドロップは無効（接続は左右間のみ）。
        # 空白扱いにすることで、同側ドロップが切断（reconnect）や同側接続にならない。
        if target is not None and target[0] == grab_side:
            target = None

        try:
            if is_click:
                self._select_plug_row(grab_side, grabbed)  # クリック = 行選択
            elif mode == "reconnect":
                if target is None:
                    self._vm.disconnect(
                        anchor, old_dst, force=self._force_disconnect_on()
                    )  # 空白 = 切断
                elif target[1] != old_dst:
                    self._vm.reconnect(
                        anchor, old_dst, target[1], force=self._force_on()
                    )  # つなぎ替え
                # 同じポートに戻した場合は何もしない（再接続のまま）
            elif target is not None and target[1] != anchor:
                self._confirm_new_connection(anchor, target[1])  # 新規（空白は取消）
        finally:
            if self._grabber is not None:
                self._grabber.releaseMouse()
            self._drag = None
            self._grabber = None
            self._overlay.clear_suppressed()
            self._overlay.clear_dimmed()
            self._overlay.clear_temp_line()

    def _confirm_new_connection(self, anchor: PlugId, target: PlugId) -> None:
        """新規ドラッグ接続を確定する（通常の親接続・master §5.2）。

        leaf 接続はドラッグではなく Connect Leaf ボタン（``connect_leaf_selected``）に
        一本化したため、ドラッグは常に通常の親接続として確定する。

        Args:
            anchor: ドラッグ開始ポートの plug。
            target: ドロップ先ポートの plug。
        """
        self._vm.try_connect(anchor, target, force=self._force_on())

    def _select_plug_row(self, side: str, plug: PlugId) -> None:
        """掴んだポートをドラッグせず離した（クリック）とき、その行を選択する。

        ポートは行の中央側の端にあり、Qt 標準の押下は ``_begin_drag`` に消費されて
        ツリーの行選択が走らない。クリック（移動なし）と判定したら、ここで掴んだ
        ポートの plug を行 index に逆引きして選択する（接続/切断はしない）。

        Args:
            side: 掴んだツリー側（``LEFT``/``RIGHT``）。
            plug: 掴んだポートの plug。
        """
        idx = self._models[side].index_for_plug(plug)
        if idx.isValid():
            self._trees[side].setCurrentIndex(idx)

    # ---- 横断切断のスラッシュ（master §5.1 優先度3） ----
    def _begin_slash(
        self, viewport: QtWidgets.QWidget, global_pos: QtCore.QPoint
    ) -> None:
        """Alt+Shift 押下で横断切断のスラッシュ線を開始する（§5.1 優先度3）。

        操作中はカーソルをカッター形に変える（Maya 慣習・無ければ無視できる装飾）。
        """
        start = self._overlay.mapFromGlobal(global_pos)
        self._slash = {"start": start}
        self._grabber = viewport
        viewport.grabMouse()
        fg = self._window.palette().color(QtGui.QPalette.WindowText)
        # 視認性のため 32px で生成。ホットスポットはブレード先端（cut.svg の
        # (3,19) を 32/24 倍した (4,25)・実際に切る点）に合わせる。
        QtWidgets.QApplication.setOverrideCursor(
            themed_cursor("cut", fg, size=32, hot_x=4, hot_y=25)
        )
        self._overlay.set_slash_line(start, start)

    def _update_slash(self, global_pos: QtCore.QPoint) -> None:
        """スラッシュ線の終端をカーソルへ追従させる。"""
        end = self._overlay.mapFromGlobal(global_pos)
        self._overlay.set_slash_line(self._slash["start"], end)

    def _end_slash(self, global_pos: QtCore.QPoint) -> None:
        """スラッシュ線が横切った全接続を切断する（§5.1 優先度3）。

        切断（実機 cmds）が例外を投げても、カッターカーソルの復帰とグラブ解放・
        スラッシュ状態のクリアを ``finally`` で必ず行う（カーソルが固まらない・問題6）。
        """
        try:
            end = self._overlay.mapFromGlobal(global_pos)
            pairs = self._overlay.connections_crossing(self._slash["start"], end)
            if pairs:
                self._vm.disconnect_pairs(pairs, force=self._force_disconnect_on())
        finally:
            self._overlay.clear_slash_line()
            QtWidgets.QApplication.restoreOverrideCursor()
            self._release_grab()
            self._slash = None

    def _release_grab(self) -> None:
        """マウスグラブを解放する（スラッシュ終了の共通処理）。"""
        if self._grabber is not None:
            self._grabber.releaseMouse()
        self._grabber = None
