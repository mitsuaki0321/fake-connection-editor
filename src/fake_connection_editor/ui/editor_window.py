"""メインウィンドウと依存注入エントリ（master §1.4 / §3）。

左右 2 ツリー + 中央オーバーレイ（ポート/線）を組み、ポート間ドラッグで接続、
空白ドロップで切断する。``build_app`` に SceneAccess 実装を注入して起動する
（UI コードは具象実装を import しない・master §1.4）。

操作:
    - ポート→ポートのドラッグで接続（向きは ViewModel が C1 で正規化）。
    - ポートを掴んで空白にドロップで、その plug の接続を切断（§5.1）。
    - アクションバーの接続/leaf 接続/値コピー、フィルタ、ゴースト実体化。
"""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from typing import TypeVar

from ..core import FilterCriteria, TypeCategory
from ..scene_access.interface import NodeId, PlugId, SceneAccess
from ..viewmodel import LEFT, RIGHT, EditorViewModel, NameMode, SortMode
from .colors import blend, category_color, set_type_colors
from .connection_overlay import ConnectionOverlay
from .errors import error_handler
from .icons import themed_icon
from .interaction import InteractionController
from .qt_compat import QAction, QActionGroup, Qt, QtCore, QtGui, QtWidgets, shiboken
from .settings import SettingsStore
from .tree_model import AttributeTreeModel
from .widgets import (
    BranchArrowStyle,
    FilterChip,
    NodeTitle,
    RowBackgroundTree,
    RowHeightDelegate,
)

logger = getLogger(__name__)

_T = TypeVar("_T")  # _keep の引数/戻り値の型を保つための型変数

_MENU_SETTINGS_KEY = "menu"  # メニューバーのオプションを 1 まとめで保存するキー

_MIDDLE_GAP = 70  # 中央線レイヤーの帯幅（px・モック準拠）
_ROW_HEIGHT = 22  # ツリー1行の高さ（px・モック準拠＝詰まり解消）
_BTN_HEIGHT = 26  # Load/Add・アクションバーのボタン高さ（px・モック準拠で統一）
_DIR_ICON_PX = 20  # 方向トグルの矢印アイコン一辺（px・26 の枠内で大きめに）

# 背景の帯（モック準拠・テーマ追従）。各領域の地色を palette の Base↔Window の相対
# 補間 ``blend(Base, Window, k)`` で表す（Maya 実機採取で Base=パネル/Window=最明帯）。
# k が大きいほど明るい帯。スクロールバーは対象外（A-2）。
_BG_TITLE_K = 0.32  # ノード名タイトルバー（モック #333 相当）
_BG_TREE_K = 0.52  # ツリー本体（モック #383838 相当・インセットの明帯）
_BG_MENU_K = 0.68  # メニューバー（モック #3c3c3c 相当）
_BG_FOOTER_K = 0.28  # フッタ（leaf/force 行・モック #323232 相当）
_BG_FUNNEL_K = 0.68  # フィルタ漏斗ボタン（モック #3c3c3c 相当）
_BG_ADD_K = 1.0  # Add ボタン（Load より一段暗い・モック #444=Window 相当）
_BG_SWAP_K = 0.30  # 左右入替ボタン（Window↔Button 補間・モック #4a4a4a 相当）


def _next_type_filter(
    before: frozenset[TypeCategory],
    clicked: TypeCategory,
    all_cats: frozenset[TypeCategory],
    *,
    ctrl: bool,
) -> frozenset[TypeCategory]:
    """型チップのクリック後の表示型集合を返す（案A: ソロ＋Ctrl 複数・Qt 非依存）。

    通常クリックは「その型のみ（ソロ）」に絞る。ただし単独表示中の型を押した
    ときは全表示に戻す。Ctrl+クリックはその型を個別にトグル（追加/除外）して
    複数選択を維持する。Ctrl+クリックですべて外して空になった場合も、全非表示に
    せず全表示へリセットする。

    Args:
        before: クリック直前の表示型集合。
        clicked: 押された型分類。
        all_cats: 全型分類の集合（全表示＝この集合）。
        ctrl: Ctrl 修飾が押されているか。

    Returns:
        クリック後に表示すべき型分類の集合。
    """
    if ctrl:
        toggled = before ^ {clicked}
        # すべて外して空になったら全表示へリセットする（全非表示にしない）。
        return frozenset(toggled) if toggled else frozenset(all_cats)
    if before == {clicked}:
        return frozenset(all_cats)
    return frozenset({clicked})


class EditorWindow(QtWidgets.QWidget):
    """左右ツリー + 接続線オーバーレイを持つエディタ本体。"""

    def __init__(
        self,
        vm: EditorViewModel,
        parent: QtWidgets.QWidget | None = None,
        node_pool: list[NodeId] | None = None,
        on_redo: Callable[[], None] | None = None,
        on_notify: Callable[[str, str], None] | None = None,
        settings: SettingsStore | None = None,
    ) -> None:
        """ウィンドウを構築する。

        Args:
            vm: 共有 ViewModel（SceneAccess 注入済み）。
            parent: Qt 親。Maya 統合時は Maya メインウィンドウを渡す（このウィンドウを
                Maya に所有させ、Maya 終了時に一緒に破棄させる・master §1.4/§10.4）。
                親を渡しても埋め込み子ウィジェットにならないよう ``Qt.Window`` を立てて
                最上位の浮動ツールウィンドウにする。dev は ``None``（単体で最上位窓）。
            node_pool: dev ピッカーに並べる選択肢ノード（Maya では ``None`` =
                実機選択を使う。dev では Fake の全ノードを渡す・master §3.2）。
            on_redo: ``Shift+Z`` 押下時に呼ぶ Redo ハンドラ（Maya 統合で ``cmds.redo``
                を注入＝窓フォーカス時でも Redo を効かせる・§7.1(1) の最小ブリッジ）。
                ``None``（dev）なら Redo キーは素通り（Qt 非依存・cmds は呼び出し側）。
            on_notify: ユーザー通知先 ``(level, message)``（``level`` は ``"warning"``
                / ``"error"``）。Maya は ``displayWarning``（黄帯）/ ``displayError``
                （赤帯）へ振り分けて注入する。``None``（dev/テスト）ならログのみ
                （Maya/Qt 非依存を保つ）。
            settings: メニューバーのオプションを次回起動へ持ち越す永続化先。Maya は
                ``OptionVarSettings`` を注入する。``None``（dev/テスト）なら永続化なし。
        """
        super().__init__(parent)
        self._on_redo = on_redo
        self._on_notify = on_notify
        self._settings = settings
        # 親（Maya メインウィンドウ）を持たせても独立した最上位窓として浮かせる。
        if parent is not None:
            self.setWindowFlags(Qt.Window)
        self._vm = vm
        self._node_pool = node_pool
        # 操作系（ドラッグ/線選択/横断/マーキー/値コピー）はコントローラに委譲する。
        # 構築後（overlay/trees/models が揃ったら）に生成する。
        self._interaction: InteractionController | None = None
        # オートスクロール（Scroll to connected）の逆方向再帰を防ぐガード。
        self._syncing = False
        # テキスト絞り込み中の自動展開（マッチ祖先のみ）。連続展開での overlay 再描画
        # を抑えるバッチフラグと、テキスト ON/OFF 遷移で復元する展開状態の退避。
        self._batch_expand = False
        self._text_active: dict[str, bool] = {}
        self._pre_filter_expanded: dict[str, set] = {}

        self.setWindowTitle("Connection Editor")
        self.resize(520, 490)  # 実機で扱いやすい初期サイズ（縦長・1カラム的）

        # コラプス三角を細い三角に差し替えるスタイル（両ツリーで共有・参照保持必須）。
        self._branch_style = BranchArrowStyle()
        self._titles: dict[str, NodeTitle] = {}
        self._trees: dict[str, QtWidgets.QTreeView] = {}
        self._models: dict[str, AttributeTreeModel] = {}
        self._pickers: dict[str, QtWidgets.QComboBox] = {}
        # フィルタ行のウィジェット（左右独立・master §9）。
        self._filter_text: dict[str, QtWidgets.QLineEdit] = {}
        self._filter_chips: dict[str, dict[TypeCategory, QtWidgets.QPushButton]] = {}
        self._filter_nonkeyable: dict[str, object] = {}
        self._filter_connected: dict[str, object] = {}
        self._filter_extra_only: dict[str, object] = {}
        # QMenu / QActionGroup のラッパを保持し GC を防ぐ（PySide6 の道連れ対策）。
        self._keepalive: list[object] = []

        # 保存済みメニュー設定を読み、sort/name はメニュー構築前に VM へ適用する
        # （Edit ラジオが vm.sort_mode()/name_mode() を見て checked を初期化するため）。
        self._saved_menu = self._read_menu_settings()
        self._apply_saved_modes_to_vm()

        outer = QtWidgets.QVBoxLayout(self)
        # 外側は左右 0・下 0（フッタを全幅で底に密着させる）。上は既定の間隔を残す。
        margins = outer.contentsMargins()
        outer.setContentsMargins(0, margins.top(), 0, 0)
        self._menu_bar = self._build_menu_bar()  # 背景調整で参照するため保持
        outer.setMenuBar(self._menu_bar)

        # フッタ以外（各行＋ツリー本体）は内側レイアウトにまとめ、左右に既定マージンを
        # 与える（スクロールバーがウィンドウ端に張り付かないように）。フッタだけ外側に
        # 直接置いて全幅にする（標準的なレイアウトのネスト・特別な仕掛けは不要）。
        content = QtWidgets.QVBoxLayout()
        content.setContentsMargins(margins.left(), 0, margins.right(), 0)
        # 上部の各行は [左 | 中央70 | 右] の3カラムで揃える（モック構造）。
        picker_l = self._build_picker_row(LEFT)
        picker_r = self._build_picker_row(RIGHT)
        if picker_l is not None and picker_r is not None:
            content.addLayout(self._build_header_row(picker_l, picker_r))
        content.addLayout(
            self._build_header_row(
                self._build_load_bar(LEFT), self._build_load_bar(RIGHT)
            )
        )
        content.addLayout(
            self._build_header_row(
                self._build_filter_bar(LEFT), self._build_filter_bar(RIGHT)
            )
        )
        content.addLayout(self._build_title_row())  # ノード名ヘッダ + 中央に入替ボタン
        # 本体（左右ツリー + 中央帯）。中央帯はポート円の中央側半分や中央帯での押下を
        # 拾うため、ドラッグ開始も受ける（overlay はマウス透過・当たり判定対策）。
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(0)
        gap = QtWidgets.QWidget()
        gap.setFixedWidth(_MIDDLE_GAP)
        self._gap = gap
        body.addLayout(self._build_tree_col(LEFT), 1)
        body.addWidget(gap)
        body.addLayout(self._build_tree_col(RIGHT), 1)
        content.addLayout(body, 1)
        outer.addLayout(content, 1)
        outer.addWidget(self._build_footer())  # フッタは全幅で底に密着

        self._overlay = ConnectionOverlay(
            vm, self._trees[LEFT], self._trees[RIGHT], self
        )
        self._overlay.raise_()

        # 操作系コントローラ（状態と分岐ロジックの実体）。eventFilter は本ウィンドウが
        # 主体のまま受け、handle_event へ転送する（アプリ全体スコープの意図を維持）。
        self._interaction = InteractionController(
            window=self,
            vm=vm,
            overlay=self._overlay,
            trees=self._trees,
            models=self._models,
            gap=self._gap,
            force_on=self._force_on,
            force_disconnect_on=self._force_disconnect_on,
            direction_on=self._direction,
        )

        # ドラッグ開始の検出はアプリ全体のイベントフィルタで行う。ポート円は viewport の
        # 境界線上にあり、円の中央帯側半分やウィジェット境界の押下は個別 viewport/gap の
        # フィルタに届かないことがある。アプリ全体で受ければ、port_at が当たればどの
        # ウィジェット上でも掴め、外れれば通常操作（スクロール/展開）を素通りできる。
        self._app_filter_installed = False
        self._install_app_filter()

        for side in (LEFT, RIGHT):
            tree = self._trees[side]
            # スクロールは高頻度なので overlay 側でコアレスケして引き直す（§3.1）。
            tree.verticalScrollBar().valueChanged.connect(self._overlay.schedule_update)
            tree.horizontalScrollBar().valueChanged.connect(
                self._overlay.schedule_update
            )
            tree.expanded.connect(lambda *_: self._on_row_toggled())
            tree.collapsed.connect(lambda *_: self._on_row_toggled())
            # 選択で反対側を接続相手までスクロール（Scroll to connected・既定 OFF）。
            # currentChanged はキーボード移動と初回クリックを拾う。既に選択中の行を
            # 再クリックしても currentIndex は変わらず発火しないため、clicked も繋いで
            # 同じ行の再クリックでも呼び戻せるようにする（スクロール後の再表示用）。
            tree.selectionModel().currentChanged.connect(
                lambda *_, s=side: self._on_tree_current_changed(s)
            )
            tree.clicked.connect(lambda *_, s=side: self._on_tree_current_changed(s))

        vm.add_listener(self._on_vm_changed)
        self._refresh_titles()
        self._apply_backgrounds()

    def _apply_backgrounds(self) -> None:
        """各領域の地色をモック準拠の帯に揃える（palette 相対・テーマ追従・A-2）。

        パネル地は Base（最暗）、メニュー/ツリーは ``blend(Base, Window, k)`` の明帯。
        タイトルバーは ``_make_title`` 側で帯を当てる。スクロールバーは対象外。
        """
        pal = self.palette()
        base = pal.color(QtGui.QPalette.Base)
        win = pal.color(QtGui.QPalette.Window)
        # パネル地（最暗・モック #2b2b2b）= Base。子の素地もこれに揃う。
        self._set_role_bg(self, QtGui.QPalette.Window, base, fill=True)
        # メニューバー = やや明るい帯（モック #3c3c3c）。Maya 独自スタイルは palette/
        # autoFillBackground を無視するため stylesheet で地色を当てる（項目は透過）。
        menu_bg = self._rgb_css(blend(base, win, _BG_MENU_K))
        self._menu_bar.setStyleSheet(
            f"QMenuBar{{background-color:{menu_bg};}}"
            "QMenuBar::item{background:transparent;}"
        )
        # ツリー本体 = 中間グレーのインセット（モック #383838）。
        for tree in self._trees.values():
            self._set_role_bg(tree, QtGui.QPalette.Base, blend(base, win, _BG_TREE_K))

    def _band(self, k: float) -> QtGui.QColor:
        """``blend(Base, Window, k)`` の帯色を返す（背景帯の共通計算・build 時に使う）。

        Args:
            k: Base→Window の補間係数（0=Base / 1=Window）。

        Returns:
            補間した ``QColor``。
        """
        pal = self.palette()
        return blend(
            pal.color(QtGui.QPalette.Base), pal.color(QtGui.QPalette.Window), k
        )

    def _border_color(self) -> QtGui.QColor:
        """ボタン/枠線の暗い境界色を返す（モック #1e1e1e 相当・palette 相対）。"""
        pal = self.palette()
        return blend(
            pal.color(QtGui.QPalette.Base), pal.color(QtGui.QPalette.Shadow), 0.3
        )

    @staticmethod
    def _rgb_css(color: QtGui.QColor) -> str:
        """``QColor`` を QSS の ``rgb(r,g,b)`` 文字列にする。"""
        return f"rgb({color.red()},{color.green()},{color.blue()})"

    def _tool_bg_qss(self, color: QtGui.QColor) -> str:
        """QToolButton 用の地色 stylesheet を返す（パレット由来の値を焼く）。

        Maya の独自スタイルは ``QToolButton`` の Button ロールを無視するため、palette
        ではなく stylesheet で地色を当てる（色値は palette から計算＝テーマ追従の素）。

        Args:
            color: 地色。

        Returns:
            ``QToolButton`` の背景＋枠線を指定する QSS 文字列。
        """
        return (
            f"QToolButton{{background-color:{self._rgb_css(color)};"
            f"border:1px solid {self._rgb_css(self._border_color())};}}"
        )

    @staticmethod
    def _set_role_bg(
        widget: QtWidgets.QWidget,
        role: QtGui.QPalette.ColorRole,
        color: QtGui.QColor,
        fill: bool = False,
    ) -> None:
        """ウィジェットの指定 palette ロールの色を差し替える（背景帯の適用）。

        Args:
            widget: 対象ウィジェット。
            role: 差し替える ColorRole（背景なら Window / ビューなら Base）。
            color: 設定する色。
            fill: True なら ``autoFillBackground`` も立てる（素の QWidget 用）。
        """
        pal = widget.palette()
        pal.setColor(role, color)
        widget.setPalette(pal)
        if fill:
            widget.setAutoFillBackground(True)

    def _keep(self, obj: _T) -> _T:
        """Qt オブジェクトの Python ラッパを保持して GC を防ぐ（受けた obj を返す）。"""
        self._keepalive.append(obj)
        return obj

    def _new_action(
        self,
        menu: QtWidgets.QMenu,
        label: str,
        *,
        checkable: bool = False,
        checked: bool = False,
        tooltip: str = "",
    ) -> QAction:
        """窓を親にした ``QAction`` を生成して menu に追加し、それを返す。

        親を窓 (``self``) に固定し C++ 実体の寿命を窓と合わせる。``menu.addAction(str)``
        は PySide6 で所有が曖昧になり GC で消えるため使わない。

        Args:
            menu: 追加先のメニュー。
            label: アクションのラベル。
            checkable: チェック可能にするか。
            checked: 初期チェック状態（``checkable`` 時のみ有効）。
            tooltip: 設定するツールチップ（空なら未設定）。

        Returns:
            生成した ``QAction``。
        """
        action = QAction(label, self)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
        if tooltip:
            action.setToolTip(tooltip)
        menu.addAction(action)
        return action

    def _build_menu_bar(self) -> QtWidgets.QMenuBar:
        """上部メニューバーを組む（Options・Edit）。

        Options に Force connect（接続/コピーの強制トグル）を置く。Edit に属性の
        並び替え・属性名表示（左右共通）を置く。

        Returns:
            設定済みの ``QMenuBar``。
        """
        bar = QtWidgets.QMenuBar()
        self._build_options_menu(bar)
        self._build_edit_menu(bar)
        return bar

    # ---- メニューバー設定の永続化（次回起動へ持ち越す・③） ----
    def _read_menu_settings(self) -> dict:
        """保存済みメニュー設定の dict を返す（未注入/未保存なら空 dict）。"""
        if self._settings is None:
            return {}
        saved = self._settings.read(_MENU_SETTINGS_KEY, {})
        return saved if isinstance(saved, dict) else {}

    def _apply_saved_modes_to_vm(self) -> None:
        """保存済みの Sort/Name モードをメニュー構築前に VM へ適用する。

        不正値は無視して既定のままにする（保存形式の破損で起動を妨げない）。
        """
        sort_name = self._saved_menu.get("sort_mode")
        if sort_name in SortMode.__members__:
            self._vm.set_sort_mode(SortMode[sort_name])
        name_name = self._saved_menu.get("name_mode")
        if name_name in NameMode.__members__:
            self._vm.set_name_mode(NameMode[name_name])

    def _save_menu_settings(self, *_args: object) -> None:
        """現在のメニューバー状態を永続化する（未注入なら no-op）。

        Options 3 トグルと Edit の Sort/Name モードを 1 まとめで保存する。各メニュー
        項目の変更（``toggled`` / ``triggered``）から呼ばれる。
        """
        if self._settings is None:
            return
        self._settings.write(
            _MENU_SETTINGS_KEY,
            {
                "force_connect": self._force_action.isChecked(),
                "force_disconnect": self._force_disconnect_action.isChecked(),
                "scroll_to_connected": self._autoscroll_action.isChecked(),
                "sort_mode": self._vm.sort_mode().name,
                "name_mode": self._vm.name_mode().name,
            },
        )

    def _build_options_menu(self, bar: QtWidgets.QMenuBar) -> None:
        """Options メニューに Force connect トグルを追加する。

        ロック解除 / 既存接続を置き換えて強制接続・上書きするモード。接続/コピーの
        各操作（ドラッグ・ボタン・右クリック）が共通で参照する（既定 OFF）。

        Args:
            bar: 追加先のメニューバー。
        """
        saved = self._saved_menu
        menu = self._keep(bar.addMenu("Options"))
        self._force_action = self._new_action(
            menu,
            "Force connect",
            checkable=True,
            checked=bool(saved.get("force_connect", False)),
            tooltip="Unlock / replace existing input and force-connect or overwrite",
        )
        self._force_action.toggled.connect(self._save_menu_settings)
        self._force_disconnect_action = self._new_action(
            menu,
            "Force disconnect",
            checkable=True,
            checked=bool(saved.get("force_disconnect", False)),
            tooltip="Unlock a locked input attribute to disconnect it",
        )
        self._force_disconnect_action.toggled.connect(self._save_menu_settings)
        self._autoscroll_action = self._new_action(
            menu,
            "Scroll to connected",
            checkable=True,
            checked=bool(saved.get("scroll_to_connected", False)),
            tooltip=(
                "Select an attribute to scroll the opposite side to its "
                "connected attribute"
            ),
        )
        self._autoscroll_action.toggled.connect(self._save_menu_settings)

    def _build_edit_menu(self, bar: QtWidgets.QMenuBar) -> None:
        """Edit メニューに並び替え・属性名表示（左右共通）を追加する。

        いずれも排他ラジオ。選択で ViewModel の ``set_sort_mode`` / ``set_name_mode``
        を呼ぶ（表示が変わるため structural 通知でツリーが再構築される）。

        Args:
            bar: 追加先のメニューバー。
        """
        menu = self._keep(bar.addMenu("Edit"))
        self._build_radio_submenu(
            menu,
            "Sort Attributes",
            (
                (SortMode.SCENE, "Scene Order"),
                (SortMode.ASC, "Name (A→Z)"),
                (SortMode.DESC, "Name (Z→A)"),
            ),
            self._vm.sort_mode(),
            self._vm.set_sort_mode,
        )
        self._build_radio_submenu(
            menu,
            "Attribute Names",
            (
                (NameMode.LONG, "Long Name"),
                (NameMode.SHORT, "Short Name"),
            ),
            self._vm.name_mode(),
            self._vm.set_name_mode,
        )

    def _build_radio_submenu(
        self,
        menu: QtWidgets.QMenu,
        title: str,
        items: tuple[tuple[object, str], ...],
        current: object,
        on_select: Callable[[object], None],
    ) -> None:
        """排他ラジオのサブメニューを組む（並び替え/名前表示で共用）。

        Args:
            menu: 親メニュー。
            title: サブメニュー名。
            items: ``(値, ラベル)`` の並び。
            current: 現在選択中の値（チェック初期化用）。
            on_select: 選択時に呼ぶハンドラ（値を1つ受ける）。
        """
        sub = self._keep(menu.addMenu(title))
        group = self._keep(QActionGroup(sub))
        group.setExclusive(True)
        for value, label in items:
            action = self._new_action(
                sub, label, checkable=True, checked=(value == current)
            )
            group.addAction(action)
            action.triggered.connect(lambda *_, v=value: on_select(v))
            action.triggered.connect(self._save_menu_settings)

    def _build_header_row(
        self, left: QtWidgets.QLayout, right: QtWidgets.QLayout
    ) -> QtWidgets.QHBoxLayout:
        """上部の 1 行を [左 | 中央70 | 右] の3カラムで組む（モック構造）。

        中央のすき間は本体の中央帯（``_MIDDLE_GAP``）と同じ幅にして、各行の
        左右カラムが本体のツリーと縦に揃うようにする。

        Args:
            left: 左カラムに入れる片側レイアウト。
            right: 右カラムに入れる片側レイアウト。

        Returns:
            3カラムの行レイアウト。
        """
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        row.addLayout(left, 1)
        row.addSpacing(_MIDDLE_GAP)
        row.addLayout(right, 1)
        return row

    def _make_title(self, side: str) -> NodeTitle:
        """ノード名ヘッダのラベル（中央寄せの薄バー・クリックで選択）を作る。

        地色は ``blend(Base, Window, _BG_TITLE_K)`` の帯（モック #333 相当）、枠線は
        Load/Add ボタンと同じ暗い境界（モック #1e1e1e 相当）。stylesheet で背景＋枠を
        当てる（border のみだと bg が透けるため両方指定する）。長い名前は ``…`` 省略。

        Args:
            side: このヘッダが表す側（クリック時にこの側のノードを選択する）。

        Returns:
            設定済みの ``NodeTitle``。
        """
        title = NodeTitle(on_click=lambda s=side: self._select_side_nodes(s))
        title.setStyleSheet(
            f"QLabel{{background-color:{self._rgb_css(self._band(_BG_TITLE_K))};"
            f"border:1px solid {self._rgb_css(self._border_color())};}}"
        )
        return title

    def _select_side_nodes(self, side: str) -> None:
        """その側のロードノードをシーンで選択する（ヘッダクリック）。"""
        nodes = self._vm.nodes(side)
        if nodes:
            self._vm.select(nodes)

    def _build_title_row(self) -> QtWidgets.QHBoxLayout:
        """ノード名ヘッダ行を組む（左右ラベル + 中央に左右入替ボタン）。"""
        left_title = self._make_title(LEFT)
        right_title = self._make_title(RIGHT)
        self._titles[LEFT] = left_title
        self._titles[RIGHT] = right_title

        swap = QtWidgets.QToolButton()
        fg = self.palette().color(QtGui.QPalette.ButtonText)
        swap.setIcon(themed_icon("swap", fg))
        swap.setIconSize(QtCore.QSize(16, 16))
        swap.setFixedSize(28, 22)
        swap.setToolTip("左右を入れ替え")
        swap.clicked.connect(lambda *_: self._do_swap())
        # 中央の入替ボタンに地色（モック #4a4a4a 相当・Window より一段明るい）。
        win = self.palette().color(QtGui.QPalette.Window)
        button = self.palette().color(QtGui.QPalette.Button)
        swap.setStyleSheet(self._tool_bg_qss(blend(win, button, _BG_SWAP_K)))

        center = QtWidgets.QHBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch(1)
        center.addWidget(swap)
        center.addStretch(1)
        center_w = QtWidgets.QWidget()
        center_w.setFixedWidth(_MIDDLE_GAP)
        center_w.setLayout(center)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(left_title, 1)
        row.addWidget(center_w)
        row.addWidget(right_title, 1)
        return row

    def _build_tree_col(self, side: str) -> QtWidgets.QHBoxLayout:
        """片側のツリー列を組んで返す。

        両サイドともスクロールバーを**外側の端**に出す（左=左端 / 右=右端・master
        §3.1 / §10.4）。中央で接続を繋ぐ構成のため、中央寄りにバーがあるとポート/線の
        起点と干渉する。標準バーを隠し、外側に置いたカスタム ``QScrollBar`` をツリー
        内蔵バーと双方向同期する（レイアウト反転だと中身まで寄るため、バー位置だけ
        移す・§10.4）。左右で外/内が揃い対称になる。
        """
        tree = RowBackgroundTree()
        tree.setHeaderHidden(True)
        tree.setItemDelegate(RowHeightDelegate(_ROW_HEIGHT, tree))
        tree.setStyle(self._branch_style)  # コラプス三角を細い三角に（A-3）
        model = AttributeTreeModel(self._vm, side, tree)
        tree.setModel(model)
        self._trees[side] = tree
        self._models[side] = model
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        # 内蔵バーを隠し外付けバーを外側の端へ（常時表示で出入りの幅変動なし・問題1）。
        bar = QtWidgets.QScrollBar(Qt.Vertical)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._link_external_scrollbar(tree, bar)
        if side == LEFT:
            row.addWidget(bar)  # 左端
            row.addWidget(tree)
        else:
            row.addWidget(tree)
            row.addWidget(bar)  # 右端
        return row

    def _build_picker_row(self, side: str) -> QtWidgets.QHBoxLayout | None:
        """ノードピッカー行を組む（dev のみ・Maya では None・非依存確認用）。

        Maya 実機は実選択を使うのでピッカーは無い。dev では Fake の全ノードから
        選ぶためのコンボボックスを各サイドの一番上に置く（§3.2）。
        """
        if self._node_pool is None:
            return None
        row = QtWidgets.QHBoxLayout()
        picker = QtWidgets.QComboBox()
        for node in self._node_pool:
            picker.addItem(self._vm.display_label(node), node)
        self._pickers[side] = picker
        row.addWidget(picker, 1)
        return row

    def _build_load_bar(self, side: str) -> QtWidgets.QHBoxLayout:
        """片側の Load/Add ボタン行を組む（モック準拠・§3.2）。

        モックに合わせ Load:Add = 2:1 のストレッチ・高さ 26px にする。
        """
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(1)  # Load/Add の間に 1px のギャップ
        load = QtWidgets.QPushButton("Load Left" if side == LEFT else "Load Right")
        add = QtWidgets.QPushButton("Add")
        fg = self.palette().color(QtGui.QPalette.ButtonText)
        load.setIcon(themed_icon("download", fg))
        add.setIcon(themed_icon("plus", fg))
        for button in (load, add):
            button.setFixedHeight(_BTN_HEIGHT)
            button.setIconSize(QtCore.QSize(14, 14))
            font = button.font()
            font.setBold(True)  # モック準拠（ラベルを太字に）
            button.setFont(font)
        # Add は Load より一段暗い地色（モック #444 相当・Load は既定の明るめ）。
        self._set_role_bg(add, QtGui.QPalette.Button, self._band(_BG_ADD_K))
        load.clicked.connect(lambda *_, s=side: self._do_load(s))
        add.clicked.connect(lambda *_, s=side: self._do_add(s))
        row.addWidget(load, 2)
        row.addWidget(add, 1)
        return row

    def _build_filter_bar(self, side: str) -> QtWidgets.QHBoxLayout:
        """片側のフィルタ行を組む（テキスト + 型チップ + 漏斗・master §9）。

        フィルタはメニューに隠さず、フィルタ行の横に出して発見性を上げる（§9）。
        型チップは型色を背景に持ち凡例も兼ねる。漏斗には頻度の低い 2 トグル
        （Non-Keyable / Connected Only）を収める。
        """
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)  # フィルタ入力 / 型バッジ群 / 漏斗ボタンの間に 4px
        fg = self.palette().color(QtGui.QPalette.Text)
        text = QtWidgets.QLineEdit()
        text.setPlaceholderText("Filter...")
        text.setClearButtonEnabled(True)
        text.setFixedHeight(22)  # モック準拠
        # 欄内左に検索アイコン（モック準拠・装飾）。窓を親にして GC で消えないように。
        search_action = QAction(themed_icon("search", fg), "", self)
        text.addAction(search_action, QtWidgets.QLineEdit.LeadingPosition)
        text.textChanged.connect(lambda *_, s=side: self._rebuild_filter(s))
        self._filter_text[side] = text
        row.addWidget(text, 1)

        # 型チップ（N/B/M/C/D）。背景が型色＝凡例、頭文字で識別（§9）。
        labels = {
            TypeCategory.NUMERIC: "N",
            TypeCategory.BOOL: "B",
            TypeCategory.MATRIX: "M",
            TypeCategory.COLOR: "C",
            TypeCategory.DATA: "D",
        }
        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(2)  # モック準拠（チップ間 2px）
        base = self.palette().color(QtGui.QPalette.Base)
        off_fg, border = self._chip_colors()
        chips: dict[TypeCategory, QtWidgets.QPushButton] = {}
        for cat in (
            TypeCategory.NUMERIC,
            TypeCategory.BOOL,
            TypeCategory.MATRIX,
            TypeCategory.COLOR,
            TypeCategory.DATA,
        ):
            chip = FilterChip(labels[cat], category_color(cat), base, off_fg, border)
            chip.setFixedSize(15, 15)  # モック準拠（丸チップ 15px）
            chip.setToolTip(f"{labels[cat]} = {cat.name.lower()}")
            chip.clicked.connect(lambda *_, s=side, c=cat: self._on_chip_clicked(s, c))
            chips[cat] = chip
            chip_row.addWidget(chip)
        self._filter_chips[side] = chips
        row.addLayout(chip_row)

        # 漏斗ドロップダウン（Non-Keyable / Connected Only）。
        funnel = QtWidgets.QToolButton()
        funnel.setIcon(themed_icon("funnel", fg))  # モック準拠（漏斗アイコン）
        funnel.setIconSize(QtCore.QSize(14, 14))
        funnel.setFixedSize(24, 22)  # モック準拠
        funnel.setToolTip("表示オプション")
        funnel.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        # 地色をモックの帯（#3c3c3c）に + メニュー三角を消す。Maya スタイルは
        # QToolButton の Button ロールを無視するため stylesheet で焼く。
        funnel.setStyleSheet(
            self._tool_bg_qss(self._band(_BG_FUNNEL_K))
            + "QToolButton::menu-indicator{image:none;}"
        )
        menu = self._keep(QtWidgets.QMenu(funnel))
        # 既定で非 keyable も表示（現状の表示を保つ）。
        nk = self._new_action(menu, "Show Non-Keyable", checkable=True, checked=True)
        nk.toggled.connect(lambda *_, s=side: self._rebuild_filter(s))
        co = self._new_action(menu, "Show Connected Only", checkable=True)
        co.toggled.connect(lambda *_, s=side: self._rebuild_filter(s))
        ex = self._new_action(
            menu,
            "Show Extra Attribute Only",
            checkable=True,
            tooltip="Show only user-defined (extra) attributes",
        )
        ex.toggled.connect(lambda *_, s=side: self._rebuild_filter(s))
        funnel.setMenu(menu)
        self._filter_nonkeyable[side] = nk
        self._filter_connected[side] = co
        self._filter_extra_only[side] = ex
        row.addWidget(funnel)
        return row

    def _chip_colors(self) -> tuple[QtGui.QColor, QtGui.QColor]:
        """型チップの低トーン文字色・枠線色を返す（パレット相対・テーマ追従）。

        パレットから相対計算し、ライト/ダークどちらでも馴染ませる（固定の明色を
        避ける）。塗り色は型色（``category_color``）からトーン別に算出する。

        Returns:
            (off_fg, border) の ``QColor`` タプル。
        """
        pal = self.palette()
        base = pal.color(QtGui.QPalette.Base)
        text = pal.color(QtGui.QPalette.Text)
        off_fg = blend(text, base, 0.55)  # 薄い文字（低トーン用）
        border = blend(text, base, 0.55)
        return off_fg, border

    def _on_chip_clicked(self, side: str, clicked_cat: TypeCategory) -> None:
        """型チップのクリックを案A（ソロ＋Ctrl 複数）で処理する。

        QPushButton の自動トグル後の状態からクリック前集合を復元し、
        ``_next_type_filter`` で次の表示型集合を決めて全チップへ反映する。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            clicked_cat: 押された型分類。
        """
        chips = self._filter_chips[side]
        after_auto = frozenset(c for c, w in chips.items() if w.isChecked())
        before = after_auto ^ {clicked_cat}  # 自動トグルを戻したクリック前集合
        ctrl = bool(QtWidgets.QApplication.keyboardModifiers() & Qt.ControlModifier)
        desired = _next_type_filter(
            before, clicked_cat, frozenset(TypeCategory), ctrl=ctrl
        )
        for cat, chip in chips.items():
            chip.blockSignals(True)
            chip.setChecked(cat in desired)
            chip.blockSignals(False)
        self._apply_chip_tones(side, desired)
        self._rebuild_filter(side)

    def _apply_chip_tones(self, side: str, enabled: frozenset[TypeCategory]) -> None:
        """表示型集合に応じて各チップのトーンを設定する（案③）。

        全表示（全カテゴリ）なら全チップ中立、絞り込み中は選択=高 / 非選択=低。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            enabled: 現在表示する型分類の集合。
        """
        filtering = enabled != frozenset(TypeCategory)
        for cat, chip in self._filter_chips[side].items():
            if not filtering:
                chip.set_tone("mid")
            else:
                chip.set_tone("high" if cat in enabled else "low")

    def _rebuild_filter(self, side: str) -> None:
        """フィルタ行の現在状態から ``FilterCriteria`` を組んで ViewModel に渡す。"""
        cats = frozenset(
            cat for cat, chip in self._filter_chips[side].items() if chip.isChecked()
        )
        criteria = FilterCriteria(
            enabled_categories=cats,
            show_non_keyable=self._filter_nonkeyable[side].isChecked(),
            show_connected_only=self._filter_connected[side].isChecked(),
            extra_only=self._filter_extra_only[side].isChecked(),
            text=self._filter_text[side].text(),
        )
        self._vm.set_filter(side, criteria)

    def _build_action_bar(self) -> QtWidgets.QHBoxLayout:
        """下部のアクション行を組む（方向トグル + 接続/コピー・master §5.2/§5.3）。

        左右で 1 つずつ選択し、方向トグル（→ / ←）の向きに従って Connect /
        Connect Leaf / Copy Value を実行する。ボタンの実処理は
        ``InteractionController`` に委ねる（生成順の都合で遅延参照する）。中央寄せに
        して左右の選択列の中間に置く。
        """
        row = QtWidgets.QHBoxLayout()
        # 方向トグルは Connect ボタンと同じ高さ（_BTN_HEIGHT）の正方形にする。
        # 矢印は SVG アイコン（テーマ追従・swap 等と同じ themed_icon 方式）。
        self._dir_toggle = QtWidgets.QToolButton()
        self._dir_toggle.setCheckable(True)
        self._dir_toggle.setFixedSize(_BTN_HEIGHT, _BTN_HEIGHT)
        self._dir_toggle.setIconSize(QtCore.QSize(_DIR_ICON_PX, _DIR_ICON_PX))
        self._dir_toggle.setStyleSheet(self._tool_bg_qss(self._band(_BG_ADD_K)))
        self._dir_toggle.toggled.connect(self._on_direction_toggled)
        self._on_direction_toggled(False)  # 既定 = 左→右（→）のアイコン/ヒントを当てる
        row.addStretch(1)
        row.addWidget(self._dir_toggle)
        # Connect / Connect Leaf / Copy Value は Load/Add と同じ 1px 間隔でまとめる
        # （トグルとの間は row の既定間隔のまま＝現状維持）。
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(1)
        actions = (
            ("Connect", lambda *_: self._interaction.connect_selected()),
            ("Connect Leaf", lambda *_: self._interaction.connect_leaf_selected()),
            ("Copy Value", lambda *_: self._interaction.copy_value_selected()),
        )
        action_btns = []
        for label, slot in actions:
            # Load ボタンと同じ高さ・太字ラベル（色は ButtonText 既定で一致）。
            btn = QtWidgets.QPushButton(label)
            btn.setFixedHeight(_BTN_HEIGHT)
            btn_font = btn.font()
            btn_font.setBold(True)
            btn.setFont(btn_font)
            btn.clicked.connect(slot)
            btns.addWidget(btn)
            action_btns.append(btn)
        # 3 ボタンの幅を最大（= Connect Leaf）にそろえる（均等幅で見た目を整える）。
        uniform_w = max(btn.sizeHint().width() for btn in action_btns)
        for btn in action_btns:
            btn.setFixedWidth(uniform_w)
        row.addLayout(btns)
        # 方向トグルと同じ幅の不可視スタブを右端に対で置き、3 ボタンの中心を
        # ウィンドウ中央に合わせる（左右対称＝stretch も spacing も対称に入る）。
        stub = QtWidgets.QWidget()
        stub.setFixedSize(_BTN_HEIGHT, _BTN_HEIGHT)
        row.addWidget(stub)
        row.addStretch(1)
        return row

    def _build_footer(self) -> QtWidgets.QWidget:
        """フッタ帯（アクション行）を地色＋上端区切り線付きの QWidget で組む。

        単に行へ色を置くと帯に見えないため、本体との境に上端ボーダー（Load ボタンと
        同じ暗い境界）を引いてフッタとして分離する（モック ``border-top`` 準拠）。地色は
        ``_BG_FOOTER_K`` 帯。stylesheet は objectName で本体のみに限定し、子（ボタン）
        には波及させない。

        Returns:
            設定済みのフッタ ``QWidget``。
        """
        footer = QtWidgets.QWidget()
        footer.setObjectName("footer")
        footer.setLayout(self._build_action_bar())
        footer.setStyleSheet(
            f"QWidget#footer{{background-color:{self._rgb_css(self._band(_BG_FOOTER_K))};"
            f"border-top:1px solid {self._rgb_css(self._border_color())};}}"
        )
        return footer

    def _on_direction_toggled(self, checked: bool) -> None:
        """方向トグルの矢印アイコンとツールチップを状態に合わせて更新する。

        Args:
            checked: True なら R2L（右→左）、False なら L2R（左→右）。
        """
        fg = self.palette().color(QtGui.QPalette.ButtonText)
        if checked:
            self._dir_toggle.setIcon(themed_icon("arrow_left", fg, _DIR_ICON_PX))
            self._dir_toggle.setToolTip("Direction: Right to Left")
        else:
            self._dir_toggle.setIcon(themed_icon("arrow_right", fg, _DIR_ICON_PX))
            self._dir_toggle.setToolTip("Direction: Left to Right")

    def _direction(self) -> bool:
        """方向トグルが L2R（左→右）なら True を返す（未チェック=L2R）。"""
        return not self._dir_toggle.isChecked()

    def _force_on(self) -> bool:
        """Force connect トグル（Options メニュー）が ON か返す。"""
        return self._force_action.isChecked()

    def _force_disconnect_on(self) -> bool:
        """Force disconnect トグル（Options メニュー）が ON か返す。"""
        return self._force_disconnect_action.isChecked()

    def _autoscroll_on(self) -> bool:
        """Scroll to connected トグル（Options メニュー）が ON か返す。"""
        return self._autoscroll_action.isChecked()

    def _on_tree_current_changed(self, side: str) -> None:
        """選択変更時に反対側を接続相手までスクロール＋選択する（Scroll to connected）。

        トグル OFF / 同期中は何もしない。選択 plug の接続相手（source/destination）を
        反対側ツリーで逆引きし、見つかればその親を展開してスクロール＋選択する。
        相手が複数なら反対側で最初に見つかった1つに合わせる。

        Args:
            side: 選択が変わった側（``LEFT``/``RIGHT``）。
        """
        if self._syncing or not self._autoscroll_on():
            return
        this_tree = self._trees[side]
        plug = self._models[side].plug_at(this_tree.currentIndex())
        if plug is None:
            return
        conns = self._vm.get_connections(plug)
        partners = list(conns.sources) + list(conns.destinations)
        other = RIGHT if side == LEFT else LEFT
        other_model = self._models[other]
        other_tree = self._trees[other]
        for partner in partners:
            index = other_model.index_for_plug(partner)
            if not index.isValid():
                continue
            self._syncing = True
            try:
                self._expand_ancestors(other_tree, index)
                other_tree.setCurrentIndex(index)
                self._align_row(this_tree, other_tree, index)
            finally:
                self._syncing = False
            return

    def _align_row(
        self,
        this_tree: QtWidgets.QTreeView,
        other_tree: QtWidgets.QTreeView,
        index: QtCore.QModelIndex,
    ) -> None:
        """相手行を選択行と同じ高さへ寄せる（接続線が水平に近くなる・可能な範囲で）。

        まず相手行を一旦可視化し、選択行の上端と相手行の上端のピクセル差を行数に
        換算して相手側の縦スクロール量に加える（縦バーは行単位＝per-item のため）。
        スクロール端で合わせ切れない場合は ``setValue`` が範囲内に丸めるため、可能な
        限り近い高さに寄る。

        Args:
            this_tree: 選択操作が起きた側のツリー。
            other_tree: 相手行を表示する反対側のツリー。
            index: 反対側で合わせたい相手行の ``QModelIndex``。
        """
        other_tree.scrollTo(index, QtWidgets.QAbstractItemView.PositionAtCenter)
        this_top = this_tree.visualRect(this_tree.currentIndex()).top()
        other_rect = other_tree.visualRect(index)
        if not other_rect.isValid():
            return
        # 縦バーは per-item（行単位）。ピクセル差を行高で割って行数に換算する。
        delta_rows = round((other_rect.top() - this_top) / _ROW_HEIGHT)
        if delta_rows:
            bar = other_tree.verticalScrollBar()
            bar.setValue(bar.value() + delta_rows)

    @staticmethod
    def _expand_ancestors(tree: QtWidgets.QTreeView, index: QtCore.QModelIndex) -> None:
        """Index の祖先行を上から順に展開する（scrollTo で見えるようにする）。"""
        parents = []
        parent = index.parent()
        while parent.isValid():
            parents.append(parent)
            parent = parent.parent()
        for parent in reversed(parents):
            tree.expand(parent)

    def _apply_pick(self, side: str) -> None:
        """ピッカーの選択をシーン選択へ反映する（dev のみ・Load/Add の直前）。

        dev は左右に別ピッカーを持つが、シーン選択は 1 つなので、押した側の
        ピッカー値をその場で選択に反映してから Load/Add する。Maya 実機では
        ピッカーが無く、実機の選択がそのまま使われる。
        """
        picker = self._pickers.get(side)
        if picker is not None and picker.currentData() is not None:
            self._vm.select([picker.currentData()])

    @error_handler
    def _do_load(self, side: str) -> None:
        """その側のピッカー選択を反映して Load（置換）する（§3.2）。"""
        self._apply_pick(side)
        self._vm.load_selected(side)

    @error_handler
    def _do_add(self, side: str) -> None:
        """その側のピッカー選択を反映して Add（追加）する（§3.2）。"""
        self._apply_pick(side)
        self._vm.add_selected(side)

    def _do_swap(self) -> None:
        """左右を丸ごと入れ替える（ノード列とフィルタ条件の両方）。

        ViewModel の状態を入れ替えたあと、フィルタUIの表示を入れ替え後の条件へ
        同期する（ツリー再構築は structural 通知が行う）。
        """
        self._vm.swap_sides()
        for side in (LEFT, RIGHT):
            self._sync_filter_widgets(side)

    def _sync_filter_widgets(self, side: str) -> None:
        """フィルタUIの表示を ViewModel の現在条件に合わせる（信号は抑止）。

        入替などで ViewModel 側の条件が変わったとき、ウィジェット側を追従させる。
        ``_rebuild_filter`` の再入を避けるため各ウィジェットの信号を一時停止する。

        Args:
            side: ``LEFT`` または ``RIGHT``。
        """
        crit = self._vm.filter_criteria(side)
        text = self._filter_text[side]
        text.blockSignals(True)
        text.setText(crit.text)
        text.blockSignals(False)
        for cat, chip in self._filter_chips[side].items():
            chip.blockSignals(True)
            chip.setChecked(cat in crit.enabled_categories)
            chip.blockSignals(False)
        self._apply_chip_tones(side, crit.enabled_categories)
        nk = self._filter_nonkeyable[side]
        nk.blockSignals(True)
        nk.setChecked(crit.show_non_keyable)
        nk.blockSignals(False)
        co = self._filter_connected[side]
        co.blockSignals(True)
        co.setChecked(crit.show_connected_only)
        co.blockSignals(False)
        ex = self._filter_extra_only[side]
        ex.blockSignals(True)
        ex.setChecked(crit.extra_only)
        ex.blockSignals(False)

    def _link_external_scrollbar(
        self, tree: QtWidgets.QTreeView, bar: QtWidgets.QScrollBar
    ) -> None:
        """ツリーの内蔵縦バーと外側カスタムバーを双方向同期する（左右共用・§3.1）。

        内蔵バー (``inner``) は Maya のスタイル再適用などで作り直され得るため、
        キャプチャしたポインタを使い回さず毎回 ``tree`` から取り直す。さらに各
        クロス参照を ``shiboken.isValid`` でガードし、C++ 実体が先に破棄された
        タイミングで触れても ``already deleted`` で落ちないようにする。
        """

        def sync_range() -> None:
            if not shiboken.isValid(tree) or not shiboken.isValid(bar):
                return
            inner = tree.verticalScrollBar()
            if not shiboken.isValid(inner):
                return
            bar.setRange(inner.minimum(), inner.maximum())
            bar.setPageStep(inner.pageStep())
            bar.setSingleStep(inner.singleStep())
            bar.setValue(inner.value())
            # バーは常に表示して幅を確保し、出入りでリスト幅が変わらないようにする
            # （問題1）。スクロール不要なときは無効表示にして触れないことを示す。
            bar.setEnabled(inner.maximum() > inner.minimum())

        def push_to_inner(value: int) -> None:
            if not shiboken.isValid(tree):
                return
            inner = tree.verticalScrollBar()
            if shiboken.isValid(inner):
                inner.setValue(value)

        def push_to_bar(value: int) -> None:
            if shiboken.isValid(bar):
                bar.setValue(value)

        inner = tree.verticalScrollBar()
        sync_range()
        inner.rangeChanged.connect(lambda *_: sync_range())
        inner.valueChanged.connect(push_to_bar)
        bar.valueChanged.connect(push_to_inner)

    # ---- 同期 ----
    def _on_vm_changed(self, structural: bool, side: str | None = None) -> None:
        """ViewModel 変更時の同期。

        接続のみの変化（structural=False）ではツリー構造は不変なので、モデルを
        作り直さず再描画だけ行う（展開状態を保つ）。load/add/remove のときだけ
        モデルを再構築する。``side`` 指定時（フィルタ等の片側変化）はその側だけ
        再構築し、Qt の全リセットコストを半減する（B）。

        Args:
            structural: ツリー構造が変わる変化なら True。
            side: 片側だけの構造変化ならその側名。``None`` なら両側を再構築する。
        """
        if structural:
            targets = (side,) if side is not None else (LEFT, RIGHT)
            for target in targets:
                selected = self._capture_selection(target)
                expanded = self._capture_expanded(target)
                self._models[target].refresh()
                # トップレベル＝ノードセクションは既定で開いて属性を見せる（§4.6・
                # 新規ロードノードも開く）。その上で展開状態を復元 or 自動展開する。
                self._trees[target].expandToDepth(0)
                self._sync_expansion(target, expanded)
                self._restore_selection(target, selected)
            self._refresh_titles()
        self._overlay.update()

    def _on_row_toggled(self) -> None:
        """行の展開/折りたたみで接続線を引き直す（自動展開のバッチ中は抑止）。"""
        if not self._batch_expand:
            self._overlay.update()

    def _sync_expansion(self, side: str, prev_expanded: set) -> None:
        """再構築後の展開状態を整える（テキスト絞り込み中はマッチ祖先を自動展開）。

        テキストが入っている間はマッチの祖先を自動展開し、深い一致を即見せる。
        テキストが空なら通常どおり以前の展開状態を復元する。テキスト ON への遷移時に
        遷移前の展開状態を退避し、OFF への遷移時にそれを復元する（クリアで元に戻す）。

        Args:
            side: 対象サイド。
            prev_expanded: 再構築前に記録した展開中の行の識別子集合。
        """
        has_text = bool(self._vm.filter_criteria(side).text)
        was_active = self._text_active.get(side, False)
        if has_text:
            if not was_active:
                # テキスト絞り込みに入る瞬間の展開状態を退避（クリア時に戻す）。
                self._pre_filter_expanded[side] = prev_expanded
                self._text_active[side] = True
            self._auto_expand(side)
        else:
            if was_active:
                # 絞り込み解除：退避した展開状態に戻す。
                prev_expanded = self._pre_filter_expanded.pop(side, prev_expanded)
                self._text_active[side] = False
            self._restore_expanded(side, prev_expanded)

    def _auto_expand(self, side: str) -> None:
        """マッチ祖先（＝絞り込み後に残る展開可能行）をまとめて展開する。

        テキスト絞り込み中の表示集合はマッチとその祖先だけなので、可視ツリーの全展開
        ＝マッチ祖先の展開になる（案a）。連続展開での overlay 再描画はバッチフラグで
        抑え、呼び出し元（``_on_vm_changed``）末尾の 1 回の更新にまとめる。

        Args:
            side: 対象サイド。
        """
        self._batch_expand = True
        try:
            self._trees[side].expandAll()
        finally:
            self._batch_expand = False

    def _capture_selection(self, side: str) -> PlugId | None:
        """現在選択中の属性 plug を退避する（structural 再構築の前・§5.3）。

        モデル再構築で ``QModelIndex`` は無効化するため、再構築後に ``index_for_plug``
        で引き直せる安定キー（``PlugId``）で覚える。セクション行は ``None``。

        Args:
            side: 対象サイド。

        Returns:
            選択中の属性 ``PlugId``。属性を選んでいなければ ``None``。
        """
        tree = self._trees[side]
        return self._models[side].plug_at(tree.currentIndex())

    def _restore_selection(self, side: str, plug: PlugId | None) -> None:
        """退避した選択 plug を再構築後のツリーで選び直す（消えていれば何もしない）。

        ライブ同期での構造再構築（Copy Value による array 要素の実体化など）後も、
        次の操作へ向けて選択を保つ。``setCurrentIndex`` の ``currentChanged`` が
        Scroll to connected 等を再発火しないよう ``_syncing`` で囲う（U-6/U-9 と同型）。

        Args:
            side: 対象サイド。
            plug: 復元する属性 ``PlugId``（``None`` なら何もしない）。
        """
        if plug is None:
            return
        index = self._models[side].index_for_plug(plug)
        if not index.isValid():
            return
        self._syncing = True
        try:
            self._trees[side].setCurrentIndex(index)
        finally:
            self._syncing = False

    def _capture_expanded(self, side: str) -> set:
        """現在展開中の行の識別子（NodeId/PlugId）を集める（structural 再構築の前）。

        モデル再構築で ``QModelIndex`` は無効化するため、uuid 基準で安定ハッシュできる
        ``NodeId``（セクション）/ ``PlugId``（属性）で記録する（問題3 の展開維持）。

        Args:
            side: 対象サイド。

        Returns:
            展開中の行の識別子集合。
        """
        tree = self._trees[side]
        model = self._models[side]
        keys: set = set()

        def visit(parent: QtCore.QModelIndex) -> None:
            for row in range(model.rowCount(parent)):
                idx = model.index(row, 0, parent)
                if tree.isExpanded(idx):
                    key = model.section_at(idx) or model.plug_at(idx)
                    if key is not None:
                        keys.add(key)
                    visit(idx)

        visit(QtCore.QModelIndex())
        return keys

    def _restore_expanded(self, side: str, keys: set) -> None:
        """``_capture_expanded`` で記録した行を再構築後のツリーで再展開する。

        遅延展開のため上から順に展開する（親を開かないと子が来ない）。

        Args:
            side: 対象サイド。
            keys: 再展開する識別子集合。
        """
        if not keys:
            return
        tree = self._trees[side]
        model = self._models[side]

        def visit(parent: QtCore.QModelIndex) -> None:
            for row in range(model.rowCount(parent)):
                idx = model.index(row, 0, parent)
                key = model.section_at(idx) or model.plug_at(idx)
                if key is not None and key in keys:
                    tree.setExpanded(idx, True)
                    visit(idx)

        visit(QtCore.QModelIndex())

    def _refresh_titles(self) -> None:
        """左右のノード名ヘッダ（読込ノード名）を更新する（モック準拠）。

        モックに合わせて読込中のノード名を並べて表示する。未ロード時は左右の
        見出し（Left/Right）にフォールバックする。
        """
        for side in (LEFT, RIGHT):
            nodes = self._vm.nodes(side)
            if nodes:
                names = ", ".join(self._vm.display_label(node) for node in nodes)
            else:
                names = "Left" if side == LEFT else "Right"
            self._titles[side].set_full_text(names)

    # ---- レイアウト追従 ----
    def resizeEvent(self, event: QtCore.QEvent) -> None:
        """オーバーレイをウィンドウ全体に追従させる。"""
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    # ---- ユーザー通知（スロット/操作の境界から呼ぶ） ----
    def _notify_user(self, level: str, message: str) -> None:
        """ユーザー通知を注入チャネルへ流す（無ければレベル相当でログ）。

        ``level`` は ``"warning"``（予測できる失敗・実行時拒否）か ``"error"``
        （予測できない例外）。Maya は ``displayWarning`` / ``displayError`` へ
        振り分けて注入する。reporter 自身が投げても UI を巻き込まないよう握る。

        Args:
            level: ``"warning"`` または ``"error"``。
            message: 表示する文言。
        """
        if self._on_notify is not None:
            try:
                self._on_notify(level, message)
                return
            except Exception:  # noqa: BLE001  reporter の失敗で UI を巻き込まない
                logger.exception("on_notify reporter で例外")
        # 未注入（dev/テスト）or reporter 失敗時はレベル相当でログ。
        if level == "warning":
            logger.warning(message)
        else:
            logger.error(message)

    def _report_error(self, exc: BaseException) -> None:
        """捕捉した例外をエラーレベルで通知する（``error_handler`` の規約名）。

        トレースバックのログは ``error_handler`` 側で済んでいるため、ここでは
        エラーレベルの通知（Maya は赤帯）への受け渡しだけを行う。

        Args:
            exc: 捕捉した例外。
        """
        self._notify_user("error", str(exc))

    # ---- アプリ全体フィルタの登録/解除（閉じたら残さない） ----
    def _install_app_filter(self) -> None:
        """アプリ全体イベントフィルタを登録する（多重登録は flag で防ぐ）。"""
        if self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _remove_app_filter(self) -> None:
        """登録済みのアプリ全体イベントフィルタを解除する。"""
        if not self._app_filter_installed:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._app_filter_installed = False

    def showEvent(self, event: QtCore.QEvent) -> None:
        """表示時にアプリ全体フィルタを（解除されていれば）登録し直す。"""
        self._install_app_filter()
        super().showEvent(event)

    def closeEvent(self, event: QtCore.QEvent) -> None:
        """閉じる時にアプリ全体フィルタを解除する（旧窓が押下を奪い続けない）。

        QApplication は Maya セッション中ずっと生きるため、登録したフィルタを残すと
        閉じた後も（次に開いた窓を含め）押下を横取りし、リロード/再起動で旧コードが
        効き続ける。閉じた時点で必ず解除する（再表示時は ``showEvent`` で復帰）。
        """
        self._remove_app_filter()
        super().closeEvent(event)

    # ---- 入力（アプリ全体フィルタ・操作系はコントローラへ委譲・master §5.1） ----
    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """アプリ全体の入力を受け、操作系コントローラへ転送する。

        ポート円は viewport の境界線上にあり個別フィルタに届かない押下があるため、
        本ウィンドウがアプリ全体で受ける（意図的な設計・PROGRESS `8baa6f0`）。実際の
        分岐（ドラッグ/線選択/横断/マーキー/値メニュー）は ``InteractionController``
        が担い、消費しなければ既定処理へ素通りさせる。
        """
        if event.type() == QtCore.QEvent.KeyPress and self._handle_redo_key(event):
            return True
        if self._interaction is not None and self._interaction.handle_event(obj, event):
            return True
        return super().eventFilter(obj, event)

    @error_handler
    def _handle_redo_key(self, event: QtCore.QEvent) -> bool:
        """Shift+Z で Redo を実行する（窓フォーカス時の Maya Redo 橋渡し・§7.1(1)）。

        ``on_redo`` 未注入（dev）は何もしない。テキスト入力中（フィルタ欄等）は通常
        入力に渡す。Ctrl 無しの純粋な Shift+Z のみ（Ctrl+Z は Maya の Undo に委ねる）。
        """
        if self._on_redo is None or event.key() != Qt.Key_Z:
            return False
        mods = event.modifiers()
        if not (mods & Qt.ShiftModifier) or (mods & Qt.ControlModifier):
            return False
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QComboBox)):
            return False  # テキスト入力中は Redo に化けさせない
        self._on_redo()
        return True

    # ---- ライブ同期（watcher）連携用アクセサ ----
    @property
    def viewmodel(self) -> EditorViewModel:
        """この窓の ViewModel を返す（Maya watcher を繋ぐため）。"""
        return self._vm

    def is_interacting(self) -> bool:
        """ユーザー操作が進行中か返す（外部変更フラッシュの延期判定用）。"""
        return self._interaction is not None and self._interaction.is_interacting()


def _as_list(value: NodeId | list[NodeId] | None) -> list[NodeId]:
    """単一ノード / リスト / None を ``list[NodeId]`` に正規化する。"""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def build_app(
    scene: SceneAccess,
    left: NodeId | list[NodeId] | None = None,
    right: NodeId | list[NodeId] | None = None,
    node_pool: list[NodeId] | None = None,
    parent: QtWidgets.QWidget | None = None,
    on_redo: Callable[[], None] | None = None,
    on_notify: Callable[[str, str], None] | None = None,
    settings: SettingsStore | None = None,
) -> tuple[QtWidgets.QApplication, EditorWindow]:
    """SceneAccess を注入してアプリとウィンドウを生成する（master §1.4）。

    UI は具象 SceneAccess 実装を import せず、ここで受け取る（dev は Fake、本番は
    MayaSceneAccess を渡す）。既存の ``QApplication`` があればそれを使う（Maya は
    自前の app を持つため新規生成しない）。

    Args:
        scene: 注入する SceneAccess 実装。
        left: 左にロードするノード（単一 / リスト / 省略）。
        right: 右にロードするノード（単一 / リスト / 省略）。
        node_pool: dev の Load/Add ピッカーに並べる選択肢（Maya では ``None``）。
        parent: 親ウィンドウ。Maya 統合時は Maya メインウィンドウを渡し、生成した
            ウィンドウを Maya に所有させる（master §10.4。workspaceControl 化自体は
            実機・§14）。dev は ``None``。
        on_redo: ``Shift+Z`` 押下時の Redo ハンドラ（Maya は ``cmds.redo`` を注入）。
            dev は ``None``。
        on_notify: ユーザー通知先 ``(level, message)``（``level`` = ``"warning"`` /
            ``"error"``）。Maya は ``displayWarning``（黄）/ ``displayError``（赤）へ
            振り分けて注入する。dev/テストは ``None``（ログのみ）。
        settings: メニューバーのオプションを次回起動へ持ち越す永続化先。Maya は
            ``OptionVarSettings`` を注入する。dev/テストは ``None``（永続化なし）。

    Returns:
        (QApplication, EditorWindow) のタプル。
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # 型色を Color Settings 由来へ差し替える（Maya=実値 / Fake=空→既定・master §4.2）。
    set_type_colors(scene.get_attribute_type_colors())
    vm = EditorViewModel(scene)
    window = EditorWindow(
        vm,
        parent=parent,
        node_pool=node_pool,
        on_redo=on_redo,
        on_notify=on_notify,
        settings=settings,
    )
    vm.set_nodes(LEFT, _as_list(left))
    vm.set_nodes(RIGHT, _as_list(right))
    return app, window
