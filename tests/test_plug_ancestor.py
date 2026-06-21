"""plug 祖先判定 ``_is_ancestor`` の単体テスト（オートスクロールの逆引きの基礎）。

``index_for_plug`` は属性階層を index_path の祖先関係でたどる。その判定部分を
純粋関数として切り出してあり、Qt 非依存で検証できる。
"""

from __future__ import annotations

from fake_connection_editor.scene_access.interface import NodeId, PlugId
from fake_connection_editor.ui.tree_model import _is_ancestor

NODE = NodeId(uuid="U1", path="|n1")
OTHER = NodeId(uuid="U2", path="|n2")


def _plug(node: NodeId, *path: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(path))


def test_parent_is_ancestor_of_child() -> None:
    """(0,) は (0, 1) の祖先。"""
    assert _is_ancestor(_plug(NODE, 0), _plug(NODE, 0, 1)) is True


def test_deeper_prefix_is_ancestor() -> None:
    """(0, 1) は (0, 1, 2) の祖先。"""
    assert _is_ancestor(_plug(NODE, 0, 1), _plug(NODE, 0, 1, 2)) is True


def test_same_plug_is_not_ancestor() -> None:
    """同一 plug は（真の）祖先ではない。"""
    assert _is_ancestor(_plug(NODE, 0), _plug(NODE, 0)) is False


def test_sibling_is_not_ancestor() -> None:
    """(0,) は (1, 0) の祖先ではない（前方一致しない）。"""
    assert _is_ancestor(_plug(NODE, 0), _plug(NODE, 1, 0)) is False


def test_deeper_is_not_ancestor_of_shallower() -> None:
    """深い plug は浅い plug の祖先ではない。"""
    assert _is_ancestor(_plug(NODE, 0, 1), _plug(NODE, 0)) is False


def test_different_node_is_not_ancestor() -> None:
    """ノードが違えば祖先ではない。"""
    assert _is_ancestor(_plug(NODE, 0), _plug(OTHER, 0, 1)) is False
