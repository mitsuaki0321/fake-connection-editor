"""ui — Qt（PySide2/6）による表示層。

テスト対象外。薄く保ち、ロジックは viewmodel / core に委譲する（master §1.2）。
Qt の import は :mod:`fake_connection_editor.ui.qt_compat` 経由に統一する。
"""

from .editor_window import EditorWindow, build_app

__all__ = ["EditorWindow", "build_app"]
