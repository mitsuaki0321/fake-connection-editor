"""EditorViewModel の検証（master §1.2 / A1=ViewModel 独立）。Maya/Qt 非依存。

段階1 の ViewModel が、ツリー供給・接続状態・ドラッグ接続/切断（向き正規化）・
変更通知を正しく行うことを Fake シーン上で検証する。
"""

from __future__ import annotations

import pytest

from fake_connection_editor.core import FilterCriteria, TypeCategory
from fake_connection_editor.scene_access import (
    AttrMeta,
    FakeSceneAccess,
    NodeId,
    PlugId,
)
from fake_connection_editor.scene_access.fake import SAMPLE_SPHERE1, SAMPLE_SPHERE2
from fake_connection_editor.viewmodel import (
    LEFT,
    RIGHT,
    EditorViewModel,
    NameMode,
    SortMode,
)
from fake_connection_editor.viewmodel.editor import ConnectBlock, CopyReason


def _plug(node: NodeId, *index: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(index))


def _loaded_vm(scene: FakeSceneAccess) -> EditorViewModel:
    vm = EditorViewModel(scene)
    vm.load(LEFT, SAMPLE_SPHERE1)
    vm.load(RIGHT, SAMPLE_SPHERE2)
    return vm


# ---- 並び替え（左右共通・array は index 順維持） ----
def test_sort_mode_orders_top_level(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # 既定は現順（シーン定義順）
    assert vm.sort_mode() == SortMode.SCENE
    scene_order = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert scene_order == ["translate", "scale", "visibility", "worldMatrix"]
    # 昇順 / 降順
    vm.set_sort_mode(SortMode.ASC)
    asc = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert asc == ["scale", "translate", "visibility", "worldMatrix"]
    vm.set_sort_mode(SortMode.DESC)
    desc = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert desc == ["worldMatrix", "visibility", "translate", "scale"]
    # 現順に戻す
    vm.set_sort_mode(SortMode.SCENE)
    assert [
        n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)
    ] == scene_order


def test_sort_mode_notifies_structural(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    events: list[bool] = []
    vm.add_listener(lambda s, _side=None: events.append(s))
    vm.set_sort_mode(SortMode.ASC)
    assert events == [True]  # structural（行順が変わる）
    events.clear()
    vm.set_sort_mode(SortMode.ASC)  # 同じ→通知なし
    assert events == []


def test_sort_keeps_array_elements_in_index_order(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.set_sort_mode(SortMode.DESC)
    # 親（inputMatrix）を先に列挙してメタを確定させる（UI の親展開→子の順を模す）。
    vm.visible_attr_nodes(RIGHT, SAMPLE_SPHERE2)
    # inputMatrix[] = 既存 [0],[2] + ゴースト（空き [1]・末尾次 [3]）。降順でも
    # 名前文字列でなく index 昇順を維持する（array 要素はソート対象外）。
    children = vm.visible_child_nodes(RIGHT, _plug(SAMPLE_SPHERE2, 3))
    assert [c.plug.index_path[-1] for c in children] == [0, 1, 2, 3]


# ---- 属性名の表示モード（long / short・左右共通） ----
def test_name_mode_switches_labels(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    assert vm.name_mode() == NameMode.LONG
    nodes = vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)
    assert [vm.attr_label(n) for n in nodes] == [
        "translate",
        "scale",
        "visibility",
        "worldMatrix",
    ]
    vm.set_name_mode(NameMode.SHORT)
    assert [vm.attr_label(n) for n in nodes] == ["t", "s", "v", "wm"]


def test_name_mode_notifies_structural(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    events: list[bool] = []
    vm.add_listener(lambda s, _side=None: events.append(s))
    vm.set_name_mode(NameMode.SHORT)
    assert events == [True]
    events.clear()
    vm.set_name_mode(NameMode.SHORT)  # 同じ→通知なし
    assert events == []


def test_sort_uses_displayed_name(scene: FakeSceneAccess) -> None:
    # short 表示 + 昇順 → short 名でソートされる（t, s, v, wm → s, t, v, wm）。
    vm = _loaded_vm(scene)
    vm.set_name_mode(NameMode.SHORT)
    vm.set_sort_mode(SortMode.ASC)
    nodes = vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)
    assert [vm.attr_label(n) for n in nodes] == ["s", "t", "v", "wm"]


# ---- ロード / ツリー供給 ----
def test_load_and_root_nodes(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    assert vm.loaded_node(LEFT) == SAMPLE_SPHERE1
    names = [n.display_name for n in vm.root_nodes(LEFT)]
    assert names == ["translate", "scale", "visibility", "worldMatrix"]


def test_empty_side_returns_no_nodes(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    assert vm.root_nodes(LEFT) == []


def test_child_nodes_lazy(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    kids = vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    assert [n.display_name for n in kids] == [
        "translateX",
        "translateY",
        "translateZ",
    ]


def test_type_tag_cached_after_enumeration(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)  # キャッシュを満たす
    assert vm.type_tag(_plug(SAMPLE_SPHERE1, 2)) == "bool"


# ---- 接続状態 ----
def test_is_connected(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    assert vm.is_connected(_plug(SAMPLE_SPHERE1, 0)) is True  # translate（出力）
    assert vm.is_connected(_plug(SAMPLE_SPHERE1, 0, 0)) is False  # translateX 未接続


def test_is_connected_to_loaded_distinguishes_external(scene: FakeSceneAccess) -> None:
    # 片側だけロード（cubeB を未ロードにする）と、ロード外への接続が区別される。
    vm = EditorViewModel(scene)
    vm.load(LEFT, SAMPLE_SPHERE1)  # 右は未ロード
    vm.root_nodes(LEFT)
    translate = _plug(SAMPLE_SPHERE1, 0)  # translate（相手 sphere2 は今は未ロード）
    assert vm.is_connected(translate) is True  # 接続はある
    assert vm.is_connected_to_loaded(translate) is False  # 相手は未ロード＝外部接続
    # 相手（sphere2）もロードすると「ロード相手と接続」に変わる。
    vm.load(RIGHT, SAMPLE_SPHERE2)
    assert vm.is_connected_to_loaded(translate) is True


# ---- ドラッグ接続（向き正規化・§6） ----
def test_try_connect_direction_normalized(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translateX(src 未接続) → translateX(dst 未接続)。逆向きでドラッグしても接続される
    src = _plug(SAMPLE_SPHERE1, 0, 0)
    dst = _plug(SAMPLE_SPHERE2, 0, 0)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))  # 型キャッシュ
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    assert vm.try_connect(dst, src) is True  # ドロップ順が逆でも C1 が正規化
    conns = vm.get_connections(dst)
    # どちらか一方向に有向で繋がっていること
    assert (src in conns.sources) or (dst in vm.get_connections(src).sources)


def test_try_connect_replaces_occupied_dst_forward(scene: FakeSceneAccess) -> None:
    # 既に入力のある dst へ別 source をドロップすると、順向きで置換する（逆流しない）。
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))  # 型キャッシュ
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    tx1 = _plug(SAMPLE_SPHERE1, 0, 0)  # translateX (source 候補)
    ty1 = _plug(SAMPLE_SPHERE1, 0, 1)  # translateY (別 source)
    tx2 = _plug(SAMPLE_SPHERE2, 0, 0)  # translateX (dst)
    assert vm.try_connect(tx1, tx2) is True
    assert vm.get_connections(tx2).sources == (tx1,)
    # 別 source を既接続 dst へドロップ → 順向きで置換（force トグル不要）
    assert vm.try_connect(ty1, tx2) is True
    assert vm.get_connections(tx2).sources == (ty1,)  # 置換された
    # 逆流していない（dst が source 側になっていない）
    assert vm.get_connections(ty1).sources == ()


def test_try_connect_rejects_incompatible(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translateX(double) ↔ visibility(bool) は数値系で互換のため、非互換例として
    # worldMatrix(matrix) ↔ translateX(double) を使う
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    assert (
        vm.try_connect(_plug(SAMPLE_SPHERE1, 3), _plug(SAMPLE_SPHERE2, 0, 0)) is False
    )


# ---- 切断 ----
def test_disconnect_all(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    translate_l = _plug(SAMPLE_SPHERE1, 0)
    assert vm.is_connected(translate_l) is True
    assert vm.disconnect_all(translate_l) is True
    assert vm.is_connected(translate_l) is False
    # 相手側（pSphere2.translate）も切れている
    assert vm.is_connected(_plug(SAMPLE_SPHERE2, 0)) is False


def test_disconnect_all_noop_when_unconnected(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    assert vm.disconnect_all(_plug(SAMPLE_SPHERE1, 0, 0)) is False


def test_disconnect_single(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    src, dst = _plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)
    vm.disconnect(src, dst)
    assert vm.is_connected(dst) is False


def test_disconnect_force_unlocks_locked_dst_and_restores(
    scene: FakeSceneAccess,
) -> None:
    # 入力側（dst）がロック中は非 force で切れず（実機 disconnectAttr も失敗）、
    # force で一時解除→切断→ロック復元。
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    src, dst = _plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)
    scene.set_locked(dst, True)
    with pytest.raises(ValueError):
        vm.disconnect(src, dst, force=False)
    assert vm.is_connected(dst) is True  # ロックで切れない
    vm.disconnect(src, dst, force=True)
    assert vm.is_connected(dst) is False  # 解除して切断
    assert scene.is_locked(dst) is True  # ロックは復元


def test_disconnect_pairs_force_unlocks_locked_dst(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    pairs = [
        (_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)),
        (_plug(SAMPLE_SPHERE1, 1), _plug(SAMPLE_SPHERE2, 1)),
    ]
    scene.set_locked(_plug(SAMPLE_SPHERE2, 0), True)
    # 非 force ではロック dst で失敗（実機も）。
    with pytest.raises(ValueError):
        vm.disconnect_pairs(pairs, force=False)
    # force でロックを解除して全切断・復元。
    assert vm.disconnect_pairs(pairs, force=True) == 2
    assert vm.is_connected(_plug(SAMPLE_SPHERE2, 0)) is False
    assert vm.is_connected(_plug(SAMPLE_SPHERE2, 1)) is False
    assert scene.is_locked(_plug(SAMPLE_SPHERE2, 0)) is True  # 復元


def test_disconnect_pairs_removes_all_and_notifies_once(
    scene: FakeSceneAccess,
) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    events: list[bool] = []
    vm.add_listener(lambda structural, _side=None: events.append(structural))
    pairs = [
        (_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)),
        (_plug(SAMPLE_SPHERE1, 1), _plug(SAMPLE_SPHERE2, 1)),
    ]
    count = vm.disconnect_pairs(pairs)
    assert count == 2
    assert vm.is_connected(_plug(SAMPLE_SPHERE2, 0)) is False
    assert vm.is_connected(_plug(SAMPLE_SPHERE2, 1)) is False
    # 通知は 1 回だけ（バッチ・接続のみの変化なので structural=False）。
    assert events == [False]


def test_disconnect_pairs_empty_is_noop(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    events: list[bool] = []
    vm.add_listener(lambda structural, _side=None: events.append(structural))
    assert vm.disconnect_pairs([]) == 0
    assert events == []


# ---- つなぎ替え（入力ポートを掴んで別ポートへ・§5.1） ----
def test_reconnect_moves_source_to_new_dst(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # visibility->visibility を、未接続の scaleX(bool↔double 互換) へ替える
    src = _plug(SAMPLE_SPHERE1, 2)  # visibility (bool, source)
    old_dst = _plug(SAMPLE_SPHERE2, 2)  # visibility
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 1))  # scaleX の型キャッシュ
    new_dst = _plug(SAMPLE_SPHERE2, 1, 0)  # scaleX (double・bool↔double 互換)
    assert vm.reconnect(src, old_dst, new_dst) is True
    assert vm.is_connected(old_dst) is False
    assert vm.get_connections(new_dst).sources == (src,)


def test_reconnect_rejected_keeps_original(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # worldMatrix -> inputMatrix[0] を translateX(double) へ替える → 非互換で拒否
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))  # inputMatrix の子
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))  # translateX
    src = _plug(SAMPLE_SPHERE1, 3)  # worldMatrix
    old_dst = _plug(SAMPLE_SPHERE2, 3, 0)  # inputMatrix[0]
    new_dst = _plug(SAMPLE_SPHERE2, 0, 0)  # translateX
    assert vm.reconnect(src, old_dst, new_dst) is False
    # 非互換なので元の接続が保たれる
    assert vm.get_connections(old_dst).sources == (src,)


# ---- leaf 接続（子属性で接続・§5.2 / §10.3 / Core C3） ----
def test_check_leaf_ok_for_compound_pair(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translate(double3) ↔ translate(double3) は 3=3・全子スカラー・互換 → 成立
    check = vm.check_leaf(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0))
    assert check.ok is True
    assert check.pairs == ((0, 0), (1, 1), (2, 2))


def test_check_leaf_ng_for_scalar_pair(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # visibility(bool) には子が無いため leaf 不成立（子数 0=0 だが…）。
    # ここでは translate(子3) ↔ visibility(子0) で数不一致を確認。
    check = vm.check_leaf(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 2))
    assert check.ok is False


def test_connect_leaf_expands_to_child_pairs(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    src_parent = _plug(SAMPLE_SPHERE1, 0)  # translate
    dst_parent = _plug(SAMPLE_SPHERE2, 0)  # translate
    # まず既存の親接続を外す（leaf は子へ繋ぐので親接続が邪魔）。
    vm.disconnect(src_parent, dst_parent)
    assert vm.connect_leaf(src_parent, dst_parent) is True
    # 子ペアが個別に接続されている（tx→tx, ty→ty, tz→tz）。
    for i in range(3):
        dst_child = _plug(SAMPLE_SPHERE2, 0, i)
        assert vm.get_connections(dst_child).sources == (_plug(SAMPLE_SPHERE1, 0, i),)
    # 親同士は接続されていない。
    assert vm.get_connections(dst_parent).sources == ()


def test_connect_leaf_direction_normalized_when_one_way_blocked(
    scene: FakeSceneAccess,
) -> None:
    # 片方向が既存接続で塞がれているとき、もう一方の向きに正規化される。
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    s1_translate = _plug(SAMPLE_SPHERE1, 0)
    s2_translate = _plug(SAMPLE_SPHERE2, 0)
    vm.disconnect(s1_translate, s2_translate)
    vm.child_nodes(s1_translate)
    vm.child_nodes(s2_translate)
    # SPHERE1.translateX を入力で塞ぐ（visibility(bool)→translateX(double)）。
    scene.connect(_plug(SAMPLE_SPHERE2, 2), _plug(SAMPLE_SPHERE1, 0, 0))
    # a=SPHERE1, b=SPHERE2。b→a は SPHERE1.translateX(dst) が塞がれて不可なので、
    # a→b（SPHERE1 子=source）の向きに正規化される。
    assert vm.connect_leaf(s1_translate, s2_translate) is True
    for i in range(3):
        assert vm.get_connections(_plug(SAMPLE_SPHERE2, 0, i)).sources == (
            _plug(SAMPLE_SPHERE1, 0, i),
        )


def test_connect_leaf_rejects_count_mismatch(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translate(子3) ↔ visibility(子0) は数不一致 → leaf 不成立
    assert vm.connect_leaf(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 2)) is False


# ---- グレーアウト候補判定（ドラッグ中・§5.1/§5.5） ----
def test_can_drag_connect_scalar_compatible(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    grabbed = _plug(SAMPLE_SPHERE1, 0, 0)  # translateX (double)
    # double ↔ double（互換）は候補、double ↔ matrix（非互換）は候補外。
    assert vm.can_drag_connect(grabbed, _plug(SAMPLE_SPHERE2, 0, 1)) is True
    assert (
        vm.can_drag_connect(grabbed, _plug(SAMPLE_SPHERE2, 3)) is False
    )  # inputMatrix


def test_can_drag_connect_self_is_false(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    grabbed = _plug(SAMPLE_SPHERE1, 2)
    assert vm.can_drag_connect(grabbed, grabbed) is False


def test_can_drag_connect_leaf_makes_parent_candidate(scene: FakeSceneAccess) -> None:
    # leaf OFF では親 translate(double3) ↔ scale(double3) は同タグで候補（通常接続）。
    # leaf ON でも C3 成立で候補。数不一致の親は leaf でも候補外。
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    grabbed = _plug(SAMPLE_SPHERE1, 0)  # translate (double3)
    target = _plug(SAMPLE_SPHERE2, 1)  # scale (double3)
    # 既存の translate→translate 接続が grabbed の出力にあるが、target は未接続。
    assert vm.can_drag_connect(grabbed, target, leaf=True) is True
    # leaf ON で visibility(子0) は数不一致かつ非 compound → 通常接続 double3↔bool は
    # 非互換なので候補外。
    assert vm.can_drag_connect(grabbed, _plug(SAMPLE_SPHERE2, 2), leaf=True) is False


# ---- force + lock（master §5.4） ----
def test_check_connect_reflects_lock(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    src = _plug(SAMPLE_SPHERE1, 0, 0)  # translateX
    dst = _plug(SAMPLE_SPHERE2, 0, 0)  # translateX
    scene.set_locked(dst, True)
    # ロック dst は非 force で不可、force で可。
    assert vm.check_connect(src, dst, force=False).ok is False
    assert vm.check_connect(src, dst, force=True).ok is True


def test_locked_dst_does_not_flip_direction(scene: FakeSceneAccess) -> None:
    # ロック先へ向けてドラッグしても、ロックを理由に向きが反転してはいけない
    # （回帰: cubeB.sx→cubeA.sx[locked] が cubeA.sx→cubeB.sx に化ける問題）。
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))  # 型キャッシュ
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    grabbed = _plug(SAMPLE_SPHERE2, 0, 0)  # 掴む側（source 意図）
    target = _plug(SAMPLE_SPHERE1, 0, 0)  # 落とす側（dst 意図・ロックする）
    scene.set_locked(target, True)
    # force なし: 反転せず接続しない（グレーアウトも一致）。
    assert vm.can_drag_connect(grabbed, target, force=False) is False
    assert vm.try_connect(grabbed, target, force=False) is False
    assert vm.get_connections(grabbed).sources == ()  # 逆向きが出来ていない
    assert vm.get_connections(target).sources == ()
    # force あり: 掴んだ向き(grabbed→target)で接続する（反転しない）。
    assert vm.can_drag_connect(grabbed, target, force=True) is True
    assert vm.try_connect(grabbed, target, force=True) is True
    assert vm.get_connections(target).sources == (grabbed,)


def test_reconnect_force_unlocks_and_restores(scene: FakeSceneAccess) -> None:
    # reconnect は src 固定の有向操作。ロック new_dst を force で繋ぎ替え→復元する。
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))  # inputMatrix の子
    src = _plug(SAMPLE_SPHERE1, 3)  # worldMatrix
    old_dst = _plug(SAMPLE_SPHERE2, 3, 0)  # inputMatrix[0]（既存接続）
    new_dst = _plug(SAMPLE_SPHERE2, 3, 2)  # inputMatrix[2]（matrix・未接続）
    scene.set_locked(new_dst, True)
    # 非 force では new_dst のロックで拒否、元の接続が保たれる。
    assert vm.reconnect(src, old_dst, new_dst, force=False) is False
    assert vm.get_connections(old_dst).sources == (src,)
    # force ならロックを一時解除して繋ぎ替え、復元する（master §5.4）。
    assert vm.reconnect(src, old_dst, new_dst, force=True) is True
    assert vm.get_connections(new_dst).sources == (src,)
    assert vm.get_connections(old_dst).sources == ()
    assert scene.is_locked(new_dst) is True


def test_connect_leaf_force_unlocks_child(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    s1t = _plug(SAMPLE_SPHERE1, 0)
    s2t = _plug(SAMPLE_SPHERE2, 0)
    vm.disconnect(s1t, s2t)
    vm.child_nodes(s1t)
    vm.child_nodes(s2t)
    # 両側の translateY をロック → どちらの向きでも非 force では leaf 不成立。
    scene.set_locked(_plug(SAMPLE_SPHERE1, 0, 1), True)
    scene.set_locked(_plug(SAMPLE_SPHERE2, 0, 1), True)
    assert vm.connect_leaf(s1t, s2t, force=False) is False
    # force なら dst 側の子ロックを一時解除して全子接続・復元（a→b の向きに正規化）。
    assert vm.connect_leaf(s1t, s2t, force=True) is True
    for i in range(3):
        assert vm.get_connections(_plug(SAMPLE_SPHERE2, 0, i)).sources == (
            _plug(SAMPLE_SPHERE1, 0, i),
        )
    # 接続先（s2.translateY）も source 側（s1.translateY）もロックが復元されている。
    assert scene.is_locked(_plug(SAMPLE_SPHERE2, 0, 1)) is True
    assert scene.is_locked(_plug(SAMPLE_SPHERE1, 0, 1)) is True


# ---- 値コピー（右クリックメニュー・master §5.3） ----
def test_copy_value_scalar(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    src = _plug(SAMPLE_SPHERE1, 0, 0)  # translateX
    dst = _plug(SAMPLE_SPHERE2, 0, 0)  # translateX（未接続）
    scene.set_value(src, 4.2)
    result = vm.copy_value(src, dst)
    assert result.ok is True
    assert scene.get_value(dst) == 4.2


def test_copy_value_warns_when_dst_connected(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translate（dst）は既に接続済み → 警告（set 値が無視される・§5.3）。
    src = _plug(SAMPLE_SPHERE1, 1)  # scale (double3)
    dst = _plug(SAMPLE_SPHERE2, 0)  # translate (double3, 接続済み)
    result = vm.copy_value(src, dst)
    assert result.ok is False
    assert result.reason is CopyReason.DST_CONNECTED


def test_copy_value_incompatible(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    # worldMatrix(matrix) → translateX(double) は非互換。
    result = vm.copy_value(_plug(SAMPLE_SPHERE1, 3), _plug(SAMPLE_SPHERE2, 0, 0))
    assert result.ok is False
    assert result.reason is CopyReason.INCOMPATIBLE


def test_copy_value_locked_needs_force(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    src = _plug(SAMPLE_SPHERE1, 0, 0)
    dst = _plug(SAMPLE_SPHERE2, 0, 0)
    scene.set_value(src, 1.5)
    scene.set_locked(dst, True)
    assert vm.copy_value(src, dst, force=False).reason is CopyReason.DST_LOCKED
    assert vm.copy_value(src, dst, force=True).ok is True
    assert scene.get_value(dst) == 1.5
    assert scene.is_locked(dst) is True  # 復元


def test_copy_value_leaf(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    src_parent = _plug(SAMPLE_SPHERE1, 1)  # scale
    dst_parent = _plug(SAMPLE_SPHERE2, 1)  # scale（親接続を外しておく）
    vm.disconnect(src_parent, dst_parent)
    vm.child_nodes(src_parent)
    vm.child_nodes(dst_parent)
    for i, v in enumerate((1.0, 2.0, 3.0)):
        scene.set_value(_plug(SAMPLE_SPHERE1, 1, i), v)
    assert vm.copy_value_leaf(src_parent, dst_parent).ok is True
    for i, v in enumerate((1.0, 2.0, 3.0)):
        assert scene.get_value(_plug(SAMPLE_SPHERE2, 1, i)) == v


# ---- 接続たどり（右クリック Load/Add Connected・master §3.2） ----
def test_connected_nodes_returns_partner_only(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # n1.translate -> n2.translate（出力）。相手ノードのみ（自ノードは含めない）。
    nodes = vm.connected_nodes(_plug(SAMPLE_SPHERE1, 0))
    assert [n.uuid for n in nodes] == [SAMPLE_SPHERE2.uuid]


def test_connected_nodes_self_connection_includes_self(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # 同一ノード内の接続（A.translateX -> A.scaleX）は相手端点が自ノード → 自ノード。
    scene.connect(_plug(SAMPLE_SPHERE1, 0, 0), _plug(SAMPLE_SPHERE1, 1, 0))
    nodes = vm.connected_nodes(_plug(SAMPLE_SPHERE1, 0, 0))
    assert [n.uuid for n in nodes] == [SAMPLE_SPHERE1.uuid]


def test_connected_nodes_unconnected_is_empty(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 1))
    # scale の子 scaleX は未接続 → 空。
    assert vm.connected_nodes(_plug(SAMPLE_SPHERE1, 1, 0)) == []


# ---- 属性値コピー（右クリック Copy Attribute Value・master §5.3） ----
def test_read_value_text_scalar(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))  # 子の型タグをキャッシュ
    plug = _plug(SAMPLE_SPHERE1, 0, 0)  # translateX (double)
    scene.set_value(plug, 4.2)
    assert vm.read_value_text(plug) == "4.2"


def test_read_value_text_vector_unwraps_outer_list(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)  # translate(double3) の型タグをキャッシュ
    plug = _plug(SAMPLE_SPHERE1, 0)  # translate (double3)
    scene.set_value(plug, [(1.0, 2.0, 3.0)])  # getAttr(double3) 相当
    assert vm.read_value_text(plug) == "(1.0, 2.0, 3.0)"


def test_read_value_text_matrix(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    plug = _plug(SAMPLE_SPHERE1, 3)  # worldMatrix (matrix)
    flat = list(range(16))
    scene.set_value(plug, flat)
    assert vm.read_value_text(plug) == str(flat)


def test_read_value_text_bool_is_copyable(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)  # visibility(bool) の型タグをキャッシュ
    plug = _plug(SAMPLE_SPHERE1, 2)  # visibility (bool)
    scene.set_value(plug, True)
    # bool は DATA でない → 値が取得できる型なのでコピー可。
    assert vm.read_value_text(plug) == "True"


def test_read_value_text_data_type_is_none() -> None:
    # message(DATA) は値を持たない型 → 型カテゴリでコピー不可と判定（実機 getAttr を
    # 叩く前に弾く）。Fake は値を持っても DATA タグなら None を返す。
    scene = FakeSceneAccess()
    node = NodeId(uuid="UUID-MSG", path="|msgNode")
    scene.set_root_attributes(
        node, [AttrMeta(_plug(node, 0), "message", "message", short_name="msg")]
    )
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)
    vm.root_nodes(LEFT)  # 型タグをキャッシュ
    assert vm.read_value_text(_plug(node, 0)) is None


# ---- 接続不可の理由（ConnectBlock・実行時警告の文言用・master §5.5） ----
def _readonly_matrix_scene() -> tuple[FakeSceneAccess, NodeId, NodeId]:
    """matrix の readable-only / 非スカラー子 compound を持つ最小シーン。

    数値サンプルでは作れない ``NO_DIRECTION`` / ``LEAF_NON_SCALAR`` を再現する。

    Returns:
        (シーン, 左ノード, 右ノード)。各ノードに ``outMat``（matrix・書込不可）と
        ``pairMat``（matrix の子を 2 つ持つ compound）を持たせる。
    """
    scene = FakeSceneAccess()
    a = NodeId(uuid="UUID-RO-A", path="|roA")
    b = NodeId(uuid="UUID-RO-B", path="|roB")
    for n in (a, b):
        scene.set_root_attributes(
            n,
            [
                AttrMeta(_plug(n, 0), "outMat", "matrix", is_writable=False),
                AttrMeta(
                    _plug(n, 1),
                    "pairMat",
                    "compound",
                    is_compound=True,
                    has_children=True,
                ),
            ],
        )
        scene.set_children(
            _plug(n, 1),
            [
                AttrMeta(_plug(n, 1, 0), "pairMat0", "matrix"),
                AttrMeta(_plug(n, 1, 1), "pairMat1", "matrix"),
            ],
        )
    return scene, a, b


def test_connect_blocker_none_when_connectable(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    # translateX(double) ↔ translateX(double・未接続) は成立 → None。
    src = _plug(SAMPLE_SPHERE1, 0, 0)
    dst = _plug(SAMPLE_SPHERE2, 0, 1)  # translateY（未接続）
    assert vm.connect_blocker(src, dst) is None


def test_connect_blocker_type_incompatible(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    # worldMatrix(matrix) ↔ translateX(double) は双方向とも非互換。
    block = vm.connect_blocker(_plug(SAMPLE_SPHERE1, 3), _plug(SAMPLE_SPHERE2, 0, 0))
    assert block is ConnectBlock.TYPE_INCOMPATIBLE


def test_connect_blocker_dst_locked_cleared_by_force(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 0))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 0))
    src = _plug(SAMPLE_SPHERE1, 0, 0)
    dst = _plug(SAMPLE_SPHERE2, 0, 1)  # translateY（未接続）
    scene.set_locked(dst, True)
    # 型・向きは成立。残るはロックのみ → 非 force で DST_LOCKED・force で解消。
    assert vm.connect_blocker(src, dst, force=False) is ConnectBlock.DST_LOCKED
    assert vm.connect_blocker(src, dst, force=True) is None


def test_connect_blocker_no_direction() -> None:
    scene, a, b = _readonly_matrix_scene()
    vm = EditorViewModel(scene)
    vm.load(LEFT, a)
    vm.load(RIGHT, b)
    vm.root_nodes(LEFT)  # 型・readable/writable をキャッシュ
    vm.root_nodes(RIGHT)
    # 双方とも matrix（型互換）だが書込不可 → readable→writable の向きが無い。
    block = vm.connect_blocker(_plug(a, 0), _plug(b, 0))
    assert block is ConnectBlock.NO_DIRECTION


def test_leaf_blocker_none_when_connectable(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    src, dst = _plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)
    vm.disconnect(src, dst)  # 親接続を外す（leaf は子へ繋ぐ）
    assert vm.leaf_blocker(src, dst) is None


def test_leaf_blocker_count_mismatch(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    # translate(子3) ↔ visibility(子0) は数不一致。
    block = vm.leaf_blocker(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 2))
    assert block is ConnectBlock.LEAF_COUNT_MISMATCH


def test_set_filter_extra_only_keeps_user_defined(scene: FakeSceneAccess) -> None:
    # ユーザー定義（extra）属性を1つ持つ最小シーンで Show Extra Attribute Only を検証。
    s = FakeSceneAccess()
    node = NodeId(uuid="UUID-EXTRA", path="|ex")
    s.set_root_attributes(
        node,
        [
            AttrMeta(_plug(node, 0), "translateX", "double"),  # 通常
            AttrMeta(
                _plug(node, 1), "myExtra", "double", is_user_defined=True
            ),  # extra
        ],
    )
    vm = EditorViewModel(s)
    vm.load(LEFT, node)
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, node)]
    assert names == ["translateX", "myExtra"]  # 既定は両方表示
    # show_non_keyable=True（UI 既定）でも extra_only が効く（_is_permissive 回帰）。
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory),
            show_non_keyable=True,
            extra_only=True,
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, node)]
    assert names == ["myExtra"]  # extra のみ


def test_set_filter_hidden_hides_hidden_by_default() -> None:
    # hidden 属性を1つ持つ最小シーンで Show Hidden を検証。
    s = FakeSceneAccess()
    node = NodeId(uuid="UUID-HIDDEN", path="|hd")
    s.set_root_attributes(
        node,
        [
            AttrMeta(_plug(node, 0), "translateX", "double"),  # 通常
            AttrMeta(
                _plug(node, 1), "internalAttr", "double", is_hidden=True
            ),  # hidden
        ],
    )
    vm = EditorViewModel(s)
    vm.load(LEFT, node)
    # show_non_keyable=True（UI 既定）でも既定は hidden を隠す（_is_permissive 回帰）。
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory),
            show_non_keyable=True,
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, node)]
    assert names == ["translateX"]  # hidden は隠れる
    # show_hidden=True で hidden も表示。
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory),
            show_non_keyable=True,
            show_hidden=True,
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, node)]
    assert names == ["translateX", "internalAttr"]


def test_leaf_blocker_non_scalar_child() -> None:
    scene, a, b = _readonly_matrix_scene()
    vm = EditorViewModel(scene)
    vm.load(LEFT, a)
    vm.load(RIGHT, b)
    # pairMat の子は matrix（非スカラー）で子数は一致 → NON_SCALAR。
    block = vm.leaf_blocker(_plug(a, 1), _plug(b, 1))
    assert block is ConnectBlock.LEAF_NON_SCALAR


# ---- ゴースト（マルチ属性の先回り行・master §5.6 / Core C4） ----
def test_child_nodes_includes_ghosts_for_array(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(RIGHT)  # inputMatrix の親メタをキャッシュ
    children = vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))
    names = [c.display_name for c in children]
    # 既存 [0][2] + ゴースト [1]（歯抜け）+ [3]（末尾次）。
    assert names == [
        "inputMatrix[0]",
        "inputMatrix[1]",
        "inputMatrix[2]",
        "inputMatrix[3]",
    ]
    ghosts = [c.is_ghost for c in children]
    assert ghosts == [False, True, False, True]


def test_is_ghost(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))
    assert vm.is_ghost(_plug(SAMPLE_SPHERE2, 3, 1)) is True  # 歯抜け
    assert vm.is_ghost(_plug(SAMPLE_SPHERE2, 3, 0)) is False  # 既存
    assert vm.is_ghost(_plug(SAMPLE_SPHERE2, 0)) is False  # array でない


def test_connect_to_ghost_materializes(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))  # ゴースト型をキャッシュ
    ghost = _plug(SAMPLE_SPHERE2, 3, 1)  # inputMatrix[1]（ゴースト・matrix）
    src = _plug(SAMPLE_SPHERE1, 3)  # worldMatrix（matrix・出力）
    assert vm.is_ghost(ghost) is True
    assert vm.try_connect(src, ghost) is True
    # 接続され、実体化されてゴーストでなくなる。
    assert vm.get_connections(ghost).sources == (src,)
    children = vm.child_nodes(_plug(SAMPLE_SPHERE2, 3))
    names = [c.display_name for c in children]
    # [1] が実体化し、新しいゴースト [3] が末尾に湧く（[0][1][2] 実在 + [3] ゴースト）。
    assert names == [
        "inputMatrix[0]",
        "inputMatrix[1]",
        "inputMatrix[2]",
        "inputMatrix[3]",
    ]
    assert vm.is_ghost(_plug(SAMPLE_SPHERE2, 3, 1)) is False
    assert vm.is_ghost(_plug(SAMPLE_SPHERE2, 3, 3)) is True


def test_empty_array_ghost_is_index_zero(scene: FakeSceneAccess) -> None:
    # 空 array は仮想 [0] のみがゴースト（master §10.2）。
    node = NodeId(uuid="U-EMPTY", path="|emptyArr")
    scene.set_root_attributes(
        node,
        [
            AttrMeta(
                _plug(node, 0),
                "inputs",
                "double",
                is_array=True,
                has_children=True,
                existing_indices=(),
            )
        ],
    )
    scene.set_children(_plug(node, 0), [])
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)
    vm.root_nodes(LEFT)
    children = vm.child_nodes(_plug(node, 0))
    assert [c.display_name for c in children] == ["inputs[0]"]
    assert children[0].is_ghost is True


# ---- フィルタ（左右独立・master §9 / Core C6） ----
def test_filter_default_shows_all(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # 初期は全許容 → トップレベル属性は全表示（非 keyable の worldMatrix も含む）。
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert names == ["translate", "scale", "visibility", "worldMatrix"]


def test_filter_by_type_keeps_matrix_and_ancestors(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # matrix のみ ON → 左は worldMatrix だけ残る。
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset({TypeCategory.MATRIX}), show_non_keyable=True
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert names == ["worldMatrix"]
    # 右の inputMatrix(array) は matrix → 残り、子（matrix 要素）も表示される。
    vm.set_filter(
        RIGHT,
        FilterCriteria(
            enabled_categories=frozenset({TypeCategory.MATRIX}), show_non_keyable=True
        ),
    )
    rnames = [n.display_name for n in vm.visible_attr_nodes(RIGHT, SAMPLE_SPHERE2)]
    assert rnames == ["inputMatrix"]
    # 既存要素 [0][2] + ゴースト [1][3]（matrix なので型フィルタを通る・§5.6）。
    children = vm.visible_child_nodes(RIGHT, _plug(SAMPLE_SPHERE2, 3))
    assert [c.display_name for c in children] == [
        "inputMatrix[0]",
        "inputMatrix[1]",
        "inputMatrix[2]",
        "inputMatrix[3]",
    ]


def test_filter_non_keyable_hidden_when_off(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # 全型 ON だが non-keyable OFF → worldMatrix（non-keyable）が消える。
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory), show_non_keyable=False
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert "worldMatrix" not in names
    assert "translate" in names


def test_filter_connected_only_keeps_connected_parents(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # visibility 接続を切り、scaleX だけ接続 → connected-only で scale 親も残る。
    vm.disconnect(_plug(SAMPLE_SPHERE1, 2), _plug(SAMPLE_SPHERE2, 2))
    vm.disconnect(_plug(SAMPLE_SPHERE1, 1), _plug(SAMPLE_SPHERE2, 1))
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 1))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 1))
    vm.try_connect(_plug(SAMPLE_SPHERE1, 1, 0), _plug(SAMPLE_SPHERE2, 1, 0))
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory),
            show_non_keyable=True,
            show_connected_only=True,
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    # translate(親接続あり) と scale(子接続あり=祖先で残る) が残り visibility は消える。
    assert "translate" in names
    assert "scale" in names
    assert "visibility" not in names
    # scale の子は接続している scaleX のみ残る。
    children = vm.visible_child_nodes(LEFT, _plug(SAMPLE_SPHERE1, 1))
    assert [c.display_name for c in children] == ["scaleX"]


def test_filter_text(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset(TypeCategory),
            show_non_keyable=True,
            text="vis",
        ),
    )
    names = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    assert names == ["visibility"]


def test_filter_independent_left_right(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.set_filter(
        LEFT,
        FilterCriteria(
            enabled_categories=frozenset({TypeCategory.BOOL}), show_non_keyable=True
        ),
    )
    # 左は bool だけ、右は既定（全表示）。
    lnames = [n.display_name for n in vm.visible_attr_nodes(LEFT, SAMPLE_SPHERE1)]
    rnames = [n.display_name for n in vm.visible_attr_nodes(RIGHT, SAMPLE_SPHERE2)]
    assert lnames == ["visibility"]
    assert "translate" in rnames and "inputMatrix" in rnames


# ---- 全接続列挙・二重丸（束出し用・§4.1/§4.5） ----
def test_connection_pairs_lists_loaded_connections(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    pairs = vm.connection_pairs()
    # サンプルの 4 接続（translate/scale/visibility/worldMatrix→inputMatrix[0]）
    assert (_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)) in pairs
    assert (_plug(SAMPLE_SPHERE1, 3), _plug(SAMPLE_SPHERE2, 3, 0)) in pairs
    assert len(pairs) == 4


def test_connection_pairs_cached_and_invalidated(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    first = vm.connection_pairs()
    assert vm.connection_pairs() is first  # キャッシュで同一オブジェクト
    vm.disconnect(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0))
    assert vm.connection_pairs() is not first  # 変更で無効化
    assert len(vm.connection_pairs()) == 3


def test_has_connected_descendant(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    # 親 scale 同士の接続を切り、子 scaleX のみ接続 → scale 親は子接続あり
    vm.disconnect(_plug(SAMPLE_SPHERE1, 1), _plug(SAMPLE_SPHERE2, 1))
    vm.child_nodes(_plug(SAMPLE_SPHERE1, 1))
    vm.child_nodes(_plug(SAMPLE_SPHERE2, 1))
    vm.try_connect(_plug(SAMPLE_SPHERE1, 1, 0), _plug(SAMPLE_SPHERE2, 1, 0))
    assert vm.has_connected_descendant(_plug(SAMPLE_SPHERE1, 1)) is True
    # translate は子接続なし
    assert vm.has_connected_descendant(_plug(SAMPLE_SPHERE1, 0)) is False


def test_side_of(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    assert vm.side_of(_plug(SAMPLE_SPHERE1, 0)) == LEFT
    assert vm.side_of(_plug(SAMPLE_SPHERE2, 0)) == RIGHT


# ---- 複数ノード（段階4 Load/Add） ----
def test_set_and_add_nodes(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.set_nodes(LEFT, [SAMPLE_SPHERE1])
    assert [n.uuid for n in vm.nodes(LEFT)] == [SAMPLE_SPHERE1.uuid]
    vm.add_nodes(LEFT, [SAMPLE_SPHERE2])
    assert [n.uuid for n in vm.nodes(LEFT)] == [
        SAMPLE_SPHERE1.uuid,
        SAMPLE_SPHERE2.uuid,
    ]


def test_add_nodes_dedups_by_uuid(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.set_nodes(LEFT, [SAMPLE_SPHERE1])
    vm.add_nodes(LEFT, [SAMPLE_SPHERE1])  # 同 uuid は追加しない
    assert len(vm.nodes(LEFT)) == 1


def test_load_replaces_nodes(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.add_nodes(LEFT, [SAMPLE_SPHERE1, SAMPLE_SPHERE2])
    vm.load(LEFT, SAMPLE_SPHERE2)  # Load = 置き換え
    assert [n.uuid for n in vm.nodes(LEFT)] == [SAMPLE_SPHERE2.uuid]


def test_remove_node(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.set_nodes(LEFT, [SAMPLE_SPHERE1, SAMPLE_SPHERE2])
    vm.remove_node(LEFT, SAMPLE_SPHERE1)
    assert [n.uuid for n in vm.nodes(LEFT)] == [SAMPLE_SPHERE2.uuid]


def test_connection_pairs_across_multiple_nodes_same_side(
    scene: FakeSceneAccess,
) -> None:
    # 左に両ノードを積んでも、両端ロード済みの接続は列挙される（重複なし）。
    vm = EditorViewModel(scene)
    vm.set_nodes(LEFT, [SAMPLE_SPHERE1, SAMPLE_SPHERE2])
    pairs = vm.connection_pairs()
    assert (_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0)) in pairs
    assert len(pairs) == len(set(pairs))


def test_display_label_minimal_unique(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.set_nodes(LEFT, [SAMPLE_SPHERE1])
    vm.set_nodes(RIGHT, [SAMPLE_SPHERE2])
    # 短縮名が一意なら短縮名（pSphere1 / pSphere2）。
    assert vm.display_label(SAMPLE_SPHERE1) == "pSphere1"
    # 同名（短縮名重複）ならフルパスにフォールバック。
    dup_a = NodeId(uuid="U-A", path="|grpA|dup")
    dup_b = NodeId(uuid="U-B", path="|grpB|dup")
    vm.set_nodes(LEFT, [dup_a])
    vm.set_nodes(RIGHT, [dup_b])
    assert vm.display_label(dup_a) == "|grpA|dup"


def test_load_and_add_selected(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    vm.select([SAMPLE_SPHERE1])
    vm.load_selected(LEFT)
    assert [n.uuid for n in vm.nodes(LEFT)] == [SAMPLE_SPHERE1.uuid]
    vm.select([SAMPLE_SPHERE2])
    vm.add_selected(LEFT)  # Add は既存を保って追加
    assert [n.uuid for n in vm.nodes(LEFT)] == [
        SAMPLE_SPHERE1.uuid,
        SAMPLE_SPHERE2.uuid,
    ]
    vm.load_selected(LEFT)  # Load は置き換え
    assert [n.uuid for n in vm.nodes(LEFT)] == [SAMPLE_SPHERE2.uuid]


def test_node_has_connection_by_uuid(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    assert vm.node_has_connection_by_uuid(SAMPLE_SPHERE1.uuid) is True
    assert vm.node_has_connection_by_uuid("UUID-UNLOADED") is False


# ---- 変更通知 ----
def test_listener_notified_on_load_and_connect(scene: FakeSceneAccess) -> None:
    vm = EditorViewModel(scene)
    calls = []
    vm.add_listener(lambda structural, _side=None: calls.append(structural))
    vm.load(LEFT, SAMPLE_SPHERE1)
    vm.load(RIGHT, SAMPLE_SPHERE2)
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    before = len(calls)
    vm.disconnect_all(_plug(SAMPLE_SPHERE1, 0))
    assert len(calls) == before + 1


def test_notify_structural_flag(scene: FakeSceneAccess) -> None:
    # load/add は structural=True、接続変更は structural=False で通知する。
    vm = EditorViewModel(scene)
    events: list[bool] = []
    vm.add_listener(lambda structural, _side=None: events.append(structural))
    vm.load(LEFT, SAMPLE_SPHERE1)  # 構造変化
    vm.load(RIGHT, SAMPLE_SPHERE2)  # 構造変化
    vm.root_nodes(LEFT)
    vm.root_nodes(RIGHT)
    events.clear()
    vm.disconnect(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0))
    vm.try_connect(_plug(SAMPLE_SPHERE1, 0), _plug(SAMPLE_SPHERE2, 0))
    assert events == [False, False]  # 接続変更はツリー不変
    vm.add_nodes(LEFT, [SAMPLE_SPHERE2])
    assert events[-1] is True  # add は構造変化


# ---- ポート有無 / 方向可否（readable/writable・§4.3/§6） ----
def _capability_scene() -> tuple[FakeSceneAccess, NodeId]:
    """readable/writable の異なる属性を並べた検証用シーンを作る。

    index 0=両方可 / 1=出力専用(writable 不可) / 2=入力専用(readable 不可) /
    3=両方不可。型はすべて matrix（互換は満たし、方向だけで弾けるようにする）。
    """
    scene = FakeSceneAccess()
    node = NodeId(path="|n", uuid="n")
    scene.set_root_attributes(
        node,
        [
            AttrMeta(_plug(node, 0), "both", "matrix"),
            AttrMeta(_plug(node, 1), "outOnly", "matrix", is_writable=False),
            AttrMeta(_plug(node, 2), "inOnly", "matrix", is_readable=False),
            AttrMeta(
                _plug(node, 3), "none", "matrix", is_readable=False, is_writable=False
            ),
        ],
    )
    return scene, node


def test_non_connectable_attr_has_no_port() -> None:
    # readable/writable どちらにもなれない属性だけポートを出さない（§4.3）。
    scene, node = _capability_scene()
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)
    vm.root_nodes(LEFT)  # メタをキャッシュさせる（ツリー構築相当）
    assert vm.has_port(_plug(node, 0)) is True  # 両方可
    assert vm.has_port(_plug(node, 1)) is True  # 出力専用でもポートあり
    assert vm.has_port(_plug(node, 2)) is True  # 入力専用でもポートあり
    assert vm.has_port(_plug(node, 3)) is False  # 両方不可だけ無し


def test_check_connect_respects_direction_capability() -> None:
    # 出力専用は dst になれず、入力専用は src になれない（force でも覆らない）。
    scene, node = _capability_scene()
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)
    vm.root_nodes(LEFT)
    both, out_only, in_only = _plug(node, 0), _plug(node, 1), _plug(node, 2)
    # 出力専用を dst にする接続は不可（force でも）。
    assert vm.check_connect(both, out_only).ok is False
    assert vm.check_connect(both, out_only, force=True).ok is False
    # 入力専用を src にする接続は不可。
    assert vm.check_connect(in_only, both).ok is False
    # 正しい向き（出力専用=src / 入力専用=dst）は可。
    assert vm.check_connect(out_only, both).ok is True
    assert vm.check_connect(both, in_only).ok is True


def test_try_connect_normalizes_to_valid_direction() -> None:
    # 入力専用ポートを掴んで出力可能な相手に落としても、有効な向きに正規化される。
    scene, node = _capability_scene()
    vm = EditorViewModel(scene)
    vm.load(LEFT, node)
    vm.root_nodes(LEFT)
    both, in_only = _plug(node, 0), _plug(node, 2)
    # in_only→both は不可だが both→in_only が成立するので接続できる。
    assert vm.try_connect(in_only, both) is True
    assert vm.is_connected(in_only) is True  # in_only が dst として接続された


# ---- 左右入替 ----
def test_swap_sides_swaps_nodes_and_filters(scene: FakeSceneAccess) -> None:
    # 入替はノード列とフィルタ条件を丸ごと左右交換する。
    vm = _loaded_vm(scene)
    crit = FilterCriteria(
        enabled_categories=frozenset({TypeCategory.NUMERIC}),
        show_non_keyable=False,
        show_connected_only=True,
        text="tx",
    )
    vm.set_filter(LEFT, crit)
    vm.swap_sides()
    assert vm.nodes(LEFT) == [SAMPLE_SPHERE2]
    assert vm.nodes(RIGHT) == [SAMPLE_SPHERE1]
    assert vm.filter_criteria(RIGHT) == crit


def test_swap_sides_notifies_structural(scene: FakeSceneAccess) -> None:
    # 入替はツリー構造が変わるので structural=True で通知する。
    vm = _loaded_vm(scene)
    events: list[bool] = []
    vm.add_listener(lambda structural, _side=None: events.append(structural))
    vm.swap_sides()
    assert events == [True]
