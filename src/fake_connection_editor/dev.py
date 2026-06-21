"""スタンドアロン開発エントリ（master §1.4 / §13）。

Maya なしで UI を起動・目視確認する。``FakeSceneAccess`` のシーンを注入し、本物の
PySide ウィンドウを開く（UI コードは具象実装を import しない・master §1.4）。

Usage:
    python -m fake_connection_editor.dev        # サンプル（pSphere1 / pSphere2）
    python -m fake_connection_editor.dev tall   # 縦長（スクロール/画面外矢印）
    python -m fake_connection_editor.dev multi  # 複数ノード（セクション/束出し）

いずれにも ``dark`` を足すとダークパレットで起動する（テーマ非依存化の確認用。
例: ``python -m fake_connection_editor.dev dark`` / ``... multi dark``）。実機 Maya では
Maya のパレットがそのまま効くため、dev でライト/ダーク両方が破綻しないかを見る。
"""

from __future__ import annotations

import logging
import sys

from .logging_config import setup_logging
from .scene_access import build_multi_scene, build_sample_scene, build_tall_scene
from .scene_access.fake import (
    MULTI_L1,
    MULTI_L2,
    MULTI_R1,
    MULTI_R2,
    SAMPLE_SPHERE1,
    SAMPLE_SPHERE2,
    TALL_LEFT,
    TALL_RIGHT,
    _plug,
)
from .ui.editor_window import build_app
from .ui.qt_compat import QtGui, QtWidgets


def _apply_dark_palette(app: QtWidgets.QApplication) -> None:
    """アプリにダークパレットを適用する（dev のテーマ非依存化確認用）。

    Fusion スタイル + 暗色 QPalette を当て、UI がパレット追従で破綻しないかを
    目視できるようにする。実機 Maya ではこの関数は使わず Maya のパレットに任せる。

    Args:
        app: 対象の ``QApplication``。
    """
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    window = QtGui.QColor(53, 53, 53)
    base = QtGui.QColor(35, 35, 35)
    text = QtGui.QColor(220, 220, 220)
    highlight = QtGui.QColor(80, 120, 200)
    pal.setColor(QtGui.QPalette.Window, window)
    pal.setColor(QtGui.QPalette.WindowText, text)
    pal.setColor(QtGui.QPalette.Base, base)
    pal.setColor(QtGui.QPalette.AlternateBase, window)
    pal.setColor(QtGui.QPalette.Text, text)
    pal.setColor(QtGui.QPalette.Button, window)
    pal.setColor(QtGui.QPalette.ButtonText, text)
    pal.setColor(QtGui.QPalette.Highlight, highlight)
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
    app.setPalette(pal)


def main() -> int:
    """シーンを注入して UI を起動する。

    引数で起動シーンを切り替える: ``tall``=縦長（左右独立スクロール §3.1 / 画面外
    矢印 §4.7）、``multi``=複数ノード（セクションヘッダ / 束出し §4.6）、既定=サンプル。

    Returns:
        プロセス終了コード。
    """
    setup_logging(level=logging.DEBUG)
    args = sys.argv[1:]
    dark = "dark" in args
    modes = [a for a in args if a != "dark"]
    mode = modes[0] if modes else "sample"
    if mode == "tall":
        scene = build_tall_scene()
        left, right = TALL_LEFT, TALL_RIGHT
    elif mode == "multi":
        scene = build_multi_scene()
        left, right = [MULTI_L1, MULTI_L2], [MULTI_R1, MULTI_R2]
    else:
        scene = build_sample_scene()
        left, right = SAMPLE_SPHERE1, SAMPLE_SPHERE2
        # dev のみ: 既定で scale を未接続にする（中空ポートで当たり判定を試しやすく）。
        # テスト用の build_sample_scene は不変（接続 4 本のまま）。
        scene.disconnect(_plug(SAMPLE_SPHERE1, 1), _plug(SAMPLE_SPHERE2, 1))
    # ダークは build_app（ウィンドウ構築）より前に適用する。チップ QSS は __init__ で
    # パレットから一度計算するため、後から palette を変えても反映されない。
    if dark:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        _apply_dark_palette(app)
    # dev は Fake の全ノードを Load/Add ピッカーの選択肢に渡す（実機は実選択を使う）。
    app, window = build_app(
        scene, left=left, right=right, node_pool=scene.all_node_ids()
    )
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
