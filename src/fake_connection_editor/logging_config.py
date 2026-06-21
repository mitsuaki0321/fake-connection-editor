"""fake_connection_editor のロギング設定。

パッケージ全体のルートロガー（``fake_connection_editor``）を一元的に構成する。
各モジュールは ``from logging import getLogger; logger = getLogger(__name__)``
でロガーを取得する。
"""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "fake_connection_editor"
DEFAULT_LOG_LEVEL = logging.INFO
LOG_FORMAT = "[%(levelname)s] %(name)s: %(message)s"
DETAILED_LOG_FORMAT = (
    "[%(levelname)s] %(asctime)s - %(name)s - %(filename)s:%(lineno)d - %(message)s"
)


def setup_logging(
    level: int = DEFAULT_LOG_LEVEL, detailed: bool = False
) -> logging.Logger:
    """ルートロガーを構成する。

    パッケージ初期化時に一度だけ呼ぶ想定。コンソール出力ハンドラを設定し、
    多重呼び出し時の重複ハンドラを避ける。

    Args:
        level: ログレベル（例: ``logging.DEBUG``、``logging.INFO``）。
        detailed: True ならタイムスタンプとファイル情報付きの詳細フォーマットを使う。

    Returns:
        構成済みの ``fake_connection_editor`` ロガー。

    Example:
        >>> from fake_connection_editor.logging_config import setup_logging
        >>> import logging
        >>> logger = setup_logging(level=logging.DEBUG)
    """
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        logger.handlers.clear()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = DETAILED_LOG_FORMAT if detailed else LOG_FORMAT
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)

    # ルートロガーへの伝播を止め、メッセージの二重出力を防ぐ
    logger.propagate = False
    logger.debug(
        "fake_connection_editor logging initialized (level=%s)",
        logging.getLevelName(level),
    )
    return logger


def set_log_level(level: int) -> None:
    """全 fake_connection_editor ロガーのログレベルを変更する。

    Args:
        level: 新しいログレベル（例: ``logging.DEBUG``）。
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


def get_log_level() -> int:
    """現在のログレベルを返す。

    Returns:
        現在のログレベル（``logging`` の整数値）。
    """
    return logging.getLogger(LOGGER_NAME).level


__all__ = ["setup_logging", "set_log_level", "get_log_level", "LOGGER_NAME"]
