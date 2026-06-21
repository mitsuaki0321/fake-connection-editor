"""fake_connection_editor — Maya Connection Editor の再実装パッケージ。

4層アーキテクチャ（ui / viewmodel / core / scene_access）で構成し、
core と viewmodel は Maya 非依存でテスト可能に保つ。

Maya 内では ``import fake_connection_editor`` のあと
``fake_connection_editor.launch()`` で起動する。
"""

from ._launch import launch

__version__ = "0.1.0"

__all__ = ["launch"]
