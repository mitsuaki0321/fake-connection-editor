"""接続線・ポートを描く中央オーバーレイ（master §4.3 / §4.4 / §7.2）。

左右ツリーの上に被せる透明な 1 枚のレイヤー。ポート（丸）も接続線も、線の端点と
ポート中心が必ず一致するよう**このオーバーレイにまとめて描く**（master §4.3 推奨。
ツリーの clip を回避し、線描画と一体管理できる）。

端点はハードコードせず、各行の ``visualRect`` 実位置から測る（master §4.4）。
スクロール・展開で行位置が変わっても、再描画すれば端点は追従する（左右独立
スクロール対応・§3.1）。再描画は ``schedule_update`` でコアレスケする。

実装済みの可視化:
    - 全接続線の常時表示・S 字ベジェ（§4.1）。
    - 畳まれた親からの束出し・二重丸（§4.5）。塗り/中空ポート（§4.3）。
    - 端点が画面外なら viewport 端でクランプし破線 + 上下矢印（§4.7）。
"""

from __future__ import annotations

import math
from logging import getLogger

from ..scene_access.interface import PlugId
from ..viewmodel import LEFT, RIGHT, EditorViewModel
from . import geometry
from .colors import desaturate, neutral, port_color
from .qt_compat import Qt, QtCore, QtGui, QtWidgets

logger = getLogger(__name__)


def _choose_connection_sides(
    src_sides: set[str], dst_sides: set[str]
) -> tuple[str, str] | None:
    """接続 (src, dst) を描く ``(a_side, b_side)`` を選ぶ（純粋関数・Qt 非依存）。

    同一ノードが左右に同時表示されると、端点が複数の側に解決され得る。
    どの側ペアで線を引くかを、**中央をまたぐ左右間**を最優先で決める。

    優先順位:
        1. ``(LEFT, RIGHT)``: src 左・dst 右（Maya 標準の出力→入力・中央をまたぐ）。
        2. ``(RIGHT, LEFT)``: 逆向きでも中央をまたぐ。
        3. 同側 ``(s, s)``: ノードが片側のみ＝正当な同一サイド接続（左を優先）。

    Args:
        src_sides: source が解決可能な側の集合。
        dst_sides: destination が解決可能な側の集合。

    Returns:
        描画に使う ``(a_side, b_side)``。両端のいずれかが未表示なら ``None``。
    """
    if LEFT in src_sides and RIGHT in dst_sides:
        return (LEFT, RIGHT)
    if RIGHT in src_sides and LEFT in dst_sides:
        return (RIGHT, LEFT)
    for side in (LEFT, RIGHT):  # 同側フォールバック（片側のみロード）。左優先。
        if side in src_sides and side in dst_sides:
            return (side, side)
    return None


class ConnectionOverlay(QtWidgets.QWidget):
    """ポートと接続線を描く透明オーバーレイ。"""

    PORT_RADIUS = 5  # ポート円の見た目半径（px）
    # ドラッグ開始の当たり判定。ポート中心からの距離が「円半径 + 掴み代」以内なら掴む
    # （円形・縦横均等）。円のすぐ外だけを少しカバーし、離れた所では掴まない。縦は
    # 最近傍で隣の行と中点分割されるため、実質その行のどこからでも近いポートを掴める。
    GRAB_PAD = 4  # 円の外側に許す掴み代（px）。広すぎると誤接続/誤切断の元。

    def __init__(
        self,
        vm: EditorViewModel,
        left_tree: QtWidgets.QTreeView,
        right_tree: QtWidgets.QTreeView,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """オーバーレイを生成する。

        Args:
            vm: 共有 ViewModel。
            left_tree: 左ツリービュー。
            right_tree: 右ツリービュー。
            parent: 親ウィジェット（左右ツリーを含む領域）。
        """
        super().__init__(parent)
        self._vm = vm
        self._trees = {LEFT: left_tree, RIGHT: right_tree}
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._temp_line: tuple[QtCore.QPoint, QtCore.QPoint] | None = None
        # ドラッグで「外して付いてくる」既存接続。描画から一時的に隠す（§5.1）。
        self._suppressed: tuple[PlugId, PlugId] | None = None
        # ドラッグ中、接続不可なポートを沈める集合（グレーアウト・§5.1/§5.5）。
        # None=ドラッグ中でない（沈めない）。set=この plug 群を沈める。
        self._dimmed: set[PlugId] | None = None
        # 横断切断のスラッシュ線（Alt+Shift ドラッグ・§5.1 優先度3）。黄の破線。
        self._slash_line: tuple[QtCore.QPoint, QtCore.QPoint] | None = None
        # スクロール時の再描画間引き（master §3.1 コアレスケ）。連続イベントを
        # 次フレームの 1 回にまとめ、毎イベントの全再描画を避ける。
        self._redraw_timer = QtCore.QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(0)
        self._redraw_timer.timeout.connect(self.update)

    def schedule_update(self) -> None:
        """次フレームに 1 回だけ再描画を予約する（master §3.1 間引き）。

        高頻度のスクロールイベントから直接 ``update`` を呼ばず、本メソッド経由で
        コアレスケして再描画のちらつきと負荷を抑える。
        """
        if not self._redraw_timer.isActive():
            self._redraw_timer.start()

    # ---- ドラッグ仮線（master §5.1 黄色の仮線） ----
    def set_temp_line(
        self, start: QtCore.QPoint | None, end: QtCore.QPoint | None
    ) -> None:
        """ドラッグ中の仮線を設定して再描画する（overlay 座標）。"""
        self._temp_line = (
            (start, end) if start is not None and end is not None else None
        )
        self.update()

    def clear_temp_line(self) -> None:
        """ドラッグ仮線を消す。"""
        self._temp_line = None
        self.update()

    # ---- つなぎ替えで掴んだ既存接続の一時非表示（§5.1） ----
    def set_suppressed(self, src: PlugId, dst: PlugId) -> None:
        """掴んで外した既存接続 (src→dst) を描画対象から外す。"""
        self._suppressed = (src, dst)
        self.update()

    def clear_suppressed(self) -> None:
        """一時非表示を解除する。"""
        self._suppressed = None
        self.update()

    # ---- ドラッグ中のグレーアウト（接続不可ポートを沈める・§5.1/§5.5） ----
    def set_dimmed(self, plugs: set[PlugId]) -> None:
        """接続不可ポート集合を設定して沈める（ドラッグ開始時に1回評価）。"""
        self._dimmed = plugs
        self.update()

    def clear_dimmed(self) -> None:
        """グレーアウトを解除する（ドラッグ終了時）。"""
        self._dimmed = None
        self.update()

    def visible_plugs(self) -> list[PlugId]:
        """現在画面に見えている全属性ポートの plug 列を返す（候補評価用）。"""
        return [
            plug for side in (LEFT, RIGHT) for plug, _, _ in self._collect_ports(side)
        ]

    # ---- 横断切断のスラッシュ（master §5.1 優先度3） ----
    def set_slash_line(self, start: QtCore.QPoint, end: QtCore.QPoint) -> None:
        """横断切断のスラッシュ線（overlay 座標）を設定して再描画する（§5.1）。"""
        self._slash_line = (start, end)
        self.update()

    def clear_slash_line(self) -> None:
        """スラッシュ線を消す。"""
        self._slash_line = None
        self.update()

    def connections_crossing(
        self, p1: QtCore.QPoint, p2: QtCore.QPoint
    ) -> list[tuple[PlugId, PlugId]]:
        """線分 p1-p2（overlay 座標）と交差する全接続線を返す（横断切断・§5.1）。"""
        out: list[tuple[PlugId, PlugId]] = []
        for pair, path in self._visible_connection_paths():
            pts = geometry.path_points(path)
            if any(
                geometry.segments_intersect(p1, p2, pts[i], pts[i + 1])
                for i in range(len(pts) - 1)
            ):
                out.append(pair)
        return out

    # ---- ポート位置の算出（visualRect 実測・master §4.4） ----
    def _port_viewport_point(self, side: str, rect: QtCore.QRect) -> QtCore.QPoint:
        """行矩形からポート中心のビューポート座標を求める（master §4.3）。

        ポート中心は中央側の**境界線上**（左ツリー=右端 / 右ツリー=左端）に置く。
        こうすると右ツリーの展開矢印（左端のインデント領域）とポートが重ならない。
        円自体はオーバーレイに描くので、境界で半分はみ出ても clip しない。
        """
        viewport = self._trees[side].viewport()
        x = viewport.width() if side == LEFT else 0
        return QtCore.QPoint(int(x), int(rect.center().y()))

    def _to_overlay(self, side: str, viewport_point: QtCore.QPoint) -> QtCore.QPoint:
        """ビューポート座標をオーバーレイ座標へ変換する。"""
        glob = self._trees[side].viewport().mapToGlobal(viewport_point)
        return self.mapFromGlobal(glob)

    def _viewport_overlay_rect(self, side: str) -> QtCore.QRect:
        """ツリー viewport をオーバーレイ座標系の矩形で返す（端クランプ用・§4.7）。"""
        viewport = self._trees[side].viewport()
        top_left = self.mapFromGlobal(viewport.mapToGlobal(QtCore.QPoint(0, 0)))
        return QtCore.QRect(top_left, viewport.size())

    def _iter_rows(
        self, side: str
    ) -> list[tuple[str, object, QtCore.QPoint, bool, bool, bool]]:
        """展開済みの全行を ``(kind, key, 中心, 画面内, 畳まれ, ゴースト)`` で集める。

        ``kind`` は ``"attr"``（``key`` = ``PlugId``）か ``"section"``（``key`` =
        ``NodeId``）。``visualRect`` は展開済みなら画面外（スクロールで外れた）行にも
        有効な矩形を返すので、画面外端点の方向判定（§4.7）と束出し（§4.5/§4.6）を
        同じ走査でまかなう。

        畳まれか:
            - 属性行: 展開可能だが畳まれている親か（二重丸/束出し・§4.5）。
            - セクション行: ノードごと畳まれているか（ヘッダから束出し・§4.6）。

        ゴースト:
            - 属性行のゴースト（実在しない array 要素・§5.6）なら True。
            - セクション行は常に False。
        """
        tree = self._trees[side]
        model = tree.model()
        if model is None:
            return []
        viewport_rect = tree.viewport().rect()
        rows: list[tuple[str, object, QtCore.QPoint, bool, bool, bool]] = []

        def visit(parent: QtCore.QModelIndex) -> None:
            for row in range(model.rowCount(parent)):
                idx = model.index(row, 0, parent)
                rect = tree.visualRect(idx)
                if rect.isValid() and rect.height() > 0:
                    pt = self._to_overlay(side, self._port_viewport_point(side, rect))
                    on_screen = rect.intersects(viewport_rect)
                    collapsed = not tree.isExpanded(idx)
                    section = model.section_at(idx)
                    if section is not None:
                        rows.append(
                            ("section", section, pt, on_screen, collapsed, False)
                        )
                    else:
                        plug = model.plug_at(idx)
                        node = model.node_at(idx)
                        if plug is not None:
                            attr_collapsed = bool(
                                node and node.is_expandable and collapsed
                            )
                            ghost = bool(node and node.is_ghost)
                            rows.append(
                                ("attr", plug, pt, on_screen, attr_collapsed, ghost)
                            )
                if tree.isExpanded(idx):
                    visit(idx)

        visit(QtCore.QModelIndex())
        return rows

    def _collect_ports(self, side: str) -> list[tuple[PlugId, QtCore.QPoint, bool]]:
        """画面内に見えている属性行の (plug, ポート中心, 畳まれた親か) を集める。

        ``_iter_rows`` から画面内の属性行だけを抽出する（hit-test / ポート描画用。
        セクションヘッダはドラッグ対象外）。
        """
        return [
            (key, pt, collapsed)
            for kind, key, pt, on_screen, collapsed, _ghost in self._iter_rows(side)
            if on_screen and kind == "attr" and (collapsed or self._vm.has_port(key))
        ]

    def _hit_rows(self, side: str) -> list[tuple[PlugId, QtCore.QPoint]]:
        """画面内の属性行を (plug, ポート中心) で返す（当たり判定用）。

        ``_iter_rows`` から画面内の属性行だけを抽出する。セクションヘッダは対象外。
        接続不可な属性はポートが無いので掴めない（§4.3）。畳み親は束出し起点だが
        ポートを持たないものは掴めない（``_collect_ports`` と違い畳み例外は無し）。
        当たり判定は中心からの距離（円形）で行うため、ポート中心のみ使う。
        """
        return [
            (key, pt)
            for kind, key, pt, on_screen, _collapsed, _ghost in self._iter_rows(side)
            if on_screen and kind == "attr" and self._vm.has_port(key)
        ]

    def port_at(self, global_pos: QtCore.QPoint) -> tuple[str, PlugId] | None:
        """グローバル座標のポートを返す（ドラッグ当たり判定・円形＋最近傍）。

        ポート中心からの距離が ``PORT_RADIUS + GRAB_PAD`` 以内のポートを対象とし、
        その中で**最近傍**（中心に最も近い）を選ぶ。円のすぐ外だけを少しカバーし、
        離れた所では掴まない。縦は最近傍で隣の行と中点分割されるため、実質その行の
        どこからでも近いポートを掴める。

        Args:
            global_pos: マウスのグローバル座標。

        Returns:
            (side, plug)。許容域に無ければ ``None``。
        """
        cursor = self.mapFromGlobal(global_pos)
        reach = self.PORT_RADIUS + self.GRAB_PAD
        reach_sq = reach * reach
        best: tuple[str, PlugId] | None = None
        best_d2: float | None = None
        for side in (LEFT, RIGHT):
            for plug, center in self._hit_rows(side):
                dx = cursor.x() - center.x()
                dy = cursor.y() - center.y()
                d2 = dx * dx + dy * dy
                if d2 <= reach_sq and (best_d2 is None or d2 < best_d2):
                    best_d2 = d2
                    best = (side, plug)
        return best

    def port_center(self, side: str, plug: PlugId) -> QtCore.QPoint | None:
        """指定ポートの中心 overlay 座標を返す（見えていなければ ``None``）。"""
        for found, pt, _ in self._collect_ports(side):
            if found == plug:
                return pt
        return None

    def find_port(self, plug: PlugId) -> tuple[str, QtCore.QPoint] | None:
        """Plug を左右どちらかのツリーから探し (side, 中心座標) を返す。"""
        for side in (LEFT, RIGHT):
            pt = self.port_center(side, plug)
            if pt is not None:
                return side, pt
        return None

    # ---- レイアウト算出（描画と当たり判定で共有） ----
    def _compute_layout(
        self,
    ) -> tuple[dict, dict, dict]:
        """全行を走査し ``(attrs, sections, vp)`` を返す（描画/hit-test 共通）。

        - ``attrs``: (side, plug) -> (中心, 画面内, 畳み, ゴースト)。画面外行も含む
          （§4.7 端矢印の方向判定 / §4.5 束出しのため）。**キーに side を含める**ので、
          同一ノードが左右に同時表示されても衝突しない（重複ノード対応）。
        - ``sections``: (side, uuid) -> (中心, 画面内)。畳まれたノードセクションのみ。
        - ``vp``: side -> viewport のオーバーレイ座標矩形（端クランプ用・§4.7）。
        """
        attrs: dict[tuple[str, PlugId], tuple[QtCore.QPoint, bool, bool, bool]] = {}
        sections: dict[tuple[str, str], tuple[QtCore.QPoint, bool]] = {}
        for side in (LEFT, RIGHT):
            for kind, key, pt, on_screen, collapsed, ghost in self._iter_rows(side):
                if kind == "attr":
                    attrs[(side, key)] = (pt, on_screen, collapsed, ghost)
                elif collapsed:  # 畳まれたセクションのみ束出しの起点になる
                    sections[(side, key.uuid)] = (pt, on_screen)
        vp = {side: self._viewport_overlay_rect(side) for side in (LEFT, RIGHT)}
        return attrs, sections, vp

    # ---- 描画 ----
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """ポート・接続線・ドラッグ仮線・線選択/切断の補助を描く。"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        attrs, sections, vp = self._compute_layout()

        self._draw_connections(painter, attrs, sections, vp)
        for (side, plug), (pt, on_screen, collapsed, ghost) in attrs.items():
            # 接続不可（source/dest どちらにもなれない）属性はポートを描かない（§4.3）。
            # 畳まれた親は子の束を出す起点なので接続可否に関わらず描く。
            if on_screen and (collapsed or self._vm.has_port(plug)):
                self._draw_port(painter, side, plug, pt, collapsed, ghost)
        # 畳まれたノードに隠れた接続があればヘッダに二重丸（§4.6）。
        for (side, uuid), (pt, on_screen) in sections.items():
            if on_screen and self._vm.node_has_connection_by_uuid(uuid):
                self._draw_section_marker(painter, side, pt)
        if self._temp_line is not None:
            self._draw_temp_line(painter, *self._temp_line)
        if self._slash_line is not None:
            self._draw_slash_line(painter, *self._slash_line)
        painter.end()

    def _resolve_endpoint(
        self, plug: PlugId, side: str, attrs: dict, sections: dict
    ) -> QtCore.QPoint | None:
        """接続端点 plug を **指定 side 内**で描画位置に解決する（束出し・§4.5/§4.6）。

        解決順: plug 自身の行 → 畳まれた祖先属性の行 → 畳まれたノードセクション
        ヘッダ。いずれも無ければ ``None``（その側に未表示）。画面外の行も対象に
        含む（端クランプは §4.7）。側の選択は ``_choose_connection_sides`` が行う。

        Args:
            plug: 解決する接続端点。
            side: 解決対象のツリー側（LEFT/RIGHT）。
            attrs: 属性行辞書（(side, plug) -> (中心, 画面内, 畳み, ゴースト)）。
            sections: 畳まれたセクション辞書（(side, uuid) -> (中心, 画面内)）。

        Returns:
            ポート中心、またはその側に無ければ ``None``。
        """
        hit = attrs.get((side, plug))
        if hit is not None:
            return hit[0]
        ip = plug.index_path
        for depth in range(len(ip) - 1, 0, -1):
            ancestor = PlugId(node=plug.node, index_path=ip[:depth])
            hit = attrs.get((side, ancestor))
            if hit is not None:
                return hit[0]
        sec = sections.get((side, plug.node.uuid))
        if sec is not None:
            return sec[0]
        return None

    def _resolvable_sides(self, plug: PlugId, attrs: dict, sections: dict) -> set[str]:
        """Plug が解決可能な側の集合を返す（左右どちらに表示されているか）。"""
        return {
            side
            for side in (LEFT, RIGHT)
            if self._resolve_endpoint(plug, side, attrs, sections) is not None
        }

    def _iter_visible_connections(self, attrs: dict, sections: dict, vp: dict):
        """描画対象の各接続を列挙する。

        各要素は ``(pair, ca, cb, a_off, b_off, a_dir, b_dir, b_side)``。
        suppressed（つなぎ替えで掴んだ線）・両端解決不可・両端画面外を除外した、
        実際に描かれる接続だけを返す。描画と当たり判定（線選択/横断/マーキー）が
        同じ集合を共有するためのジェネレータ（端点解決・端クランプの重複を排除）。
        ``b_side`` は dst を解決した側（向き矢印の直接表示判定に使う）。
        """
        for src, dst in self._vm.connection_pairs():
            if self._suppressed == (src, dst):
                continue
            sides = _choose_connection_sides(
                self._resolvable_sides(src, attrs, sections),
                self._resolvable_sides(dst, attrs, sections),
            )
            if sides is None:  # 両端のいずれかが未表示
                continue
            a_side, b_side = sides
            a = self._resolve_endpoint(src, a_side, attrs, sections)
            b = self._resolve_endpoint(dst, b_side, attrs, sections)
            ca, a_off, a_dir = geometry.clamp(a, vp[a_side])
            cb, b_off, b_dir = geometry.clamp(b, vp[b_side])
            if a_off and b_off:
                # 両端が画面外なら線の意味が薄いので描かない（モック準拠）。
                continue
            yield (src, dst), ca, cb, a_off, b_off, a_dir, b_dir, b_side

    def _visible_connection_paths(
        self,
    ) -> list[tuple[tuple[PlugId, PlugId], QtGui.QPainterPath]]:
        """現在描かれている各接続線の ``(pair, ベジェ経路)`` を返す（当たり判定用）。"""
        attrs, sections, vp = self._compute_layout()
        return [
            (pair, geometry.bezier_path(ca, cb))
            for pair, ca, cb, *_ in self._iter_visible_connections(attrs, sections, vp)
        ]

    def _draw_connections(
        self, painter: QtGui.QPainter, attrs: dict, sections: dict, vp: dict
    ) -> None:
        """全接続線を S 字ベジェで描く（束出し / 画面外端クランプ対応・§4.1/§4.7）。

        線色 = source 側の型色。端点が画面外なら viewport 端でクランプして破線にし、
        端に「この先に続く」矢印を出す（master §4.7・モック準拠）。
        """
        for (
            pair,
            ca,
            cb,
            a_off,
            b_off,
            a_dir,
            b_dir,
            b_side,
        ) in self._iter_visible_connections(attrs, sections, vp):
            src, dst = pair
            color = port_color(self._vm.type_tag(src))
            self._draw_bezier(painter, ca, cb, color, dashed=(a_off or b_off))
            if a_off:
                self._draw_arrow(painter, ca, a_dir, color)
            if b_off:
                self._draw_arrow(painter, cb, b_dir, color)
            else:
                # dst 自身の行が直接見えている（畳み親も含む・束ね先でない）なら
                # 向き矢印を dst ポート手前に出す（画面外は §4.7 の山形が示す）。
                # 束ね先（子が畳み親に集約）は dst が attrs に無いのでスキップ。
                if attrs.get((b_side, dst)) is not None:
                    self._draw_dir_arrow(
                        painter,
                        geometry.bezier_path(ca, cb),
                        color,
                        tip_inset=self.PORT_RADIUS,
                    )

    def _draw_arrow(
        self,
        painter: QtGui.QPainter,
        point: QtCore.QPoint,
        direction: str,
        color: QtGui.QColor,
    ) -> None:
        """画面外端を示す山形矢印を描く（master §4.7・モック準拠）。"""
        x, y = point.x(), point.y()
        path = QtGui.QPainterPath()
        if direction == "up":
            path.moveTo(x - 4, y + 4)
            path.lineTo(x, y)
            path.lineTo(x + 4, y + 4)
        else:
            path.moveTo(x - 4, y - 4)
            path.lineTo(x, y)
            path.lineTo(x + 4, y - 4)
        painter.setPen(QtGui.QPen(color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _draw_dir_arrow(
        self,
        painter: QtGui.QPainter,
        path: QtGui.QPainterPath,
        color: QtGui.QColor,
        tip_inset: float = 0.0,
    ) -> None:
        """接続の向きを示す塗り三角を path の dst 端に沿わせて描く（master §6 有向）。

        端を水平と決め打ちにすると、展開で急な対角線になった線とポート手前で数 px
        ずれる。これを避けるため経路上の 2 点から接線方向を取り、その向きに三角を
        沿わせる。tip は終端から ``tip_inset`` だけ手前に置く（確定線は dst ポートの
        直前に出すため ``PORT_RADIUS`` を渡す。仮線は終端＝カーソルなので 0）。

        Args:
            painter: 描画先。
            path: 接続線のベジェ経路。
            color: 三角の色（線と同じ source 型色 / 仮線は黄）。
            tip_inset: 終端から tip を手前へずらす量（px）。
        """
        length = path.length()
        if length <= 0:
            return
        size, half = 7.0, 4.0
        tip_len = max(0.0, length - tip_inset)
        base_len = max(0.0, tip_len - size)
        tip = path.pointAtPercent(path.percentAtLength(tip_len))
        base = path.pointAtPercent(path.percentAtLength(base_len))
        dx, dy = tip.x() - base.x(), tip.y() - base.y()
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / norm, dx / norm  # 進行方向に直交する単位ベクトル
        tri = QtGui.QPainterPath(tip)
        tri.lineTo(base.x() + nx * half, base.y() + ny * half)
        tri.lineTo(base.x() - nx * half, base.y() - ny * half)
        tri.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPath(tri)

    def _draw_bezier(
        self,
        painter: QtGui.QPainter,
        start: QtCore.QPoint,
        end: QtCore.QPoint,
        color: QtGui.QColor,
        dashed: bool = False,
    ) -> None:
        """確定済みの接続線（型色のベジェ）を描く。

        ``dashed`` が真なら破線にする（端点が画面外の線・master §4.7）。
        """
        pen = QtGui.QPen(color, 2)
        if dashed:
            pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(geometry.bezier_path(start, end))

    def _base_color(self, side: str) -> QtGui.QColor:
        """ツリーの背景色（palette Base）を返す（テーマ追従の混色基準）。"""
        return self._trees[side].palette().color(QtGui.QPalette.Base)

    def _text_color(self, side: str) -> QtGui.QColor:
        """ツリーの前景色（palette Text）を返す（テーマ追従の中立グレー基準）。"""
        return self._trees[side].palette().color(QtGui.QPalette.Text)

    def _desaturate(self, side: str, color: QtGui.QColor) -> QtGui.QColor:
        """型色をそのサイドの背景へ半分寄せた輪郭色を返す（colors.desaturate）。"""
        return desaturate(color, self._base_color(side))

    def _neutral(self, side: str, t: float) -> QtGui.QColor:
        """前景色を背景へ ``t`` 寄せた中立グレーを返す（colors.neutral）。"""
        return neutral(self._text_color(side), self._base_color(side), t)

    def _draw_port(
        self,
        painter: QtGui.QPainter,
        side: str,
        plug: PlugId,
        center: QtCore.QPoint,
        collapsed: bool,
        ghost: bool = False,
    ) -> None:
        """ポート円を描く（master §4.3 / §4.5 / §5.6）。

        - ロード相手と接続 = 型色で塗りつぶし（配線が画面に描ける）。
        - ロード外とだけ接続 = 型色の輪 + 中心ドット（接続はあるが配線は画面外）。
        - 未接続 = 背景色塗り + 型色の輪郭（中空・明るく描いて接続不可と分離）。
        - 畳まれた親で子に隠れた接続あり = 二重丸（外輪 + 内側ドット・C5/§4.5）。
        - ゴースト（実在しない array 要素・§5.6）= 破線の中空輪郭で区別。
        ポート背後は背景色の円でマスクし、接続線が透けないようにする。
        """
        color = port_color(self._vm.type_tag(plug))
        base = self._base_color(side)
        r = self.PORT_RADIUS
        detached = self._suppressed is not None and plug == self._suppressed[1]
        double = collapsed and not detached and self._vm.has_connected_descendant(plug)
        dimmed = self._dimmed is not None and plug in self._dimmed

        # 背景マスク円（線が透けないように）
        mask_r = (r + 2) if double else (r + 1)
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawEllipse(geometry.circle(center, mask_r))

        if dimmed:
            # ドラッグ中の接続不可ポート: 接続状態に依らず薄い中立色の中空で沈める。
            painter.setPen(QtGui.QPen(self._neutral(side, 0.6), 1.5))
            painter.setBrush(base)
            painter.drawEllipse(geometry.circle(center, r))
        elif ghost:
            # ゴースト: 型色の破線中空輪郭（先回り表示・実体化前・§5.6）。
            pen = QtGui.QPen(self._desaturate(side, color), 1.5)
            pen.setDashPattern([2, 2])
            painter.setPen(pen)
            painter.setBrush(base)
            painter.drawEllipse(geometry.circle(center, r))
        elif double:
            # 二重丸: 外輪（背景塗り + 型色輪郭）+ 内側の型色ドット
            painter.setPen(QtGui.QPen(color, 1.5))
            painter.setBrush(base)
            painter.drawEllipse(geometry.circle(center, r + 2))
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(geometry.circle(center, 2.5))
        elif self._vm.is_connected(plug) and not detached:
            if self._vm.is_connected_to_loaded(plug):
                # ロード相手と接続: 型色で塗りつぶし
                painter.setPen(QtGui.QPen(color, 1.5))
                painter.setBrush(color)
                painter.drawEllipse(geometry.circle(center, r))
            else:
                # ロード外とだけ接続: 型色の輪 + 中心ドット（画面外接続）
                painter.setPen(QtGui.QPen(color, 1.5))
                painter.setBrush(base)
                painter.drawEllipse(geometry.circle(center, r))
                painter.setPen(Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(geometry.circle(center, 2.0))
        else:
            # 未接続: 型色そのままの明るい輪郭 + 背景塗り（中空）。接続済み（塗り）とは
            # 「塗りの有無」で二値判別でき（§4.3）、ドラッグ中の接続不可（中立グレー）
            # とも色味で分離する（実機 Node Editor の white master port と同思想）。
            painter.setPen(QtGui.QPen(color, 1.5))
            painter.setBrush(base)
            painter.drawEllipse(geometry.circle(center, r))

    def _draw_section_marker(
        self, painter: QtGui.QPainter, side: str, center: QtCore.QPoint
    ) -> None:
        """畳まれたノードヘッダに二重丸を描く（隠れた接続あり・master §4.6）。

        型色を持たない（ノード単位の合図）ため中立グレー（テーマ追従）で描く。
        """
        base = self._base_color(side)
        color = self._neutral(side, 0.45)
        r = self.PORT_RADIUS
        painter.setPen(Qt.NoPen)
        painter.setBrush(base)
        painter.drawEllipse(geometry.circle(center, r + 2))
        painter.setPen(QtGui.QPen(color, 1.5))
        painter.setBrush(base)
        painter.drawEllipse(geometry.circle(center, r + 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(geometry.circle(center, 2.5))

    def _draw_temp_line(
        self, painter: QtGui.QPainter, start: QtCore.QPoint, end: QtCore.QPoint
    ) -> None:
        """ドラッグ中の仮線（黄色のベジェ・master §5.1）を描く。

        確定線と同じ S 字ベジェにし、掴んだポートから水平に出てカーソルへ向かう。
        """
        color = QtGui.QColor(230, 200, 40)
        path = geometry.bezier_path(start, end)
        painter.setPen(QtGui.QPen(color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        # カーソル（＝接続先）へ向きを示す矢印を出す（確定線と一貫・§5.1）。
        self._draw_dir_arrow(painter, path, color)

    def _draw_slash_line(
        self, painter: QtGui.QPainter, start: QtCore.QPoint, end: QtCore.QPoint
    ) -> None:
        """横断切断のスラッシュ線（黄の破線・Maya ノードエディタ準拠・§5.1）を描く。"""
        pen = QtGui.QPen(QtGui.QColor(230, 200, 40), 2)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(start, end)
