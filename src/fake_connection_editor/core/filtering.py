"""フィルタ合成（master §8 C6, §9）。

型チップ（N/B/M/C/D）・Show Non-Keyable・Show Connected Only・テキスト検索を
合成し、ある属性を表示するか判定する。Maya 非依存の純粋関数。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..scene_access.interface import AttrMeta


class TypeCategory(Enum):
    """型チップの分類（master §9。N/B/M/C/D）。"""

    NUMERIC = "N"  # double/float/int/double3/float3 等
    BOOL = "B"  # bool
    MATRIX = "M"  # matrix
    COLOR = "C"  # color（現状の型タグには未登場・予約）
    DATA = "D"  # message/data/compound(無型) 等


# 型タグ → 分類（master §2.0 の型タグ集合が基準）。
# color は現状の型タグに無いため予約（将来 "color" タグ追加時に対応）。
_CATEGORY_BY_TAG = {
    "bool": TypeCategory.BOOL,
    "matrix": TypeCategory.MATRIX,
    "message": TypeCategory.DATA,
    "data": TypeCategory.DATA,
    "color": TypeCategory.COLOR,
}


def classify(type_tag: str) -> TypeCategory:
    """型タグを型チップの分類に対応づける（master §9）。

    既知の非数値タグ（bool/matrix/message/data/color）以外はすべて NUMERIC 扱い
    （double/float/int/double3/float3 等）。

    Args:
        type_tag: 正規化済み型タグ。

    Returns:
        対応する ``TypeCategory``。
    """
    return _CATEGORY_BY_TAG.get(type_tag, TypeCategory.NUMERIC)


@dataclass(frozen=True)
class FilterCriteria:
    """フィルタ条件（master §9）。

    Attributes:
        enabled_categories: 表示する型分類の集合。属性の分類がここに無ければ隠す。
        show_non_keyable: True なら non-keyable 属性も表示する（既定 False）。
        show_connected_only: True なら接続済みの属性だけ表示する（既定 False）。
        extra_only: True ならユーザー定義（extra）属性だけ表示する（既定 False）。
        show_hidden: True なら hidden 属性も表示する（既定 False＝隠す）。
        text: テキスト検索語（部分一致・大文字小文字無視）。空なら無効。
    """

    enabled_categories: frozenset[TypeCategory]
    show_non_keyable: bool = False
    show_connected_only: bool = False
    extra_only: bool = False
    show_hidden: bool = False
    text: str = ""

    @classmethod
    def all_visible(cls) -> FilterCriteria:
        """全型を表示し、トグル/検索を無効にした既定条件を返す。"""
        return cls(enabled_categories=frozenset(TypeCategory), show_hidden=True)


def should_display(
    meta: AttrMeta,
    *,
    is_connected: bool,
    criteria: FilterCriteria,
    match_short: bool = False,
) -> bool:
    """C6: 1 属性をフィルタ条件で表示するか判定する（master §9）。

    判定（いずれかに該当すれば隠す）:
        1. 型分類が ``enabled_categories`` に無い。
        2. non-keyable かつ ``show_non_keyable`` が False。
        3. ``show_connected_only`` が True かつ未接続。
        4. ``extra_only`` が True かつユーザー定義属性でない。
        5. hidden 属性かつ ``show_hidden`` が False。
        6. ``text`` が非空かつ検索対象名に部分一致しない（大小無視）。

    テキスト検索の対象名は表示モードに合わせる。``match_short`` が True なら shortName
    （空なら longName へフォールバック）、False なら longName に一致判定する。画面で
    見えている名前で検索できるようにするため（比較は 1 回＝速度/ヒット数は不変）。

    ノード名へのテキスト一致はノード文脈を持つ呼び出し側（ViewModel/proxy）で合成する。
    本関数は属性単位の判定に徹する（master §1.3）。

    Args:
        meta: 対象属性のメタデータ。
        is_connected: この属性が接続済みか（Show Connected Only 用）。
        criteria: フィルタ条件。
        match_short: True なら shortName を、False なら longName を検索対象にする。

    Returns:
        表示するなら True。
    """
    if classify(meta.type_tag) not in criteria.enabled_categories:
        return False
    if not meta.is_keyable and not criteria.show_non_keyable:
        return False
    if criteria.show_connected_only and not is_connected:
        return False
    if criteria.extra_only and not meta.is_user_defined:
        return False
    if meta.is_hidden and not criteria.show_hidden:
        return False
    if criteria.text:
        target = (
            (meta.short_name or meta.display_name)
            if match_short
            else (meta.display_name)
        )
        if criteria.text.casefold() not in target.casefold():
            return False
    return True
