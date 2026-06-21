"""Maya ``optionVar`` による設定永続化（``SettingsStore`` の Maya 実装）。

``ui.settings.SettingsStore`` を満たし、値を JSON 直列化してツール名で名前空間化した
``optionVar`` に保存する。``asset-dependency`` の ``ui/_lib/optionvar.py`` を参考に
本ツール向けへ最小化した。

Maya 依存のため ``ui.__init__`` からは読み込まない（テストは ``InMemorySettings`` を
使う）。Maya 内の ``launch()``（``samples/maya_smoke.py``・ローカル）で注入する。
"""

from __future__ import annotations

import json
from logging import getLogger
from typing import Any

import maya.cmds as cmds

logger = getLogger(__name__)


class OptionVarSettings:
    """ツール設定を Maya ``optionVar`` に読み書きする ``SettingsStore``。

    値は JSON 直列化し、キーは ``"<tool_name>.<key>"`` で名前空間化して他ツールとの
    衝突を避ける。

    Attributes:
        tool_name: optionVar キーの名前空間に使うツール名。
    """

    def __init__(self, tool_name: str) -> None:
        """初期化する。

        Args:
            tool_name: ツール名（optionVar キーの名前空間に使う）。
        """
        self.tool_name = tool_name

    def _full_key(self, key: str) -> str:
        """``key`` をツール名で名前空間化した完全キーにする。"""
        return f"{self.tool_name}.{key}"

    def read(self, key: str, default: Any = None) -> Any:
        """``key`` の値を optionVar から読む（無ければ ``default``）。

        JSON 復元に失敗した場合は生の文字列を返す。

        Args:
            key: 読み出すキー。
            default: 未保存時に返す既定値。

        Returns:
            復元した値、未保存なら ``default``。
        """
        full_key = self._full_key(key)
        if not cmds.optionVar(exists=full_key):
            return default
        value = cmds.optionVar(q=full_key)
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    def write(self, key: str, value: Any) -> None:
        """``value`` を JSON 直列化して optionVar に保存する。

        直列化に失敗した場合はログに残して握る（永続化失敗で UI を巻き込まない）。

        Args:
            key: 保存するキー。
            value: 保存する値（JSON 直列化可能であること）。
        """
        full_key = self._full_key(key)
        try:
            serialized = json.dumps(value)
        except (TypeError, ValueError) as e:
            logger.error("optionVar の直列化に失敗 %s: %s", full_key, e)
            return
        cmds.optionVar(sv=(full_key, serialized))


__all__ = ["OptionVarSettings"]
