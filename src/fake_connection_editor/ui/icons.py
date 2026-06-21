"""同梱 SVG をテーマ追従色でアイコン化するヘルパ。

SVG はモック（``mocs/connection_editor_mockup.html``）の line アイコンを流用。
SVG 内の線色は固定（黒）だが、ここで描画後にアルファ形状を palette 由来の色で
塗り直す（``CompositionMode_SourceIn``）ため、ライト/ダークどちらでも前景色に
追従する。固定色を焼かずテーマ非依存を保つ方針と整合する。
"""

from __future__ import annotations

from logging import getLogger
from pathlib import Path

from .qt_compat import QtCore, QtGui, QtSvg

logger = getLogger(__name__)

_ICON_DIR = Path(__file__).parent / "icons"


def themed_icon(name: str, color: QtGui.QColor, size: int = 16) -> QtGui.QIcon:
    """同梱 SVG を ``color`` で塗ったアイコンを返す（テーマ追従）。

    Args:
        name: ``icons/<name>.svg`` のベース名（拡張子なし）。
        color: 線を塗る前景色（通常は palette の text/buttonText）。
        size: 生成する正方アイコンの一辺（px）。

    Returns:
        指定色で塗った ``QIcon``。SVG が見つからない場合は空の ``QIcon``。
    """
    path = _ICON_DIR / f"{name}.svg"
    if not path.exists():
        logger.warning("icon not found: %s", path)
        return QtGui.QIcon()

    renderer = QtSvg.QSvgRenderer(str(path))
    image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(image)
    renderer.render(painter)
    # 描画したアルファ形状を前景色で塗り直す（線色をテーマ追従にする）。
    painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceIn)
    painter.fillRect(image.rect(), color)
    painter.end()

    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))


def themed_cursor(
    name: str, color: QtGui.QColor, size: int = 24, hot_x: int = 12, hot_y: int = 12
) -> QtGui.QCursor:
    """同梱 SVG を ``color`` で塗ったカーソルを返す（テーマ追従）。

    横断切断（Alt+Shift ドラッグ）中にカーソルをカッター形へ変える用途
    （master §5.1 優先度3）。``themed_icon`` と同じ塗り直し方式で前景色に追従する。

    Args:
        name: ``icons/<name>.svg`` のベース名（拡張子なし）。
        color: 線を塗る前景色。
        size: 生成する正方カーソルの一辺（px）。
        hot_x: ホットスポット x（既定はカーソル中央＝刃の交点）。
        hot_y: ホットスポット y。

    Returns:
        指定色で塗った ``QCursor``。
    """
    pixmap = themed_icon(name, color, size).pixmap(size, size)
    return QtGui.QCursor(pixmap, hot_x, hot_y)
