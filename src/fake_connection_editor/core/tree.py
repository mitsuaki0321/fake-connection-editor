"""ツリー構築（遅延展開単位）（master §8 C7, §4.5 / §5.6）。

SceneAccess の直下メタ列（``list_children`` の戻り値）を、UI モデルが扱う Core 表現
（``TreeNode``）に変換する。array についてはゴースト行（C4）を併合する。
Maya 非依存・Qt 非依存の純粋関数。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..scene_access.interface import AttrMeta, PlugId
from .ghost import ghost_indices


@dataclass(frozen=True)
class TreeNode:
    """ツリー1行分の Core 表現（master §7.2 のモデルが抱えるデータ）。

    色・座標等の表示の関心事は持たない（master §1.3）。

    Attributes:
        plug: この行の ``PlugId``（ゴースト行は仮想インデックスの PlugId）。
        display_name: 表示名（longName）。
        short_name: 短縮名（shortName）。名前表示切替用（常に解決済み＝空でも long）。
        type_tag: 正規化済み型タグ。
        is_expandable: 展開可能か（compound の子 / array の要素を持つ）。
        is_array: array（マルチ属性）か。
        is_compound: compound か。
        is_ghost: ゴースト行（実在しない先回り表示の array 要素）か。
    """

    plug: PlugId
    display_name: str
    type_tag: str
    is_expandable: bool
    is_array: bool
    is_compound: bool
    is_ghost: bool = False
    short_name: str = ""


def _node_from_meta(meta: AttrMeta) -> TreeNode:
    """``AttrMeta`` を実在行の ``TreeNode`` に変換する（short 無しは long に倒す）。"""
    return TreeNode(
        plug=meta.plug,
        display_name=meta.display_name,
        short_name=meta.short_name or meta.display_name,
        type_tag=meta.type_tag,
        is_expandable=meta.has_children,
        is_array=meta.is_array,
        is_compound=meta.is_compound,
    )


def build_child_nodes(metas: list[AttrMeta]) -> list[TreeNode]:
    """C7: 直下メタ列を ``TreeNode`` 列に変換する（純粋な変換）。

    compound の子 / トップレベルなど、ゴーストを伴わない展開単位に使う。
    array のゴースト併合は ``build_array_child_nodes`` を使う。

    Args:
        metas: ``list_children`` / ``list_root_attributes`` の戻り値。

    Returns:
        ``TreeNode`` 列（入力順を保つ）。
    """
    return [_node_from_meta(meta) for meta in metas]


def build_array_child_nodes(
    parent: AttrMeta, existing_child_metas: list[AttrMeta]
) -> list[TreeNode]:
    """Array の子行（既存要素 + ゴースト行）を index 昇順で組む（master §5.6）。

    既存要素は ``existing_child_metas`` から、ゴースト行は親の ``existing_indices``
    から C4（``ghost_indices``）で算出して併合する。ゴースト行の ``PlugId`` は
    親の ``index_path`` に仮想インデックスを足したもの、表示名は ``"親名[i]"``。

    Args:
        parent: array 属性（``is_array`` かつ ``existing_indices`` を持つ）のメタ。
        existing_child_metas: その array の既存要素メタ列（``list_children`` 戻り値）。

    Returns:
        既存 + ゴーストを index 昇順に並べた ``TreeNode`` 列。
    """
    existing = parent.existing_indices or ()
    ghosts = ghost_indices(existing)

    nodes: list[tuple[int, TreeNode]] = []
    for meta in existing_child_metas:
        nodes.append((meta.plug.index_path[-1], _node_from_meta(meta)))
    parent_short = parent.short_name or parent.display_name
    for i in ghosts:
        plug = PlugId(node=parent.plug.node, index_path=parent.plug.index_path + (i,))
        ghost = TreeNode(
            plug=plug,
            display_name=f"{parent.display_name}[{i}]",
            short_name=f"{parent_short}[{i}]",
            type_tag=parent.type_tag,
            is_expandable=False,
            is_array=False,
            is_compound=False,
            is_ghost=True,
        )
        nodes.append((i, ghost))

    nodes.sort(key=lambda pair: pair[0])
    return [node for _, node in nodes]
