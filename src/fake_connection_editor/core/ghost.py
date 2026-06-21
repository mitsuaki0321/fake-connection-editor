"""マルチ属性のゴースト行算出（master §8 C4, §5.6 / §10.2）。

array（マルチ属性）の「実在しないが接続可能なインデックス」を先回り表示するための
ゴースト行インデックスを算出する。Maya 非依存の純粋関数。
"""

from __future__ import annotations


def ghost_indices(existing: tuple[int, ...]) -> tuple[int, ...]:
    """C4: array のゴースト行インデックスを算出する（master §5.6 / §10.2）。

    規則:
        - 歯抜けの空きインデックス全部（既存の最大値までで未使用のもの）。
        - 末尾の次の空き 1 つ（既存の最大値 + 1）。
        - array が空（既存ゼロ）なら仮想 [0] のみ（master §10.2 の特殊ケース）。

    例:
        - (0, 2) → (1, 3)   # [1] が歯抜け + 末尾次 [3]
        - ()     → (0,)     # 空 array は仮想 [0]
        - (0, 1, 2) → (3,)  # 歯抜けなし + 末尾次 [3]
        - (2,)   → (0, 1, 3)  # [0][1] が歯抜け + 末尾次 [3]

    Args:
        existing: ``getExistingArrayAttributeIndices()`` 由来の既存インデックス列。

    Returns:
        ゴースト行のインデックス列（昇順）。
    """
    if not existing:
        return (0,)
    used = set(existing)
    last = max(existing)
    holes = tuple(i for i in range(last + 1) if i not in used)
    return holes + (last + 1,)
