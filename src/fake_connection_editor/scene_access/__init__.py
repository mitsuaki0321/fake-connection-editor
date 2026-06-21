"""scene_access — シーンアクセス抽象層。

SceneAccess 抽象IF と、その2実装（FakeSceneAccess / MayaSceneAccess）を置く
（master §2）。core はこの抽象IFのみに依存し、Maya 実装を知らない。
"""

from .fake import (
    FakeSceneAccess,
    build_multi_scene,
    build_sample_scene,
    build_tall_scene,
)
from .interface import (
    AttrMeta,
    Connections,
    NodeId,
    PlugId,
    SceneAccess,
    TypeTag,
)
from .maya import MayaSceneAccess, normalize_type
from .maya_backend import MayaBackend, RawAttr

__all__ = [
    "AttrMeta",
    "Connections",
    "NodeId",
    "PlugId",
    "SceneAccess",
    "TypeTag",
    "FakeSceneAccess",
    "build_sample_scene",
    "build_tall_scene",
    "build_multi_scene",
    "MayaSceneAccess",
    "MayaBackend",
    "RawAttr",
    "normalize_type",
]
