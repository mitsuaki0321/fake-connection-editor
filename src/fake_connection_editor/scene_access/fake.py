"""SceneAccess のインメモリ実装 ``FakeSceneAccess``（master §2.3 / §2.4）。

固定データ（HTML モックの leftNodes/conns 相当）を返すインメモリ実装。Maya 非依存
なので、Core/ViewModel テストと dev 起動（master §1.4）はすべてこれで行える。

``FakeSceneAccess`` 自体はデータ非依存（汎用）にし、builder メソッドで属性ツリー・
接続を組み立てる。これにより、テストごとに任意のシーン（空 array・巨大ノード等。
master §11）を構築できる。pSphere1 / pSphere2 のサンプルは ``build_sample_scene()``
で組む（master §2.4 の検証フィクスチャ相当）。
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from logging import getLogger

from .interface import AttrMeta, Connections, NodeId, PlugId

logger = getLogger(__name__)


class FakeSceneAccess:
    """インメモリの SceneAccess 実装。

    属性ツリーは「親 plug → 直下の子メタ列」の辞書で持ち、遅延展開を模す。
    接続は有向ペアで保持する（入力は 1 本、出力は多数）。
    """

    def __init__(self) -> None:
        """空のシーンを生成する。

        属性・接続は ``set_root_attributes`` / ``set_children`` / ``connect`` で
        後から組み立てる。
        """
        # 属性ツリー。トップレベルは node 単位、子は (node.uuid, index_path) 単位。
        self._roots: dict[str, list[AttrMeta]] = {}
        self._children: dict[tuple[str, tuple[int, ...]], list[AttrMeta]] = {}
        # 接続（有向）。入力 1 本: dst -> src / 出力 多数: src -> [dst, ...]。
        self._dst_to_src: dict[PlugId, PlugId] = {}
        self._src_to_dsts: dict[PlugId, list[PlugId]] = {}
        # ロック状態（master §5.4）。未登録は False（未ロック）扱い。
        self._locked: dict[PlugId, bool] = {}
        # 値（値コピー用・master §5.3）。未登録は 0.0（数値スカラー既定）扱い。
        self._values: dict[PlugId, object] = {}
        # 選択（Load/Add 用・master §3.2）。実機の cmds.ls(sl=True) 相当。
        self._selection: list[NodeId] = []
        # ノード登録順（dev ピッカーが全ノードを列挙するための Fake 専用補助）。
        self._node_order: list[NodeId] = []

    # ---- builder（テスト・サンプル構築用） ----
    def set_root_attributes(self, node: NodeId, metas: list[AttrMeta]) -> None:
        """ノードのトップレベル属性メタ列を設定する。

        Args:
            node: 対象ノード。
            metas: トップレベル属性の ``AttrMeta`` 列。
        """
        self._roots[node.uuid] = list(metas)
        self._seed_locks(metas)
        if all(n.uuid != node.uuid for n in self._node_order):
            self._node_order.append(node)

    def set_children(self, plug: PlugId, metas: list[AttrMeta]) -> None:
        """ある plug の直下の子メタ列を設定する。

        Args:
            plug: 親 plug。
            metas: 直下の子の ``AttrMeta`` 列。
        """
        self._children[(plug.node.uuid, plug.index_path)] = list(metas)
        self._seed_locks(metas)

    def _seed_locks(self, metas: list[AttrMeta]) -> None:
        """メタの ``is_locked`` スナップショットを実行時ロック状態へ反映する。"""
        for meta in metas:
            if meta.is_locked:
                self._locked[meta.plug] = True

    # ---- SceneAccess IF 実装 ----
    def list_root_attributes(self, node: NodeId) -> list[AttrMeta]:
        """ノード直下のトップレベル属性メタ列を返す（遅延展開: 直下のみ）。"""
        return list(self._roots.get(node.uuid, []))

    def list_children(self, plug: PlugId) -> list[AttrMeta]:
        """Compound の子 / array の既存要素の直下メタ列を返す（遅延展開: 直下のみ）。"""
        return list(self._children.get((plug.node.uuid, plug.index_path), []))

    def get_connections(self, plug: PlugId) -> Connections:
        """Plug 自身の接続状態を返す（子孫は集約しない）。"""
        src = self._dst_to_src.get(plug)
        dsts = self._src_to_dsts.get(plug, [])
        return Connections(
            sources=(src,) if src is not None else (),
            destinations=tuple(dsts),
        )

    def list_node_connections(self, node: NodeId) -> list[tuple[PlugId, PlugId]]:
        """外向き接続（node が source）の全 (src, dst) を返す（高速経路）。"""
        return [
            (src, dst)
            for src, dsts in self._src_to_dsts.items()
            if src.node.uuid == node.uuid
            for dst in dsts
        ]

    def connected_plugs(self, node: NodeId) -> set[PlugId]:
        """接続を持つ plug の集合を返す（node 上・入出力どちらでも・高速経路）。"""
        plugs: set[PlugId] = set()
        for src, dsts in self._src_to_dsts.items():
            if src.node.uuid == node.uuid and dsts:
                plugs.add(src)
        for dst in self._dst_to_src:
            if dst.node.uuid == node.uuid:
                plugs.add(dst)
        return plugs

    def connect(self, src: PlugId, dst: PlugId, force: bool = False) -> None:
        """Src を source、dst を destination として接続する。

        Maya の ``connectAttr`` 準拠（master §5.4）: ``force`` は既存入力接続の
        置き換えだけを担い、**ロックは無視できない**。ロック dst は force でも
        弾く（ロック解除は呼び出し側 = ViewModel が事前に行う）。

        Raises:
            ValueError: dst がロックされている場合（force でも不可）。
            ValueError: dst が既に入力接続を持ち、``force`` が False の場合。
        """
        if self._locked.get(dst, False):
            raise ValueError("destination is locked (unlock before connect)")
        existing = self._dst_to_src.get(dst)
        if existing is not None:
            if not force:
                raise ValueError("destination already connected (use force)")
            # force: 既存入力を置き換える
            self._src_to_dsts[existing].remove(dst)
        self._dst_to_src[dst] = src
        self._src_to_dsts.setdefault(src, []).append(dst)

    def get_value(self, plug: PlugId):
        """Plug の現在値を返す（未設定は 0.0・master §5.3）。"""
        return self._values.get(plug, 0.0)

    def set_value(self, plug: PlugId, value) -> None:
        """Plug に値を設定する（ロック復元は呼び出し側・master §5.3/§5.4）。

        Raises:
            ValueError: plug がロックされている場合（force 解除は呼び出し側が行う）。
        """
        if self._locked.get(plug, False):
            raise ValueError("plug is locked (unlock before set)")
        self._values[plug] = value

    def is_locked(self, plug: PlugId) -> bool:
        """Plug が現在ロックされているか返す（master §5.4）。"""
        return self._locked.get(plug, False)

    def set_locked(self, plug: PlugId, locked: bool) -> None:
        """Plug のロック状態を設定する（builder / force 復元の両用・master §5.4）。"""
        self._locked[plug] = locked

    def disconnect(self, src: PlugId, dst: PlugId) -> None:
        """Src → dst の接続を切断する（存在しなければ何もしない）。

        Maya の ``disconnectAttr`` 準拠（master §5.4）: ロックされた入力（dst）は
        切断できない（ロック解除は呼び出し側 = ViewModel の force 経路が行う）。

        Raises:
            ValueError: dst がロックされている場合（force 解除は呼び出し側が行う）。
        """
        if self._locked.get(dst, False):
            raise ValueError("destination is locked (unlock before disconnect)")
        if self._dst_to_src.get(dst) == src:
            del self._dst_to_src[dst]
            self._src_to_dsts[src].remove(dst)

    def materialize_array_element(self, plug: PlugId) -> None:
        """ゴースト行の array 要素を実体化する（master §5.6・接続前に呼ぶ）。

        親 array の ``existing_indices`` に当該インデックスを足し、子メタ列に新要素を
        追加する。これによりゴーストが通常行になり、C4 が末尾に新しいゴーストを
        算出するサイクルが回る。既に存在する要素なら何もしない。

        Maya 実装（書き込み経路）は §14 次フェーズ。Fake はインメモリで完結する。

        Args:
            plug: 実体化する array 要素の ``PlugId``（親 index_path + 要素 index）。
        """
        index = plug.index_path[-1]
        parent_path = plug.index_path[:-1]
        parent_key = (plug.node.uuid, parent_path)
        parent_meta = self._find_meta(plug.node.uuid, parent_path)
        if parent_meta is None or not parent_meta.is_array:
            return
        existing = parent_meta.existing_indices or ()
        if index in existing:
            return
        # 親メタの existing_indices を更新（昇順）。
        new_existing = tuple(sorted((*existing, index)))
        self._replace_meta(
            plug.node.uuid,
            parent_path,
            replace(parent_meta, existing_indices=new_existing),
        )
        # 子メタ列に新要素を追加（要素型は親の型タグ・index 昇順）。
        children = list(self._children.get(parent_key, []))
        children.append(
            AttrMeta(
                plug=plug,
                display_name=f"{parent_meta.display_name}[{index}]",
                type_tag=parent_meta.type_tag,
            )
        )
        children.sort(key=lambda m: m.plug.index_path[-1])
        self._children[parent_key] = children

    def _find_meta(self, uuid: str, index_path: tuple[int, ...]) -> AttrMeta | None:
        """Uuid + index_path の AttrMeta を roots/children から探す（実体化補助）。"""
        if len(index_path) == 1:
            for meta in self._roots.get(uuid, []):
                if meta.plug.index_path == index_path:
                    return meta
            return None
        for meta in self._children.get((uuid, index_path[:-1]), []):
            if meta.plug.index_path == index_path:
                return meta
        return None

    def _replace_meta(
        self, uuid: str, index_path: tuple[int, ...], new_meta: AttrMeta
    ) -> None:
        """roots/children 内の AttrMeta を差し替える（実体化補助）。"""
        if len(index_path) == 1:
            self._roots[uuid] = [
                new_meta if m.plug.index_path == index_path else m
                for m in self._roots.get(uuid, [])
            ]
            return
        parent_key = (uuid, index_path[:-1])
        self._children[parent_key] = [
            new_meta if m.plug.index_path == index_path else m
            for m in self._children.get(parent_key, [])
        ]

    def get_selected_nodes(self) -> list[NodeId]:
        """設定済みの選択ノード列を返す（master §3.2）。"""
        return list(self._selection)

    def set_selection(self, nodes: list[NodeId]) -> None:
        """選択を置き換える（dev ピッカー / 将来の select-in-scene 用）。"""
        self._selection = list(nodes)

    def all_node_ids(self) -> list[NodeId]:
        """登録済み全ノードを登録順で返す（Fake 専用・dev ピッカーの選択肢）。"""
        return list(self._node_order)

    def undo_chunk(self) -> contextlib.AbstractContextManager[None]:
        """no-op の context manager を返す（Fake は undo を持たない・§2.2）。"""
        return contextlib.nullcontext()

    def get_attribute_type_colors(self) -> dict[str, tuple[float, float, float]]:
        """空 dict を返す（Fake は色設定を持たない→UI は既定の暫定色・master §4.2）。"""
        return {}


# ----------------------------------------------------------------------------
# サンプルシーン（master §2.4 の検証フィクスチャ相当・HTML モックの Python 版）
# ----------------------------------------------------------------------------
# pSphere1 / pSphere2 の 2 ノード。dev 起動（master §1.4）とテストの両方で使う。
SAMPLE_SPHERE1 = NodeId(uuid="UUID-SPHERE-1", path="|grpA|pSphere1")
SAMPLE_SPHERE2 = NodeId(uuid="UUID-SPHERE-2", path="|pSphere2")


def _plug(node: NodeId, *index: int) -> PlugId:
    """``PlugId`` を簡潔に組むヘルパ。"""
    return PlugId(node=node, index_path=tuple(index))


def _xyz_children(
    node: NodeId, root_index: int, prefix: str, short_prefix: str
) -> list[AttrMeta]:
    """compound（translate/scale 等）の X/Y/Z スカラー子を組む（long/short 名）。"""
    axes = (("X", "x", 0), ("Y", "y", 1), ("Z", "z", 2))
    return [
        AttrMeta(
            _plug(node, root_index, i),
            f"{prefix}{axis}",
            "double",
            short_name=f"{short_prefix}{ax}",
        )
        for axis, ax, i in axes
    ]


def build_sample_scene() -> FakeSceneAccess:
    """pSphere1 / pSphere2 を定義したサンプルシーンを返す。

    構成（master §2.4）:
        - pSphere1: translate/scale（double3 compound）, visibility(bool),
          worldMatrix(matrix, non-keyable)。
        - pSphere2: translate/scale, visibility, inputMatrix[]（matrix array・
          既存 index=(0, 2) で [1] が歯抜け）。
        - 接続 4 本: translate→translate, scale→scale, visibility→visibility,
          worldMatrix→inputMatrix[0]。

    Returns:
        サンプルデータを投入済みの ``FakeSceneAccess``。
    """
    scene = FakeSceneAccess()

    # --- pSphere1 ---
    n1 = SAMPLE_SPHERE1
    scene.set_root_attributes(
        n1,
        [
            AttrMeta(
                _plug(n1, 0),
                "translate",
                "double3",
                short_name="t",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(
                _plug(n1, 1),
                "scale",
                "double3",
                short_name="s",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(_plug(n1, 2), "visibility", "bool", short_name="v"),
            AttrMeta(
                _plug(n1, 3),
                "worldMatrix",
                "matrix",
                short_name="wm",
                is_keyable=False,
            ),
        ],
    )
    scene.set_children(_plug(n1, 0), _xyz_children(n1, 0, "translate", "t"))
    scene.set_children(_plug(n1, 1), _xyz_children(n1, 1, "scale", "s"))

    # --- pSphere2 ---
    n2 = SAMPLE_SPHERE2
    scene.set_root_attributes(
        n2,
        [
            AttrMeta(
                _plug(n2, 0),
                "translate",
                "double3",
                short_name="t",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(
                _plug(n2, 1),
                "scale",
                "double3",
                short_name="s",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(_plug(n2, 2), "visibility", "bool", short_name="v"),
            AttrMeta(
                _plug(n2, 3),
                "inputMatrix",
                "matrix",
                short_name="im",
                is_array=True,
                has_children=True,
                existing_indices=(0, 2),
            ),
        ],
    )
    scene.set_children(_plug(n2, 0), _xyz_children(n2, 0, "translate", "t"))
    scene.set_children(_plug(n2, 1), _xyz_children(n2, 1, "scale", "s"))
    # inputMatrix の子 = 既存要素のみ（ゴーストは Core C4 が existing_indices から算出）
    scene.set_children(
        _plug(n2, 3),
        [
            AttrMeta(_plug(n2, 3, 0), "inputMatrix[0]", "matrix", short_name="im[0]"),
            AttrMeta(_plug(n2, 3, 2), "inputMatrix[2]", "matrix", short_name="im[2]"),
        ],
    )

    # --- 接続 4 本 ---
    scene.connect(_plug(n1, 0), _plug(n2, 0))  # translate -> translate
    scene.connect(_plug(n1, 1), _plug(n2, 1))  # scale -> scale
    scene.connect(_plug(n1, 2), _plug(n2, 2))  # visibility -> visibility
    scene.connect(_plug(n1, 3), _plug(n2, 3, 0))  # worldMatrix -> inputMatrix[0]

    return scene


# ----------------------------------------------------------------------------
# 縦長シーン（dev 専用・左右独立スクロール / 画面外矢印 §3.1/§4.7 の目視確認用）
# ----------------------------------------------------------------------------
# 既定ウィンドウに収まらない行数（既定 40 属性）にして、スクロールバーが出る状態を
# 作る。接続は上端〜下端に散らし、スクロールで端点が画面外に出る様子を見られるように
# する。テストには使わない（テストは build_sample_scene を使う）。
TALL_LEFT = NodeId(uuid="UUID-TALL-L", path="|driver")
TALL_RIGHT = NodeId(uuid="UUID-TALL-R", path="|driven")

# 型を循環させて型色の違いも分かるようにする（master §4.2）。
_TALL_TYPES = ["double", "double", "bool", "matrix", "double"]


def build_tall_scene(count: int = 40) -> FakeSceneAccess:
    """属性を多数並べた縦長の dev 専用シーンを返す（§3.1/§4.7 確認用）。

    左右に同数のフラット属性（``attr00``..）を並べ、上端・中央・下端付近に接続を
    散らす。既定 ``count`` は既定ウィンドウに収まらない行数なので、起動すると
    左右にスクロールバーが出る（左は左端・右は右端）。

    Args:
        count: 左右それぞれの属性数。

    Returns:
        サンプルデータを投入済みの ``FakeSceneAccess``。
    """
    scene = FakeSceneAccess()
    for node in (TALL_LEFT, TALL_RIGHT):
        metas = [
            AttrMeta(_plug(node, i), f"attr{i:02d}", _TALL_TYPES[i % len(_TALL_TYPES)])
            for i in range(count)
        ]
        scene.set_root_attributes(node, metas)

    # 上端〜下端に散らした接続（同型同士のみ。型互換は C2 が判定するが、ここでは
    # 単純に同 index＝同型で繋ぐ）。スクロールで端点が画面外に出るのを確認できる。
    for i in (0, 1, count // 2, count // 2 + 1, count - 2, count - 1):
        scene.connect(_plug(TALL_LEFT, i), _plug(TALL_RIGHT, i))

    return scene


# ----------------------------------------------------------------------------
# 複数ノードシーン（dev 専用・セクションヘッダ / 束出し §4.6 の目視確認用）
# ----------------------------------------------------------------------------
# 左右に別ノードを複数積む（同一ノードの左右同時表示は避ける）。
MULTI_L1 = NodeId(uuid="UUID-MULTI-L1", path="|ctrlA")
MULTI_L2 = NodeId(uuid="UUID-MULTI-L2", path="|ctrlB")
MULTI_R1 = NodeId(uuid="UUID-MULTI-R1", path="|jointA")
MULTI_R2 = NodeId(uuid="UUID-MULTI-R2", path="|jointB")


def _add_xform(scene: FakeSceneAccess, node: NodeId) -> None:
    """translate/scale(double3) + visibility(bool) を持つノードを組む。"""
    scene.set_root_attributes(
        node,
        [
            AttrMeta(
                _plug(node, 0),
                "translate",
                "double3",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(
                _plug(node, 1),
                "scale",
                "double3",
                is_compound=True,
                has_children=True,
            ),
            AttrMeta(_plug(node, 2), "visibility", "bool"),
        ],
    )
    scene.set_children(_plug(node, 0), _xyz_children(node, 0, "translate", "t"))
    scene.set_children(_plug(node, 1), _xyz_children(node, 1, "scale", "s"))


def build_multi_scene() -> FakeSceneAccess:
    """左右に複数ノードを積んだ dev 専用シーンを返す（§4.6 確認用）。

    左 = ctrlA / ctrlB、右 = jointA / jointB。接続は左右をまたぐもののみ
    （同一サイド内・同一ノードの左右同時は対象外）。子属性への接続を 1 本含め、
    ノードを畳んだときのヘッダ束出し・二重丸を確認できる。

    Returns:
        サンプルデータを投入済みの ``FakeSceneAccess``。
    """
    scene = FakeSceneAccess()
    for node in (MULTI_L1, MULTI_L2, MULTI_R1, MULTI_R2):
        _add_xform(scene, node)

    scene.connect(_plug(MULTI_L1, 0), _plug(MULTI_R1, 0))  # ctrlA.t -> jointA.t
    scene.connect(_plug(MULTI_L1, 2), _plug(MULTI_R1, 2))  # ctrlA.v -> jointA.v
    scene.connect(_plug(MULTI_L2, 0), _plug(MULTI_R2, 0))  # ctrlB.t -> jointB.t
    # 子属性への接続（畳み時の束出し確認）: ctrlA.translateX -> jointB.translateX
    scene.connect(_plug(MULTI_L1, 0, 0), _plug(MULTI_R2, 0, 0))

    return scene
