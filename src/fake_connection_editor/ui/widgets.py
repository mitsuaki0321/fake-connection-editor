"""エディタが使うカスタム Qt 部品（デリゲート / スタイル / ウィジェット）。

``EditorWindow`` 本体には依存しない自己完結した描画部品をまとめる。依存は
``qt_compat`` と ``colors.blend`` のみ。本体（``editor_window``）はここから import
して使う。
"""

from __future__ import annotations

from collections.abc import Callable

from .colors import blend
from .qt_compat import Qt, QtCore, QtGui, QtWidgets

_CHIP_MID_K = 0.45  # 型チップ中立トーン（全表示時）の型色→地色ブレンド係数
_CHIP_LOW_K = 0.78  # 型チップ低トーン（絞り込みで外した型）のブレンド係数


class RowHeightDelegate(QtWidgets.QStyledItemDelegate):
    """ツリー行の高さだけを底上げするデリゲート（行間隔の調整・モック準拠）。

    ``sizeHint`` の幅は基底実装（文字幅）に委ね、高さのみ下限を設ける。
    スタイルシートでの ``item`` 装飾はネイティブ描画/選択色を崩しやすいため、
    幅を壊さないこの方式で行間だけを広げる。
    """

    def __init__(self, height: int, parent: QtCore.QObject | None = None) -> None:
        """デリゲートを生成する。

        Args:
            height: 1行の最小高さ（px）。
            parent: Qt 親オブジェクト。
        """
        super().__init__(parent)
        self._height = height

    def sizeHint(
        self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex
    ) -> QtCore.QSize:
        """基底の推奨サイズの高さだけを下限調整して返す。"""
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), self._height))
        return size

    def initStyleOption(
        self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex
    ) -> None:
        """選択行を常にアクティブ状態として描く（左右ツリーで同じ選択色に）。

        左右ツリーは片方しかフォーカスを持てず、フォーカスを失った側の選択行は
        スタイルが ``Inactive`` 扱いでグレーに描いてしまう（palette の差し替えは
        Maya のスタイルに無視される）。各行へ ``State_Active`` を立てて常にアクティブ
        扱いにし、どちらの側も同じ選択色（Active の Highlight）で表示する。状態フラグ
        のみの操作なので型色地・テキスト・branch などネイティブ描画は崩さない。
        """
        super().initStyleOption(option, index)
        option.state |= QtWidgets.QStyle.State_Active


class BranchArrowStyle(QtWidgets.QProxyStyle):
    """ツリーのコラプス三角（branch インジケータ）を細い三角に差し替えるスタイル。

    Qt 標準の三角と同じ向き・色・塗り（palette 追従の塗りつぶし）を踏襲し、**底辺
    （頂点の反対側の幅広い辺）だけを ``_BASE_SCALE`` 倍**に縮めてスリムにする。
    子を持つ行のみ自前描画し、それ以外は基底スタイルへ委譲する。
    """

    _BASE_SCALE = 0.9  # 底辺の倍率（細く。1.0=標準幅 と 0.8 の中間）
    _DEPTH = 3.5  # 頂点までの距離（中心から・縮めない）
    _HALF_BASE = 4.0  # 底辺の半分（標準）。実描画では _BASE_SCALE を掛ける

    def drawPrimitive(
        self,
        element: QtWidgets.QStyle.PrimitiveElement,
        option: QtWidgets.QStyleOption,
        painter: QtGui.QPainter,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        """子を持つ branch のみ細い三角を描き、他は基底スタイルへ委譲する。"""
        children = bool(option.state & QtWidgets.QStyle.State_Children)
        if element == QtWidgets.QStyle.PE_IndicatorBranch and children:
            self._draw_arrow(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    def _draw_arrow(
        self, option: QtWidgets.QStyleOption, painter: QtGui.QPainter
    ) -> None:
        """セル中心に、底辺を縮めた三角を描く（開=下向き / 閉=右向き）。"""
        rect = option.rect
        cx = rect.center().x() + 0.5
        cy = rect.center().y() + 0.5
        depth = self._DEPTH
        half = self._HALF_BASE * self._BASE_SCALE
        opened = bool(option.state & QtWidgets.QStyle.State_Open)
        if opened:
            # 下向き（▼）: 頂点が下、底辺は上（水平）。底辺=横幅を縮める。
            points = [
                QtCore.QPointF(cx - half, cy - depth),
                QtCore.QPointF(cx + half, cy - depth),
                QtCore.QPointF(cx, cy + depth),
            ]
        else:
            # 右向き（▶）: 頂点が右、底辺は左（垂直）。底辺=縦幅を縮める。
            points = [
                QtCore.QPointF(cx - depth, cy - half),
                QtCore.QPointF(cx - depth, cy + half),
                QtCore.QPointF(cx + depth, cy),
            ]
        color = option.palette.color(QtGui.QPalette.Text)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QtGui.QPolygonF(points))
        painter.restore()


class RowBackgroundTree(QtWidgets.QTreeView):
    """行の背景を行全体（左端〜右端）に塗るツリー（モック準拠の地色）。

    Qt 標準の ``BackgroundRole`` はアイテム矩形（インデント後〜右端）にしか乗らず、
    左のインデント領域が地色のまま残る。``drawRow`` で行矩形（全幅）を先に塗ること
    で、接続行/セクション/ゴーストの地色を行全体へ広げる。選択行はネイティブの選択
    色を優先するため自前塗りをしない（モデル構造・選択挙動は不変）。
    """

    def drawRow(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        """選択していない行は ``BackgroundRole`` を行全幅へ塗ってから描画する。"""
        if not (option.state & QtWidgets.QStyle.State_Selected):
            brush = index.data(Qt.BackgroundRole)
            if brush is not None:
                painter.fillRect(option.rect, brush)
        super().drawRow(painter, option, index)


class FilterChip(QtWidgets.QPushButton):
    """型フィルタの丸チップ（1文字を円の中心へ幾何計算で配置・master §9）。

    スタイルシートのテキスト揃えは小さい円ボタンでベースラインがずれるため、
    円・枠・文字をすべて自前描画する。文字位置はウィジェット中心（=円の中心）と
    フォントメトリクスから計算し、見た目の中央に置く。``checkable`` トグルのまま。
    """

    def __init__(
        self,
        letter: str,
        on_color: QtGui.QColor,
        base: QtGui.QColor,
        off_fg: QtGui.QColor,
        border: QtGui.QColor,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """チップを生成する。

        Args:
            letter: 表示する1文字（型分類の頭文字）。
            on_color: 高トーン（選択時）の塗り色（型色フル）。
            base: 地色（中/低トーンは型色をここへブレンドして作る）。
            off_fg: 低トーン（外した型）の文字色。
            border: 枠線色。
            parent: Qt 親。
        """
        super().__init__(parent)
        self._letter = letter
        # 3トーンの塗り色（型色 ↔ 地色のブレンド）。high=型色フル（選択）/
        # mid=中立（全表示）/ low=絞り込みで外した型（中立より地へ沈める）。
        self._bg = {
            "high": on_color,
            "mid": blend(on_color, base, _CHIP_MID_K),
            "low": blend(on_color, base, _CHIP_LOW_K),
        }
        self._off_fg = off_fg
        self._border = border
        self._tone = "mid"  # 初期は全表示（中立）
        self.setCheckable(True)
        self.setChecked(True)
        # 円（15px）に収まる小さめの太字（モックの font-size:9px 相当）。
        font = self.font()
        font.setPixelSize(10)
        font.setBold(True)
        self.setFont(font)
        # 角（円の外側）は塗らず親の背景を透かす。
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_tone(self, tone: str) -> None:
        """表示トーン（``"high"``/``"mid"``/``"low"``）を設定して再描画する。"""
        if tone != self._tone:
            self._tone = tone
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """円・枠・中央文字を自前描画する（円の半径から中心配置を計算）。"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        center = QtCore.QPointF(rect.width() / 2.0, rect.height() / 2.0)
        radius = min(rect.width(), rect.height()) / 2.0 - 0.5  # 枠線分だけ内側

        painter.setBrush(self._bg[self._tone])
        pen = QtGui.QPen(self._border)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)

        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        # 低トーン（外した型）は薄文字、高/中トーンは白文字。
        fg = self._off_fg if self._tone == "low" else QtGui.QColor(255, 255, 255)
        painter.setPen(fg)
        # 円の中心に文字の視覚中央を合わせる。tightBoundingRect で字面の上下端を取り、
        # ベースライン y を「中心 - 字面中央オフセット」に置く（半径基準の幾何配置）。
        metrics = QtGui.QFontMetricsF(font)
        bounds = metrics.tightBoundingRect(self._letter)
        x = center.x() - metrics.horizontalAdvance(self._letter) / 2.0
        y = center.y() - (bounds.top() + bounds.bottom()) / 2.0
        painter.drawText(QtCore.QPointF(x, y), self._letter)


class NodeTitle(QtWidgets.QLabel):
    """ノード名ヘッダ。幅に収まらない名前は省略（…）し、クリックで選択する。

    ロードノードを増やすと名前列が伸びて窓が広がる問題があった。幅は content では
    なくレイアウト配分で決め（``SizePolicy.Ignored``）、収まらない名前は
    ``elidedText`` で末尾を ``…`` に詰める。左クリックで ``on_click`` を呼び、その側の
    ロードノードをシーンで選択する（master §3.2）。
    """

    def __init__(
        self, on_click: Callable[[], None], parent: QtWidgets.QWidget | None = None
    ) -> None:
        """ヘッダラベルを生成する。

        Args:
            on_click: 左クリック時に呼ぶハンドラ（その側のノードを選択する）。
            parent: Qt 親。
        """
        super().__init__(parent)
        self._full = ""
        self._on_click = on_click
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(22)
        # 幅は中身ではなくレイアウト配分で決める（長い名前で窓が広がるのを防ぐ）。
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_full_text(self, text: str) -> None:
        """完全なノード名列を設定する（表示は幅に合わせて省略する）。"""
        self._full = text
        self.setToolTip(text)
        self._update_elided()

    def _update_elided(self) -> None:
        """現在の幅に収まるよう末尾を ``…`` に詰めて表示する。"""
        metrics = QtGui.QFontMetrics(self.font())
        avail = max(0, self.width() - 8)  # 枠線 + 左右の余白ぶん
        super().setText(metrics.elidedText(self._full, Qt.ElideRight, avail))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """幅が変わったら省略表示を計算し直す。"""
        super().resizeEvent(event)
        self._update_elided()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """左クリックでその側のノードを選択する。"""
        if event.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(event)
