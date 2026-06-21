"""方向トグルの src/dst 決定 ``_apply_direction`` の単体テスト（塊1・方向切替）。

純粋関数なので Qt（QApplication）非依存で検証できる。Connect / Connect Leaf /
Copy Value のボタン操作は、方向トグルが示す向き（左→右 / 右→左）に従って
左右の選択を src・dst に振り分ける。
"""

from __future__ import annotations

from fake_connection_editor.ui.interaction import _apply_direction


def test_l2r_keeps_left_as_src() -> None:
    """L2R（左→右）なら左が src・右が dst。"""
    assert _apply_direction("L", "R", l2r=True) == ("L", "R")


def test_r2l_swaps_to_right_as_src() -> None:
    """R2L（右→左）なら右が src・左が dst。"""
    assert _apply_direction("L", "R", l2r=False) == ("R", "L")


def test_none_selection_preserved_l2r() -> None:
    """未選択（None）はそのまま伝播する（L2R）。"""
    assert _apply_direction(None, "R", l2r=True) == (None, "R")


def test_none_selection_preserved_r2l() -> None:
    """未選択（None）はそのまま伝播する（R2L・src 側が None になる）。"""
    assert _apply_direction(None, "R", l2r=False) == ("R", None)
