"""PySide2/PySide6 互換レイヤー。

PySide2（Maya 2022 以前）と PySide6（Maya 2023 以降）の import 差分を吸収し、
UI 層が単一の窓口から Qt を参照できるようにする薄いシム。

設計方針（master §1.2 / §A2b）:
    - 外部依存ゼロの自前薄シム（Qt.py は使わない）。
    - クラスは ``QtCore`` / ``QtGui`` / ``QtWidgets`` のモジュール経由で参照する。
      バインディング間で所属が変わるもの（``QAction`` / ``QShortcut``）と
      ``shiboken`` のみ、ここで個別に吸収する。
    - 必要なものだけを公開する。不足したら都度追加する。

Example:
    from fake_connection_editor.ui.qt_compat import QtCore, QtWidgets, Qt, Signal
"""

from __future__ import annotations

try:
    # PySide2（Maya 2022 以前）。QAction/QActionGroup/QShortcut は QtWidgets に属する
    import shiboken2 as shiboken
    from PySide2 import QtCore, QtGui, QtSvg, QtWidgets
    from PySide2.QtWidgets import QAction, QActionGroup, QShortcut

    QT_BINDING = "PySide2"
    QT_VERSION_MAJOR = 5
except ImportError:
    # PySide6（Maya 2023 以降）。QAction / QActionGroup / QShortcut は QtGui に移動した
    import shiboken6 as shiboken
    from PySide6 import QtCore, QtGui, QtSvg, QtWidgets
    from PySide6.QtGui import QAction, QActionGroup, QShortcut

    QT_BINDING = "PySide6"
    QT_VERSION_MAJOR = 6

# よく使う列挙・シグナル/スロットの別名
Qt = QtCore.Qt
Signal = QtCore.Signal
Slot = QtCore.Slot


def is_pyside2() -> bool:
    """PySide2 を使用中なら True を返す。

    Returns:
        PySide2 を使っていれば True。
    """
    return QT_BINDING == "PySide2"


def is_pyside6() -> bool:
    """PySide6 を使用中なら True を返す。

    Returns:
        PySide6 を使っていれば True。
    """
    return QT_BINDING == "PySide6"


__all__ = [
    "QtCore",
    "QtGui",
    "QtSvg",
    "QtWidgets",
    "Qt",
    "Signal",
    "Slot",
    "QAction",
    "QActionGroup",
    "QShortcut",
    "shiboken",
    "QT_BINDING",
    "QT_VERSION_MAJOR",
    "is_pyside2",
    "is_pyside6",
]
