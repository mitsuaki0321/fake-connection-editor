"""SceneAccess IF + FakeSceneAccess の検証（master §2.4 の _verify() を pytest 化）。

段階1 の最小IF（NodeId/PlugId/AttrMeta/Connections + 5関数）が実ノード相当の
構成（compound / array 歯抜け / 接続）を表現でき、同一性が uuid 基準である
ことを検証する。すべて Maya 非依存。
"""

from __future__ import annotations

import pytest

from fake_connection_editor.scene_access import (
    FakeSceneAccess,
    NodeId,
    PlugId,
)


def _plug(node: NodeId, *index: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(index))


# ---- 列挙・遅延展開 ----
def test_list_root_attributes(scene: FakeSceneAccess, sphere1: NodeId) -> None:
    roots = scene.list_root_attributes(sphere1)
    assert [a.display_name for a in roots] == [
        "translate",
        "scale",
        "visibility",
        "worldMatrix",
    ]
    assert roots[0].is_compound and roots[0].has_children
    assert roots[2].type_tag == "bool"
    # worldMatrix は non-keyable（フィルタ Non-Keyable 用）
    assert roots[3].is_keyable is False


def test_list_children_lazy_expand(scene: FakeSceneAccess, sphere1: NodeId) -> None:
    kids = scene.list_children(_plug(sphere1, 0))
    assert [a.display_name for a in kids] == [
        "translateX",
        "translateY",
        "translateZ",
    ]
    # 全子スカラー（master §10.3 leaf 接続の前提）
    assert all(a.type_tag == "double" for a in kids)


def test_array_existing_indices(scene: FakeSceneAccess, sphere2: NodeId) -> None:
    inmat = scene.list_root_attributes(sphere2)[3]
    assert inmat.is_array
    # [1] が歯抜け → C4 が空きを検出する材料
    assert inmat.existing_indices == (0, 2)
    # 子は既存要素のみ（ゴーストは Core C4 が算出）
    kids = scene.list_children(_plug(sphere2, 3))
    assert [a.display_name for a in kids] == ["inputMatrix[0]", "inputMatrix[2]"]


# ---- 接続取得 ----
def test_get_connections(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    # destination 側から source を引ける
    assert scene.get_connections(_plug(sphere2, 0)).sources == (_plug(sphere1, 0),)
    # source 側から destination を引ける
    assert scene.get_connections(_plug(sphere1, 0)).destinations == (_plug(sphere2, 0),)
    # worldMatrix -> inputMatrix[0]（matrix → array 要素）
    assert scene.get_connections(_plug(sphere2, 3, 0)).sources == (_plug(sphere1, 3),)


def test_list_node_connections_returns_outgoing(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    # sphere1 の外向き接続を一括列挙（connection_pairs 高速経路）。サンプルは
    # translate/scale/visibility/worldMatrix の 4 本が sphere1 -> sphere2。
    out1 = scene.list_node_connections(sphere1)
    assert set(out1) == {
        (_plug(sphere1, 0), _plug(sphere2, 0)),
        (_plug(sphere1, 1), _plug(sphere2, 1)),
        (_plug(sphere1, 2), _plug(sphere2, 2)),
        (_plug(sphere1, 3), _plug(sphere2, 3, 0)),
    }
    # すべて src が sphere1 上（外向きの定義）
    assert all(src.node.uuid == sphere1.uuid for src, _ in out1)
    # sphere2 は外向き接続を持たない（受け手のみ）
    assert scene.list_node_connections(sphere2) == []


# ---- 同一性（uuid 基準） ----
def test_node_identity_by_uuid(sphere1: NodeId) -> None:
    # path が違っても uuid が同じなら同一
    renamed = NodeId(uuid="UUID-SPHERE-1", path="|renamed|pSphere1")
    assert renamed == sphere1
    assert _plug(renamed, 0) == _plug(sphere1, 0)


def test_connections_resolve_with_renamed_node(
    scene: FakeSceneAccess, sphere1: NodeId
) -> None:
    # uuid 一致なら path 違いの PlugId でも接続を引ける
    renamed = NodeId(uuid="UUID-SPHERE-1", path="|renamed|pSphere1")
    assert scene.get_connections(_plug(renamed, 0)).destinations
    assert scene.get_connections(_plug(sphere1, 0)).destinations == (
        scene.get_connections(_plug(renamed, 0)).destinations
    )


# ---- 書き込み（connect / disconnect / force） ----
def test_connect_requires_force_when_occupied(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    # inputMatrix[0] は既に worldMatrix から接続済み
    with pytest.raises(ValueError):
        scene.connect(_plug(sphere1, 2), _plug(sphere2, 3, 0))


def test_connect_force_replaces_existing(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    dst = _plug(sphere2, 3, 0)
    scene.connect(_plug(sphere1, 2), dst, force=True)
    assert scene.get_connections(dst).sources == (_plug(sphere1, 2),)
    # 旧 source（worldMatrix）の destinations からは外れている
    assert dst not in scene.get_connections(_plug(sphere1, 3)).destinations


def test_disconnect(scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId) -> None:
    src, dst = _plug(sphere1, 3), _plug(sphere2, 3, 0)
    scene.disconnect(src, dst)
    assert scene.get_connections(dst).sources == ()
    assert dst not in scene.get_connections(src).destinations


def test_disconnect_nonexistent_is_noop(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    # 存在しない接続の切断は例外を出さず何もしない
    scene.disconnect(_plug(sphere1, 0), _plug(sphere2, 2))
    assert scene.get_connections(_plug(sphere2, 2)).sources == (_plug(sphere1, 2),)


# ---- ロック（master §5.4 force） ----
def test_lock_get_set(scene: FakeSceneAccess, sphere2: NodeId) -> None:
    plug = _plug(sphere2, 1, 0)  # scaleX
    assert scene.is_locked(plug) is False
    scene.set_locked(plug, True)
    assert scene.is_locked(plug) is True
    scene.set_locked(plug, False)
    assert scene.is_locked(plug) is False


def test_connect_rejected_when_dst_locked(
    scene: FakeSceneAccess, sphere1: NodeId, sphere2: NodeId
) -> None:
    # ロック dst は force でも connectAttr 単体では弾く（master §5.4）。
    dst = _plug(sphere2, 1, 0)  # scaleX（未接続）
    scene.set_locked(dst, True)
    with pytest.raises(ValueError):
        scene.connect(_plug(sphere1, 1, 0), dst)
    with pytest.raises(ValueError):
        scene.connect(_plug(sphere1, 1, 0), dst, force=True)
