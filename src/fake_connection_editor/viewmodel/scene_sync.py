"""シーン外部変更のコアレス用ディスパッチャ（ライブ同期・MAYA_PLAN §7.1(2)）。

Maya 側でロード中ノードの接続/構造が変わったとき、コールバックが高速多発しても
**1 回の再読込に畳む**ためのダーティ集約。Maya にも Qt にも依存しない純粋ロジックで、
スケジューリング（Qt タイマ等）と Maya コールバックは呼び出し側（watcher）が担う。

使い方:
    dispatcher.mark_connections()    # 接続/切断・Undo/Redo
    dispatcher.mark_structure()      # attr 追加 / array 要素 / lock 切替
    dispatcher.mark_removed(uuid)    # ノード削除
    ...（バーストの間 mark_* が積もる）...
    dispatcher.flush()               # アイドル/スロットルで 1 回だけ実行
"""

from __future__ import annotations

from logging import getLogger
from typing import Protocol

logger = getLogger(__name__)


class SceneReloadTarget(Protocol):
    """``SceneSyncDispatcher`` が再読込を委譲する先（= ``EditorViewModel``）。"""

    def reload_connections(self) -> None:
        """接続のみの外部変更を取り込む。"""
        ...

    def reload_structure(self) -> None:
        """構造変化を伴う外部変更を取り込む。"""
        ...

    def drop_nodes(self, uuids: set[str]) -> bool:
        """指定 uuid を一覧から外す（外したら True）。"""
        ...


class SceneSyncDispatcher:
    """外部変更のダーティを集約し、``flush`` で最小限の再読込に畳む。

    優先順は **削除 ＞ 構造 ＞ 接続**。上位の再読込は下位を内包する（構造再読込は
    接続も読み直すため、構造が必要なら接続再読込は不要）。
    """

    def __init__(self, target: SceneReloadTarget) -> None:
        """ディスパッチャを生成する。

        Args:
            target: 再読込メソッドを持つ ViewModel。
        """
        self._target = target
        self._connections = False
        self._structure = False
        self._removed: set[str] = set()

    def mark_connections(self) -> None:
        """接続のみの変化を予約する（接続/切断・Undo/Redo）。"""
        self._connections = True

    def mark_structure(self) -> None:
        """構造変化を予約する（attr 追加 / array 要素 / lock 切替）。"""
        self._structure = True

    def mark_removed(self, uuid: str) -> None:
        """ノード削除を予約する。"""
        self._removed.add(uuid)

    def has_pending(self) -> bool:
        """未処理のダーティがあるか（スケジュール要否の判定用）。"""
        return self._connections or self._structure or bool(self._removed)

    def flush(self) -> None:
        """溜まったダーティを優先順で 1 アクションにまとめて実行する。

        実行前にフラグを確定・リセットするので、再読込中に新たな mark_* が来ても
        次回 ``flush`` で拾える（取りこぼさない）。
        """
        removed = self._removed
        structure = self._structure
        connections = self._connections
        self._removed = set()
        self._structure = False
        self._connections = False

        did_structural = False
        if removed:
            did_structural = self._target.drop_nodes(removed)
        if structure and not did_structural:
            self._target.reload_structure()
            did_structural = True
        if connections and not did_structural:
            self._target.reload_connections()
