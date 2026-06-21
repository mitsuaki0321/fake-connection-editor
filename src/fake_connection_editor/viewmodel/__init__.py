"""viewmodel — UI 状態・選択・フィルタ適用を担う純粋 Python 層。

Maya にも Qt にも依存せず、Fake 上でユニットテスト可能に保つ（master §1.2）。
"""

from .editor import LEFT, RIGHT, EditorViewModel, NameMode, SortMode
from .scene_sync import SceneSyncDispatcher

__all__ = [
    "EditorViewModel",
    "LEFT",
    "RIGHT",
    "SceneSyncDispatcher",
    "SortMode",
    "NameMode",
]
