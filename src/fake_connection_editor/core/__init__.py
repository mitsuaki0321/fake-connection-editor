"""core — Maya 非依存のドメインロジック層。

型互換判定・接続可否・leaf 接続成立判定・ゴースト算出・フィルタ合成・
ツリー構築など（master §8 の Core ロジック）を純粋関数として置く。
すべて FakeSceneAccess の固定データだけで pytest 検証できることを不変条件とする。
"""

from .connection import ConnectCheck, ConnectReason, check_connect
from .filtering import (
    FilterCriteria,
    TypeCategory,
    classify,
    should_display,
)
from .ghost import ghost_indices
from .tree import TreeNode, build_array_child_nodes, build_child_nodes
from .type_compat import (
    LeafConnectCheck,
    LeafReason,
    check_leaf_connect,
    is_compatible,
    is_scalar,
)

__all__ = [
    # C1
    "ConnectCheck",
    "ConnectReason",
    "check_connect",
    # C2 / C3
    "is_compatible",
    "is_scalar",
    "LeafConnectCheck",
    "LeafReason",
    "check_leaf_connect",
    # C4
    "ghost_indices",
    # C6
    "TypeCategory",
    "FilterCriteria",
    "classify",
    "should_display",
    # C7
    "TreeNode",
    "build_child_nodes",
    "build_array_child_nodes",
]
