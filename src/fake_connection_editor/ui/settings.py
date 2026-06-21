"""設定の永続化境界（メニューバーのオプションを次回起動へ持ち越す）。

``SettingsStore`` は read/write の薄い契約。実装は注入で差し替える:
    - Maya: ``ui.maya_settings.OptionVarSettings``（``cmds.optionVar`` 永続化）。
    - dev/テスト: ``InMemorySettings``（プロセス内 dict）。``None`` 注入なら永続化なし。

Maya にも Qt にも依存しない（アーキ制約・100% pytest 可能に保つ）。値の直列化は
実装側（Maya は JSON）に委ね、本層は Python 値のまま read/write する。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SettingsStore(Protocol):
    """ツール設定の永続化先（注入される実装の契約）。"""

    def read(self, key: str, default: Any = None) -> Any:
        """``key`` の値を返す（無ければ ``default``）。"""
        ...

    def write(self, key: str, value: Any) -> None:
        """``key`` に ``value`` を保存する。"""
        ...


class InMemorySettings:
    """プロセス内 dict に保持する ``SettingsStore``（dev/テスト用・永続化しない）。"""

    def __init__(self) -> None:
        """空の保存領域を生成する。"""
        self._data: dict[str, Any] = {}

    def read(self, key: str, default: Any = None) -> Any:
        """``key`` の値を返す（無ければ ``default``）。"""
        return self._data.get(key, default)

    def write(self, key: str, value: Any) -> None:
        """``key`` に ``value`` を保存する。"""
        self._data[key] = value


__all__ = ["SettingsStore", "InMemorySettings"]
