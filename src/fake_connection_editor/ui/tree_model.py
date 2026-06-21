"""属性ツリーの Qt モデル（master §7.1 単一ツリー / §4.6 セクション）。

トップレベル = ノード（セクションヘッダ）、その子 = 属性ツリー、という 2 階層の
``QAbstractItemModel``。``EditorViewModel`` が供給する ``NodeId``（セクション）と
``TreeNode``（Core C7・属性）を載せる薄い UI 層。遅延展開（展開可能な行だけ子を
取りに行く）に対応する。トップレベルはノード、ゴースト行（array 空き番号）も
属性ツリーに併合する。
"""

from __future__ import annotations

from ..core import TreeNode
from ..scene_access.interface import NodeId, PlugId
from ..viewmodel import EditorViewModel
from .colors import blend, port_color
from .qt_compat import Qt, QtCore, QtGui

# 接続行の地色 = 背景(Base)へ型色をこの係数で寄せる（モック準拠の視認できる地色）。
# 小さすぎると暗背景で潰れて見えないため、型色がはっきり感じられる強さに置く。
_CONNECTED_BG_K = 0.4


def _is_ancestor(a: PlugId, b: PlugId) -> bool:
    """Plug ``a`` が plug ``b`` の真の祖先か返す（同ノードで index_path が前方一致）。

    Args:
        a: 祖先候補の plug。
        b: 子孫候補の plug。

    Returns:
        ``a`` が ``b`` より浅く、``b`` の index_path の先頭が ``a`` と一致すれば True。
    """
    return (
        a.node == b.node
        and len(a.index_path) < len(b.index_path)
        and b.index_path[: len(a.index_path)] == a.index_path
    )


class _Item:
    """モデル内部のツリーノード（親子参照 + 子の遅延キャッシュ）。

    ``section`` か ``attr`` のどちらか一方を持つ（不可視ルートはどちらも None）。
    """

    __slots__ = ("section", "attr", "parent", "children")

    def __init__(
        self,
        parent: _Item | None,
        section: NodeId | None = None,
        attr: TreeNode | None = None,
    ) -> None:
        self.section = section  # ノードセクション行なら NodeId
        self.attr = attr  # 属性行なら TreeNode
        self.parent = parent
        self.children: list[_Item] | None = None  # None = 未展開


class AttributeTreeModel(QtCore.QAbstractItemModel):
    """片側ツリーのモデル。``EditorViewModel`` から行データを引く。

    トップレベル = ロード済みノード（セクション）、その子 = 属性階層。
    """

    def __init__(
        self, vm: EditorViewModel, side: str, parent: QtCore.QObject | None = None
    ) -> None:
        """モデルを生成する。

        Args:
            vm: 共有 ViewModel。
            side: ``LEFT`` または ``RIGHT``。
            parent: Qt 親オブジェクト。
        """
        super().__init__(parent)
        self._vm = vm
        self._side = side
        self._root = _Item(None)

    def refresh(self) -> None:
        """モデルを作り直す（ロード/接続変更時。展開状態はリセットされる）。"""
        self.beginResetModel()
        self._root = _Item(None)
        self.endResetModel()

    def _ensure_children(self, item: _Item) -> None:
        """Item の子を未取得なら ViewModel から取得してキャッシュする。"""
        if item.children is not None:
            return
        if item.parent is None:
            # ルート直下 = ロード済みノードのセクション行。
            item.children = [
                _Item(item, section=node) for node in self._vm.nodes(self._side)
            ]
        elif item.section is not None:
            # セクション直下 = そのノードのトップレベル属性（フィルタ適用・§9）。
            item.children = [
                _Item(item, attr=tn)
                for tn in self._vm.visible_attr_nodes(self._side, item.section)
            ]
        elif item.attr is not None and item.attr.is_expandable:
            # 属性直下 = compound の子 / array の要素（フィルタ適用・§9）。
            item.children = [
                _Item(item, attr=tn)
                for tn in self._vm.visible_child_nodes(self._side, item.attr.plug)
            ]
        else:
            item.children = []

    @staticmethod
    def _is_expandable(item: _Item) -> bool:
        """この行が展開可能か（セクションは常に / 属性は is_expandable）。"""
        if item.section is not None:
            return True
        return bool(item.attr and item.attr.is_expandable)

    # ---- QAbstractItemModel 実装 ----
    def index(
        self, row: int, column: int, parent: QtCore.QModelIndex = QtCore.QModelIndex()
    ) -> QtCore.QModelIndex:
        """行/列/親から ``QModelIndex`` を作る。"""
        if not self.hasIndex(row, column, parent):
            return QtCore.QModelIndex()
        pitem = parent.internalPointer() if parent.isValid() else self._root
        self._ensure_children(pitem)
        if 0 <= row < len(pitem.children):
            return self.createIndex(row, column, pitem.children[row])
        return QtCore.QModelIndex()

    def parent(self, index: QtCore.QModelIndex) -> QtCore.QModelIndex:
        """子インデックスから親インデックスを返す。"""
        if not index.isValid():
            return QtCore.QModelIndex()
        pitem = index.internalPointer().parent
        if pitem is None or pitem is self._root:
            return QtCore.QModelIndex()
        gp = pitem.parent
        self._ensure_children(gp)
        return self.createIndex(gp.children.index(pitem), 0, pitem)

    def rowCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        """親配下の行数を返す。"""
        if parent.column() > 0:
            return 0
        pitem = parent.internalPointer() if parent.isValid() else self._root
        if pitem is not self._root and not self._is_expandable(pitem):
            return 0
        self._ensure_children(pitem)
        return len(pitem.children)

    def columnCount(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> int:
        """列数（常に 1）を返す。"""
        return 1

    def hasChildren(self, parent: QtCore.QModelIndex = QtCore.QModelIndex()) -> bool:
        """展開可能か返す（遅延展開の矢印表示用）。"""
        if not parent.isValid():
            return True
        return self._is_expandable(parent.internalPointer())

    def data(self, index: QtCore.QModelIndex, role: int = Qt.DisplayRole):
        """行データを返す（表示文字列 + セクションヘッダのアクセント装飾・§4.6）。"""
        if not index.isValid():
            return None
        item = index.internalPointer()
        is_section = item.section is not None
        if role == Qt.DisplayRole:
            return (
                self._vm.display_label(item.section)
                if is_section
                else self._vm.attr_label(item.attr)
            )
        if is_section and role == Qt.FontRole:
            font = QtGui.QFont()
            font.setBold(True)
            return font
        if is_section and role == Qt.BackgroundRole:
            # 選択色をわずかに混ぜたヘッダ地色（テーマ追従・ライト/ダーク両対応）。
            pal = QtGui.QGuiApplication.palette()
            base = pal.color(QtGui.QPalette.Base)
            accent = pal.color(QtGui.QPalette.Highlight)
            return QtGui.QBrush(blend(base, accent, 0.25))
        # ゴースト行（実在しない array 要素）はイタリック + 薄色で区別（master §5.6）。
        if not is_section and item.attr.is_ghost and role == Qt.FontRole:
            font = QtGui.QFont()
            font.setItalic(True)
            return font
        if not is_section and item.attr.is_ghost and role == Qt.ForegroundRole:
            # 前景色を背景へ半分寄せた薄字（テーマ追従）。
            pal = QtGui.QGuiApplication.palette()
            text = pal.color(QtGui.QPalette.Text)
            base = pal.color(QtGui.QPalette.Base)
            return QtGui.QBrush(blend(text, base, 0.5))
        if not is_section and role == Qt.BackgroundRole:
            return self._attr_background(item.attr)
        return None

    def _attr_background(self, attr: TreeNode) -> QtGui.QBrush | None:
        """属性行の背景ブラシを返す（テーマ追従・モック準拠の補助表現）。

        接続済み行は型色へ淡く寄せた地色、ゴースト行は中立色へ寄せた地色で区別する。
        いずれも palette Base 起点の相対色でライト/ダーク両対応（固定色を焼かない）。

        Args:
            attr: 対象行の ``TreeNode``。

        Returns:
            背景ブラシ。装飾不要な行は None。
        """
        pal = QtGui.QGuiApplication.palette()
        base = pal.color(QtGui.QPalette.Base)
        if attr.is_ghost:
            # ゴースト行は中立色へごく淡く寄せた地色（実在しないことを示す）。
            text = pal.color(QtGui.QPalette.Text)
            return QtGui.QBrush(blend(base, text, 0.07))
        if self._vm.is_connected(attr.plug) or self._vm.has_connected_descendant(
            attr.plug
        ):
            # 接続行（畳んだ親で子に接続がある場合も含む）は型色へ寄せた地色。
            return QtGui.QBrush(blend(base, port_color(attr.type_tag), _CONNECTED_BG_K))
        return None

    # ---- UI からの補助アクセサ ----
    def node_at(self, index: QtCore.QModelIndex) -> TreeNode | None:
        """インデックスの属性 ``TreeNode`` を返す（セクション行は None）。"""
        if not index.isValid():
            return None
        return index.internalPointer().attr

    def plug_at(self, index: QtCore.QModelIndex) -> PlugId | None:
        """インデックスの属性 ``PlugId`` を返す（セクション行は None）。"""
        node = self.node_at(index)
        return node.plug if node else None

    def section_at(self, index: QtCore.QModelIndex) -> NodeId | None:
        """インデックスがノードセクション行ならその ``NodeId`` を返す。"""
        if not index.isValid():
            return None
        return index.internalPointer().section

    def index_for_plug(self, plug: PlugId) -> QtCore.QModelIndex:
        """Plug に対応する行の ``QModelIndex`` を返す（無ければ無効インデックス）。

        セクション（``plug.node``）を見つけ、属性階層を index_path の祖先関係で
        たどって該当行へ降りる。遅延展開を内部で進めるが、フィルタで隠れている
        plug は見つからず無効インデックスを返す。

        Args:
            plug: 探す属性の ``PlugId``。

        Returns:
            該当行の ``QModelIndex``。見つからなければ無効インデックス。
        """
        self._ensure_children(self._root)
        section = next((c for c in self._root.children if c.section == plug.node), None)
        if section is None:
            return QtCore.QModelIndex()
        item = section
        while True:
            self._ensure_children(item)
            nxt = None
            for child in item.children or []:
                if child.attr is None:
                    continue
                cp = child.attr.plug
                if cp == plug:
                    return self._index_of(child)
                if _is_ancestor(cp, plug):
                    nxt = child
                    break
            if nxt is None:
                return QtCore.QModelIndex()
            item = nxt

    def _index_of(self, item: _Item) -> QtCore.QModelIndex:
        """内部 ``_Item`` から ``QModelIndex`` を作る（親配下の行位置で特定）。"""
        parent = item.parent
        if parent is None or parent.children is None:
            return QtCore.QModelIndex()
        return self.createIndex(parent.children.index(item), 0, item)

    def is_section(self, index: QtCore.QModelIndex) -> bool:
        """インデックスがノードセクション行か返す。"""
        return self.section_at(index) is not None
