"""UI 境界の例外を捕捉して通知へ流すデコレータ（Maya/Qt 非依存）。

Maya で PySide のスロットが投げた例外はコマンドライン（赤帯）に出ず ScriptEditor
止まりになり、ユーザーに伝わりにくい。ここで境界（スロット/イベント）の例外を捕捉し、
ログ出力＋**注入された reporter**（Maya では ``MGlobal.displayError``）へ渡す。
reporter 未注入の dev/テストではログのみ（コンソールに出る）。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from typing import Any

logger = getLogger(__name__)


def _report(args: tuple, exc: BaseException) -> None:
    """デコレート対象がインスタンスメソッドなら ``_report_error`` へ流す。

    Args:
        args: ラップした関数の位置引数（先頭が ``self`` の想定）。
        exc: 捕捉した例外。
    """
    target = args[0] if args else None
    reporter = getattr(target, "_report_error", None)
    if callable(reporter):
        reporter(exc)


def error_handler(func: Callable) -> Callable:
    """スロット/境界メソッドの例外を捕捉・通知して握りつぶすデコレータ。

    例外時はトレースバックをログに出し、対象インスタンスの ``_report_error`` に
    渡してから ``None`` を返す（呼び出し側のイベントループへ伝播させない）。正常時は
    関数の戻り値をそのまま返す。

    Notes:
        - ``functools.wraps`` は使わない（参照実装の方針踏襲＝Maya のコマンドポートへ
          エラーを出すための慣習）。
        - 通知先（reporter）は ``_report_error`` 経由で注入されたものに委ねるため、
          このモジュールは Maya/Qt に依存しない。

    Args:
        func: ラップするスロット/境界メソッド。

    Returns:
        例外を捕捉・通知するラッパ関数。
    """

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001  境界で全例外を握り通知する
            # 通知/ログは「何が問題か」だけ（例外メッセージ）。トレースバックは
            # exception() が付ける（dev/ScriptEditor 用）。汎用ラベルは付けない。
            logger.exception("%s", exc)
            _report(args, exc)
            return None

    return wrapper
