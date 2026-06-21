"""属性メタ（``AttrMeta``）のキャッシュとクエリを担う小さなストア。

ツリー供給・接続索引・フィルタの 3 責務が共有する ``plug → AttrMeta`` のキャッシュを
1 つのオブジェクトに集約する。``SceneAccess`` には依存せず、列挙済みのメタを受け取って
保持し、型タグ・ポート有無・readable/writable・ゴースト判定を提供する純粋な格納庫。
"""

from __future__ import annotations

from logging import getLogger

from ..core import TreeNode
from ..scene_access.interface import AttrMeta, PlugId

logger = getLogger(__name__)


class MetaStore:
    """``plug → AttrMeta`` のキャッシュと、そこから導く属性クエリを担う。

    ViewModel が ``list_*`` で列挙したメタを ``cache`` で受け取り保持する。型色・
    接続可否（C1）・ゴースト判定（§5.6）はすべてこのストア越しに解決する。
    """

    def __init__(self) -> None:
        """空のストアを生成する。"""
        self._meta: dict[PlugId, AttrMeta] = {}

    def cache(self, metas: list[AttrMeta]) -> list[AttrMeta]:
        """列挙したメタを ``plug → meta`` で保持してそのまま返す。

        Args:
            metas: 保持する属性メタ列。

        Returns:
            受け取った ``metas`` をそのまま（呼び出し側でツリー構築に使う）。
        """
        for meta in metas:
            self._meta[meta.plug] = meta
        return metas

    def put(self, plug: PlugId, meta: AttrMeta) -> None:
        """1 件のメタを上書き保存する（実体化後の親 array 更新等）。"""
        self._meta[plug] = meta

    def clear(self) -> None:
        """キャッシュを空にする（シーン外部変更の再読込・構造変化時）。"""
        self._meta.clear()

    def get(self, plug: PlugId) -> AttrMeta | None:
        """Plug のメタを返す（未キャッシュなら ``None``）。"""
        return self._meta.get(plug)

    def __contains__(self, plug: PlugId) -> bool:
        """Plug がキャッシュ済みか。"""
        return plug in self._meta

    def type_tag(self, plug: PlugId) -> str:
        """Plug の正規化済み型タグを返す（未キャッシュなら空文字）。"""
        meta = self._meta.get(plug)
        return meta.type_tag if meta else ""

    def has_port(self, plug: PlugId) -> bool:
        """Plug にポートを出すか（接続可能か・master §4.3）を返す。

        readable か writable のどちらかなら接続可能＝ポートを描く。メタ未取得は
        従来どおり表示扱い（True）にしてゴースト等の取りこぼしを防ぐ。
        """
        meta = self._meta.get(plug)
        if meta is None:
            return True
        return meta.is_readable or meta.is_writable

    def is_readable(self, plug: PlugId) -> bool:
        """Plug が読み取り可（source になれる）か（メタ未取得は True）。"""
        meta = self._meta.get(plug)
        return meta.is_readable if meta is not None else True

    def is_writable(self, plug: PlugId) -> bool:
        """Plug が書き込み可（destination になれる）か（メタ未取得は True）。"""
        meta = self._meta.get(plug)
        return meta.is_writable if meta is not None else True

    def is_ghost(self, plug: PlugId) -> bool:
        """Plug がゴースト（実在しない array 要素）か返す（master §5.6）。

        親が array で、当該インデックスが既存集合に無ければゴースト。トップレベルや
        親メタ未取得の plug は False。

        Args:
            plug: 対象 plug。

        Returns:
            ゴーストなら True。
        """
        if not plug.index_path:
            return False
        parent = self._meta.get(PlugId(node=plug.node, index_path=plug.index_path[:-1]))
        if parent is None or not parent.is_array:
            return False
        return plug.index_path[-1] not in (parent.existing_indices or ())

    def register_ghosts(self, nodes: list[TreeNode]) -> None:
        """ゴースト行の型タグを登録する（型色・C1 判定用）。"""
        for node in nodes:
            if node.is_ghost and node.plug not in self._meta:
                self._meta[node.plug] = AttrMeta(
                    plug=node.plug,
                    display_name=node.display_name,
                    type_tag=node.type_tag,
                )
