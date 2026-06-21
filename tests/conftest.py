"""pytest 共通フィクスチャ。

段階1 のサンプルシーン（pSphere1 / pSphere2）を組み込み済みの
``FakeSceneAccess`` を提供する（master §2.4）。Maya 非依存。
"""

from __future__ import annotations

import pytest

from fake_connection_editor.scene_access import (
    FakeSceneAccess,
    NodeId,
    build_sample_scene,
)
from fake_connection_editor.scene_access.fake import SAMPLE_SPHERE1, SAMPLE_SPHERE2


@pytest.fixture
def scene() -> FakeSceneAccess:
    """pSphere1 / pSphere2 を投入済みのサンプルシーン。"""
    return build_sample_scene()


@pytest.fixture
def sphere1() -> NodeId:
    """サンプルシーンの pSphere1 ノード。"""
    return SAMPLE_SPHERE1


@pytest.fixture
def sphere2() -> NodeId:
    """サンプルシーンの pSphere2 ノード。"""
    return SAMPLE_SPHERE2
