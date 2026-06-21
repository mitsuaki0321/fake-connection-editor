"""``ui.errors.error_handler`` の単体テスト（Maya/Qt 非依存）。

境界の例外をログ＋``_report_error`` に流して握りつぶすこと、正常時は戻り値を
そのまま返し reporter を呼ばないことを固定する。
"""

from __future__ import annotations

import pytest

from fake_connection_editor.ui.errors import error_handler


class _Recorder:
    """``_report_error`` を持つダミーのデコレート対象。"""

    def __init__(self) -> None:
        self.errors: list[BaseException] = []

    def _report_error(self, exc: BaseException) -> None:
        self.errors.append(exc)

    @error_handler
    def ok(self, value: int) -> int:
        return value * 2

    @error_handler
    def boom(self) -> int:
        raise ValueError("boom")


def test_success_returns_value_and_no_report() -> None:
    rec = _Recorder()
    assert rec.ok(3) == 6
    assert rec.errors == []


def test_exception_is_caught_reported_and_swallowed() -> None:
    rec = _Recorder()
    assert rec.boom() is None  # 例外は握って None
    assert len(rec.errors) == 1
    assert isinstance(rec.errors[0], ValueError)
    assert str(rec.errors[0]) == "boom"


def test_no_report_error_attr_is_safe() -> None:
    """``_report_error`` を持たない対象でも例外は握られ落ちない。"""

    class _Bare:
        @error_handler
        def boom(self) -> None:
            raise RuntimeError("x")

    assert _Bare().boom() is None


def test_exception_message_is_logged_without_generic_label(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ログは汎用ラベルでなく「何が問題か」（例外メッセージ）を出す。"""
    rec = _Recorder()
    with caplog.at_level("ERROR", logger="fake_connection_editor.ui.errors"):
        rec.boom()
    assert any("boom" in r.message for r in caplog.records)
    assert not any("UI 境界で例外" in r.message for r in caplog.records)
