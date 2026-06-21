"""エッジケース網羅（master §11・B-2）。Maya/Qt 非依存。

空シーン / 未ロードノード / 未知 plug / 巨大ノード（遅延展開）など、サンプルシーンの
正常系では踏まないカスタムデータの振る舞いを Fake 上で固定する。
"""

from __future__ import annotations

from fake_connection_editor.core import FilterCriteria, TypeCategory
from fake_connection_editor.scene_access import (
    AttrMeta,
    Connections,
    FakeSceneAccess,
    NodeId,
    PlugId,
)
from fake_connection_editor.scene_access.fake import SAMPLE_SPHERE1, SAMPLE_SPHERE2
from fake_connection_editor.viewmodel import LEFT, RIGHT, EditorViewModel


def _plug(node: NodeId, *index: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(index))


# ---- 空シーン（何もロードしていない） ----
def test_empty_scene_supplies_nothing() -> None:
    vm = EditorViewModel(FakeSceneAccess())
    assert vm.root_nodes(LEFT) == []
    assert vm.root_nodes(RIGHT) == []
    assert vm.nodes(LEFT) == []
    assert vm.nodes(RIGHT) == []
    assert vm.connection_pairs() == []


def test_empty_scene_markers_are_false() -> None:
    vm = EditorViewModel(FakeSceneAccess())
    unknown = _plug(NodeId(uuid="U-X", path="|x"), 0)
    assert vm.has_connected_descendant(unknown) is False
    assert vm.node_has_connection_by_uuid("U-X") is False
    assert vm.side_of(unknown) is None


# ---- 未ロードノードの除外（接続の片端だけロード） ----
def test_connection_pairs_excludes_unloaded_endpoint(scene: FakeSceneAccess) -> None:
    # 左に pSphere1 だけロードし、右（pSphere2）は未ロード。
    # サンプルの 4 接続はすべて dst が pSphere2 なので、相手未ロード＝描けないため除外。
    vm = EditorViewModel(scene)
    vm.load(LEFT, SAMPLE_SPHERE1)
    assert vm.connection_pairs() == []
    # 右もロードすると 4 本そろう。
    vm.load(RIGHT, SAMPLE_SPHERE2)
    assert len(vm.connection_pairs()) == 4


# ---- 未知 plug（メタ未取得 / シーンに無い）への問い合わせ堅牢性 ----
def test_unknown_plug_queries_are_safe() -> None:
    vm = EditorViewModel(FakeSceneAccess())
    unknown = _plug(NodeId(uuid="U-X", path="|x"), 0)
    assert vm.type_tag(unknown) == ""  # 型未キャッシュは空文字
    assert vm.has_port(unknown) is True  # メタ未取得は表示扱い（取りこぼし防止）
    assert vm.get_connections(unknown) == Connections()  # 接続なし
    assert vm.is_connected(unknown) is False
    assert vm.is_ghost(unknown) is False  # 親メタ未取得はゴースト扱いしない


# ---- 巨大ノード + 遅延展開（master §10.1 / NFR-02） ----
class _CountingFake(FakeSceneAccess):
    """``list_children`` / ``get_connections`` / ノード接続列挙の回数を数える Fake。"""

    def __init__(self) -> None:
        super().__init__()
        self.children_calls = 0
        self.conn_calls = 0
        self.node_conn_calls = 0
        self.connected_plugs_calls = 0

    def list_children(self, plug: PlugId) -> list[AttrMeta]:
        self.children_calls += 1
        return super().list_children(plug)

    def get_connections(self, plug: PlugId) -> Connections:
        self.conn_calls += 1
        return super().get_connections(plug)

    def list_node_connections(self, node: NodeId) -> list[tuple[PlugId, PlugId]]:
        self.node_conn_calls += 1
        return super().list_node_connections(node)

    def connected_plugs(self, node: NodeId) -> set[PlugId]:
        self.connected_plugs_calls += 1
        return super().connected_plugs(node)


def _build_large_node(scene: FakeSceneAccess, node: NodeId, count: int) -> None:
    """``count`` 個の compound 属性（各 3 子）を持つ巨大ノードを組む。"""
    roots = [
        AttrMeta(
            _plug(node, i),
            f"attr{i}",
            "double3",
            is_compound=True,
            has_children=True,
        )
        for i in range(count)
    ]
    scene.set_root_attributes(node, roots)
    for i in range(count):
        scene.set_children(
            _plug(node, i),
            [AttrMeta(_plug(node, i, j), f"attr{i}_{j}", "double") for j in range(3)],
        )


def test_large_node_lazy_expansion() -> None:
    scene = _CountingFake()
    node = NodeId(uuid="U-BIG", path="|big")
    _build_large_node(scene, node, count=100)
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)

    # トップレベルは全件供給されるが、子はまだ取りに行かない（遅延展開）。
    roots = vm.root_nodes(LEFT)
    assert len(roots) == 100
    assert all(n.is_expandable for n in roots)
    assert scene.children_calls == 0

    # 1 親を展開したぶんだけ list_children が呼ばれる。
    kids = vm.child_nodes(_plug(node, 0))
    assert [k.display_name for k in kids] == ["attr0_0", "attr0_1", "attr0_2"]
    assert scene.children_calls == 1


def test_connection_pairs_uses_node_level_query() -> None:
    # connection_pairs は全 plug を get_connections で舐めず（O(全 plug)）、ノード単位の
    # 接続列挙（O(ノード)）で集約する（実機 OpenMaya 照会の固まり対策）。
    scene = _CountingFake()
    node = NodeId(uuid="U-BIG", path="|big")
    _build_large_node(scene, node, count=100)  # 100 親 × 3 子 = 多数の plug
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)

    scene.conn_calls = 0
    scene.node_conn_calls = 0
    pairs = vm.connection_pairs()
    assert pairs == []  # この巨大ノードは接続なし
    assert scene.conn_calls == 0  # per-plug 照会は呼ばない（重い経路を踏まない）
    assert scene.node_conn_calls == 1  # ロード 1 ノード = 1 回だけ列挙


def test_type_filter_skips_connection_queries() -> None:
    # 型/テキストフィルタでは接続照会（実機は OpenMaya で重い）を呼ばない（問題2）。
    scene = _CountingFake()
    node = NodeId(uuid="U-BIG", path="|big")
    _build_large_node(scene, node, count=20)
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)

    # 型フィルタ（MATRIX のみ）= 接続を一切見ない
    vm.set_filter(
        LEFT, FilterCriteria(enabled_categories=frozenset({TypeCategory.MATRIX}))
    )
    scene.conn_calls = 0
    scene.connected_plugs_calls = 0
    vm.visible_attr_nodes(LEFT, node)  # _visible_set を計算させる
    assert scene.conn_calls == 0
    assert scene.connected_plugs_calls == 0

    # connected-only フィルタ = 接続を見るが、per-plug 照会ではなくノード単位の
    # connected_plugs を 1 回だけ呼ぶ（O(ノード)・全 plug 照会を避ける）
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory), show_connected_only=True
        ),
    )
    scene.conn_calls = 0
    scene.connected_plugs_calls = 0
    vm.visible_attr_nodes(LEFT, node)
    assert scene.conn_calls == 0  # per-plug get_connections は呼ばない
    assert scene.connected_plugs_calls == 1  # ロード 1 ノード = 1 回だけ列挙


def test_filter_change_reuses_walk_cache() -> None:
    # フィルタを変えても属性木の再列挙（OpenMaya）をしない（列挙キャッシュ・問題2）。
    scene = _CountingFake()
    node = NodeId(uuid="U-BIG", path="|big")
    _build_large_node(scene, node, count=20)
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)

    vm.set_filter(
        LEFT, FilterCriteria(enabled_categories=frozenset({TypeCategory.MATRIX}))
    )
    vm.visible_attr_nodes(LEFT, node)  # 初回 walk（列挙が走る）
    calls_first = scene.children_calls
    assert calls_first > 0

    # 別フィルタへ変更 → 列挙キャッシュ命中で list_children は増えない
    vm.set_filter(
        LEFT,
        FilterCriteria(enabled_categories=frozenset(TypeCategory), text="attr1"),
    )
    vm.visible_attr_nodes(LEFT, node)
    assert scene.children_calls == calls_first
