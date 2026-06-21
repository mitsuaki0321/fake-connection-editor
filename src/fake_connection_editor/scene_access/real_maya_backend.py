"""``MayaBackend`` の実 Maya 実装（§M2・**Maya GUI で要検証**）。

``maya.cmds`` / ``maya.api.OpenMaya`` を直接使う唯一のモジュール。読み取りは OpenMaya
（undo に乗せない）、書き込みは cmds（1 アクション = 1 undo チャンク）で行う
（master §2.1/§2.2 / MAYA_PLAN §6）。**Maya 内でのみ import 可能**（パッケージの
``__init__`` からは読み込まない＝Maya 非依存テストを壊さない）。

注意（重要）:
    本モジュールは Claude の環境では実行・検証できない。OpenMaya のバージョン差
    （``MFnAttribute.parent`` の有無・列挙トークン名など）があるので、
    **Maya GUI で ``samples/maya_smoke.py`` を走らせて各メソッドの結果を確認し、
    ずれがあれば修正する前提の初版**である（MAYA_PLAN §M2）。

ハンドル:
    NodeHandle = ``om.MObject`` / PlugHandle = ``om.MPlug``。MayaSceneAccess は
    これらを不透明に扱い、``plug_key`` でのみ plain key を取り出す。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from logging import getLogger
from typing import Any

import maya.api.OpenMaya as om  # noqa: N813  (Maya 慣習の別名)
import maya.cmds as cmds

from .maya_backend import PlugKey, RawAttr

logger = getLogger(__name__)

# ``setAttr`` が ``-type`` フラグを要求する typed attribute の型名。
# matrix / string / 各種配列など。単純数値・double3 等は不要（従来パス）。
_TYPED_SETATTR: frozenset[str] = frozenset(
    {
        "matrix",
        "string",
        "stringArray",
        "doubleArray",
        "Int32Array",
        "vectorArray",
        "pointArray",
        "matrixArray",
    }
)


def _numeric_token_map() -> dict[int, str]:
    """``MFnNumericData`` の型 enum → ``normalize_type`` 用トークンの対応を作る。

    バージョンで欠落し得る enum は ``getattr`` で除外する。
    """
    table: dict[int, str] = {}
    for name in (
        "kBoolean",
        "kByte",
        "kChar",
        "kShort",
        "k2Short",
        "k3Short",
        "kInt",
        "kLong",
        "k2Int",
        "k2Long",
        "k3Int",
        "k3Long",
        "kFloat",
        "k2Float",
        "k3Float",
        "kDouble",
        "k2Double",
        "k3Double",
        "k4Double",
    ):
        value = getattr(om.MFnNumericData, name, None)
        if value is not None:
            table[value] = name
    return table


def _data_token_map() -> dict[int, str]:
    """``MFnData`` の型 enum → typed inner data トークンの対応を作る。

    ``kFloatMatrix`` 等はバージョンで欠落し得るので ``getattr`` で除外する
    （MAYA_PLAN §6）。
    """
    table: dict[int, str] = {}
    for name in (
        "kMatrix",
        "kFloatMatrix",
        "kString",
        "kStringArray",
        "kMesh",
        "kNurbsCurve",
        "kNurbsSurface",
        "kIntArray",
        "kDoubleArray",
        "kPointArray",
        "kVectorArray",
        "kComponentList",
    ):
        value = getattr(om.MFnData, name, None)
        if value is not None:
            table[value] = name
    return table


_NUMERIC_TOKENS = _numeric_token_map()
_DATA_TOKENS = _data_token_map()


class RealMayaBackend:
    """``MayaBackend`` の実 Maya 実装（Maya GUI 専用・§M2）。"""

    # ---- 解決 ----
    def node_handle(self, uuid: str) -> om.MObject | None:
        """Uuid からノード ``MObject`` を返す（無ければ None）。"""
        names = cmds.ls(uuid) or []
        if not names:
            return None
        sel = om.MSelectionList()
        sel.add(names[0])
        return sel.getDependNode(0)

    def node_uuid(self, node: om.MObject) -> str:
        """ノードの uuid 文字列を返す。"""
        return om.MFnDependencyNode(node).uuid().asString()

    def node_path(self, node: om.MObject) -> str:
        """ノードの表示用フルパスを返す（DAG はフルパス・DG は名前）。"""
        if node.hasFn(om.MFn.kDagNode):
            return om.MFnDagNode(node).fullPathName()
        return om.MFnDependencyNode(node).name()

    def selected_nodes(self) -> list[om.MObject]:
        """現在選択中のノード ``MObject`` 列を返す。"""
        sel = om.MGlobal.getActiveSelectionList()
        nodes: list[om.MObject] = []
        for i in range(sel.length()):
            try:
                nodes.append(sel.getDependNode(i))
            except (RuntimeError, TypeError):
                continue  # コンポーネント等はスキップ
        return nodes

    def select(self, nodes: list[om.MObject]) -> None:
        """選択を置き換える（``cmds.select`` ・undo 維持）。"""
        names = [self.node_path(n) for n in nodes]
        cmds.select(names, replace=True)

    # ---- 歩行（読み・OpenMaya） ----
    def _root_attrs(self, node: om.MObject) -> list[om.MObject]:
        """ノードのトップレベル属性（compound の子でないもの）の MObject 列を返す。

        ``attributeCount``/``attribute`` を走り、``MFnAttribute.parent`` が null の
        ものだけを採用する（compound の子は除外）。順序は定義順。
        """
        fn = om.MFnDependencyNode(node)
        roots: list[om.MObject] = []
        for i in range(fn.attributeCount()):
            attr = fn.attribute(i)
            if om.MFnAttribute(attr).parent.isNull():
                roots.append(attr)
        return roots

    def root_attr_plugs(self, node: om.MObject) -> list[om.MPlug]:
        """ノード直下のトップレベル属性 plug 列を返す（位置順）。"""
        return [om.MPlug(node, attr) for attr in self._root_attrs(node)]

    def child_plugs(self, plug: om.MPlug) -> list[om.MPlug]:
        """Compound の子 plug 列を返す（位置順）。"""
        if not plug.isCompound:
            return []
        return [plug.child(i) for i in range(plug.numChildren())]

    def array_existing_indices(self, plug: om.MPlug) -> tuple[int, ...]:
        """Array の既存論理インデックス列を返す（existing array indices・§6）。"""
        if not plug.isArray:
            return ()
        return tuple(plug.getExistingArrayAttributeIndices())

    def element_plug(self, plug: om.MPlug, logical_index: int) -> om.MPlug:
        """Array の論理インデックス要素 plug を返す（``elementByLogicalIndex``）。"""
        return plug.elementByLogicalIndex(logical_index)

    # ---- 生事実・同一性 ----
    def raw_attr(self, plug: om.MPlug) -> RawAttr:
        """Plug の正規化前メタ（``RawAttr``）を返す。

        型は属性の MFn で判別し、``normalize_type`` が解釈できるトークン
        （api_type / sub_type）に写す（matrix unwrap 等は normalize 側・MAYA_PLAN §4）。
        """
        attr = plug.attribute()
        mfn = om.MFnAttribute(attr)
        api_type, sub_type = self._type_tokens(attr)
        display = plug.partialName(
            includeNodeName=False, useFullAttributePath=False, useLongNames=True
        )
        short = plug.partialName(
            includeNodeName=False, useFullAttributePath=False, useLongNames=False
        )
        return RawAttr(
            display_name=display,
            short_name=short,
            api_type=api_type,
            sub_type=sub_type,
            is_array=plug.isArray,
            is_compound=plug.isCompound,
            is_keyable=plug.isKeyable,
            is_readable=mfn.readable,
            is_writable=mfn.writable,
            has_children=plug.isCompound or plug.isArray,
            is_locked=plug.isLocked,
            is_user_defined=plug.isDynamic,  # listAttr(ud=True) 相当（実機で要確認）
        )

    @staticmethod
    def _type_tokens(attr: om.MObject) -> tuple[str, str]:
        """属性 MObject から (api_type, sub_type) トークンを判別する。

        numeric を compound より先に見る（numeric compound = translate 等を
        ``kNumericAttribute`` + ``k3Double`` として拾うため）。
        """
        if attr.hasFn(om.MFn.kNumericAttribute):
            ntype = om.MFnNumericAttribute(attr).numericType()
            return "kNumericAttribute", _NUMERIC_TOKENS.get(ntype, "")
        if attr.hasFn(om.MFn.kUnitAttribute):
            unit = om.MFnUnitAttribute(attr).unitType()
            if unit == om.MFnUnitAttribute.kAngle:
                return "kDoubleAngleAttribute", ""
            if unit == om.MFnUnitAttribute.kTime:
                return "kTimeAttribute", ""
            return "kDoubleLinearAttribute", ""  # kDistance
        if attr.hasFn(om.MFn.kTypedAttribute):
            dtype = om.MFnTypedAttribute(attr).attrType()
            return "kTypedAttribute", _DATA_TOKENS.get(dtype, "")
        if attr.hasFn(om.MFn.kMatrixAttribute):
            return "kMatrixAttribute", ""
        if attr.hasFn(om.MFn.kMessageAttribute):
            return "kMessageAttribute", ""
        if attr.hasFn(om.MFn.kEnumAttribute):
            return "kEnumAttribute", ""
        if attr.hasFn(om.MFn.kCompoundAttribute):
            return "kCompoundAttribute", ""
        return attr.apiTypeStr, ""  # 未知 → normalize で data フォールバック

    def plug_key(self, plug: om.MPlug) -> PlugKey:
        """Plug を plain key ``(uuid, index_path)`` に落とす（leaf 名非依存・§6）。

        plug から ``isElement``→``array()`` / ``isChild``→``parent()`` で根まで遡り、
        各段のインデックス（array は論理 index・compound は子の位置）を集める。
        """
        node = plug.node()
        uuid = om.MFnDependencyNode(node).uuid().asString()
        indices: list[int] = []
        current = plug
        while True:
            if current.isElement:
                indices.append(current.logicalIndex())
                current = current.array()
            elif current.isChild:
                parent = current.parent()
                position = next(
                    i for i in range(parent.numChildren()) if parent.child(i) == current
                )
                indices.append(position)
                current = parent
            else:
                indices.append(self._root_position(node, current.attribute()))
                break
        indices.reverse()
        return (uuid, tuple(indices))

    def _root_position(self, node: om.MObject, attr: om.MObject) -> int:
        """トップレベル属性列の中での attr の位置を返す（root_attr_plugs と整合）。"""
        roots = self._root_attrs(node)
        for i, root in enumerate(roots):
            if root == attr:
                return i
        return 0

    # ---- 接続（読み・OpenMaya） ----
    def plug_sources(self, plug: om.MPlug) -> list[om.MPlug]:
        """この plug を destination とする source plug 列を返す（入力）。"""
        return list(plug.connectedTo(True, False))

    def plug_destinations(self, plug: om.MPlug) -> list[om.MPlug]:
        """この plug を source とする destination plug 列を返す（出力）。"""
        return list(plug.connectedTo(False, True))

    def node_connections(self, node: om.MObject) -> list[tuple[om.MPlug, om.MPlug]]:
        """外向き接続（node が source）の全 (src, dst) plug 対を返す（高速経路）。

        ``MFnDependencyNode.getConnections()`` は**接続のある plug だけ**を 1 回で返す
        （全 plug を舐めない）。各 plug の出力（``connectedTo(False, True)``）を取り、
        外向き接続だけを (src=この plug, dst=接続先) として集める。入力（この plug が
        destination）はスキップ＝相手ノード側の外向き列挙で拾われる（二重計上回避）。
        """
        fn = om.MFnDependencyNode(node)
        pairs: list[tuple[om.MPlug, om.MPlug]] = []
        for plug in fn.getConnections():
            for dst in plug.connectedTo(False, True):  # この plug が source の接続
                pairs.append((plug, dst))
        return pairs

    def connected_plugs(self, node: om.MObject) -> list[om.MPlug]:
        """接続を持つ plug 列を返す（node 上・入出力どちらでも・高速経路）。

        ``MFnDependencyNode.getConnections()`` は**接続のある plug だけ**を 1 回で返す
        （入出力どちらも含む）。connected-only フィルタの membership 判定用。
        """
        return list(om.MFnDependencyNode(node).getConnections())

    # ---- 書き込み（cmds・1 アクション = 1 undo チャンク） ----
    def _name(self, key: PlugKey) -> str | None:
        """Plain key を cmds 用の属性名（"node.attr[idx].child"）に解決する。

        index_path を root から下って ``MPlug`` を得て ``MPlug.name()`` を返す
        （cmds は leaf 名でも解決するため接続/設定には十分・MAYA_PLAN §6）。
        """
        uuid, index_path = key
        node = self.node_handle(uuid)
        if node is None or not index_path:
            return None
        roots = self._root_attrs(node)
        if index_path[0] >= len(roots):
            return None
        plug = om.MPlug(node, roots[index_path[0]])
        for index in index_path[1:]:
            if plug.isArray:
                plug = plug.elementByLogicalIndex(index)
            elif plug.isCompound:
                if index >= plug.numChildren():
                    return None
                plug = plug.child(index)
            else:
                return None
        return plug.name()

    def connect(self, src: PlugKey, dst: PlugKey, force: bool) -> None:
        """Src → dst を接続する（``connectAttr(f=force)``）。"""
        s, d = self._name(src), self._name(dst)
        if s is not None and d is not None:
            cmds.connectAttr(s, d, force=force)

    def disconnect(self, src: PlugKey, dst: PlugKey) -> None:
        """Src → dst を切断する（``disconnectAttr``）。

        接続が無い場合 ``disconnectAttr`` は ``RuntimeError`` を投げるため、
        ``isConnected`` で確認してから切る（横断切断で既に消えた線等の防御・問題6）。
        """
        s, d = self._name(src), self._name(dst)
        if s is not None and d is not None and cmds.isConnected(s, d):
            cmds.disconnectAttr(s, d)

    def get_value(self, plug: PlugKey) -> Any:
        """Plug の現在値を返す（``getAttr``）。"""
        name = self._name(plug)
        return cmds.getAttr(name) if name is not None else None

    def set_value(self, plug: PlugKey, value: Any) -> None:
        """Plug に値を設定する（``setAttr``）。

        複合値（tuple/list）は展開して渡す。matrix / string / 各種配列のような typed
        attribute は ``setAttr`` が ``-type`` フラグを要求するため、書き込み先の型を
        ``getAttr(type=True)`` で引き、必要なときだけ ``type=`` を付ける（§M2）。
        """
        name = self._name(plug)
        if name is None:
            return
        attr_type = cmds.getAttr(name, type=True)
        typed = attr_type in _TYPED_SETATTR
        if isinstance(value, (tuple, list)):
            # getAttr(double3) は [(x,y,z)] を返すので 1 段ほどく。
            # matrix は flat な 16 個の float で返るのでそのまま展開する。
            single_nested = len(value) == 1 and isinstance(value[0], (tuple, list))
            flat = value[0] if single_nested else value
            if typed:
                cmds.setAttr(name, *flat, type=attr_type)
            else:
                cmds.setAttr(name, *flat)
        elif typed:
            # string などのスカラー typed。
            cmds.setAttr(name, value, type=attr_type)
        else:
            cmds.setAttr(name, value)

    def is_locked(self, plug: PlugKey) -> bool:
        """Plug が現在ロックされているか返す（``getAttr(lock=True)``）。"""
        name = self._name(plug)
        return bool(cmds.getAttr(name, lock=True)) if name is not None else False

    def set_locked(self, plug: PlugKey, locked: bool) -> None:
        """Plug のロック状態を設定する（``setAttr(lock=)``）。"""
        name = self._name(plug)
        if name is not None:
            cmds.setAttr(name, lock=locked)

    def materialize(self, plug: PlugKey) -> None:
        """ゴースト array 要素を実体化する（接続前に呼ぶ・§5.6）。

        ``elementByLogicalIndex`` で論理要素を参照すると plug が生成される。実体化の
        確実な書き込み経路と undo 粒度は Maya GUI で要確認（§M2）。
        """
        uuid, index_path = plug
        node = self.node_handle(uuid)
        if node is None or len(index_path) < 2:
            return
        parent_key = (uuid, index_path[:-1])
        parent_name = self._name(parent_key)
        if parent_name is not None:
            # 親 array の論理要素を参照して実体化（cmds で touch）。
            cmds.getAttr(f"{parent_name}[{index_path[-1]}]")

    # ---- 表示色（Color Settings・型色読み取り） ----
    def display_color(self, name: str) -> tuple[float, float, float] | None:
        """名前付き表示色の RGB（0.0〜1.0）を返す（``displayRGBColor`` ・§4.2）。

        Node Editor の Attribute Types 色（``nodeEditorNumericAttribute`` 等）を引く。
        未知の名前は ``displayRGBColor`` が例外を投げるため ``None`` で返す。
        """
        try:
            rgb = cmds.displayRGBColor(name, query=True)
        except (RuntimeError, ValueError):
            return None
        if not rgb or len(rgb) < 3:
            return None
        return (float(rgb[0]), float(rgb[1]), float(rgb[2]))

    @contextmanager
    def undo_chunk(self) -> Iterator[None]:
        """書き込みを 1 undo チャンクにまとめる（``undoInfo`` open/close・§2.2）。

        ViewModel の 1 アクション（force の解除→接続→復元 / 複数線の一括切断など）を
        Maya の Undo 1 回で戻せるようにする。
        """
        cmds.undoInfo(openChunk=True)
        try:
            yield
        finally:
            cmds.undoInfo(closeChunk=True)
