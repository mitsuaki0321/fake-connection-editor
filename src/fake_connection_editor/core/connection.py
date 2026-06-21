"""接続可否の共通判定（master §8 C1, §5.4 / §5.5）。

ドラッグ接続・ボタン接続の双方が呼ぶ「1つの共通判定」（master §5.5）。
Maya 非依存の純粋関数。入力は正規化済み型タグ + destination の接続/ロック状態。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .type_compat import is_compatible


class ConnectReason(Enum):
    """C1 接続可否判定の理由コード。"""

    OK = "ok"
    TYPE_INCOMPATIBLE = "type_incompatible"  # 型が互換でない（C2 不成立）
    SRC_NOT_READABLE = "src_not_readable"  # source が出力不可（readable でない）
    DST_NOT_WRITABLE = "dst_not_writable"  # destination が入力不可（writable でない）
    DST_LOCKED = "dst_locked"  # destination がロックされている（force で解除可）


@dataclass(frozen=True)
class ConnectCheck:
    """C1 接続可否判定の結果。

    Attributes:
        ok: 接続可能なら True。
        reason: 判定理由（``ConnectReason``）。
    """

    ok: bool
    reason: ConnectReason


def check_connect(
    src_type: str,
    dst_type: str,
    *,
    dst_locked: bool,
    src_readable: bool = True,
    dst_writable: bool = True,
    force: bool = False,
) -> ConnectCheck:
    """C1: src→dst の接続可否を判定する（master §5.5）。

    判定順:
        1. 型互換（C2）。非互換なら ``TYPE_INCOMPATIBLE``（force でも覆らない）。
        2. 方向の可否（capability・master §4.3/§6）。source が出力不可なら
           ``SRC_NOT_READABLE``、destination が入力不可なら ``DST_NOT_WRITABLE``。
           これらは属性に内在する性質なので **force でも覆らない**。
        3. ロック。``dst_locked`` かつ非 force なら ``DST_LOCKED``。
           force なら解除→接続→再ロックで通す（master §5.4 (b)）。

    既存入力接続（destination が既に入力を持つ）は **拒否しない**。ドラッグ接続は
    既存を順向きで置換するのが既定挙動なので（標準 Connection Editor 準拠）、向き判定や
    可否には影響させない。置換は接続実行側が ``connect(force=True)`` で担う
    （§5.4 (a)）。これにより既接続 dst へのドロップが逆向きに繋がる現象を防ぐ。

    Args:
        src_type: source 側の型タグ。
        dst_type: destination 側の型タグ。
        dst_locked: destination がロックされているか。
        src_readable: source が読み取り可（出力＝source になれる）か。
        dst_writable: destination が書き込み可（入力＝destination になれる）か。
        force: ロックの一時解除を許すか（既存接続の置換は force に依らず既定で行う）。

    Returns:
        判定結果（``ConnectCheck``）。
    """
    if not is_compatible(src_type, dst_type):
        return ConnectCheck(False, ConnectReason.TYPE_INCOMPATIBLE)
    if not src_readable:
        return ConnectCheck(False, ConnectReason.SRC_NOT_READABLE)
    if not dst_writable:
        return ConnectCheck(False, ConnectReason.DST_NOT_WRITABLE)
    if dst_locked and not force:
        return ConnectCheck(False, ConnectReason.DST_LOCKED)
    return ConnectCheck(True, ConnectReason.OK)
