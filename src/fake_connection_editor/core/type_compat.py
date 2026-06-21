"""型互換ロジック（master §8 C2 / C3, §10.3）。

C2: 型タグ間の接続互換（実機 connectAttr 採取に基づく明示ペア表）。
C3: leaf 接続（子属性で接続）の成立判定。

いずれも Maya 非依存の純粋関数。入力は正規化済み型タグ（master §1.3）。

C2 の互換ペアは Maya 実機の ``connectAttr`` 許否を採取して固めた（採取スクリプト＝
``samples/maya_c2_probe.py`` / 手順＝docs/MAYA_VERIFY §8 / 経緯＝docs/MAYA_PLAN §7）。
採取マトリクスは ``tests/data/c2_matrix.json`` に保全し、表駆動テスト（master §11.1）で
本表と照合する。正規化で情報が落ちる次の 2 点だけ、実機採取から**意図的に逸脱**させて
いる（実 ``connectAttr`` が最終ガードするため、ゲートとしては保守側に倒す）:

- **doubleAngle 異常**: ``doubleAngle`` 単体がベクトルへ接続できるが、正規化で
  ``double`` へ畳む以上区別できないため、一般スカラー挙動（scalar↔vector 不可）に倒す。
- **構造依存の compound**: 2 子 compound が 2 要素ベクトルと繋がる等は子構成依存の
  artifact なので一般化せず、``compound`` は same-tag のみ互換とする。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import product

# 数値スカラー群: 相互に昇格して接続可（bool/int/float/double）。angle/distance/time/
# enum・short/long/byte は正規化で int/double に畳まれ、正規化タグとしては出現しない。
_SCALAR_GROUP = ("bool", "int", "float", "double")

# ベクトル群: 要素数が一致するとき群内で相互に接続可（実機採取）。
_VEC2_GROUP = ("double2", "float2", "int2")
_VEC3_GROUP = ("double3", "float3", "int3")

# 同一タグのみ互換。data/compound は粒度上ロスがある（data=doubleArray/mesh/nurbs 等を、
# compound=任意の子構成を一括）が、同一タグは許容して実 connectAttr に最終判定を委ねる。
_SAME_ONLY_TAGS = ("matrix", "string", "stringArray", "data", "compound")

# C2 が扱う正規化タグの母集合（message を含む全タグ）。
_ALL_TAGS = (
    *_SCALAR_GROUP,
    *_VEC2_GROUP,
    *_VEC3_GROUP,
    *_SAME_ONLY_TAGS,
    "message",
)

# leaf 接続（C3）対象のスカラー型タグ（master §10.3 条件2）。
_SCALAR_TAGS = frozenset(_SCALAR_GROUP)


def _build_compatible_pairs() -> frozenset[tuple[str, str]]:
    """実機採取に基づく互換な順序つき (src, dst) タグペア集合を組む。

    Returns:
        接続互換なタグペアの集合（``is_compatible`` の引き）。
    """
    pairs: set[tuple[str, str]] = set()
    # 群内は相互互換（同一タグを含む）。
    for group in (_SCALAR_GROUP, _VEC2_GROUP, _VEC3_GROUP):
        pairs.update(product(group, group))
    # その他のタグは同一タグのみ。
    pairs.update((tag, tag) for tag in _SAME_ONLY_TAGS)
    # message はワイルドカード: 任意→message は全許可、message→任意は compound 以外可。
    pairs.update((tag, "message") for tag in _ALL_TAGS)
    pairs.update(("message", tag) for tag in _ALL_TAGS if tag != "compound")
    return frozenset(pairs)


_COMPATIBLE_PAIRS = _build_compatible_pairs()


def is_scalar(type_tag: str) -> bool:
    """型タグがスカラー型か判定する（master §10.3 条件2 用）。

    Args:
        type_tag: 正規化済み型タグ。

    Returns:
        スカラー型なら True（bool/int/float/double）。
    """
    return type_tag in _SCALAR_TAGS


def is_compatible(src_type: str, dst_type: str) -> bool:
    """C2: src→dst が型互換かを判定する（実機採取の明示ペア表・master §5.1 / §10.3）。

    判定は ``_COMPATIBLE_PAIRS``（実機 ``connectAttr`` 採取由来）への所属で行う。要点:
        - 数値スカラー群 {bool,int,float,double} は相互に昇格互換。
        - ベクトルは要素数一致時のみ群内で相互互換（double3↔float3↔int3 等）。
        - matrix/string/stringArray/data/compound は同一タグのみ。
        - message は双方向ワイルドカード（message→compound のみ非互換）。

    Args:
        src_type: source 側の型タグ。
        dst_type: destination 側の型タグ。

    Returns:
        互換なら True。
    """
    return (src_type, dst_type) in _COMPATIBLE_PAIRS


class LeafReason(Enum):
    """C3 leaf 接続判定の理由コード。"""

    OK = "ok"
    COUNT_MISMATCH = "count_mismatch"  # 子数が一致しない
    NON_SCALAR_CHILD = "non_scalar_child"  # 子に非スカラーが含まれる
    CHILD_INCOMPATIBLE = "child_incompatible"  # 子ペアが C2 非互換


@dataclass(frozen=True)
class LeafConnectCheck:
    """C3 leaf 接続成立判定の結果。

    Attributes:
        ok: 成立するなら True。
        reason: 判定理由（``LeafReason``）。
        pairs: 成立時の子ペア（src 子 index, dst 子 index）の位置対応列。
            非成立なら空。実 PlugId への対応付けは呼び出し側（ViewModel）が行う。
    """

    ok: bool
    reason: LeafReason
    pairs: tuple[tuple[int, int], ...] = ()


def check_leaf_connect(
    src_child_types: list[str], dst_child_types: list[str]
) -> LeafConnectCheck:
    """C3: leaf 接続（子属性で接続）が成立するか判定する（master §10.3）。

    成立条件:
        1. 子数が一致、かつ
        2. 全子がスカラー型、かつ
        3. 各子ペアが C2（スカラー型昇格）で互換。

    例:
        - double3 ↔ float3 → OK（3=3 / 全子スカラー / double↔float 昇格可）
        - double3 ↔ double2 → NG（数不一致）
        - double3 ↔ string3 → NG（子が非スカラー）

    Args:
        src_child_types: source 親の子型タグ列（位置順）。
        dst_child_types: destination 親の子型タグ列（位置順）。

    Returns:
        判定結果（``LeafConnectCheck``）。
    """
    if len(src_child_types) != len(dst_child_types):
        return LeafConnectCheck(False, LeafReason.COUNT_MISMATCH)

    for s, d in zip(src_child_types, dst_child_types):
        if not is_scalar(s) or not is_scalar(d):
            return LeafConnectCheck(False, LeafReason.NON_SCALAR_CHILD)
        if not is_compatible(s, d):
            return LeafConnectCheck(False, LeafReason.CHILD_INCOMPATIBLE)

    pairs = tuple((i, i) for i in range(len(src_child_types)))
    return LeafConnectCheck(True, LeafReason.OK, pairs)
