"""接続線・ポートの純粋幾何（副作用なし・テスト可能）。

オーバーレイの描画と当たり判定で使う幾何計算をまとめる。Qt の型（``QtCore`` /
``QtGui``）には依存するが状態を持たず副作用もないため、単体テストできる。描画
（painter への書き込み）や palette 依存の色計算は ``connection_overlay`` 側に残す。
"""

from __future__ import annotations

import math

from .qt_compat import QtCore, QtGui


def bezier_path(start: QtCore.QPoint, end: QtCore.QPoint) -> QtGui.QPainterPath:
    """端点から水平に出入りする 3 次ベジェ経路を作る（master §4.1）。

    Args:
        start: 始点。
        end: 終点。

    Returns:
        始点・終点を水平接線で結ぶ ``QPainterPath``。
    """
    dx = (end.x() - start.x()) * 0.5
    path = QtGui.QPainterPath(QtCore.QPointF(start))
    path.cubicTo(
        QtCore.QPointF(start.x() + dx, start.y()),
        QtCore.QPointF(end.x() - dx, end.y()),
        QtCore.QPointF(end),
    )
    return path


def clamp(
    point: QtCore.QPoint, rect: QtCore.QRect
) -> tuple[QtCore.QPoint, bool, str | None]:
    """端点を viewport の上下端にクランプする（master §4.7・モック準拠）。

    端から 5px 以内（または外）を画面外とみなし、端の 4px 内側へ寄せる。

    Args:
        point: クランプ対象の端点。
        rect: 基準にする viewport 矩形。

    Returns:
        (クランプ後の点, 画面外か, 方向 "up"/"down" または None)。
    """
    y = point.y()
    if y < rect.top() + 5 or y > rect.bottom() - 5:
        cy = max(rect.top() + 4, min(rect.bottom() - 4, y))
        direction = "up" if y < rect.top() else "down"
        return QtCore.QPoint(point.x(), cy), True, direction
    return point, False, None


def circle(center: QtCore.QPoint, radius: float) -> QtCore.QRectF:
    """中心と半径から円の矩形を作る。"""
    return QtCore.QRectF(
        center.x() - radius, center.y() - radius, radius * 2, radius * 2
    )


def path_points(path: QtGui.QPainterPath, samples: int = 40) -> list[QtCore.QPointF]:
    """ベジェ経路を等間隔に標本化したポリラインの点列を返す。"""
    return [path.pointAtPercent(i / samples) for i in range(samples + 1)]


def dist_point_segment(
    p: QtCore.QPointF, a: QtCore.QPointF, b: QtCore.QPointF
) -> float:
    """点 p と線分 a-b の最短距離を返す。"""
    ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p.x() - ax, p.y() - ay)
    t = ((p.x() - ax) * dx + (p.y() - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(p.x() - cx, p.y() - cy)


def segments_intersect(
    p1: QtCore.QPointF,
    p2: QtCore.QPointF,
    p3: QtCore.QPointF,
    p4: QtCore.QPointF,
) -> bool:
    """線分 p1-p2 と p3-p4 が交差するか（端点共有・平行は無視の簡易判定）。"""

    def ccw(a, b, c) -> float:
        return (c.y() - a.y()) * (b.x() - a.x()) - (b.y() - a.y()) * (c.x() - a.x())

    d1 = ccw(p3, p4, p1)
    d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3)
    d4 = ccw(p1, p2, p4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)
