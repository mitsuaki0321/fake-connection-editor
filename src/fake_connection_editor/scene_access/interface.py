"""SceneAccess の抽象IFとデータ型（master §2）。

シーン（Maya またはインメモリの Fake）から属性ツリー・接続を読み書きするための
最小IFを定義する。Core はこの抽象IF（``SceneAccess`` Protocol）と、ここで定義する
正規化済みデータ型（``NodeId`` / ``PlugId`` / ``AttrMeta`` / ``Connections``）にのみ
依存し、生の ``MPlug`` / ``MObject`` 等の Maya オブジェクトを境界外に出さない
（master §1.3 / §10.4）。

シーン読み書きに必要な最小IFのみを公開する（型の正規化詳細は master §14）。
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol

# 正規化済み型タグ（最小集合。全量は master §14）。
# 例: "double","float","int","bool","double3","float3","matrix","message","data"
# matrix unwrap 等の正規化は SceneAccess 実装が吸収し、Core は文字列タグだけ扱う。
TypeTag = str


@dataclass(frozen=True)
class NodeId:
    """ノードの同一性と表示名を担う識別子。

    同一性は ``uuid`` のみで判定する（``path`` はリネーム等で変わるため除外）。

    Attributes:
        uuid: 同一性の基準（不変）。
        path: 表示用フルパス（minimal unique path の素材。同一性には使わない）。
    """

    uuid: str
    path: str = field(compare=False)


@dataclass(frozen=True)
class PlugId:
    """ノード内の plug（属性）の識別子。

    同一性は ``node``（uuid）と ``index_path`` で判定する。``index_path`` は
    compound の子 / array の要素を親からのインデックス列でたどる
    （leaf 名に頼らない。master §10.1 の API 罠回避）。

    Attributes:
        node: 所属ノード。
        index_path: 親からのインデックス列（例: translate.translateY = (0, 1)）。
    """

    node: NodeId
    index_path: tuple[int, ...]


@dataclass(frozen=True)
class AttrMeta:
    """属性1つ分の正規化済みメタデータ（master §2.1）。

    列挙系IF（``list_root_attributes`` / ``list_children``）の戻り値要素。
    旧案の ``get_plug_meta`` はこの ``AttrMeta`` に統合済み（master §15）。

    Attributes:
        plug: この属性の ``PlugId``。
        display_name: 表示名（longName・例: "translateX", "inputMatrix[0]"）。
        short_name: 短縮名（shortName・例: "tx"）。表示名切替用（空なら long と同じ）。
        type_tag: 正規化済み型タグ。
        is_array: array（マルチ属性）か。
        is_compound: compound（子を持つ複合属性）か。
        is_readable: 読み取り可（出力＝source になれるか・master §4.3/§6）。
            Maya の ``MFnAttribute.isReadable``。型とは独立した属性メタ。
        is_writable: 書き込み可（入力＝destination になれるか・master §4.3/§6）。
            Maya の ``MFnAttribute.isWritable``。ポート有無は readable or writable。
        is_keyable: キー可能か（フィルタ Non-Keyable 用。master §9）。
        has_children: 子を持つか（遅延展開の「展開可能か」判定）。
        is_locked: ロックされているか（列挙時のスナップショット。接続/値コピーは
            非 force だと弾かれる。force 時は一時解除→復元・master §5.4）。実行時の
            最新状態は ``SceneAccess.is_locked`` で取る。
        is_user_defined: ユーザー定義（extra/dynamic）属性か（``addAttr`` で足した
            属性・``cmds.listAttr(ud=True)`` 相当）。Show Extra Attribute Only 用。
        existing_indices: array のみ。``getExistingArrayAttributeIndices()`` 由来の
            既存インデックス列（ゴースト算出 C4 の入力。master §5.6/§10.2）。
            array でない場合は ``None``。空 array は ``()``。
    """

    plug: PlugId
    display_name: str
    type_tag: TypeTag
    short_name: str = ""
    is_array: bool = False
    is_compound: bool = False
    is_readable: bool = True
    is_writable: bool = True
    is_keyable: bool = True
    has_children: bool = False
    is_locked: bool = False
    is_user_defined: bool = False
    existing_indices: tuple[int, ...] | None = None


@dataclass(frozen=True)
class Connections:
    """ある plug の接続状態（master §2.1）。

    子孫の接続は集約しない（その plug 自身の接続のみ）。

    Attributes:
        sources: この plug を destination とする source plug 列（入力。通常 0/1 本）。
        destinations: この plug を source とする destination plug 列（出力。0..N 本）。
    """

    sources: tuple[PlugId, ...] = ()
    destinations: tuple[PlugId, ...] = ()


class SceneAccess(Protocol):
    """シーンアクセスの抽象IF（master §2.3）。

    Core はこの抽象IFのみに依存する。実装は ``FakeSceneAccess``（インメモリ）と
    ``MayaSceneAccess``（本番）の2つを差し替える。読み取りは undo に乗せず、
    書き込み（connect/disconnect）は 1 アクション = 1 undo チャンク（master §2.2）。
    """

    def list_root_attributes(self, node: NodeId) -> list[AttrMeta]:
        """ノード直下のトップレベル属性メタ列を返す（遅延展開: 直下のみ）。

        Args:
            node: 対象ノード。

        Returns:
            トップレベル属性の ``AttrMeta`` 列。
        """
        ...

    def list_children(self, plug: PlugId) -> list[AttrMeta]:
        """Compound の子 / array の既存要素の直下メタ列を返す（遅延展開: 直下のみ）。

        ゴースト行（空きインデックス）は含めない。ゴーストは Core の C4 が
        ``existing_indices`` から算出する（master §5.6/§10.2）。

        Args:
            plug: 親 plug。

        Returns:
            直下の子属性の ``AttrMeta`` 列。
        """
        ...

    def get_connections(self, plug: PlugId) -> Connections:
        """Plug 自身の接続状態を返す（子孫は集約しない）。

        Args:
            plug: 対象 plug。

        Returns:
            ``sources`` / ``destinations`` を持つ ``Connections``。
        """
        ...

    def list_node_connections(self, node: NodeId) -> list[tuple[PlugId, PlugId]]:
        """外向き接続（node が source）の全 (src, dst) を一括列挙して返す。

        ``connection_pairs`` の高速化用。全 plug を 1 つずつ ``get_connections`` で
        照会すると ``O(全 plug)`` の Maya 照会になり重い（属性数千で数百 ms）。実機は
        ``MFnDependencyNode.getConnections()`` で**接続のある plug だけ**を一括取得して
        ``O(ノード)`` に落とす。両端のロード判定は呼び出し側（ViewModel）が行う。

        Args:
            node: 対象ノード。

        Returns:
            ``(source plug, destination plug)`` の列（src は ``node`` 上の plug）。
        """
        ...

    def connected_plugs(self, node: NodeId) -> set[PlugId]:
        """接続を持つ plug の集合を返す（node 上・入出力どちらでも）。

        ``{p : その plug 自身に接続がある}``。connected-only フィルタが全 plug に
        ``get_connections`` を呼ぶ O(全 plug) の OpenMaya 照会を避け、一度の列挙
        （実機 ``MFnDependencyNode.getConnections()``）で接続済み plug 集合を作る。
        相手端点のロード有無は問わない（接続が 1 本でもあれば含む）。

        Args:
            node: 対象ノード。

        Returns:
            接続を持つ plug の ``PlugId`` 集合。
        """
        ...

    def connect(self, src: PlugId, dst: PlugId, force: bool = False) -> None:
        """Src を source、dst を destination として接続する。

        Args:
            src: source 側 plug。
            dst: destination 側 plug。
            force: True なら dst の既存入力接続を置き換える（master §5.4）。
        """
        ...

    def disconnect(self, src: PlugId, dst: PlugId) -> None:
        """Src → dst の接続を切断する。

        Args:
            src: source 側 plug。
            dst: destination 側 plug。
        """
        ...

    def get_value(self, plug: PlugId):
        """Plug の現在値を返す（値コピー元・master §5.3）。

        Maya 実装は ``cmds.getAttr(plug)`` 相当。型は属性に依存する（数値/真偽/
        タプル等）。Core は値の中身を解釈せず、コピー元→先へ透過的に渡すだけ。

        Args:
            plug: 対象 plug。

        Returns:
            Plug の現在値（型は属性依存）。
        """
        ...

    def set_value(self, plug: PlugId, value) -> None:
        """Plug に値を設定する（値コピー先・master §5.3）。

        Maya 実装は ``cmds.setAttr(plug, value)`` 相当。ロック解除/復元は呼び出し側
        （ViewModel）が force 時に行う（接続と同じ方針・master §5.4）。

        Args:
            plug: 対象 plug。
            value: 設定する値（``get_value`` で得た値）。
        """
        ...

    def is_locked(self, plug: PlugId) -> bool:
        """Plug が現在ロックされているか返す（C1 判定 / force 復元用・master §5.4）。

        Maya 実装は ``cmds.getAttr(plug, lock=True)`` 相当。Fake はフラグを返す。

        Args:
            plug: 対象 plug。

        Returns:
            ロックされていれば True。
        """
        ...

    def set_locked(self, plug: PlugId, locked: bool) -> None:
        """Plug のロック状態を設定する（force の一時解除→復元用・master §5.4）。

        Maya 実装は ``cmds.setAttr(plug, lock=locked)`` 相当。force 接続/値コピーは
        ロックを一旦外し、処理後に元の状態へ戻す（ViewModel が orchestrate する）。

        Args:
            plug: 対象 plug。
            locked: 設定するロック状態。
        """
        ...

    def materialize_array_element(self, plug: PlugId) -> None:
        """ゴースト行の array 要素を実体化する（master §5.6・接続前に呼ぶ）。

        ゴースト（実在しない array インデックス）に接続する直前、当該要素を実在化
        する。Maya 実装は ``cmds`` で要素を生成する書き込み経路（§14 次フェーズ）。
        Fake はインメモリで ``existing_indices`` と子メタを更新する。既存要素なら
        何もしない。

        Args:
            plug: 実体化する array 要素の ``PlugId``。
        """
        ...

    def get_selected_nodes(self) -> list[NodeId]:
        """現在シーンで選択中のノード列を返す（Load/Add ボタン用・master §3.2）。

        Maya 実装は ``cmds.ls(selection=True)`` 相当。Fake 実装は設定済みの選択を
        返す。

        Returns:
            選択中ノードの ``NodeId`` 列（順序は選択順）。
        """
        ...

    def set_selection(self, nodes: list[NodeId]) -> None:
        """シーンの選択を置き換える（Maya は ``cmds.select``・dev ピッカー用）。

        Args:
            nodes: 選択するノード列。
        """
        ...

    def undo_chunk(self) -> AbstractContextManager[None]:
        """1 ユーザーアクションを 1 undo チャンクにまとめる context manager を返す。

        ViewModel は接続/切断/値コピー等の書き込みアクション全体をこの ``with`` で
        囲む。複数の cmds（force の解除→接続→復元 / 複数線の一括切断など）を 1 回の
        undo で戻せるようにする（master §2.2「1 アクション = 1 undo チャンク」）。
        Maya 実装は ``undoInfo(openChunk/closeChunk)``、Fake は no-op。

        Returns:
            ``with`` で使える context manager。
        """
        ...

    def get_attribute_type_colors(self) -> dict[str, tuple[float, float, float]]:
        """型分類ごとのポート色（RGB 0.0〜1.0）を返す（型色・master §4.2）。

        キーは分類のプレーン文字列 ``"numeric"`` / ``"bool"`` / ``"matrix"`` /
        ``"color"`` / ``"data"``（= ``core.TypeCategory.name`` の小文字）。SceneAccess
        は core に依存しないため文字列契約で渡し、UI 側で ``TypeCategory`` へ結合する。
        Maya 実装は Color Settings（Node Editor → Attribute Types）から読む。色設定を
        持たない実装（Fake）は空 dict を返してよい（UI は既定の暫定色を使う）。欠けた
        キーも UI 既定にフォールバックする。

        Returns:
            分類文字列 → ``(r, g, b)``（各 0.0〜1.0）。未設定なら空 dict。
        """
        ...
