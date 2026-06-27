"""SceneAccess の本番実装 ``MayaSceneAccess``（MAYA_PLAN §3）。

注入された ``MayaBackend`` 越しに Maya プリミティブを呼び、``SceneAccess`` Protocol
（§2.3 の 12 メソッド）を満たす。歩行（index_path で plug を下る）・型タグ正規化・
接続端点の ``PlugId`` 化はこのクラスの責務で、Maya 非依存にテストできる（§11.5）。
``cmds`` / ``OpenMaya`` は import しない（backend が吸収）。
"""

from __future__ import annotations

from logging import getLogger

from .interface import AttrMeta, Connections, NodeId, PlugId, TypeTag
from .maya_backend import MayaBackend, PlugHandle, PlugKey, RawAttr

logger = getLogger(__name__)

# 数値属性の単位トークン → 正規化済み型タグ（最小集合。全量は §M2 で実機採取）。
_NUMERIC_TAGS = {
    "kBoolean": "bool",
    "kByte": "int",
    "kChar": "int",
    "kShort": "int",
    "kInt": "int",
    "kLong": "int",
    "kInt64": "int",
    "kFloat": "float",
    "kDouble": "double",
    "k2Short": "int2",
    "k3Short": "int3",
    "k2Int": "int2",
    "k3Int": "int3",
    "k2Long": "int2",
    "k3Long": "int3",
    "k2Float": "float2",
    "k3Float": "float3",
    "k2Double": "double2",
    "k3Double": "double3",
    "k4Double": "double4",
}

# 単純対応の属性種別トークン → 型タグ（linear/angle/time は double 系へ寄せる）。
_API_TAGS = {
    "kDoubleLinearAttribute": "double",
    "kFloatLinearAttribute": "float",
    "kDoubleAngleAttribute": "double",
    "kFloatAngleAttribute": "float",
    "kTimeAttribute": "double",
    "kMatrixAttribute": "matrix",
    "kFloatMatrixAttribute": "matrix",
    "kMessageAttribute": "message",
    "kEnumAttribute": "int",
}

# kTypedAttribute の inner data 種別トークン → 型タグ。
_TYPED_TAGS = {
    "kMatrix": "matrix",
    "kFloatMatrix": "matrix",
    "kString": "string",
    "kStringArray": "stringArray",
}

# 型分類（プレーン文字列・core 非依存）→ Color Settings の表示色キー（master §4.2）。
# 本ツールは 5 分類に畳むため、Node Editor の細分キーから代表を 1 つ当てる。
# data は Maya では黒（kMultiAmbiguous）。暗背景で見えないため UI 側で明度を底上げする。
_TYPE_COLOR_KEYS = {
    "numeric": "nodeEditorNumericAttribute",
    "bool": "nodeEditorBooleanAttribute",
    "matrix": "nodeEditorMatrixAttribute",
    "color": "nodeEditorColorAttribute",
    "data": "nodeEditorMultiAmbiguousAttribute",
}


def normalize_type(api_type: str, sub_type: str = "") -> TypeTag:
    """Maya の生の型トークンを正規化済み型タグへ写す（純粋関数・MAYA_PLAN §4）。

    matrix は専用 attr（``kMatrixAttribute``）でも typed の inner（``kTypedAttribute`` +
    ``kMatrix``）でも ``"matrix"`` に統一（unwrap・MAYA_PLAN §6）。未知は ``"data"``。
    最小集合のみ対応し、全量は §M2 で実機採取して拡張する。

    Args:
        api_type: 属性種別トークン（``RawAttr.api_type``）。
        sub_type: 詳細トークン（数値単位 / typed inner data）。

    Returns:
        正規化済み型タグ。
    """
    if api_type == "kNumericAttribute":
        return _NUMERIC_TAGS.get(sub_type, "double")
    if api_type == "kTypedAttribute":
        return _TYPED_TAGS.get(sub_type, "data")
    if api_type in _API_TAGS:
        return _API_TAGS[api_type]
    if api_type == "kCompoundAttribute":
        return "compound"
    return "data"


class MayaSceneAccess:
    """``SceneAccess`` の本番実装（``MayaBackend`` を注入）。

    UI/Core/ViewModel は本クラスを ``FakeSceneAccess`` と差し替えるだけで使える
    （依存注入・master §13 原則3）。
    """

    def __init__(self, backend: MayaBackend) -> None:
        """MayaSceneAccess を生成する。

        Args:
            backend: 注入する Maya プリミティブ境界（Fake / Real）。
        """
        self._backend = backend

    # ---- 読み取り（遅延展開・OpenMaya ベース） ----
    def list_root_attributes(self, node: NodeId) -> list[AttrMeta]:
        """ノード直下のトップレベル属性メタ列を返す（直下のみ・master §2.1）。"""
        handle = self._backend.node_handle(node.uuid)
        if handle is None:
            return []
        return [
            self._to_meta(node, plug) for plug in self._backend.root_attr_plugs(handle)
        ]

    def list_children(self, plug: PlugId) -> list[AttrMeta]:
        """Compound の子 / array の既存要素の直下メタ列を返す（ゴーストは含めない）。"""
        handle = self._resolve(plug)
        if handle is None:
            return []
        raw = self._backend.raw_attr(handle)
        if raw.is_array:
            return [
                self._to_meta(plug.node, self._backend.element_plug(handle, index))
                for index in self._backend.array_existing_indices(handle)
            ]
        return [
            self._to_meta(plug.node, child)
            for child in self._backend.child_plugs(handle)
        ]

    def get_connections(self, plug: PlugId) -> Connections:
        """Plug 自身の接続状態を返す（子孫は集約しない・master §2.1）。"""
        handle = self._resolve(plug)
        if handle is None:
            return Connections()
        sources = tuple(
            self._to_plug_id(plug.node, s) for s in self._backend.plug_sources(handle)
        )
        destinations = tuple(
            self._to_plug_id(plug.node, d)
            for d in self._backend.plug_destinations(handle)
        )
        return Connections(sources=sources, destinations=destinations)

    def list_node_connections(self, node: NodeId) -> list[tuple[PlugId, PlugId]]:
        """外向き接続（node が source）の全 (src, dst) を返す（高速経路）。

        backend の ``node_connections``（接続のある plug だけを一括取得）でハンドル対を
        得て、両端を ``PlugId`` に化かす。全 plug を ``get_connections`` で舐めるより
        Maya 照会が ``O(全 plug)`` → ``O(ノード)`` に減る。
        """
        handle = self._backend.node_handle(node.uuid)
        if handle is None:
            return []
        return [
            (self._to_plug_id(node, src), self._to_plug_id(node, dst))
            for src, dst in self._backend.node_connections(handle)
        ]

    def connected_plugs(self, node: NodeId) -> set[PlugId]:
        """接続を持つ plug の集合を返す（node 上・connected-only 用・高速経路）。

        backend の ``connected_plugs``（接続のある plug だけを一括取得）でハンドル列を
        得て ``PlugId`` 化する。全 plug を ``get_connections`` で舐める O(全 plug) を
        1 ノード 1 列挙に落とす。
        """
        handle = self._backend.node_handle(node.uuid)
        if handle is None:
            return set()
        return {
            self._to_plug_id(node, plug)
            for plug in self._backend.connected_plugs(handle)
        }

    # ---- 書き込み（cmds ベース・1 アクション = 1 undo チャンク） ----
    def connect(self, src: PlugId, dst: PlugId, force: bool = False) -> None:
        """Src を source、dst を destination として接続する（master §2.2/§5.4）。"""
        self._backend.connect(self._key(src), self._key(dst), force)

    def disconnect(self, src: PlugId, dst: PlugId) -> None:
        """Src → dst の接続を切断する。"""
        self._backend.disconnect(self._key(src), self._key(dst))

    def get_value(self, plug: PlugId):
        """Plug の現在値を返す（値コピー元・master §5.3）。"""
        return self._backend.get_value(self._key(plug))

    def set_value(self, plug: PlugId, value) -> None:
        """Plug に値を設定する（値コピー先・master §5.3）。"""
        self._backend.set_value(self._key(plug), value)

    def is_locked(self, plug: PlugId) -> bool:
        """Plug が現在ロックされているか返す（master §5.4）。"""
        return self._backend.is_locked(self._key(plug))

    def set_locked(self, plug: PlugId, locked: bool) -> None:
        """Plug のロック状態を設定する（force の一時解除→復元用・master §5.4）。"""
        self._backend.set_locked(self._key(plug), locked)

    def materialize_array_element(self, plug: PlugId) -> None:
        """ゴースト array 要素を実体化する（接続前に呼ぶ・master §5.6）。"""
        self._backend.materialize(self._key(plug))

    # ---- 選択（Load/Add・master §3.2） ----
    def get_selected_nodes(self) -> list[NodeId]:
        """現在選択中のノード列を返す（``ls(sl=True)`` 相当）。"""
        return [
            NodeId(uuid=self._backend.node_uuid(h), path=self._backend.node_path(h))
            for h in self._backend.selected_nodes()
        ]

    def set_selection(self, nodes: list[NodeId]) -> None:
        """シーンの選択を置き換える（``select`` 相当）。"""
        handles = [self._backend.node_handle(n.uuid) for n in nodes]
        self._backend.select([h for h in handles if h is not None])

    def undo_chunk(self):
        """1 アクション = 1 undo チャンクの cm を返す（backend へ委譲・§2.2）。"""
        return self._backend.undo_chunk()

    def get_attribute_type_colors(self) -> dict[str, tuple[float, float, float]]:
        """型分類ごとの色を Color Settings から読む（backend 経由・master §4.2）。

        ``_TYPE_COLOR_KEYS`` の各分類について backend の ``display_color`` を引き、
        取れたものだけを ``{分類文字列: (r,g,b)}`` で返す（未設定キーは UI 既定へ）。

        Returns:
            分類文字列 → ``(r, g, b)``（0.0〜1.0）。
        """
        colors: dict[str, tuple[float, float, float]] = {}
        for category, key in _TYPE_COLOR_KEYS.items():
            rgb = self._backend.display_color(key)
            if rgb is not None:
                colors[category] = rgb
        return colors

    # ---- 内部ヘルパ ----
    def _to_meta(self, node: NodeId, plug: PlugHandle) -> AttrMeta:
        """Backend の生事実（``RawAttr``）を正規化済み ``AttrMeta`` に組む。

        型タグを正規化し、array なら ``existing_indices`` を付与（C4 ゴーストの入力）。

        Args:
            node: 所属ノード（``PlugId.node`` に使う・path 込みを再利用）。
            plug: 対象 plug ハンドル。

        Returns:
            正規化済み ``AttrMeta``。
        """
        raw: RawAttr = self._backend.raw_attr(plug)
        _, index_path = self._backend.plug_key(plug)
        existing = self._backend.array_existing_indices(plug) if raw.is_array else None
        return AttrMeta(
            plug=PlugId(node=node, index_path=index_path),
            display_name=raw.display_name,
            short_name=raw.short_name,
            type_tag=normalize_type(raw.api_type, raw.sub_type),
            is_array=raw.is_array,
            is_compound=raw.is_compound,
            is_readable=raw.is_readable,
            is_writable=raw.is_writable,
            is_keyable=raw.is_keyable,
            has_children=raw.has_children,
            is_locked=raw.is_locked,
            is_user_defined=raw.is_user_defined,
            is_hidden=raw.is_hidden,
            existing_indices=existing,
        )

    def _to_plug_id(self, ref_node: NodeId, plug: PlugHandle) -> PlugId:
        """接続端点ハンドルを ``PlugId`` に化かす（端点が同ノードなら path を再利用）。

        backend の ``plug_key`` から uuid と index_path を得る。端点が ``ref_node`` と
        同じノードなら path 込みの ``ref_node`` を使い、別ノードなら uuid のみで
        ``NodeId`` を作る（path はここでは取得しない。同一性は uuid で足りる）。
        """
        uuid, index_path = self._backend.plug_key(plug)
        if uuid == ref_node.uuid:
            owner = ref_node
        else:
            handle = self._backend.node_handle(uuid)
            path = self._backend.node_path(handle) if handle is not None else uuid
            owner = NodeId(uuid=uuid, path=path)
        return PlugId(node=owner, index_path=index_path)

    def _resolve(self, plug: PlugId) -> PlugHandle | None:
        """``PlugId`` の index_path を root から下って plug ハンドルを解決する。

        leaf 名は使わず、compound は位置インデックス・array は論理インデックスで下る
        （master §10.1 / MAYA_PLAN §6 の leaf 名回避）。

        Args:
            plug: 解決する ``PlugId``。

        Returns:
            plug ハンドル。解決できなければ ``None``。
        """
        node = self._backend.node_handle(plug.node.uuid)
        if node is None or not plug.index_path:
            return None
        roots = self._backend.root_attr_plugs(node)
        first = plug.index_path[0]
        if first >= len(roots):
            return None
        handle = roots[first]
        for index in plug.index_path[1:]:
            raw = self._backend.raw_attr(handle)
            if raw.is_array:
                handle = self._backend.element_plug(handle, index)
            elif raw.is_compound:
                children = self._backend.child_plugs(handle)
                if index >= len(children):
                    return None
                handle = children[index]
            else:
                return None
            if handle is None:
                return None
        return handle

    @staticmethod
    def _key(plug: PlugId) -> PlugKey:
        """``PlugId`` を書き込み系 backend 用の plain key に落とす。"""
        return (plug.node.uuid, plug.index_path)
