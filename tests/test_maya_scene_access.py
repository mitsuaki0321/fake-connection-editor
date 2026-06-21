"""MayaSceneAccess + FakeMayaBackend のテスト（MAYA_PLAN §5・§11.5）。Maya 非依存。

歩行（index_path 解決・leaf 名非依存）・existing indices・型正規化・接続端点マッピング・
書き込み委譲・ゴースト実体化を、採取値を写したモックバックエンド上で固定する。
"""

from __future__ import annotations

from fake_connection_editor.scene_access import MayaSceneAccess, normalize_type
from fake_connection_editor.scene_access.fake_maya_backend import (
    SAMPLE_A,
    SAMPLE_B,
    build_sample_maya_backend,
)
from fake_connection_editor.scene_access.interface import NodeId, PlugId


def _plug(node: NodeId, *index: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(index))


def _scene() -> MayaSceneAccess:
    return MayaSceneAccess(build_sample_maya_backend())


# ---- 型正規化（純粋関数・表駆動） ----
def test_undo_chunk_is_usable_context_manager() -> None:
    # Fake バックエンドは no-op だが、with で使えること（§2.2 配線の健全性）。
    scene = _scene()
    with scene.undo_chunk():
        src = _plug(SAMPLE_A, 0, 1)
        dst = _plug(SAMPLE_B, 0, 1)
        scene.connect(src, dst)
    assert scene.get_connections(src).destinations == (dst,)


def test_normalize_type_table() -> None:
    assert normalize_type("kNumericAttribute", "kDouble") == "double"
    assert normalize_type("kNumericAttribute", "kBoolean") == "bool"
    assert normalize_type("kNumericAttribute", "k3Double") == "double3"
    assert normalize_type("kNumericAttribute", "k3Float") == "float3"
    assert normalize_type("kDoubleLinearAttribute") == "double"
    assert normalize_type("kFloatLinearAttribute") == "float"
    # matrix は専用 attr でも typed の inner kMatrix でも統一（unwrap）
    assert normalize_type("kMatrixAttribute") == "matrix"
    assert normalize_type("kTypedAttribute", "kMatrix") == "matrix"
    assert normalize_type("kMessageAttribute") == "message"
    assert normalize_type("kCompoundAttribute") == "compound"
    # 未知は data フォールバック
    assert normalize_type("kSomethingUnknown") == "data"
    assert normalize_type("kTypedAttribute", "kMesh") == "data"


# ---- トップレベル列挙 ----
def test_list_root_attributes() -> None:
    scene = _scene()
    metas = scene.list_root_attributes(SAMPLE_A)
    by_name = {m.display_name: m for m in metas}
    assert [m.display_name for m in metas] == [
        "translate",
        "visibility",
        "inputMatrix",
        "lockedAttr",
        "message",
    ]
    # 型正規化が効いている
    assert by_name["translate"].type_tag == "double3"
    assert by_name["visibility"].type_tag == "bool"
    assert by_name["inputMatrix"].type_tag == "matrix"
    assert by_name["message"].type_tag == "message"
    # フラグ
    assert by_name["translate"].is_compound and by_name["translate"].has_children
    assert by_name["inputMatrix"].is_array
    assert by_name["inputMatrix"].existing_indices == (0, 2)  # 歯抜け[1]
    assert by_name["lockedAttr"].is_locked
    # ユーザー定義（extra）フラグが normalize を通って引き継がれる
    assert by_name["lockedAttr"].is_user_defined is True
    assert by_name["translate"].is_user_defined is False
    # index_path は位置順
    assert by_name["translate"].plug.index_path == (0,)
    assert by_name["inputMatrix"].plug.index_path == (2,)


def test_unknown_node_returns_empty() -> None:
    scene = _scene()
    assert scene.list_root_attributes(NodeId(uuid="missing", path="|x")) == []


# ---- compound 子の遅延展開（位置インデックス歩行） ----
def test_list_children_compound() -> None:
    scene = _scene()
    children = scene.list_children(_plug(SAMPLE_A, 0))  # translate
    assert [c.display_name for c in children] == [
        "translateX",
        "translateY",
        "translateZ",
    ]
    assert [c.plug.index_path for c in children] == [(0, 0), (0, 1), (0, 2)]
    assert all(c.type_tag == "double" for c in children)


# ---- array 既存要素の列挙（論理インデックス歩行・ゴーストは含めない） ----
def test_list_children_array_existing_only() -> None:
    scene = _scene()
    elements = scene.list_children(_plug(SAMPLE_A, 2))  # inputMatrix
    # 既存 [0],[2] のみ（歯抜け[1]=ゴーストは Core C4 が出す。ここでは出さない）
    assert [e.plug.index_path for e in elements] == [(2, 0), (2, 2)]
    assert [e.display_name for e in elements] == ["inputMatrix[0]", "inputMatrix[2]"]
    assert all(e.type_tag == "matrix" for e in elements)


# ---- 接続端点マッピング（両ノードに跨る） ----
def test_get_connections_maps_endpoints() -> None:
    scene = _scene()
    src = _plug(SAMPLE_A, 0, 0)  # nodeA.translateX
    dst = _plug(SAMPLE_B, 0, 0)  # nodeB.translateX
    a = scene.get_connections(src)
    assert a.destinations == (dst,)
    assert a.sources == ()
    b = scene.get_connections(dst)
    assert b.sources == (src,)
    assert b.destinations == ()
    # 端点ノードの同一性は uuid 基準
    assert b.sources[0].node.uuid == SAMPLE_A.uuid


def test_list_node_connections_maps_outgoing() -> None:
    # ノード単位の外向き接続列挙（connection_pairs 高速経路）も端点を PlugId 化する。
    scene = _scene()
    src = _plug(SAMPLE_A, 0, 0)  # nodeA.translateX
    dst = _plug(SAMPLE_B, 0, 0)  # nodeB.translateX
    out_a = scene.list_node_connections(SAMPLE_A)
    assert out_a == [(src, dst)]  # A の外向き接続は A.tx -> B.tx の 1 本
    assert out_a[0][1].node.uuid == SAMPLE_B.uuid  # dst は別ノード（uuid 基準）
    assert scene.list_node_connections(SAMPLE_B) == []  # B は外向き接続なし


# ---- 書き込み委譲（connect/disconnect が正しい接続に反映される） ----
def test_connect_and_disconnect() -> None:
    scene = _scene()
    s = _plug(SAMPLE_A, 0, 1)  # nodeA.translateY
    d = _plug(SAMPLE_B, 0, 1)  # nodeB.translateY
    assert scene.get_connections(s).destinations == ()
    scene.connect(s, d)
    assert scene.get_connections(s).destinations == (d,)
    assert scene.get_connections(d).sources == (s,)
    scene.disconnect(s, d)
    assert scene.get_connections(s).destinations == ()


def test_connect_force_replaces_existing_input() -> None:
    scene = _scene()
    # nodeB.translateX は既に nodeA.translateX から入力を受けている
    dst = _plug(SAMPLE_B, 0, 0)
    new_src = _plug(SAMPLE_A, 0, 2)  # nodeA.translateZ
    scene.connect(new_src, dst, force=True)
    conns = scene.get_connections(dst)
    assert conns.sources == (new_src,)  # 既存入力が置換された


# ---- 値 / ロックの委譲 ----
def test_value_and_lock_delegation() -> None:
    scene = _scene()
    locked = _plug(SAMPLE_A, 3)  # lockedAttr
    assert scene.get_value(locked) == 5.0
    assert scene.is_locked(locked) is True
    scene.set_locked(locked, False)
    assert scene.is_locked(locked) is False
    scene.set_value(locked, 9.0)
    assert scene.get_value(locked) == 9.0


# ---- ゴースト実体化（materialize 後に list_children へ現れる） ----
def test_materialize_array_element() -> None:
    scene = _scene()
    ghost = _plug(SAMPLE_A, 2, 1)  # inputMatrix[1]（歯抜け = ゴースト）
    before = scene.list_children(_plug(SAMPLE_A, 2))
    assert [e.plug.index_path for e in before] == [(2, 0), (2, 2)]
    scene.materialize_array_element(ghost)
    after = scene.list_children(_plug(SAMPLE_A, 2))
    assert [e.plug.index_path for e in after] == [(2, 0), (2, 1), (2, 2)]
    materialized = next(e for e in after if e.plug.index_path == (2, 1))
    assert materialized.display_name == "inputMatrix[1]"
    assert materialized.type_tag == "matrix"


# ---- 型色（Color Settings 読み取り・分類→Maya キー） ----
def test_attribute_type_colors_maps_categories() -> None:
    scene = _scene()
    colors = scene.get_attribute_type_colors()
    # 採取値（maya_color_probe.py 由来）が分類文字列キーで返る
    assert colors["numeric"] == (0.4784, 0.6, 0.4196)
    assert colors["bool"] == (0.851, 0.7098, 0.5529)
    assert colors["matrix"] == (0.3137, 0.4902, 0.549)
    assert colors["color"] == (0.9098, 0.0, 0.0)
    assert colors["data"] == (0.0, 0.0, 0.0)  # Maya の data は黒（UI 側で明度底上げ）


def test_attribute_type_colors_omits_unset_keys() -> None:
    from fake_connection_editor.scene_access.fake_maya_backend import FakeMayaBackend

    # display_color 未設定の backend では空 dict（UI 既定にフォールバック）
    scene = MayaSceneAccess(FakeMayaBackend())
    assert scene.get_attribute_type_colors() == {}


# ---- 選択（Load/Add 用） ----
def test_selection_roundtrip() -> None:
    scene = _scene()
    selected = scene.get_selected_nodes()
    assert [n.uuid for n in selected] == [SAMPLE_A.uuid]
    assert selected[0].path == SAMPLE_A.path  # path も復元
    scene.set_selection([SAMPLE_B])
    assert [n.uuid for n in scene.get_selected_nodes()] == [SAMPLE_B.uuid]
