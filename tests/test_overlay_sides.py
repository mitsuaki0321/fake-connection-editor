"""接続線の側選択 ``_choose_connection_sides`` の単体テスト（段階4b・重複ノード対応）。

純粋関数なので Qt（QApplication）非依存で検証できる。同一ノードが左右に同時表示
されたときに、接続線をどの側ペアで引くか（中央をまたぐ左右間を最優先）を固定する。
"""

from __future__ import annotations

from fake_connection_editor.ui.connection_overlay import _choose_connection_sides
from fake_connection_editor.viewmodel import LEFT, RIGHT


def test_prefers_left_to_right_across_middle() -> None:
    """src 左・dst 右なら中央をまたぐ (LEFT, RIGHT) を選ぶ。"""
    assert _choose_connection_sides({LEFT}, {RIGHT}) == (LEFT, RIGHT)


def test_prefers_right_to_left_when_reversed() -> None:
    """src 右・dst 左でも中央をまたぐ (RIGHT, LEFT) を選ぶ。"""
    assert _choose_connection_sides({RIGHT}, {LEFT}) == (RIGHT, LEFT)


def test_duplicate_dst_routes_across_middle() -> None:
    """dst が左右両方にあるとき、左→右で中央をまたぐ側を優先する（本件の核）。"""
    assert _choose_connection_sides({LEFT}, {LEFT, RIGHT}) == (LEFT, RIGHT)


def test_duplicate_both_endpoints_picks_canonical() -> None:
    """両端が左右両方にあるとき、標準の (LEFT, RIGHT) を選ぶ。"""
    assert _choose_connection_sides({LEFT, RIGHT}, {LEFT, RIGHT}) == (LEFT, RIGHT)


def test_same_side_fallback_left() -> None:
    """両端とも左にしか無いなら同側 (LEFT, LEFT) にフォールバックする。"""
    assert _choose_connection_sides({LEFT}, {LEFT}) == (LEFT, LEFT)


def test_same_side_fallback_right() -> None:
    """両端とも右にしか無いなら同側 (RIGHT, RIGHT) にフォールバックする。"""
    assert _choose_connection_sides({RIGHT}, {RIGHT}) == (RIGHT, RIGHT)


def test_unresolvable_returns_none() -> None:
    """いずれかの端が未表示なら描かない（None）。"""
    assert _choose_connection_sides(set(), {LEFT}) is None
    assert _choose_connection_sides({LEFT}, set()) is None
