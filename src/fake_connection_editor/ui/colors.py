"""型色（ポート/線の色）の対応（master §4.2）。

型タグを Core の ``classify``（C6）で分類し、分類ごとの色を返す。既定は固定の
暫定色だが、Maya では ``set_type_colors`` で Color Settings の実値に差し替える
（master §4.2）。Fake/dev は暫定色のまま。
"""

from __future__ import annotations

from ..core import TypeCategory, classify
from .qt_compat import QtGui

# 分類ごとの暫定色（master §4.2 の表＝既定。Maya では set_type_colors で上書き）。
_DEFAULT_COLOR_BY_CATEGORY = {
    TypeCategory.NUMERIC: QtGui.QColor(120, 180, 90),  # 緑
    TypeCategory.BOOL: QtGui.QColor(205, 185, 145),  # ベージュ
    TypeCategory.MATRIX: QtGui.QColor(90, 140, 205),  # 青
    TypeCategory.COLOR: QtGui.QColor(205, 95, 95),  # 赤
    TypeCategory.DATA: QtGui.QColor(60, 60, 60),  # 黒（暗灰）
}

# 実効の分類→色（起動時に set_type_colors で Maya Color Settings 由来へ差し替え）。
_COLOR_BY_CATEGORY = dict(_DEFAULT_COLOR_BY_CATEGORY)

# ポート色の最低明度（0-255・最大チャンネル）。Maya の data は黒で暗背景に埋もれる
# ため、これ未満なら中立グレーへ底上げして可視性を確保する（master §4.2・B 方針）。
_MIN_PORT_VALUE = 90


def _ensure_visible(color: QtGui.QColor) -> QtGui.QColor:
    """暗すぎる型色（Maya の data=黒 等）を中立グレーへ底上げして返す。

    最大チャンネルが ``_MIN_PORT_VALUE`` 以上ならそのまま、未満なら可視なグレーに
    置き換える（色味の無い data はグレーで判別できれば十分・暗背景対策）。

    Args:
        color: 評価する型色。

    Returns:
        可視性を確保した ``QColor``。
    """
    if max(color.red(), color.green(), color.blue()) >= _MIN_PORT_VALUE:
        return color
    return QtGui.QColor(_MIN_PORT_VALUE, _MIN_PORT_VALUE, _MIN_PORT_VALUE)


def set_type_colors(colors: dict[str, tuple[float, float, float]] | None) -> None:
    """型分類→色を Color Settings 由来の値で上書きする（master §4.2）。

    キーは ``"numeric"`` 等の分類文字列（``SceneAccess.get_attribute_type_colors``
    の契約）。欠けたキーは既定の暫定色を保つ。暗すぎる色は ``_ensure_visible`` で
    底上げする。アプリ起動時に 1 度だけ呼ぶ想定（色はアプリ全体で共有のため）。

    Args:
        colors: 分類文字列 → ``(r, g, b)``（各 0.0〜1.0）。``None`` / 空なら既定のまま。
    """
    merged = dict(_DEFAULT_COLOR_BY_CATEGORY)
    if colors:
        for category in TypeCategory:
            rgb = colors.get(category.name.lower())
            if rgb is not None:
                qcolor = QtGui.QColor(
                    round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255)
                )
                merged[category] = _ensure_visible(qcolor)
    _COLOR_BY_CATEGORY.clear()
    _COLOR_BY_CATEGORY.update(merged)


def port_color(type_tag: str) -> QtGui.QColor:
    """型タグに対応するポート/線の色を返す（master §4.2）。

    Args:
        type_tag: 正規化済み型タグ。

    Returns:
        分類に対応する ``QColor``（暫定配色）。
    """
    return _COLOR_BY_CATEGORY[classify(type_tag)]


def category_color(category: TypeCategory) -> QtGui.QColor:
    """型分類に対応する色を返す（型チップの背景色 = 凡例・master §9）。

    Args:
        category: 型分類（``TypeCategory``）。

    Returns:
        分類に対応する ``QColor``（暫定配色）。
    """
    return _COLOR_BY_CATEGORY[category]


def blend(c1: QtGui.QColor, c2: QtGui.QColor, t: float) -> QtGui.QColor:
    """2 色を線形補間する（``t``=0 で c1、1 で c2）。

    テーマ非依存の相対色を作るための土台。背景色（palette Base）へ寄せれば
    ライト/ダークどちらでも自然に脱色・中和できる（ハードコードの明色を避ける）。

    Args:
        c1: 起点の色。
        c2: 終点の色。
        t: 補間係数（0.0〜1.0）。

    Returns:
        補間した ``QColor``。
    """
    return QtGui.QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


def desaturate(color: QtGui.QColor, base: QtGui.QColor) -> QtGui.QColor:
    """型色を背景へ半分寄せた輪郭色を返す（未接続/ゴースト用・テーマ追従）。

    ライトでは明るく、ダークでは暗く脱色され、どちらでも背景に馴染む。

    Args:
        color: 元の型色。
        base: 背景色（palette Base）。

    Returns:
        背景へ半分寄せた ``QColor``。
    """
    return blend(color, base, 0.5)


def neutral(text: QtGui.QColor, base: QtGui.QColor, t: float) -> QtGui.QColor:
    """前景色を背景へ ``t`` だけ寄せた中立グレーを返す（テーマ追従）。

    ``t`` が大きいほど背景寄り（薄い）。dimmed/二重丸マーカー等の型色を持たない
    合図に使う（ハードコードの固定グレーを避ける）。

    Args:
        text: 前景色（palette Text）。
        base: 背景色（palette Base）。
        t: 背景へ寄せる係数（0.0〜1.0）。

    Returns:
        中立グレーの ``QColor``。
    """
    return blend(text, base, t)
