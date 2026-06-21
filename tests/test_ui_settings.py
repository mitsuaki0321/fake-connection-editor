"""``ui.settings.InMemorySettings`` の単体テスト（Maya/Qt 非依存）。

``SettingsStore`` 契約（read/write・既定値・型保持）を固定する。Maya 実装
（``OptionVarSettings``）は ``cmds`` 依存のためここでは対象外（実機確認）。
"""

from __future__ import annotations

from fake_connection_editor.ui.settings import InMemorySettings, SettingsStore


def test_read_returns_default_when_missing() -> None:
    store = InMemorySettings()
    assert store.read("missing") is None
    assert store.read("missing", {"a": 1}) == {"a": 1}


def test_write_then_read_roundtrips_value() -> None:
    store = InMemorySettings()
    payload = {"force_connect": True, "sort_mode": "ASC", "name_mode": "LONG"}
    store.write("menu", payload)
    assert store.read("menu") == payload


def test_write_overwrites_existing() -> None:
    store = InMemorySettings()
    store.write("menu", {"a": 1})
    store.write("menu", {"a": 2})
    assert store.read("menu") == {"a": 2}


def test_satisfies_settings_store_protocol() -> None:
    assert isinstance(InMemorySettings(), SettingsStore)
