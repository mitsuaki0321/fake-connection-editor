"""型チップのクリック後表示集合 ``_next_type_filter`` の単体テスト（案A: ソロ＋Ctrl）。

純粋関数なので Qt（QApplication）非依存で検証できる。通常クリックは「その型のみ」
に絞り（単独中の型を押すと全表示へ復帰）、Ctrl+クリックは個別トグルで複数選択を
維持する。
"""

from __future__ import annotations

from fake_connection_editor.core import TypeCategory
from fake_connection_editor.ui.editor_window import _next_type_filter

ALL = frozenset(TypeCategory)
N = TypeCategory.NUMERIC
B = TypeCategory.BOOL


def test_solo_from_all() -> None:
    """全表示で通常クリック → その型のみ（ソロ）。"""
    assert _next_type_filter(ALL, N, ALL, ctrl=False) == frozenset({N})


def test_solo_reclick_restores_all() -> None:
    """単独表示中の型を通常クリック → 全表示に戻る。"""
    assert _next_type_filter(frozenset({N}), N, ALL, ctrl=False) == ALL


def test_solo_switch_type() -> None:
    """単独表示中に別の型を通常クリック → その型の単独に切り替わる。"""
    assert _next_type_filter(frozenset({N}), B, ALL, ctrl=False) == frozenset({B})


def test_solo_from_multi() -> None:
    """複数表示中に通常クリック → その型のみ（単独化）。"""
    assert _next_type_filter(frozenset({N, B}), N, ALL, ctrl=False) == frozenset({N})


def test_ctrl_excludes_from_all() -> None:
    """全表示で Ctrl+クリック → その型を除外（複数維持）。"""
    assert _next_type_filter(ALL, N, ALL, ctrl=True) == ALL - {N}


def test_ctrl_adds_to_solo() -> None:
    """単独表示中に Ctrl+クリック → その型を追加（複数化）。"""
    assert _next_type_filter(frozenset({N}), B, ALL, ctrl=True) == frozenset({N, B})


def test_ctrl_removes_from_multi() -> None:
    """複数表示中に Ctrl+クリック → その型を除外。"""
    assert _next_type_filter(frozenset({N, B}), N, ALL, ctrl=True) == frozenset({B})


def test_ctrl_empty_resets_to_all() -> None:
    """Ctrl+クリックで最後の1つを外し空になったら全表示へリセット（全非表示にしない）。"""
    assert _next_type_filter(frozenset({N}), N, ALL, ctrl=True) == ALL
