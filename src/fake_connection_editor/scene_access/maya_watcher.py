"""Maya 側の外部変更を監視してツール表示を追従させる watcher（§M2・Maya GUI で要検証）。

ロード中ノードのアトリビュートが Maya 側で変わったとき（接続/切断・Undo/Redo・attr
追加・array 要素・lock 切替・ノード削除）に、ツールの ViewModel を再読込して表示を
同期する（MAYA_PLAN §7.1(2)・合意挙動 2026-06-18）。

設計:
    - **コールバックは OpenMaya**（接続粒度を scriptJob では拾いにくい）。ノード単位で
      ``MNodeMessage.addAttributeChangedCallback`` を 1 つ張り、msg フラグで接続/構造に
      分類する。Undo/Redo でもこのコールバックが鳴くため表示同期はフォーカス非依存。
      削除は ``MNodeMessage.addNodePreRemovalCallback``。
    - **コアレス＋スロットル**: コールバック内では ``SceneSyncDispatcher`` にダーティを
      立てるだけ＋ Qt タイマでフラッシュを予約。DG はコールバック内で読まない（変更途中
      の評価事故を避け、必ずタイマ発火後＝アイドルで読む）。高速 Undo のバーストを畳む。
    - **ドラッグ中は延期**: ユーザー操作中（``is_busy``）はフラッシュを再予約し、
      モデル再構築で足元を崩さない。
    - **ライフサイクル**: VM のリスナーに登録し、ロードノード集合が変わったら per-node
      コールバックを貼り替える。``dispose`` で全コールバックを ``removeCallback``
      （窓破棄時に必ず呼ぶ＝ダングリング＝Maya クラッシュ防止）。

注意（重要）:
    本モジュールは Claude の環境では実行・検証できない。OpenMaya のコールバック名/
    シグネチャ（特に ``addNodePreRemovalCallback`` の引数・``AttributeMessage`` の各
    enum 有無）はバージョン差があり得るので、**Maya GUI で ``docs/MAYA_VERIFY §9`` の
    手順で確認し、ずれたら直す前提の初版**である。``__init__`` からは import しない
    （Maya 非依存テストを壊さない＝``real_maya_backend`` と同じ扱い）。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger

import maya.api.OpenMaya as om  # noqa: N813  (Maya 慣習の別名)
import maya.cmds as cmds

from ..ui.qt_compat import QtCore
from ..viewmodel import SceneSyncDispatcher
from ..viewmodel.scene_sync import SceneReloadTarget

logger = getLogger(__name__)


def _attr_masks() -> tuple[int, int]:
    """``AttributeMessage`` の (接続マスク, 構造マスク) を組む（欠落 enum は除外）。"""
    msg = om.MNodeMessage
    connection = msg.kConnectionMade | msg.kConnectionBroken
    structure = 0
    for name in (
        "kAttributeAdded",
        "kAttributeRemoved",
        "kAttributeArrayAdded",
        "kAttributeArrayRemoved",
        "kAttributeLocked",
        "kAttributeUnlocked",
    ):
        structure |= getattr(msg, name, 0)
    return connection, structure


_CONNECTION_MASK, _STRUCTURE_MASK = _attr_masks()


class MayaSceneWatcher:
    """ロード中ノードの Maya 側変更を監視し、VM を再読込して表示を同期する。"""

    def __init__(
        self,
        vm: SceneReloadTarget,
        *,
        is_busy: Callable[[], bool] | None = None,
        throttle_ms: int = 80,
    ) -> None:
        """Watcher を生成し、現在ロード中のノードの監視を開始する。

        Args:
            vm: 再読込先の ViewModel（``loaded_uuids``/``reload_*``/``drop_nodes`` と
                ``add_listener``/``remove_listener`` を持つ ``EditorViewModel``）。
            is_busy: ユーザー操作中なら True を返す述語（フラッシュ延期用）。省略時は
                常に False（延期しない）。通常は ``EditorWindow.is_interacting``。
            throttle_ms: フラッシュの最短間隔（ミリ秒）。バーストを 1 回に畳む。
        """
        self._vm = vm
        self._is_busy = is_busy or (lambda: False)
        self._throttle_ms = throttle_ms
        self._dispatcher = SceneSyncDispatcher(vm)
        self._node_cbs: dict[str, list[int]] = {}
        self._disposed = False

        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

        self._vm.add_listener(self._on_vm_changed)  # type: ignore[attr-defined]
        self._sync_watched()

    # ---- 監視対象の貼り替え ----
    def _on_vm_changed(self, structural: bool, side: str | None = None) -> None:
        """VM の変更通知。ロードノード集合の変化に合わせて監視を貼り替える。

        Args:
            structural: ツリー構造が変わる変化か（watcher は使わない）。
            side: 片側だけの変化ならその側名（watcher は使わない）。
        """
        if not self._disposed:
            self._sync_watched()

    def _sync_watched(self) -> None:
        """ロード中 uuid 集合に合わせて per-node コールバックを追加/削除する。"""
        want = self._vm.loaded_uuids()  # type: ignore[attr-defined]
        have = set(self._node_cbs)
        for uuid in have - want:
            self._remove_node_cbs(uuid)
        for uuid in want - have:
            self._add_node_cbs(uuid)

    def _add_node_cbs(self, uuid: str) -> None:
        """1 ノードに属性変更/削除コールバックを張る（uuid を clientData に渡す）。"""
        node = self._mobject(uuid)
        if node is None:
            return
        ids: list[int] = []
        try:
            ids.append(
                om.MNodeMessage.addAttributeChangedCallback(
                    node, self._on_attr_changed, uuid
                )
            )
            ids.append(
                om.MNodeMessage.addNodePreRemovalCallback(
                    node, self._on_pre_removal, uuid
                )
            )
        except Exception:  # noqa: BLE001  (実機 API 差は MAYA_VERIFY §9 で確認)
            logger.exception("コールバック登録に失敗: %s", uuid)
            for cb_id in ids:
                self._safe_remove(cb_id)
            return
        self._node_cbs[uuid] = ids

    def _remove_node_cbs(self, uuid: str) -> None:
        """1 ノードのコールバックを解除する。"""
        for cb_id in self._node_cbs.pop(uuid, []):
            self._safe_remove(cb_id)

    @staticmethod
    def _safe_remove(cb_id: int) -> None:
        """コールバック ID を安全に解除する。"""
        try:
            om.MMessage.removeCallback(cb_id)
        except Exception:  # noqa: BLE001
            logger.exception("removeCallback 失敗: %s", cb_id)

    @staticmethod
    def _mobject(uuid: str) -> om.MObject | None:
        """Uuid からノード ``MObject`` を返す（無ければ None）。"""
        names = cmds.ls(uuid) or []
        if not names:
            return None
        sel = om.MSelectionList()
        sel.add(names[0])
        return sel.getDependNode(0)

    # ---- コールバック（軽量・DG を読まない） ----
    def _on_attr_changed(
        self, msg: int, plug: om.MPlug, other_plug: om.MPlug, uuid: str
    ) -> None:
        """属性変更コールバック。msg フラグで接続/構造に分類して予約する。"""
        try:
            if msg & _CONNECTION_MASK:
                self._dispatcher.mark_connections()
            if msg & _STRUCTURE_MASK:
                self._dispatcher.mark_structure()
            self._schedule()
        except Exception:  # noqa: BLE001  (コールバック内例外は DG を壊すので必ず握る)
            logger.exception("attributeChanged コールバックで例外")

    def _on_pre_removal(self, node: om.MObject, uuid: str) -> None:
        """ノード削除直前コールバック。削除を予約する。"""
        try:
            self._dispatcher.mark_removed(uuid)
            self._schedule()
        except Exception:  # noqa: BLE001
            logger.exception("preRemoval コールバックで例外")

    # ---- フラッシュ駆動（コアレス＋スロットル＋ドラッグ中延期） ----
    def _schedule(self) -> None:
        """フラッシュをタイマで予約する（スロットル＝多発を畳む）。"""
        if self._disposed or not self._dispatcher.has_pending():
            return
        if not self._timer.isActive():
            self._timer.start(self._throttle_ms)

    def _on_timer(self) -> None:
        """タイマ発火。操作中は再予約、そうでなければ 1 回フラッシュする。"""
        if self._disposed:
            return
        if self._is_busy():
            self._timer.start(self._throttle_ms)  # ドラッグ等が終わるまで延期
            return
        self._dispatcher.flush()
        if self._dispatcher.has_pending():  # フラッシュ中に来た分を次サイクルへ
            self._timer.start(self._throttle_ms)

    # ---- 破棄 ----
    def dispose(self) -> None:
        """全コールバックとリスナーを解除する（窓破棄時に必ず呼ぶ）。"""
        if self._disposed:
            return
        self._disposed = True
        self._timer.stop()
        try:
            self._vm.remove_listener(self._on_vm_changed)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.exception("remove_listener 失敗")
        for uuid in list(self._node_cbs):
            self._remove_node_cbs(uuid)
