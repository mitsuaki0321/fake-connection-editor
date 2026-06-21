"""シーン外部変更のライブ同期（VM 再読込 API＋コアレス）の検証（MAYA_PLAN §7.1(2)）。

Maya/Qt 非依存。FakeSceneAccess をツール外から直接変更し、VM が再読込で追従すること、
および ``SceneSyncDispatcher`` が外部変更のバーストを優先順で 1 アクションに畳むことを
固める（実機 watcher は Step B・Maya GUI 検証）。
"""

from __future__ import annotations

from fake_connection_editor.scene_access import FakeSceneAccess, NodeId, PlugId
from fake_connection_editor.scene_access.fake import SAMPLE_SPHERE1, SAMPLE_SPHERE2
from fake_connection_editor.viewmodel import (
    LEFT,
    RIGHT,
    EditorViewModel,
    SceneSyncDispatcher,
)


def _plug(node: NodeId, *index: int) -> PlugId:
    return PlugId(node=node, index_path=tuple(index))


def _loaded_vm(scene: FakeSceneAccess) -> EditorViewModel:
    vm = EditorViewModel(scene)
    vm.load(LEFT, SAMPLE_SPHERE1)
    vm.load(RIGHT, SAMPLE_SPHERE2)
    return vm


# ---------------------------------------------------------------------------
# VM 再読込 API
# ---------------------------------------------------------------------------
def test_reload_connections_picks_up_external_connect(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    src = _plug(SAMPLE_SPHERE1, 0, 0)  # pSphere1.translateX
    dst = _plug(SAMPLE_SPHERE2, 0, 0)  # pSphere2.translateX
    vm.connection_pairs()  # キャッシュを満たす
    # ツール外（Maya 相当）で接続を作る。
    scene.connect(src, dst)
    assert (src, dst) not in vm.connection_pairs()  # キャッシュが残り未反映
    vm.reload_connections()
    assert (src, dst) in vm.connection_pairs()  # 再読込で反映


def test_reload_connections_notifies_non_structural(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    flags: list[bool] = []
    vm.add_listener(lambda s, _side=None: flags.append(s))
    vm.reload_connections()
    assert flags == [False]


def test_reload_structure_clears_meta_cache(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    vm.root_nodes(LEFT)  # メタをキャッシュ
    assert vm.type_tag(_plug(SAMPLE_SPHERE1, 2)) == "bool"
    vm.reload_structure()
    # メタキャッシュが捨てられ、再列挙までは型タグが取れない（空文字）。
    assert vm.type_tag(_plug(SAMPLE_SPHERE1, 2)) == ""


def test_reload_structure_notifies_structural(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    flags: list[bool] = []
    vm.add_listener(lambda s, _side=None: flags.append(s))
    vm.reload_structure()
    assert flags == [True]


def test_drop_nodes_removes_and_reports(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    flags: list[bool] = []
    vm.add_listener(lambda s, _side=None: flags.append(s))
    assert vm.drop_nodes({SAMPLE_SPHERE1.uuid}) is True
    assert vm.nodes(LEFT) == []
    assert vm.nodes(RIGHT) == [SAMPLE_SPHERE2]
    assert flags == [True]  # 構造通知


def test_drop_nodes_noop_when_absent(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    flags: list[bool] = []
    vm.add_listener(lambda s, _side=None: flags.append(s))
    assert vm.drop_nodes({"UUID-不在"}) is False
    assert vm.nodes(LEFT) == [SAMPLE_SPHERE1]
    assert flags == []  # 通知なし


def test_loaded_uuids(scene: FakeSceneAccess) -> None:
    vm = _loaded_vm(scene)
    assert vm.loaded_uuids() == {SAMPLE_SPHERE1.uuid, SAMPLE_SPHERE2.uuid}


# ---------------------------------------------------------------------------
# SceneSyncDispatcher（コアレス・優先順）
# ---------------------------------------------------------------------------
class _SpyTarget:
    """再読込呼び出しを記録するスパイ（drop_nodes の戻り値を制御）。"""

    def __init__(self, drop_result: bool = True) -> None:
        self.calls: list[object] = []
        self._drop_result = drop_result

    def reload_connections(self) -> None:
        self.calls.append("connections")

    def reload_structure(self) -> None:
        self.calls.append("structure")

    def drop_nodes(self, uuids: set[str]) -> bool:
        self.calls.append(("drop", set(uuids)))
        return self._drop_result


def test_dispatcher_connections_only() -> None:
    target = _SpyTarget()
    d = SceneSyncDispatcher(target)
    d.mark_connections()
    d.flush()
    assert target.calls == ["connections"]


def test_dispatcher_structure_subsumes_connections() -> None:
    target = _SpyTarget()
    d = SceneSyncDispatcher(target)
    d.mark_connections()
    d.mark_structure()
    d.flush()
    assert target.calls == ["structure"]  # 接続は構造再読込に内包


def test_dispatcher_removal_subsumes_all() -> None:
    target = _SpyTarget(drop_result=True)
    d = SceneSyncDispatcher(target)
    d.mark_removed("u")
    d.mark_structure()
    d.mark_connections()
    d.flush()
    assert target.calls == [("drop", {"u"})]  # 削除が再構築するので下位は不要


def test_dispatcher_removal_noop_falls_through_to_structure() -> None:
    target = _SpyTarget(drop_result=False)  # 外す対象が無く再構築しなかった
    d = SceneSyncDispatcher(target)
    d.mark_removed("u")
    d.mark_structure()
    d.flush()
    assert target.calls == [("drop", {"u"}), "structure"]


def test_dispatcher_coalesces_burst() -> None:
    target = _SpyTarget()
    d = SceneSyncDispatcher(target)
    for _ in range(5):
        d.mark_connections()
    d.flush()
    assert target.calls == ["connections"]  # 連打が 1 回に畳まれる


def test_dispatcher_flush_resets_pending() -> None:
    target = _SpyTarget()
    d = SceneSyncDispatcher(target)
    d.mark_structure()
    assert d.has_pending() is True
    d.flush()
    assert d.has_pending() is False
    d.flush()  # 2 回目は何もしない
    assert target.calls == ["structure"]
