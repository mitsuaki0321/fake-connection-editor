"""Core ロジックの検証（master §8）。すべて Maya 非依存。

C1 接続可否 / C2 型互換+昇格 / C3 leaf 接続成立 / C4 ゴースト算出 /
C6 フィルタ合成 / C7 ツリー構築。
"""

from __future__ import annotations

import pytest

from fake_connection_editor.core import (
    ConnectReason,
    FilterCriteria,
    LeafReason,
    TreeNode,
    TypeCategory,
    build_array_child_nodes,
    build_child_nodes,
    check_connect,
    check_leaf_connect,
    classify,
    ghost_indices,
    is_compatible,
    is_scalar,
    should_display,
)
from fake_connection_editor.scene_access import AttrMeta, NodeId, PlugId

N = NodeId(uuid="UUID-N", path="|n")


def _plug(*index: int) -> PlugId:
    return PlugId(node=N, index_path=tuple(index))


# ---------------------------------------------------------------------------
# C2 型互換 + 昇格
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        ("double", "double", True),  # 同一タグ
        ("matrix", "matrix", True),  # 非スカラー同一タグ
        ("double3", "double3", True),  # compound 同一タグ
        ("float", "double", True),  # 数値昇格
        ("int", "float", True),  # 数値昇格
        ("bool", "double", True),  # bool↔数値（暫定）
        ("double3", "float3", True),  # ベクトル跨ぎ（要素数一致・実機採取）
        ("double2", "double3", False),  # ベクトルは要素数不一致だと不可
        ("matrix", "double", False),  # 非スカラー↔スカラー
        ("matrix", "message", True),  # message はワイルドカード（実機採取）
        ("message", "compound", False),  # message→compound だけは不可
        ("message", "message", True),  # message 同一タグ
    ],
)
def test_c2_is_compatible(src: str, dst: str, expected: bool) -> None:
    assert is_compatible(src, dst) is expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("double", True),
        ("float", True),
        ("int", True),
        ("bool", True),
        ("string", False),
        ("matrix", False),
        ("double3", False),
        ("message", False),
        ("data", False),
    ],
)
def test_c2_is_scalar(tag: str, expected: bool) -> None:
    assert is_scalar(tag) is expected


# ---------------------------------------------------------------------------
# C3 leaf 接続成立
# ---------------------------------------------------------------------------
def test_c3_double3_to_float3_ok() -> None:
    res = check_leaf_connect(
        ["double", "double", "double"], ["float", "float", "float"]
    )
    assert res.ok is True
    assert res.reason is LeafReason.OK
    assert res.pairs == ((0, 0), (1, 1), (2, 2))


def test_c3_count_mismatch() -> None:
    res = check_leaf_connect(["double", "double", "double"], ["double", "double"])
    assert res.ok is False
    assert res.reason is LeafReason.COUNT_MISMATCH
    assert res.pairs == ()


def test_c3_non_scalar_child() -> None:
    # 子に matrix（非スカラー）が混ざる → 条件2 で弾く
    res = check_leaf_connect(["double", "matrix"], ["double", "matrix"])
    assert res.ok is False
    assert res.reason is LeafReason.NON_SCALAR_CHILD


# ---------------------------------------------------------------------------
# C1 接続可否
# ---------------------------------------------------------------------------
def test_c1_ok() -> None:
    res = check_connect("double", "double", dst_locked=False)
    assert res.ok is True
    assert res.reason is ConnectReason.OK


def test_c1_type_incompatible_not_overridable_by_force() -> None:
    res = check_connect("matrix", "double", dst_locked=False, force=True)
    assert res.ok is False
    assert res.reason is ConnectReason.TYPE_INCOMPATIBLE


def test_c1_locked_blocks_without_force() -> None:
    res = check_connect("double", "double", dst_locked=True)
    assert res.ok is False
    assert res.reason is ConnectReason.DST_LOCKED


def test_c1_occupied_does_not_block() -> None:
    # 既存入力接続は拒否しない（ドラッグは順向きで置換する・逆流防止）。
    res = check_connect("double", "double", dst_locked=False)
    assert res.ok is True
    assert res.reason is ConnectReason.OK


def test_c1_force_overrides_locked() -> None:
    res = check_connect("double", "double", dst_locked=True, force=True)
    assert res.ok is True
    assert res.reason is ConnectReason.OK


# ---------------------------------------------------------------------------
# C4 ゴースト算出
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ((0, 2), (1, 3)),  # 歯抜け [1] + 末尾次 [3]
        ((), (0,)),  # 空 array → 仮想 [0]
        ((0, 1, 2), (3,)),  # 歯抜けなし + 末尾次 [3]
        ((2,), (0, 1, 3)),  # [0][1] 歯抜け + 末尾次 [3]
        ((0,), (1,)),  # [0] のみ → 末尾次 [1]
    ],
)
def test_c4_ghost_indices(existing: tuple, expected: tuple) -> None:
    assert ghost_indices(existing) == expected


# ---------------------------------------------------------------------------
# C6 フィルタ合成
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("double", TypeCategory.NUMERIC),
        ("double3", TypeCategory.NUMERIC),
        ("bool", TypeCategory.BOOL),
        ("matrix", TypeCategory.MATRIX),
        ("message", TypeCategory.DATA),
        ("data", TypeCategory.DATA),
        ("color", TypeCategory.COLOR),
    ],
)
def test_c6_classify(tag: str, expected: TypeCategory) -> None:
    assert classify(tag) is expected


def _meta(
    tag: str = "double",
    *,
    keyable: bool = True,
    name: str = "attr",
    user_defined: bool = False,
    hidden: bool = False,
) -> AttrMeta:
    return AttrMeta(
        _plug(0),
        name,
        tag,
        is_keyable=keyable,
        is_user_defined=user_defined,
        is_hidden=hidden,
    )


def test_c6_all_visible_shows_keyable_connected_or_not() -> None:
    crit = FilterCriteria.all_visible()
    assert should_display(_meta(), is_connected=False, criteria=crit) is True


def test_c6_type_category_off_hides() -> None:
    crit = FilterCriteria(enabled_categories=frozenset({TypeCategory.MATRIX}))
    assert should_display(_meta("double"), is_connected=True, criteria=crit) is False
    assert should_display(_meta("matrix"), is_connected=True, criteria=crit) is True


def test_c6_non_keyable_hidden_unless_shown() -> None:
    crit = FilterCriteria.all_visible()
    nk = _meta(keyable=False)
    assert should_display(nk, is_connected=True, criteria=crit) is False
    crit_show = FilterCriteria(
        enabled_categories=frozenset(TypeCategory), show_non_keyable=True
    )
    assert should_display(nk, is_connected=True, criteria=crit_show) is True


def test_c6_connected_only() -> None:
    crit = FilterCriteria(
        enabled_categories=frozenset(TypeCategory), show_connected_only=True
    )
    assert should_display(_meta(), is_connected=False, criteria=crit) is False
    assert should_display(_meta(), is_connected=True, criteria=crit) is True


def test_c6_extra_only() -> None:
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory), extra_only=True)
    # ユーザー定義属性だけ表示し、通常属性は隠す。
    assert should_display(_meta(user_defined=True), is_connected=True, criteria=crit)
    assert (
        should_display(_meta(user_defined=False), is_connected=True, criteria=crit)
        is False
    )


def test_c6_hidden_hidden_unless_shown() -> None:
    # 既定（show_hidden=False）は hidden を隠す。
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory))
    assert should_display(_meta(hidden=True), is_connected=True, criteria=crit) is False
    assert should_display(_meta(hidden=False), is_connected=True, criteria=crit) is True
    # show_hidden=True で hidden も表示。
    crit_show = FilterCriteria(
        enabled_categories=frozenset(TypeCategory), show_hidden=True
    )
    assert should_display(_meta(hidden=True), is_connected=True, criteria=crit_show)


def test_c6_text_substring_case_insensitive() -> None:
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory), text="trans")
    assert (
        should_display(_meta(name="translateX"), is_connected=True, criteria=crit)
        is True
    )
    assert (
        should_display(_meta(name="scaleX"), is_connected=True, criteria=crit) is False
    )


def _named_meta(long: str, short: str) -> AttrMeta:
    return AttrMeta(_plug(0), long, "double", is_keyable=True, short_name=short)


def test_c6_text_matches_short_name_when_match_short() -> None:
    """short 表示時は shortName で検索（tx → translateX がヒット）。"""
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory), text="tx")
    meta = _named_meta("translateX", "tx")
    # long 検索では tx は translateX に含まれずヒットしない。
    assert should_display(meta, is_connected=True, criteria=crit) is False
    # short 検索（match_short）なら tx でヒットする。
    assert (
        should_display(meta, is_connected=True, criteria=crit, match_short=True) is True
    )


def test_c6_match_short_falls_back_to_long_when_short_empty() -> None:
    """shortName が空なら match_short でも longName に一致判定する。"""
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory), text="trans")
    meta = _named_meta("translateX", "")
    assert (
        should_display(meta, is_connected=True, criteria=crit, match_short=True) is True
    )


def test_c6_long_search_still_works_in_short_mode() -> None:
    """short 表示でも long 名の語で打てばヒットしない（見える名で検索が原則）。"""
    crit = FilterCriteria(enabled_categories=frozenset(TypeCategory), text="translate")
    meta = _named_meta("translateX", "tx")
    # match_short のとき検索対象は short("tx") なので translate は当たらない。
    assert (
        should_display(meta, is_connected=True, criteria=crit, match_short=True)
        is False
    )


# ---------------------------------------------------------------------------
# C7 ツリー構築
# ---------------------------------------------------------------------------
def test_c7_build_child_nodes_preserves_order() -> None:
    metas = [
        AttrMeta(_plug(0, 0), "translateX", "double"),
        AttrMeta(_plug(0, 1), "translateY", "double"),
    ]
    nodes = build_child_nodes(metas)
    assert [n.display_name for n in nodes] == ["translateX", "translateY"]
    assert all(isinstance(n, TreeNode) for n in nodes)
    assert all(n.is_ghost is False for n in nodes)


def test_c7_build_array_child_nodes_merges_ghosts() -> None:
    parent = AttrMeta(
        _plug(3),
        "inputMatrix",
        "matrix",
        is_array=True,
        has_children=True,
        existing_indices=(0, 2),
    )
    existing = [
        AttrMeta(_plug(3, 0), "inputMatrix[0]", "matrix"),
        AttrMeta(_plug(3, 2), "inputMatrix[2]", "matrix"),
    ]
    nodes = build_array_child_nodes(parent, existing)
    # index 昇順: 0(実), 1(ゴースト), 2(実), 3(ゴースト末尾次)
    assert [n.display_name for n in nodes] == [
        "inputMatrix[0]",
        "inputMatrix[1]",
        "inputMatrix[2]",
        "inputMatrix[3]",
    ]
    assert [n.is_ghost for n in nodes] == [False, True, False, True]
    # ゴースト plug は親の index_path に仮想 index を足したもの
    assert nodes[1].plug == PlugId(node=N, index_path=(3, 1))


def test_c7_empty_array_yields_virtual_zero() -> None:
    parent = AttrMeta(
        _plug(3),
        "inputMatrix",
        "matrix",
        is_array=True,
        has_children=True,
        existing_indices=(),
    )
    nodes = build_array_child_nodes(parent, [])
    assert [n.display_name for n in nodes] == ["inputMatrix[0]"]
    assert nodes[0].is_ghost is True
