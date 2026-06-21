"""``MayaBackend`` のインメモリ実装（テスト/dev 用・MAYA_PLAN §5）。

``RealMayaBackend``（実 cmds/OpenMaya・§M2）を Maya GUI で書く前に、実機採取値を写した
ツリーをハンドルで模す。これを ``MayaSceneAccess`` に注入すれば、歩行・existing indices
解釈・型正規化・接続端点マッピング・書き込み委譲を **Maya 非依存で pytest** できる
（master §11.5）。ハンドルは ``(uuid, index_path)`` タプル（不透明に扱う）。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field, replace
from typing import Any

from .interface import NodeId
from .maya_backend import PlugKey, RawAttr


@dataclass
class _Attr:
    """FakeMayaBackend 内部の生属性ノード（1 属性 = 1 ノード）。"""

    display_name: str
    api_type: str
    short_name: str = ""
    sub_type: str = ""
    is_array: bool = False
    is_compound: bool = False
    is_keyable: bool = True
    is_readable: bool = True
    is_writable: bool = True
    is_locked: bool = False
    is_user_defined: bool = False
    value: Any = None
    children: list[_Attr] = field(default_factory=list)  # compound 子（位置順）
    elements: dict[int, _Attr] = field(default_factory=dict)  # array 論理index→要素
    elem_proto: _Attr | None = None  # array: materialize 時の要素テンプレート


@dataclass
class _Node:
    """FakeMayaBackend 内部のノード（トップレベル属性列を持つ）。"""

    uuid: str
    path: str
    roots: list[_Attr]


class FakeMayaBackend:
    """``MayaBackend`` のインメモリ実装（``MayaSceneAccess`` のテスト用バックエンド）。

    ハンドル = ``(uuid, index_path)`` タプル。``MayaSceneAccess`` はこれを不透明として
    扱い、``plug_key`` でのみ plain key を取り出す。
    """

    def __init__(self) -> None:
        """空のバックエンドを生成する。"""
        self._nodes: dict[str, _Node] = {}
        self._selection: list[str] = []
        self._conns: set[tuple[PlugKey, PlugKey]] = set()
        # 表示色（Color Settings 模擬）。名前 → (r,g,b)。未設定は None を返す。
        self._display_colors: dict[str, tuple[float, float, float]] = {}

    # ---- ビルダー（テストデータ構築用・契約外） ----
    def add_node(self, uuid: str, path: str, roots: list[_Attr]) -> None:
        """ノードを追加する（トップレベル属性列を渡す）。"""
        self._nodes[uuid] = _Node(uuid=uuid, path=path, roots=roots)

    def add_connection(self, src: PlugKey, dst: PlugKey) -> None:
        """接続 (src→dst) を追加する。"""
        self._conns.add((src, dst))

    def set_selected(self, uuids: list[str]) -> None:
        """選択中ノードの uuid 列を設定する。"""
        self._selection = list(uuids)

    def set_display_color(self, name: str, rgb: tuple[float, float, float]) -> None:
        """表示色を設定する（``display_color`` の採取値モック・テスト用）。"""
        self._display_colors[name] = rgb

    def _attr(self, handle: PlugKey) -> _Attr | None:
        """ハンドル ``(uuid, index_path)`` を内部 ``_Attr`` へ解決（無ければ None）。"""
        uuid, index_path = handle
        node = self._nodes.get(uuid)
        if node is None or not index_path:
            return None
        if index_path[0] >= len(node.roots):
            return None
        cur: _Attr | None = node.roots[index_path[0]]
        for index in index_path[1:]:
            if cur is None:
                return None
            if cur.is_array:
                cur = cur.elements.get(index)
            elif cur.is_compound:
                cur = cur.children[index] if index < len(cur.children) else None
            else:
                return None
        return cur

    # ---- 解決 ----
    def node_handle(self, uuid: str) -> str | None:
        """Uuid からノードハンドル（= uuid 文字列）を返す。"""
        return uuid if uuid in self._nodes else None

    def node_uuid(self, node: str) -> str:
        """ノードハンドルの uuid を返す。"""
        return node

    def node_path(self, node: str) -> str:
        """ノードハンドルの表示用フルパスを返す。"""
        return self._nodes[node].path

    def selected_nodes(self) -> list[str]:
        """選択中のノードハンドル列を返す。"""
        return [u for u in self._selection if u in self._nodes]

    def select(self, nodes: list[str]) -> None:
        """選択を置き換える。"""
        self._selection = list(nodes)

    # ---- 歩行 ----
    def root_attr_plugs(self, node: str) -> list[PlugKey]:
        """ノード直下のトップレベル属性 plug ハンドル列を返す（位置順）。"""
        return [(node, (i,)) for i in range(len(self._nodes[node].roots))]

    def child_plugs(self, plug: PlugKey) -> list[PlugKey]:
        """Compound の子 plug ハンドル列を返す（位置順）。"""
        uuid, index_path = plug
        cur = self._attr(plug)
        if cur is None:
            return []
        return [(uuid, index_path + (i,)) for i in range(len(cur.children))]

    def array_existing_indices(self, plug: PlugKey) -> tuple[int, ...]:
        """Array の既存論理インデックス列を返す（昇順）。"""
        cur = self._attr(plug)
        return tuple(sorted(cur.elements)) if cur is not None else ()

    def element_plug(self, plug: PlugKey, logical_index: int) -> PlugKey:
        """Array の論理インデックス要素 plug ハンドルを返す。"""
        uuid, index_path = plug
        return (uuid, index_path + (logical_index,))

    # ---- 生事実・同一性 ----
    def raw_attr(self, plug: PlugKey) -> RawAttr:
        """Plug の正規化前メタ（``RawAttr``）を返す。"""
        cur = self._attr(plug)
        if cur is None:
            return RawAttr(display_name="", api_type="")
        return RawAttr(
            display_name=cur.display_name,
            short_name=cur.short_name,
            api_type=cur.api_type,
            sub_type=cur.sub_type,
            is_array=cur.is_array,
            is_compound=cur.is_compound,
            is_keyable=cur.is_keyable,
            is_readable=cur.is_readable,
            is_writable=cur.is_writable,
            has_children=cur.is_compound or cur.is_array,
            is_locked=cur.is_locked,
            is_user_defined=cur.is_user_defined,
        )

    def plug_key(self, plug: PlugKey) -> PlugKey:
        """Plug ハンドルを plain key に落とす（Fake はハンドル自身が key）。"""
        return plug

    # ---- 接続 ----
    def plug_sources(self, plug: PlugKey) -> list[PlugKey]:
        """この plug を destination とする source plug ハンドル列を返す。"""
        return [s for (s, d) in self._conns if d == plug]

    def plug_destinations(self, plug: PlugKey) -> list[PlugKey]:
        """この plug を source とする destination plug ハンドル列を返す。"""
        return [d for (s, d) in self._conns if s == plug]

    def node_connections(self, node: str) -> list[tuple[PlugKey, PlugKey]]:
        """外向き接続（node が source）の全 (src, dst) plug 対を返す（高速経路）。"""
        return [(s, d) for (s, d) in self._conns if s[0] == node]

    def connected_plugs(self, node: str) -> list[PlugKey]:
        """接続を持つ plug 列を返す（node 上・入出力どちらでも・高速経路）。"""
        plugs: set[PlugKey] = set()
        for s, d in self._conns:
            if s[0] == node:
                plugs.add(s)
            if d[0] == node:
                plugs.add(d)
        return list(plugs)

    # ---- 書き込み ----
    def connect(self, src: PlugKey, dst: PlugKey, force: bool) -> None:
        """Src → dst を接続する（force は dst の既存入力を置換）。"""
        if force:
            self._conns = {(s, d) for (s, d) in self._conns if d != dst}
        self._conns.add((src, dst))

    def disconnect(self, src: PlugKey, dst: PlugKey) -> None:
        """Src → dst を切断する。"""
        self._conns.discard((src, dst))

    def get_value(self, plug: PlugKey) -> Any:
        """Plug の現在値を返す。"""
        cur = self._attr(plug)
        return cur.value if cur is not None else None

    def set_value(self, plug: PlugKey, value: Any) -> None:
        """Plug に値を設定する。"""
        cur = self._attr(plug)
        if cur is not None:
            cur.value = value

    def is_locked(self, plug: PlugKey) -> bool:
        """Plug が現在ロックされているか返す。"""
        cur = self._attr(plug)
        return cur.is_locked if cur is not None else False

    def set_locked(self, plug: PlugKey, locked: bool) -> None:
        """Plug のロック状態を設定する。"""
        cur = self._attr(plug)
        if cur is not None:
            cur.is_locked = locked

    def materialize(self, plug: PlugKey) -> None:
        """ゴースト array 要素を実体化する（要素テンプレートを複製して追加）。"""
        uuid, index_path = plug
        parent = self._attr((uuid, index_path[:-1]))
        index = index_path[-1]
        if (
            parent is not None
            and parent.is_array
            and parent.elem_proto is not None
            and index not in parent.elements
        ):
            proto = parent.elem_proto
            parent.elements[index] = replace(
                proto, display_name=f"{proto.display_name}[{index}]"
            )

    def display_color(self, name: str) -> tuple[float, float, float] | None:
        """名前付き表示色の RGB を返す（未設定は None・``displayRGBColor`` 模擬）。"""
        return self._display_colors.get(name)

    def undo_chunk(self) -> contextlib.AbstractContextManager[None]:
        """no-op の context manager を返す（Fake は undo を持たない）。"""
        return contextlib.nullcontext()


# ---- サンプルシーン（テスト/検証用フィクスチャ） ----
SAMPLE_A = NodeId(uuid="uuidA", path="|grpA|ctrlA")
SAMPLE_B = NodeId(uuid="uuidB", path="|jointB")


def _translate() -> _Attr:
    """translate（compound double3・子 tx/ty/tz double）を作る。"""
    return _Attr(
        display_name="translate",
        api_type="kNumericAttribute",
        sub_type="k3Double",
        is_compound=True,
        children=[
            _Attr(display_name="translateX", api_type="kDoubleLinearAttribute"),
            _Attr(display_name="translateY", api_type="kDoubleLinearAttribute"),
            _Attr(display_name="translateZ", api_type="kDoubleLinearAttribute"),
        ],
    )


def build_sample_maya_backend() -> FakeMayaBackend:
    """実機相当のサンプルを持つ ``FakeMayaBackend`` を作る（MAYA_PLAN §5）。

    nodeA: translate(double3) / visibility(bool) / inputMatrix(matrix array・[0,2])
    / lockedAttr(double・ロック・extra=ユーザー定義) / msg(message)。
    nodeB: translate(double3)。
    接続: nodeA.translateX → nodeB.translateX。選択: nodeA。

    Returns:
        構築済み ``FakeMayaBackend``。
    """
    matrix_proto = _Attr(
        display_name="inputMatrix", api_type="kTypedAttribute", sub_type="kMatrix"
    )
    input_matrix = _Attr(
        display_name="inputMatrix",
        api_type="kTypedAttribute",
        sub_type="kMatrix",
        is_array=True,
        elements={
            0: replace(matrix_proto, display_name="inputMatrix[0]"),
            2: replace(matrix_proto, display_name="inputMatrix[2]"),
        },
        elem_proto=matrix_proto,
    )
    backend = FakeMayaBackend()
    backend.add_node(
        SAMPLE_A.uuid,
        SAMPLE_A.path,
        [
            _translate(),
            _Attr(
                display_name="visibility",
                api_type="kNumericAttribute",
                sub_type="kBoolean",
            ),
            input_matrix,
            _Attr(
                display_name="lockedAttr",
                api_type="kDoubleLinearAttribute",
                is_locked=True,
                is_user_defined=True,  # extra（addAttr 相当）= listAttr(ud=True)
                value=5.0,
            ),
            _Attr(display_name="message", api_type="kMessageAttribute"),
        ],
    )
    backend.add_node(SAMPLE_B.uuid, SAMPLE_B.path, [_translate()])
    # nodeA.translateX (0,0) → nodeB.translateX (0,0)
    backend.add_connection((SAMPLE_A.uuid, (0, 0)), (SAMPLE_B.uuid, (0, 0)))
    backend.set_selected([SAMPLE_A.uuid])
    # Node Editor の Attribute Types 色（実機採取値・maya_color_probe.py 由来）。
    for name, rgb in (
        ("nodeEditorNumericAttribute", (0.4784, 0.6, 0.4196)),
        ("nodeEditorBooleanAttribute", (0.851, 0.7098, 0.5529)),
        ("nodeEditorMatrixAttribute", (0.3137, 0.4902, 0.549)),
        ("nodeEditorColorAttribute", (0.9098, 0.0, 0.0)),
        ("nodeEditorMultiAmbiguousAttribute", (0.0, 0.0, 0.0)),
    ):
        backend.set_display_color(name, rgb)
    return backend
