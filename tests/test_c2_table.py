"""C2 互換表を実機採取マトリクスと照合する（master §11.1 表駆動テスト）。

``tests/data/c2_matrix.json`` は Maya 実機の ``connectAttr`` 許否を採取したもの
（採取スクリプト＝``samples/maya_c2_probe.py``）。``is_compatible`` がこの採取結果に
一致することを全ペアで固定する。正規化で情報が落ちる 2 点だけ実機から意図的に逸脱
させているため（``core/type_compat`` の docstring 参照）、その分は ``_DEVIATIONS`` /
``None`` 値として False を期待する。
"""

from __future__ import annotations

import json
from pathlib import Path

from fake_connection_editor.core import is_compatible

_MATRIX: dict[str, object] = json.loads(
    (Path(__file__).parent / "data" / "c2_matrix.json").read_text(encoding="utf-8")
)

# 実機採取では True だが、正規化で一般化できないため意図的に False へ倒すペア。
# （2 子 compound ↔ 2 要素ベクトルという子構成依存の artifact。type_compat 参照）
_DEVIATIONS: set[tuple[str, str]] = {
    ("compound", "double2"),
    ("compound", "float2"),
    ("compound", "int2"),
    ("double2", "compound"),
    ("float2", "compound"),
    ("int2", "compound"),
}


def test_is_compatible_matches_acquired_matrix() -> None:
    for key, raw in _MATRIX.items():
        src, dst = key.split(">")
        # None（doubleAngle 異常で同一正規化タグ内が食い違い）と逸脱は False を期待。
        expected = False if raw is None or (src, dst) in _DEVIATIONS else bool(raw)
        assert is_compatible(src, dst) is expected, f"{key}: 採取={raw}"
