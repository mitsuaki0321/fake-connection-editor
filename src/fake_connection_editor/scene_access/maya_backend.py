"""MayaSceneAccess が依存する Maya プリミティブの薄い境界（MAYA_PLAN §2）。

``MayaSceneAccess`` は ``cmds`` / ``OpenMaya`` を直接 import せず、この
``MayaBackend`` Protocol 越しにプリミティブを呼ぶ（master §10.4）。生の ``MPlug`` /
``MObject`` は**不透明ハンドル**として授受し、境界外（``MayaSceneAccess`` の戻り値）には
``PlugId`` / ``AttrMeta`` 等の plain data だけを出す。

粒度は細かい（ハンドルベース）。歩行・existing indices 解釈・型正規化・leaf 名回避は
``MayaSceneAccess`` 側に置き、Maya 非依存でテストできるようにするため（master §11.5）。
実装は ``FakeMayaBackend``（採取値モック）と ``RealMayaBackend``（実 Maya・§M2）。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

# 不透明ハンドル。MayaSceneAccess は中身を解釈せず backend メソッドにのみ渡す
# （RealMayaBackend では MObject / MPlug を内部退避して int 等で返す想定）。
NodeHandle = Any
PlugHandle = Any

# 書き込み系で使う plain key（= PlugId の素材）。backend が再解決して cmds を組む。
PlugKey = tuple[str, tuple[int, ...]]


@dataclass(frozen=True)
class RawAttr:
    """属性1つ分の**正規化前**の生事実（backend が返す・MAYA_PLAN §2）。

    型タグ正規化（matrix unwrap 等）は ``MayaSceneAccess.normalize_type`` が行うため、
    ここでは Maya の生の型トークン（``api_type`` / ``sub_type``）をそのまま運ぶ。

    Attributes:
        display_name: 表示名（longName・例 "translateX"）。backend が用意する
            （leaf 名で良い。同一性は ``plug_key`` の index_path が担う）。
        short_name: 短縮名（shortName・例 "tx"）。表示名切替用（空なら long と同じ）。
        api_type: 属性種別トークン（例 "kNumericAttribute", "kTypedAttribute",
            "kMatrixAttribute", "kMessageAttribute", "kCompoundAttribute",
            "kDoubleLinearAttribute"）。
        sub_type: 詳細トークン。``kNumericAttribute`` の単位（"kDouble", "kBoolean",
            "k3Double" 等）や ``kTypedAttribute`` の inner data 種別（"kMatrix",
            "kString" 等）。無ければ空文字。
        is_array: array（マルチ属性）か。
        is_compound: compound（子を持つ複合属性）か。
        is_keyable: キー可能か（フィルタ Non-Keyable 用）。
        is_readable: 読み取り可（source になれるか）。
        is_writable: 書き込み可（destination になれるか）。
        has_children: 子を持つか（遅延展開の展開可否）。
        is_locked: ロックされているか（列挙時スナップショット）。
        is_user_defined: ユーザー定義（extra/dynamic）属性か（``listAttr(ud=True)``
            相当・フィルタ Show Extra Attribute Only 用）。
    """

    display_name: str
    api_type: str
    short_name: str = ""
    sub_type: str = ""
    is_array: bool = False
    is_compound: bool = False
    is_keyable: bool = True
    is_readable: bool = True
    is_writable: bool = True
    has_children: bool = False
    is_locked: bool = False
    is_user_defined: bool = False


class MayaBackend(Protocol):
    """Maya プリミティブの薄い境界（MayaSceneAccess が依存する唯一の Maya 面）。

    読み（解決/歩行/生事実/接続）はハンドルベースで遅延・直下のみ。書き込みは
    ``PlugKey`` で受け、1 アクション = 1 undo チャンク（master §2.2/§5.4）。
    """

    # ---- 解決（uuid / 選択 → ハンドル） ----
    def node_handle(self, uuid: str) -> NodeHandle | None:
        """Uuid からノードハンドルを返す（無ければ None）。"""
        ...

    def node_uuid(self, node: NodeHandle) -> str:
        """ノードハンドルの uuid を返す。"""
        ...

    def node_path(self, node: NodeHandle) -> str:
        """ノードハンドルの表示用フルパスを返す。"""
        ...

    def selected_nodes(self) -> list[NodeHandle]:
        """現在選択中のノードハンドル列を返す（``ls(sl=True)`` 相当）。"""
        ...

    def select(self, nodes: list[NodeHandle]) -> None:
        """選択を置き換える（``select`` 相当）。"""
        ...

    # ---- 歩行（読み・遅延・直下のみ） ----
    def root_attr_plugs(self, node: NodeHandle) -> list[PlugHandle]:
        """ノード直下のトップレベル属性 plug ハンドル列を返す（位置順）。"""
        ...

    def child_plugs(self, plug: PlugHandle) -> list[PlugHandle]:
        """Compound の子 plug ハンドル列を返す（位置順）。array には使わない。"""
        ...

    def array_existing_indices(self, plug: PlugHandle) -> tuple[int, ...]:
        """Array の既存論理インデックス列を返す（existing array indices）。

        ``getExistingArrayAttributeIndices`` 由来。``numElements`` は未評価 multi で
        過少なため使わない（MAYA_PLAN §6）。
        """
        ...

    def element_plug(self, plug: PlugHandle, logical_index: int) -> PlugHandle:
        """Array の論理インデックス要素 plug を返す（``elementByLogicalIndex``）。"""
        ...

    # ---- 属性の生事実・同一性 ----
    def raw_attr(self, plug: PlugHandle) -> RawAttr:
        """Plug の正規化前メタ（``RawAttr``）を返す。"""
        ...

    def plug_key(self, plug: PlugHandle) -> PlugKey:
        """Plug ハンドルを plain key ``(uuid, index_path)`` に落とす（境界変換点）。"""
        ...

    # ---- 接続（読み） ----
    def plug_sources(self, plug: PlugHandle) -> list[PlugHandle]:
        """この plug を destination とする source plug ハンドル列を返す（入力）。"""
        ...

    def plug_destinations(self, plug: PlugHandle) -> list[PlugHandle]:
        """この plug を source とする destination plug ハンドル列を返す（出力）。"""
        ...

    def node_connections(self, node: NodeHandle) -> list[tuple[PlugHandle, PlugHandle]]:
        """外向き接続（node が source）の全 (src, dst) plug ハンドル対を返す。

        全 plug を舐めず、接続のある plug だけを一括列挙する高速経路
        （``MFnDependencyNode.getConnections()`` 相当・``connection_pairs`` 用）。
        src は ``node`` 上の plug、dst は接続先 plug。
        """
        ...

    def connected_plugs(self, node: NodeHandle) -> list[PlugHandle]:
        """接続を持つ plug ハンドル列を返す（node 上・入出力どちらでも・高速経路）。

        ``MFnDependencyNode.getConnections()`` 相当（接続のある plug だけを一括列挙）。
        connected-only フィルタの membership 判定用。
        """
        ...

    # ---- 書き込み（cmds・1 アクション = 1 undo チャンク） ----
    def connect(self, src: PlugKey, dst: PlugKey, force: bool) -> None:
        """Src → dst を接続する（``connectAttr(f=force)`` 相当）。"""
        ...

    def disconnect(self, src: PlugKey, dst: PlugKey) -> None:
        """Src → dst を切断する（``disconnectAttr`` 相当）。"""
        ...

    def get_value(self, plug: PlugKey) -> Any:
        """Plug の現在値を返す（``getAttr`` 相当）。"""
        ...

    def set_value(self, plug: PlugKey, value: Any) -> None:
        """Plug に値を設定する（``setAttr`` 相当）。"""
        ...

    def is_locked(self, plug: PlugKey) -> bool:
        """Plug が現在ロックされているか返す（``getAttr(lock=True)`` 相当）。"""
        ...

    def set_locked(self, plug: PlugKey, locked: bool) -> None:
        """Plug のロック状態を設定する（``setAttr(lock=)`` 相当）。"""
        ...

    def materialize(self, plug: PlugKey) -> None:
        """ゴースト array 要素を実体化する（接続前に呼ぶ・書込経路は §M2）。"""
        ...

    def undo_chunk(self) -> AbstractContextManager[None]:
        """1 アクションを 1 undo チャンクにまとめる context manager を返す（§2.2）。"""
        ...

    # ---- 表示色（Color Settings・型色読み取り） ----
    def display_color(self, name: str) -> tuple[float, float, float] | None:
        """名前付き表示色の RGB（0.0〜1.0）を返す（``displayRGBColor`` 相当）。

        Node Editor の Attribute Types 色など（例 ``nodeEditorNumericAttribute``）を
        引くのに使う。未知の名前は ``None`` を返す（master §4.2）。
        """
        ...
