"""Maya 内で Connection Editor を起動するエントリ。

Maya のスクリプトエディタから::

    import fake_connection_editor
    fake_connection_editor.launch()

``launch`` は ``__init__`` で公開する（このモジュールは内部実装）。Maya API は
``launch`` 呼び出し時に遅延 import するため、非 Maya 環境でも import 自体は通り、
``core`` / ``viewmodel`` の Maya 非依存テストには影響しない。
"""

from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .ui.editor_window import EditorWindow

logger = getLogger(__name__)

# 起動した窓を保持して GC を防ぐ（再 launch で置き換える）。
_active_window: EditorWindow | None = None


def _maya_main_window():
    """Maya メインウィンドウを ``QWidget`` として返す（PySide2/6 両対応）。

    Returns:
        Maya メインウィンドウの ``QWidget``。取得できなければ ``None``。
    """
    from maya import OpenMayaUI as omui  # noqa: N813  (Maya 慣習の別名)

    from .ui.qt_compat import QtWidgets

    ptr = omui.MQtUtil.mainWindow()
    if ptr is None:
        return None
    try:
        from shiboken2 import wrapInstance  # PySide2 (Maya 2023)
    except ImportError:
        from shiboken6 import wrapInstance  # PySide6 (Maya 2025+)
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def launch() -> EditorWindow:
    """Connection Editor を Maya メインウィンドウの子として起動する。

    実シーンの選択を左右の Load / Add ボタンで読み込む。ロード中ノードの外部変更
    （接続・属性追加・ロック・Undo/Redo・削除）は ``MayaSceneWatcher`` でライブ同期し、
    窓破棄（``destroyed``）でコールバックを解除する。メニューバーのオプションは
    ``optionVar`` で次回起動へ持ち越す。

    Returns:
        生成した ``EditorWindow``。モジュール側でも参照を保持して GC を防ぐ。
    """
    import maya.cmds as cmds
    from maya.api.OpenMaya import MGlobal

    from .scene_access.maya import MayaSceneAccess
    from .scene_access.maya_watcher import MayaSceneWatcher
    from .scene_access.real_maya_backend import RealMayaBackend
    from .ui.editor_window import build_app
    from .ui.maya_settings import OptionVarSettings

    def _redo() -> None:
        # 窓フォーカス時の Shift+Z で Maya Redo（対象が無くても無害）。
        try:
            cmds.redo()
        except RuntimeError:
            pass

    def _notify(level: str, message: str) -> None:
        # warning=黄帯 / error=赤帯（Maya コマンドラインへ）。
        reporter = MGlobal.displayError if level == "error" else MGlobal.displayWarning
        reporter(message)

    scene = MayaSceneAccess(RealMayaBackend())
    _, window = build_app(
        scene,
        parent=_maya_main_window(),
        on_redo=_redo,
        on_notify=_notify,
        settings=OptionVarSettings("fake_connection_editor"),
    )

    watcher = MayaSceneWatcher(window.viewmodel, is_busy=window.is_interacting)
    window._watcher = watcher  # 窓と寿命を合わせる（GC 回避）
    window.destroyed.connect(lambda *_: watcher.dispose())

    window.show()

    global _active_window
    _active_window = window
    return window
