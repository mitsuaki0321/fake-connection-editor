"""エディタの状態を保持する ViewModel（master §1.2 / A1=ViewModel 独立）。

Maya にも Qt にも依存しない純粋 Python。ロード済みノード・接続操作・接続状態を
保持し、Core（§8）の判定関数を呼ぶ。UI へは ``TreeNode`` 等のプレーンデータと
変更通知（Qt 非依存の listener コールバック）で伝える。

範囲:
    - 左右に複数ノードをロード（Load=置換 / Add=追加）。
    - ツリー（遅延展開）・接続状態・型タグの供給。
    - ドラッグ接続/切断・force/leaf 接続・ゴースト要素の実体化。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from logging import getLogger

from ..core import (
    ConnectCheck,
    FilterCriteria,
    LeafConnectCheck,
    LeafReason,
    TreeNode,
    TypeCategory,
    build_array_child_nodes,
    build_child_nodes,
    check_connect,
    check_leaf_connect,
    ghost_indices,
    is_compatible,
    should_display,
)
from ..scene_access.interface import (
    AttrMeta,
    Connections,
    NodeId,
    PlugId,
    SceneAccess,
)
from .meta_store import MetaStore

logger = getLogger(__name__)

LEFT = "left"
RIGHT = "right"


class SortMode(Enum):
    """属性の並び順（左右共通・master Connection Editor 準拠）。

    array 要素はインデックス順を保ち、ソート対象はトップレベル属性と compound 子。
    """

    SCENE = "scene"  # 現順（シーン定義順・ソートしない）
    ASC = "asc"  # 名前昇順（A→Z）
    DESC = "desc"  # 名前降順（Z→A）


class NameMode(Enum):
    """属性名の表示モード（左右共通・master Connection Editor 準拠）。"""

    LONG = "long"  # longName（例 translateX）
    SHORT = "short"  # shortName（例 tx）


class CopyReason(Enum):
    """値コピー（master §5.3）の結果理由コード。"""

    OK = "ok"
    INCOMPATIBLE = "incompatible"  # 型非互換 / leaf 不成立
    DST_CONNECTED = "dst_connected"  # 先に入力接続あり（set 値が無視される・警告）
    DST_LOCKED = "dst_locked"  # ロック（force で解除可）


class ConnectBlock(Enum):
    """接続/leaf 接続が成立しない理由コード（操作レベル・双方向を合成）。

    接続は双方向（a→b 不可なら b→a）で試すため、単一方向の ``ConnectReason`` /
    ``LeafReason`` をそのまま出せない。UI の文言（§5.5）はこの操作レベルの理由で
    振り分ける。``DST_LOCKED`` のみ force（一時解除）で覆る。
    """

    TYPE_INCOMPATIBLE = "type_incompatible"  # 型が互換でない（C2 不成立・双方向）
    NO_DIRECTION = "no_direction"  # 有効な向き（readable→writable）が無い
    DST_LOCKED = "dst_locked"  # ロックされている（force で解除可）
    LEAF_COUNT_MISMATCH = "leaf_count_mismatch"  # leaf: 子数が一致しない
    LEAF_NON_SCALAR = "leaf_non_scalar"  # leaf: 子に非スカラーが含まれる
    LEAF_CHILD_INCOMPATIBLE = "leaf_child_incompatible"  # leaf: 子ペアが C2 非互換


# C3 構造の失敗理由（LeafReason）を操作レベルの ConnectBlock へ写す（OK は来ない）。
_LEAF_REASON_BLOCK = {
    LeafReason.COUNT_MISMATCH: ConnectBlock.LEAF_COUNT_MISMATCH,
    LeafReason.NON_SCALAR_CHILD: ConnectBlock.LEAF_NON_SCALAR,
    LeafReason.CHILD_INCOMPATIBLE: ConnectBlock.LEAF_CHILD_INCOMPATIBLE,
}


@dataclass(frozen=True)
class CopyResult:
    """値コピーの結果（master §5.3）。

    Attributes:
        ok: コピーできたら True。
        reason: 結果理由（``CopyReason``）。``DST_CONNECTED`` は警告（UI が
            「接続があると set 値が無視される」と知らせる）。
    """

    ok: bool
    reason: CopyReason


class EditorViewModel:
    """左右ツリーのロード状態と接続操作を担う presenter。

    Attributes は公開せず、メソッド経由でアクセスする。型タグ等の属性メタは
    列挙時にキャッシュし、接続可否判定（C1）に使う。
    """

    def __init__(self, scene: SceneAccess) -> None:
        """ViewModel を生成する。

        Args:
            scene: 注入する SceneAccess 実装（Fake / Maya）。
        """
        self._scene = scene
        # 各サイドにロード済みのノード列（複数ノード可）。順序が表示順。
        self._nodes: dict[str, list[NodeId]] = {LEFT: [], RIGHT: []}
        # 属性メタのキャッシュ＋クエリ（型タグ/ポート有無/readable/ゴースト判定）。
        self._meta_store = MetaStore()
        self._listeners: list[Callable[[bool], None]] = []
        # ロード済みノード間の接続 (src, dst) のキャッシュ。_notify で無効化。
        self._pairs_cache: list[tuple[PlugId, PlugId]] | None = None
        # ノード配下の全属性メタ列の列挙キャッシュ（uuid→list）。実機の OpenMaya
        # 再列挙を避ける（NFR-02）。属性木は接続/フィルタ変更で不変なので、ノード集合
        # 変化と materialize でのみ無効化する（_notify では消さない）。
        self._walk_cache: dict[str, list[AttrMeta]] = {}
        # ツリー行（TreeNode）生成のキャッシュ。属性木は接続/フィルタ変更で不変なので、
        # フィルタ打鍵ごとの再構築でシーンを問い直さず再利用する（_walk_cache と同じ
        # 無効化方針＝ノード集合変化と materialize でのみ消す）。
        self._attr_nodes_cache: dict[str, list[TreeNode]] = {}
        self._child_nodes_cache: dict[PlugId, list[TreeNode]] = {}
        # フィルタ条件（左右独立・master §9）。初期は全許容（現状表示を壊さない）。
        permissive = FilterCriteria(
            enabled_categories=frozenset(TypeCategory), show_non_keyable=True
        )
        self._criteria: dict[str, FilterCriteria] = {
            LEFT: permissive,
            RIGHT: permissive,
        }
        # 表示集合キャッシュ（side, node.uuid → 表示 plug 集合 or None=全表示）。
        self._visible_cache: dict[tuple[str, str], frozenset[PlugId] | None] = {}
        # 接続済み plug 集合のキャッシュ（node.uuid → 集合）。is_connected を per-plug
        # の get_connections（実機 OpenMaya）から O(1) メンバーシップに落とす（行背景の
        # 毎行照会対策）。接続変化のたびに _notify で無効化する。
        self._connected_cache: dict[str, frozenset[PlugId]] = {}
        # 属性の並び順（左右共通・既定は現順）。set_sort_mode で structural 通知。
        self._sort_mode = SortMode.SCENE
        # 属性名の表示モード（左右共通・既定 long）。set_name_mode で structural 通知。
        self._name_mode = NameMode.LONG

    # ---- 変更通知（Qt 非依存の observer） ----
    def add_listener(self, callback: Callable[[bool, str | None], None]) -> None:
        """状態変更時に呼ばれるコールバックを登録する。

        Args:
            callback: 通知先。引数 ``structural`` と ``side`` を受ける。
                ``structural``: True=ツリー構造が変わる変化 [load/add/remove/filter]
                → UI はモデル再構築が要る。False=接続のみの変化 → 再描画だけでよい。
                ``side``: 構造変化が片側だけのとき（フィルタ等）その側名、両側または
                不明なら ``None``（UI は両側を再構築する）。
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[bool, str | None], None]) -> None:
        """登録済みリスナーを解除する（watcher の dispose 等）。

        Args:
            callback: 解除するコールバック（未登録なら無視）。
        """
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, structural: bool, side: str | None = None) -> None:
        """登録済みリスナーを順に呼ぶ（接続キャッシュも無効化する）。

        Args:
            structural: ツリー構造が変わる変化なら True、接続のみなら False。
                接続のみの変化ではツリーは不変なので、UI 側は再描画だけ行い
                展開状態を保てる（master §4.6）。
            side: 構造変化が片側だけのとき（フィルタ等）その側名。両側または不明なら
                ``None``（UI は両側を再構築する）。再構築は重いので片側で済むなら渡す。
        """
        self._pairs_cache = None
        self._visible_cache.clear()
        self._connected_cache.clear()
        for callback in self._listeners:
            callback(structural, side)

    def _clear_structure_caches(self) -> None:
        """属性木に依存するキャッシュ（列挙・ツリー行）をまとめて無効化する。

        属性構造が変わる操作（ノード集合変化・materialize・外部の構造変更）でのみ呼ぶ。
        接続/フィルタ変更では構造は不変なので呼ばない（``_notify`` 側で別キャッシュを
        無効化する）。
        """
        self._walk_cache.clear()
        self._attr_nodes_cache.clear()
        self._child_nodes_cache.clear()

    # ---- シーン外部変更の再読込（ライブ同期・MAYA_PLAN §7.1(2)） ----
    def reload_connections(self) -> None:
        """接続のみの外部変更を取り込む（接続/切断・Undo/Redo）。

        接続キャッシュを捨てて再描画通知のみ行う。ツリー構造は不変なので
        ``structural=False``＝UI は展開を保ったまま overlay を引き直す。
        """
        self._notify(structural=False)

    def reload_structure(self) -> None:
        """構造変化を伴う外部変更を取り込む（attr 追加/array 要素/lock 切替）。

        属性列挙・メタのキャッシュを捨てて構造通知する。UI は展開を保存→復元して
        モデルを作り直す（接続キャッシュも ``_notify`` で無効化される）。
        """
        self._clear_structure_caches()
        self._meta_store.clear()
        self._notify(structural=True)

    def drop_nodes(self, uuids: set[str]) -> bool:
        """指定 uuid のノードを左右のロード一覧から外す（Maya で削除された等）。

        該当が無ければ何もしない（無駄な再構築を避ける）。1 件でも外したら属性列挙・
        メタのキャッシュを捨てて構造通知する。

        Args:
            uuids: 取り除くノードの uuid 集合。

        Returns:
            実際に 1 件以上外して再構築通知したら True（外す対象が無ければ False）。
        """
        removed = False
        for side in (LEFT, RIGHT):
            kept = [n for n in self._nodes[side] if n.uuid not in uuids]
            if len(kept) != len(self._nodes[side]):
                self._nodes[side] = kept
                removed = True
        if removed:
            self._clear_structure_caches()
            self._meta_store.clear()
            self._notify(structural=True)
        return removed

    def loaded_uuids(self) -> set[str]:
        """左右にロード済みの全ノード uuid を返す（watcher の監視対象解決用）。"""
        return {n.uuid for nodes in self._nodes.values() for n in nodes}

    # ---- ロード（複数ノード・master §3.2 Load/Add） ----
    def load(self, side: str, node: NodeId | None) -> None:
        """片側を 1 ノードで置き換える（``None`` で空に）。

        単一ノードの簡易 API（dev/テスト用）。複数ノードは ``set_nodes`` /
        ``add_nodes`` を使う。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            node: ロードするノード（``None`` で空に）。
        """
        self.set_nodes(side, [node] if node is not None else [])

    def set_nodes(self, side: str, nodes: list[NodeId]) -> None:
        """片側のノード列を置き換える（Load・master §3.2）。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            nodes: 表示順のノード列（重複 uuid は除去）。
        """
        self._nodes[side] = self._dedup(nodes)
        self._clear_structure_caches()  # ノード集合が変わったので列挙キャッシュを無効化
        logger.debug("set_nodes %s = %s", side, [n.path for n in self._nodes[side]])
        self._notify(structural=True)

    def swap_sides(self) -> None:
        """左右を丸ごと入れ替える（ノード列とフィルタ条件の両方を交換）。

        パネルごと左右が入れ替わる挙動（読込ノードもフィルタも一緒に移動）。
        接続の向きは plug 単位で保持されるため、表示上の左右だけが入れ替わる。
        """
        self._nodes[LEFT], self._nodes[RIGHT] = (
            self._nodes[RIGHT],
            self._nodes[LEFT],
        )
        self._criteria[LEFT], self._criteria[RIGHT] = (
            self._criteria[RIGHT],
            self._criteria[LEFT],
        )
        logger.debug("swap_sides")
        self._notify(structural=True)

    def add_nodes(self, side: str, nodes: list[NodeId]) -> None:
        """片側に既存表示を保ったままノードを追加する（Add・master §3.2）。

        既にロード済み（同 uuid）のノードは追加しない。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            nodes: 追加するノード列。
        """
        self.set_nodes(side, self._nodes[side] + list(nodes))

    def remove_node(self, side: str, node: NodeId) -> None:
        """片側から 1 ノードを取り除く（uuid 一致で削除）。"""
        self.set_nodes(side, [n for n in self._nodes[side] if n.uuid != node.uuid])

    def select(self, nodes: list[NodeId]) -> None:
        """シーンの選択を設定する（dev ピッカー用・master §3.2）。"""
        self._scene.set_selection(nodes)

    def load_selected(self, side: str) -> None:
        """選択中ノードで片側を置き換える（Load ボタン・master §3.2）。"""
        self.set_nodes(side, self._scene.get_selected_nodes())

    def add_selected(self, side: str) -> None:
        """選択中ノードを片側に追加する（Add ボタン・master §3.2）。"""
        self.add_nodes(side, self._scene.get_selected_nodes())

    @staticmethod
    def _dedup(nodes: list[NodeId]) -> list[NodeId]:
        """重複する uuid を除去して順序を保つ。"""
        seen: set[str] = set()
        result: list[NodeId] = []
        for node in nodes:
            if node.uuid not in seen:
                seen.add(node.uuid)
                result.append(node)
        return result

    def nodes(self, side: str) -> list[NodeId]:
        """指定サイドにロード済みのノード列を返す（表示順）。"""
        return list(self._nodes[side])

    def loaded_node(self, side: str) -> NodeId | None:
        """指定サイドの先頭ノードを返す（単一ロードの簡易アクセサ）。"""
        return self._nodes[side][0] if self._nodes[side] else None

    def display_label(self, node: NodeId) -> str:
        """ノードのセクション見出し表示名を返す（minimal unique・master §4.6）。

        ロード済み全ノードで短縮名（フルパス末尾）が一意ならそれを、重複するなら
        フルパスを返す（最小一意パスへの精緻化は後段）。

        Args:
            node: 対象ノード。

        Returns:
            セクションヘッダに出す表示名。
        """
        leaf = node.path.rsplit("|", 1)[-1]
        leaves = [
            n.path.rsplit("|", 1)[-1] for nodes in self._nodes.values() for n in nodes
        ]
        return leaf if leaves.count(leaf) <= 1 else node.path

    # ---- ツリー供給（Core C7 経由・遅延展開） ----
    def attr_nodes(self, node: NodeId) -> list[TreeNode]:
        """指定ノードのトップレベル属性を ``TreeNode`` 列で返す（セクション直下）。

        構造はフィルタ/接続で不変なので結果をキャッシュし、フィルタ打鍵ごとの再構築
        でシーンを問い直さない（無効化は ``_clear_structure_caches``）。
        """
        cached = self._attr_nodes_cache.get(node.uuid)
        if cached is None:
            cached = build_child_nodes(
                self._meta_store.cache(self._scene.list_root_attributes(node))
            )
            self._attr_nodes_cache[node.uuid] = cached
        return cached

    def root_nodes(self, side: str) -> list[TreeNode]:
        """指定サイドの先頭ノードのトップレベル属性を返す（単一ロード簡易アクセサ）。

        複数ノード時はツリーのトップレベル＝ノード（セクション）になるため、属性は
        ``attr_nodes(node)`` で各ノードから取る。本メソッドは後方互換用。
        """
        node = self.loaded_node(side)
        return self.attr_nodes(node) if node is not None else []

    def child_nodes(self, plug: PlugId) -> list[TreeNode]:
        """ある plug の直下の子属性を ``TreeNode`` 列で返す（遅延展開・master §5.6）。

        親が array なら既存要素にゴースト行（空き番号 + 末尾次・C4）を併合する。
        ゴースト行の型タグは ``_meta`` にも登録し、型色・接続可否判定で実在行と
        同じく扱えるようにする（接続時に ``materialize_array_element`` で実体化）。

        構造はフィルタ/接続で不変なので結果をキャッシュする（無効化は
        ``_clear_structure_caches`` と materialize 時の該当ノード pop）。array の
        ``meta_store`` 更新・ゴースト登録の副作用はキャッシュ未命中（＝構造変化後）に
        のみ走る。
        """
        cached = self._child_nodes_cache.get(plug)
        if cached is not None:
            return cached
        metas = self._meta_store.cache(self._scene.list_children(plug))
        parent = self._meta_store.get(plug)
        if parent is not None and parent.is_array:
            # 既存インデックスは実際の子メタから導出する（実体化後も最新を反映し、
            # メタのスナップショットが古くてもゴーストが重複しない）。
            actual = tuple(sorted(m.plug.index_path[-1] for m in metas))
            parent = replace(parent, existing_indices=actual)
            self._meta_store.put(plug, parent)  # is_ghost 判定も最新に保つ
            nodes = build_array_child_nodes(parent, metas)
            self._meta_store.register_ghosts(nodes)
            self._child_nodes_cache[plug] = nodes
            return nodes
        result = build_child_nodes(metas)
        self._child_nodes_cache[plug] = result
        return result

    def is_ghost(self, plug: PlugId) -> bool:
        """Plug がゴースト（実在しない array 要素）か返す（§5.6・MetaStore 委譲）。"""
        return self._meta_store.is_ghost(plug)

    # ---- フィルタ（左右独立・master §9 / Core C6） ----
    def filter_criteria(self, side: str) -> FilterCriteria:
        """指定サイドの現在のフィルタ条件を返す。"""
        return self._criteria[side]

    def set_filter(self, side: str, criteria: FilterCriteria) -> None:
        """指定サイドのフィルタ条件を設定して通知する（行構成が変わる・master §9）。

        ツリーの表示行が変わるため structural=True で通知し、UI はモデルを
        再構築する。

        Args:
            side: ``LEFT`` または ``RIGHT``。
            criteria: 新しいフィルタ条件。
        """
        self._criteria[side] = criteria
        # フィルタは片側だけ変わるので side を渡し、UI はその側だけ再構築する
        # （Qt 全リセットの半減）。
        self._notify(structural=True, side=side)

    @staticmethod
    def _is_permissive(criteria: FilterCriteria) -> bool:
        """条件が「全表示（フィルタ無効）」か判定する（高速パス用）。"""
        return (
            criteria.enabled_categories == frozenset(TypeCategory)
            and criteria.show_non_keyable
            and not criteria.show_connected_only
            and not criteria.extra_only
            and not criteria.text
        )

    def _visible_set(self, side: str, node: NodeId) -> frozenset[PlugId] | None:
        """指定ノードで表示する plug 集合を返す（直接マッチ＋祖先・master §9）。

        全表示（フィルタ無効）なら ``None``（呼び出し側は全行表示）。フィルタ有効なら、
        C6（``should_display``）の直接マッチに加え、その祖先 plug も集合に含める
        （ツリー構造を保つため。matrix だけ ON で worldMatrix の親を残す /
        connected-only で接続子を持つ親を残す）。結果は (side, uuid) でキャッシュする。

        Args:
            side: 対象サイド（フィルタ条件の取得用）。
            node: 対象ノード。

        Returns:
            表示する plug の集合、または全表示なら ``None``。
        """
        criteria = self._criteria[side]
        if self._is_permissive(criteria):
            return None
        key = (side, node.uuid)
        if key in self._visible_cache:
            return self._visible_cache[key]
        visible: set[PlugId] = set()
        # 接続状態は connected-only のときだけ要る。型/テキスト/non-keyable フィルタでは
        # 接続照会を一切しない（NFR-02・問題2）。connected-only でも、全 plug に
        # get_connections（O(全 plug) の OpenMaya）を投げず、ノード単位で接続済み
        # plug 集合を一度だけ作って membership 判定にする（A・問題）。
        needs_connection = criteria.show_connected_only
        connected_set = self._scene.connected_plugs(node) if needs_connection else set()
        # テキスト検索の対象名は表示モードに合わせる（見えている名前で検索・案2）。
        match_short = self._name_mode == NameMode.SHORT

        def add_if_visible(meta: AttrMeta) -> None:
            connected = meta.plug in connected_set if needs_connection else False
            if should_display(
                meta,
                is_connected=connected,
                criteria=criteria,
                match_short=match_short,
            ):
                ip = meta.plug.index_path
                for depth in range(1, len(ip) + 1):
                    visible.add(PlugId(node=meta.plug.node, index_path=ip[:depth]))

        for meta in self._walk_metas(node):
            add_if_visible(meta)
            if meta.is_array:
                # ゴースト行も表示判定に含める（connected-only では未接続なので消える）
                parent_short = meta.short_name or meta.display_name
                for i in ghost_indices(meta.existing_indices or ()):
                    gplug = PlugId(
                        node=meta.plug.node, index_path=meta.plug.index_path + (i,)
                    )
                    add_if_visible(
                        AttrMeta(
                            plug=gplug,
                            display_name=f"{meta.display_name}[{i}]",
                            short_name=f"{parent_short}[{i}]",
                            type_tag=meta.type_tag,
                        )
                    )
        result = frozenset(visible)
        self._visible_cache[key] = result
        return result

    def visible_attr_nodes(self, side: str, node: NodeId) -> list[TreeNode]:
        """フィルタ適用後のトップレベル属性を返す（セクション直下・master §9）。"""
        nodes = self.attr_nodes(node)
        vset = self._visible_set(side, node)
        if vset is not None:
            nodes = [n for n in nodes if n.plug in vset]
        return self._sorted(nodes, is_array=False)

    def visible_child_nodes(self, side: str, plug: PlugId) -> list[TreeNode]:
        """フィルタ適用後の子属性を返す（遅延展開・master §9）。"""
        nodes = self.child_nodes(plug)
        vset = self._visible_set(side, plug.node)
        if vset is not None:
            nodes = [n for n in nodes if n.plug in vset]
        parent = self._meta_store.get(plug)
        return self._sorted(nodes, is_array=bool(parent and parent.is_array))

    # ---- 並び替え（左右共通・master Connection Editor 準拠） ----
    def sort_mode(self) -> SortMode:
        """現在の属性並び順を返す。"""
        return self._sort_mode

    def set_sort_mode(self, mode: SortMode) -> None:
        """属性並び順を設定して通知する（行順が変わるため structural=True）。

        Args:
            mode: 新しい並び順。現状と同じなら何もしない。
        """
        if mode == self._sort_mode:
            return
        self._sort_mode = mode
        self._notify(structural=True)

    def _sorted(self, nodes: list[TreeNode], *, is_array: bool) -> list[TreeNode]:
        """並び順に従って ``TreeNode`` 列を整える（純粋・array はインデックス順維持）。

        現順（SCENE）または array 要素のときは入力順をそのまま返す。昇順/降順は
        現在の表示名（``attr_label`` = long/short）の大小無視で安定ソートする。

        Args:
            nodes: ソート対象の行列。
            is_array: 親が array なら True（要素の index 順を崩さない）。

        Returns:
            並び替え後の行列。
        """
        if self._sort_mode == SortMode.SCENE or is_array:
            return nodes
        reverse = self._sort_mode == SortMode.DESC
        return sorted(
            nodes, key=lambda n: self.attr_label(n).casefold(), reverse=reverse
        )

    # ---- 属性名の表示モード（左右共通・long / short） ----
    def name_mode(self) -> NameMode:
        """現在の属性名表示モードを返す。"""
        return self._name_mode

    def set_name_mode(self, mode: NameMode) -> None:
        """属性名表示モードを設定して通知する（表示名が変わるため structural=True）。

        Args:
            mode: 新しい表示モード。現状と同じなら何もしない。
        """
        if mode == self._name_mode:
            return
        self._name_mode = mode
        self._notify(structural=True)

    def attr_label(self, node: TreeNode) -> str:
        """現在の表示モードでの属性行ラベル（long/short）を返す。

        Args:
            node: 対象の ``TreeNode``。

        Returns:
            表示する属性名（short が空なら long にフォールバック）。
        """
        if self._name_mode == NameMode.SHORT:
            return node.short_name or node.display_name
        return node.display_name

    # ---- 型・接続状態の供給 ----
    def type_tag(self, plug: PlugId) -> str:
        """Plug の正規化済み型タグを返す（未キャッシュなら空文字・MetaStore 委譲）。"""
        return self._meta_store.type_tag(plug)

    def has_port(self, plug: PlugId) -> bool:
        """Plug にポートを出すか（接続可能か・master §4.3・MetaStore へ委譲）。"""
        return self._meta_store.has_port(plug)

    def get_connections(self, plug: PlugId) -> Connections:
        """Plug の接続状態を返す。"""
        return self._scene.get_connections(plug)

    def _connected_set(self, node: NodeId) -> frozenset[PlugId]:
        """ノードの接続済み plug 集合を返す（per-node 1回照会＋キャッシュ）。

        ``is_connected`` を per-plug の ``get_connections``（実機 OpenMaya）から O(1)
        メンバーシップに落とすための集合。``connected_plugs`` は入出力・相手のロード
        有無を問わず「接続が 1 本でもある plug」を返し、``is_connected`` と等価。

        Args:
            node: 対象ノード。

        Returns:
            接続を持つ plug の集合。
        """
        cached = self._connected_cache.get(node.uuid)
        if cached is None:
            cached = frozenset(self._scene.connected_plugs(node))
            self._connected_cache[node.uuid] = cached
        return cached

    def is_connected(self, plug: PlugId) -> bool:
        """Plug が接続済み（入出力いずれか）か返す（接続の有無）。"""
        return plug in self._connected_set(plug.node)

    def is_connected_to_loaded(self, plug: PlugId) -> bool:
        """Plug がロード中ノードの相手と接続しているか返す（ポート塗りの判定）。

        相手端点のノードがロード済み＝描画できる配線がある＝ポートを塗る。接続は
        あるが相手が未ロード（ロード外とだけ接続）なら False（その場合 overlay は
        リング＋中心ドットで「画面外接続」を示す・master §4.3 拡張）。

        Args:
            plug: 対象 plug。

        Returns:
            ロード中の相手と接続していれば True。
        """
        loaded = self.loaded_uuids()
        conns = self._scene.get_connections(plug)
        return any(
            end.node.uuid in loaded for end in (*conns.sources, *conns.destinations)
        )

    # ---- 全接続の列挙（束出し・全線常時表示用・master §4.1/§4.5） ----
    def _walk_metas(self, node: NodeId) -> list[AttrMeta]:
        """ノード配下の全属性メタを再帰列挙して返す（接続列挙・フィルタ用・キャッシュ付）。

        遅延展開を破って全展開する（接続列挙とフィルタの祖先判定に全列挙が要る）。
        実機では ``list_children`` が OpenMaya 列挙なので、結果を **ノード単位で
        キャッシュ**して、フィルタ変更や接続変更での再列挙を避ける（NFR-02・問題2）。
        キャッシュはノード集合の変化（load/add/remove）と materialize でのみ無効化する
        （属性木は接続変更・フィルタ変更では不変）。

        Args:
            node: 対象ノード。

        Returns:
            配下の全属性メタ（トップ→子の前順）。
        """
        cached = self._walk_cache.get(node.uuid)
        if cached is not None:
            return cached
        result: list[AttrMeta] = []

        def rec(metas: list[AttrMeta]) -> None:
            for meta in metas:
                result.append(meta)
                if meta.has_children:
                    rec(self._meta_store.cache(self._scene.list_children(meta.plug)))

        rec(self._meta_store.cache(self._scene.list_root_attributes(node)))
        self._walk_cache[node.uuid] = result
        return result

    def connection_pairs(self) -> list[tuple[PlugId, PlugId]]:
        """ロード済みノード間の全接続 (src, dst) を返す（キャッシュ付）。

        両端のノードがロード済みの接続のみ対象（描画できる接続）。

        Returns:
            (source plug, destination plug) の列。
        """
        if self._pairs_cache is not None:
            return self._pairs_cache
        loaded = {n.uuid for nodes in self._nodes.values() for n in nodes}
        pairs: list[tuple[PlugId, PlugId]] = []
        seen: set[tuple[PlugId, PlugId]] = set()
        # ノード単位で外向き接続を一括列挙（接続のある plug だけ）。全 plug を
        # get_connections で舐める O(全 plug) を O(ノード) に落とす（NFR-02）。
        for side in (LEFT, RIGHT):
            for node in self._nodes[side]:
                for src, dst in self._scene.list_node_connections(node):
                    pair = (src, dst)
                    if dst.node.uuid in loaded and pair not in seen:
                        seen.add(pair)
                        pairs.append(pair)
        self._pairs_cache = pairs
        return pairs

    def has_connected_descendant(self, plug: PlugId) -> bool:
        """C5: plug の子孫（自身は除く）に接続があるか（二重丸判定・§4.5）。

        Args:
            plug: 畳まれた親 plug。

        Returns:
            子孫のいずれかに接続があれば True。
        """
        depth = len(plug.index_path)
        for src, dst in self.connection_pairs():
            for endpoint in (src, dst):
                ip = endpoint.index_path
                if (
                    endpoint.node.uuid == plug.node.uuid
                    and len(ip) > depth
                    and ip[:depth] == plug.index_path
                ):
                    return True
        return False

    def node_has_connection_by_uuid(self, uuid: str) -> bool:
        """指定 uuid のノードに関わる接続があるか（ヘッダ二重丸判定・§4.6）。

        Args:
            uuid: 対象ノードの uuid。

        Returns:
            そのノードの plug が source または destination の接続があれば True。
        """
        for src, dst in self.connection_pairs():
            if src.node.uuid == uuid or dst.node.uuid == uuid:
                return True
        return False

    def side_of(self, plug: PlugId) -> str | None:
        """Plug が左右どちらのツリーに属するかを返す（接続線の端点解決用）。

        同一ノードが左右両方に出る場合は最初に見つかった側を返す（同時表示の
        重複描画対応・master §4.6）。
        """
        for side in (LEFT, RIGHT):
            for node in self._nodes[side]:
                if node.uuid == plug.node.uuid:
                    return side
        return None

    # ---- leaf 接続（子属性で接続・master §5.2 / §10.3 / Core C3） ----
    def _child_plugs_and_types(self, plug: PlugId) -> tuple[list[PlugId], list[str]]:
        """Plug の直下の子の (PlugId 列, 型タグ列) を位置順で返す（leaf 判定用）。

        型タグはここで ``_meta`` にキャッシュされるので、後段の C1 判定に使える。

        Args:
            plug: 親 plug（compound など）。

        Returns:
            (子 plug 列, 子型タグ列)。子を持たなければ両方空。
        """
        metas = self._meta_store.cache(self._scene.list_children(plug))
        plugs = [m.plug for m in metas]
        types = [m.type_tag for m in metas]
        return plugs, types

    def check_leaf(self, a: PlugId, b: PlugId) -> LeafConnectCheck:
        """A と b の leaf 接続成立可否を C3 で判定する（向きは問わない・§10.3）。

        子型は C2 で対称に判定されるため、向きに依らず同じ結果になる。pairs は
        a の子→b の子の位置対応。実 plug への対応付けは ``connect_leaf`` 側で行う。

        Args:
            a: 一方の親 plug。
            b: 他方の親 plug。

        Returns:
            判定結果（``LeafConnectCheck``）。
        """
        _, a_types = self._child_plugs_and_types(a)
        _, b_types = self._child_plugs_and_types(b)
        return check_leaf_connect(a_types, b_types)

    def _leaf_direction_ok(self, src: PlugId, dst: PlugId, *, force: bool) -> bool:
        """Src 親→dst 親の leaf 接続が成立可能か（接続はしない・判定のみ）。

        C3（子数一致・全子スカラー・各子 C2 互換）に加え、各子ペアが C1（ロック/
        既存接続）でも通るかを確認する。``_try_leaf_direction`` とグレーアウト候補
        判定の共通土台（master §5.5 判定一元化）。

        Args:
            src: source 側の親 plug。
            dst: destination 側の親 plug。
            force: 強制接続するか。

        Returns:
            子ペアを接続可能なら True。
        """
        src_plugs, src_types = self._child_plugs_and_types(src)
        dst_plugs, dst_types = self._child_plugs_and_types(dst)
        leaf = check_leaf_connect(src_types, dst_types)
        if not leaf.ok:
            return False
        return all(
            self.check_connect(src_plugs[si], dst_plugs[di], force=force).ok
            for si, di in leaf.pairs
        )

    def _try_leaf_direction(self, src: PlugId, dst: PlugId, *, force: bool) -> bool:
        """Src 親→dst 親で leaf 接続を試す（成立時のみ子ペアを接続して通知）。

        ``_leaf_direction_ok`` が真のときだけ、子ペアごとに接続する。1 つでも
        弾かれたら何も接続しない（部分接続を残さない）。

        Args:
            src: source 側の親 plug。
            dst: destination 側の親 plug。
            force: 強制接続するか。

        Returns:
            子ペアを接続できたら True。
        """
        if not self._leaf_direction_ok(src, dst, force=force):
            return False
        src_plugs, _ = self._child_plugs_and_types(src)
        dst_plugs, _ = self._child_plugs_and_types(dst)
        leaf = check_leaf_connect(
            [self.type_tag(p) for p in src_plugs],
            [self.type_tag(p) for p in dst_plugs],
        )
        for si, di in leaf.pairs:
            self._do_connect(src_plugs[si], dst_plugs[di], force=force)
        self._notify(structural=False)
        return True

    def can_drag_connect(
        self,
        grabbed: PlugId,
        target: PlugId,
        *,
        leaf: bool = False,
        force: bool = False,
    ) -> bool:
        """掴んだ plug を target に落とせるか（グレーアウト候補判定・§5.1/§5.5）。

        ドラッグ確定（``_confirm_new_connection``）と同じ意味で判定する（接続は
        しない）。leaf ON で親同士の leaf 接続が成立すれば候補、そうでなければ
        通常接続（どちらかの向きで C1 成立）を候補とする。

        Args:
            grabbed: ドラッグ開始ポートの plug。
            target: 候補ポートの plug。
            leaf: leaf トグルの状態。
            force: force トグルの状態。

        Returns:
            落とせる（接続が成立する）なら True。
        """
        if grabbed == target:
            return False
        if leaf:
            oriented = self._leaf_orient(grabbed, target)
            if oriented is not None and self._leaf_direction_ok(*oriented, force=force):
                return True
        oriented = self._orient(grabbed, target)
        return oriented is not None and self.check_connect(*oriented, force=force).ok

    def connect_leaf(self, a: PlugId, b: PlugId, *, force: bool = False) -> bool:
        """A と b の子同士を leaf 接続する（向きを正規化・master §5.2）。

        a→b（a の子=source）を試し、不可なら b→a を試す。compound 同士で C3 が
        成立し、各子ペアが C1 を通るときだけ子ペアを接続する（tx→tx, ty→ty,
        tz→tz）。leaf OFF の通常接続は ``try_connect`` を使う。

        Args:
            a: ドラッグ開始側の親 plug。
            b: ドロップ先の親 plug。
            force: 強制接続するか。

        Returns:
            leaf 接続できたら True。
        """
        if a == b:
            return False
        oriented = self._leaf_orient(a, b)
        if oriented is None:
            return False
        src, dst = oriented
        with self._scene.undo_chunk():  # 1 アクション = 1 undo（§2.2）
            return self._try_leaf_direction(src, dst, force=force)

    def leaf_blocker(
        self, a: PlugId, b: PlugId, *, force: bool = False
    ) -> ConnectBlock | None:
        """Leaf 接続（``connect_leaf``）が成立しない理由を返す（成立するなら None）。

        まず C3 構造（``check_leaf_connect``）を見て子数/非スカラー/子非互換を分け、
        構造が成立すれば ``_leaf_orient`` で向きを決める。向きが無ければ
        ``NO_DIRECTION``、向きは取れるが子ペアの C1（force 込み）を通らなければ残るは
        ロックのみ＝``DST_LOCKED``。reason は向きに依らず対称なので a→b で評価する。

        Args:
            a: 一方の親 plug。
            b: 他方の親 plug。
            force: 強制接続するか（UI の Force connect トグル）。

        Returns:
            成立しない理由（``ConnectBlock``）。成立するなら None。
        """
        _, a_types = self._child_plugs_and_types(a)
        _, b_types = self._child_plugs_and_types(b)
        leaf = check_leaf_connect(a_types, b_types)
        if not leaf.ok:
            return _LEAF_REASON_BLOCK[leaf.reason]
        oriented = self._leaf_orient(a, b)
        if oriented is None:
            return ConnectBlock.NO_DIRECTION
        src, dst = oriented
        if self._leaf_direction_ok(src, dst, force=force):
            return None
        return ConnectBlock.DST_LOCKED

    # ---- 接続操作 ----
    def check_connect(
        self, src: PlugId, dst: PlugId, *, force: bool = False
    ) -> ConnectCheck:
        """src→dst の接続可否を C1 で判定する（ロック状態を実状態から反映・§5.4）。

        方向の可否は src/dst のメタの readable/writable で判定する（master §4.3/§6）。
        メタ未取得は従来どおり可（True）として扱う。
        """
        return check_connect(
            self.type_tag(src),
            self.type_tag(dst),
            dst_locked=self._scene.is_locked(dst),
            src_readable=self._meta_store.is_readable(src),
            dst_writable=self._meta_store.is_writable(dst),
            force=force,
        )

    @contextmanager
    def _temporarily_unlocked(self, plug: PlugId, *, force: bool) -> Iterator[None]:
        """Force 時にロック plug を一時解除し、終了時に元の状態へ復元する（§5.4）。

        ``connectAttr`` / ``setAttr`` はロック先へ書けないため、force 指定時のみ
        ``set_locked(False)`` で開け、``finally`` で元のロック状態へ戻す。force でない
        とき、または元からロックされていないときは何もしない。

        Args:
            plug: 対象 plug（destination 側）。
            force: 強制実行するか（False なら一切ロックに触れない）。

        Yields:
            None。``with`` ブロック内で接続 / 値設定を行う。
        """
        was_locked = force and self._scene.is_locked(plug)
        if was_locked:
            self._scene.set_locked(plug, False)
        try:
            yield
        finally:
            if was_locked:
                self._scene.set_locked(plug, True)  # 元のロック状態へ復元

    def _do_connect(self, src: PlugId, dst: PlugId, *, force: bool) -> bool:
        """C1 通過済みの接続を実行する（実体化 / 置換 / ロック復元・§5.4/§5.6）。

        dst がゴースト（array の空き要素）なら接続前に実体化する（master §5.6）。
        force の 2 レイヤー（master §5.4）: (a) 既存入力の置換は **常に**
        ``connect(force=True)`` で行う（ドロップ＝上書きが既定）。(b) ロック解除は
        ``force`` 引数（UI トグル）に従い ``_temporarily_unlocked`` で一時解除→復元。

        Args:
            src: source 側 plug（C1 通過済み）。
            dst: destination 側 plug（C1 通過済み）。
            force: ロックを一時解除するか（既存接続の置換は force に依らない）。

        Returns:
            ゴーストを実体化した（=ツリー構造が変わった）なら True。
        """
        materialized = self.is_ghost(dst)
        if materialized:
            self._scene.materialize_array_element(dst)
            # array に要素が増え属性木が変わるので当該ノードのキャッシュを無効化
            self._walk_cache.pop(dst.node.uuid, None)
            self._attr_nodes_cache.pop(dst.node.uuid, None)
            for plug in [
                p for p in self._child_nodes_cache if p.node.uuid == dst.node.uuid
            ]:
                self._child_nodes_cache.pop(plug, None)
        # 既存入力を順向きで置換するため常に force（connectAttr -f）で繋ぐ（§5.4 (a)）。
        # ロックの一時解除は UI トグル（force 引数）に従う（§5.4 (b)）。
        with self._temporarily_unlocked(dst, force=force):
            self._scene.connect(src, dst, force=True)
        return materialized

    def _orient(self, a: PlugId, b: PlugId) -> tuple[PlugId, PlugId] | None:
        """通常接続の向きを **capability（型＋readable/writable）** で決める（§6）。

        サイドは role-neutral（§6 両用ポート）。向きは型互換と readable/writable だけで
        決め、**ロックや既存接続は向きの判断に使わない**（ロックは選んだ向きに対し force
        で扱う。ロックを理由に逆向きへ反転させない）。a→b（ドラッグ方向）を優先し、不可
        なら b→a を試す。``force=True`` の ``check_connect`` でロックを無視した
        capability を見る（型/readable/writable は force でも覆らない・C1）。

        Args:
            a: ドラッグ開始側 plug。
            b: ドロップ先 plug。

        Returns:
            成立する向きの ``(src, dst)``。どちらも capability を満たさなければ None。
        """
        if self.check_connect(a, b, force=True).ok:
            return (a, b)
        if self.check_connect(b, a, force=True).ok:
            return (b, a)
        return None

    def _leaf_orient(self, a: PlugId, b: PlugId) -> tuple[PlugId, PlugId] | None:
        """Leaf 接続の向きを capability（C3 構造）で決める（ロックは無視・§5.2/§6）。

        ``_leaf_direction_ok`` を ``force=True``（ロック無視）で評価し、子構造が成立する
        向きを選ぶ。a→b を優先。ロックは選んだ向きに対し force で扱う。

        Args:
            a: ドラッグ開始側の親 plug。
            b: ドロップ先の親 plug。

        Returns:
            成立する向きの ``(src, dst)``。成立しなければ None。
        """
        if self._leaf_direction_ok(a, b, force=True):
            return (a, b)
        if self._leaf_direction_ok(b, a, force=True):
            return (b, a)
        return None

    def _try_connect_directed(self, src: PlugId, dst: PlugId, *, force: bool) -> bool:
        """src→dst の向きで接続を試す（C1 成立時のみ実行して通知）。

        ``try_connect`` の片方向版。C1 を通れば ``_do_connect`` で接続し、ゴースト
        実体化の有無で通知種別を切り替える（§5.6）。通らなければ何もしない。

        Args:
            src: source 側 plug。
            dst: destination 側 plug。
            force: 強制接続するか。

        Returns:
            接続できれば True。
        """
        if not self.check_connect(src, dst, force=force).ok:
            return False
        materialized = self._do_connect(src, dst, force=force)
        self._notify(structural=materialized)
        return True

    def try_connect(self, a: PlugId, b: PlugId, *, force: bool = False) -> bool:
        """A と b を向きを正規化して接続する（master §6 双方向操作）。

        a→b を C1 で試し、不可なら b→a を試す。どちらかが通れば接続して通知する。
        force 時はロック dst を一時解除→接続→復元する（§5.4）。ゴーストへ接続した
        場合は実体化でツリーが変わるため structural 通知する（§5.6）。

        Args:
            a: ドラッグ開始側 plug。
            b: ドロップ先 plug。
            force: 強制接続するか。

        Returns:
            接続できれば True。
        """
        if a == b:
            return False
        oriented = self._orient(a, b)
        if oriented is None:
            return False
        src, dst = oriented
        with self._scene.undo_chunk():  # 1 アクション = 1 undo（§2.2）
            return self._try_connect_directed(src, dst, force=force)

    def connect_blocker(
        self, a: PlugId, b: PlugId, *, force: bool = False
    ) -> ConnectBlock | None:
        """通常接続（``try_connect``）が成立しない理由を返す（成立するなら None）。

        ``try_connect`` と同じ判断木で双方向を評価し、UI 文言（§5.5）用の操作レベル
        理由に畳む。``_orient``（force=True で型・readable/writable を見る）が向きを
        返せなければ、型互換かどうかで ``TYPE_INCOMPATIBLE`` / ``NO_DIRECTION`` を
        分ける。向きは取れるが force 込みの C1 を通らなければ残るはロックのみ。

        Args:
            a: 一方の plug。
            b: 他方の plug。
            force: 強制接続するか（UI の Force connect トグル）。

        Returns:
            成立しない理由（``ConnectBlock``）。成立するなら None。
        """
        oriented = self._orient(a, b)
        if oriented is None:
            if not is_compatible(self.type_tag(a), self.type_tag(b)):
                return ConnectBlock.TYPE_INCOMPATIBLE
            return ConnectBlock.NO_DIRECTION
        src, dst = oriented
        if self.check_connect(src, dst, force=force).ok:
            return None
        return ConnectBlock.DST_LOCKED

    def disconnect(self, src: PlugId, dst: PlugId, *, force: bool = False) -> None:
        """src→dst の単一接続を切断する（線を掴んで空白へドロップ・§5.1）。

        ``force`` 時は入力側（dst）がロックされていても一時解除して切断し、元の
        ロック状態へ復元する（Maya の ``disconnectAttr`` はロック入力で失敗しうる
        ため・接続/値設定と同方針 §5.4）。

        Args:
            src: source 側 plug。
            dst: destination 側 plug。
            force: ロックされた dst を一時解除して切断するか。
        """
        with self._scene.undo_chunk():
            with self._temporarily_unlocked(dst, force=force):
                self._scene.disconnect(src, dst)
        self._notify(structural=False)

    def reconnect(
        self, src: PlugId, old_dst: PlugId, new_dst: PlugId, *, force: bool = False
    ) -> bool:
        """Src の接続先を old_dst から new_dst へつなぎ替える（§5.1）。

        new_dst が C1 で受け付けられる場合のみ、old_dst を切断して new_dst に
        繋ぎ直す。受け付けられなければ何もしない（元の接続を保つ）。

        Args:
            src: 維持する source 側 plug。
            old_dst: 外す既存の destination。
            new_dst: 新しい destination。
            force: 強制接続するか（既定 False）。

        Returns:
            つなぎ替えたら True。
        """
        if new_dst == old_dst:
            return False
        if not self.check_connect(src, new_dst, force=force).ok:
            return False
        with self._scene.undo_chunk():
            self._scene.disconnect(src, old_dst)
            materialized = self._do_connect(src, new_dst, force=force)
        self._notify(structural=materialized)
        return True

    def disconnect_all(self, plug: PlugId, *, force: bool = False) -> bool:
        """Plug に関わる接続をすべて切断する（§5.1）。

        ``force`` 時は各切断の入力側（plug が入力なら plug 自身、出力なら相手 dst）の
        ロックを一時解除→切断→復元する（§5.4）。

        Args:
            plug: 切断対象 plug。
            force: ロックされた入力側を一時解除して切断するか。

        Returns:
            1 本以上切断したら True。
        """
        conns = self._scene.get_connections(plug)
        changed = False
        with self._scene.undo_chunk():
            for src in conns.sources:  # src→plug（plug が入力＝ロックされうる側）
                with self._temporarily_unlocked(plug, force=force):
                    self._scene.disconnect(src, plug)
                changed = True
            for dst in conns.destinations:  # plug→dst（dst が入力側）
                with self._temporarily_unlocked(dst, force=force):
                    self._scene.disconnect(plug, dst)
                changed = True
        if changed:
            self._notify(structural=False)
        return changed

    def disconnect_pairs(
        self, pairs: list[tuple[PlugId, PlugId]], *, force: bool = False
    ) -> int:
        """複数の接続 (src, dst) をまとめて切断する（横断切断・§5.1）。

        ``disconnect`` の複数版。各ペアを切断し、**通知は最後に1回だけ**行う
        （バッチ化して再描画のちらつきとキャッシュ再計算を抑える）。``force`` 時は
        各 dst のロックを一時解除→切断→復元する（§5.4）。

        Args:
            pairs: 切断する (source plug, destination plug) の列。
            force: ロックされた dst を一時解除して切断するか。

        Returns:
            実際に切断した本数。
        """
        count = 0
        with self._scene.undo_chunk():
            for src, dst in pairs:
                with self._temporarily_unlocked(dst, force=force):
                    self._scene.disconnect(src, dst)
                count += 1
        if count:
            self._notify(structural=False)
        return count

    # ---- 値コピー（右クリックメニュー・master §5.3） ----
    def _do_set_value(self, plug: PlugId, value, *, force: bool) -> None:
        """値を設定する（force 時はロック一時解除→復元・接続と同方針 §5.4）。"""
        with self._temporarily_unlocked(plug, force=force):
            self._scene.set_value(plug, value)

    def copy_value(
        self, src: PlugId, dst: PlugId, *, force: bool = False
    ) -> CopyResult:
        """Src の値を dst にコピーする（master §5.3）。

        判定順: 型互換（C2）→ コピー先の入力接続（あれば警告・set 値が無視される）
        → ロック（force で一時解除）。コピー先に接続があるとき、ドラッグ接続と違い
        向きは正規化しない（ユーザーが src/dst を明示選択する操作のため）。

        Args:
            src: コピー元 plug。
            dst: コピー先 plug。
            force: ロックを一時解除して設定するか。

        Returns:
            結果（``CopyResult``）。``DST_CONNECTED`` は警告。
        """
        if not is_compatible(self.type_tag(src), self.type_tag(dst)):
            return CopyResult(False, CopyReason.INCOMPATIBLE)
        if self._scene.get_connections(dst).sources:
            return CopyResult(False, CopyReason.DST_CONNECTED)
        if self._scene.is_locked(dst) and not force:
            return CopyResult(False, CopyReason.DST_LOCKED)
        with self._scene.undo_chunk():
            self._do_set_value(dst, self._scene.get_value(src), force=force)
        return CopyResult(True, CopyReason.OK)

    def copy_value_leaf(
        self, src_parent: PlugId, dst_parent: PlugId, *, force: bool = False
    ) -> CopyResult:
        """子属性ごとに値をコピーする（子属性で値コピー・master §5.3）。

        C3（子数一致・全子スカラー・各子互換）が成立する compound 同士で、各子の
        値を位置対応でコピーする（tx→tx, ...）。向きは正規化しない（src→dst 固定）。
        いずれかの子先に入力接続があれば警告で中止、ロックは force で一時解除する。

        Args:
            src_parent: コピー元の親 plug。
            dst_parent: コピー先の親 plug。
            force: ロックを一時解除して設定するか。

        Returns:
            結果（``CopyResult``）。
        """
        check = self.check_leaf(src_parent, dst_parent)
        if not check.ok:
            return CopyResult(False, CopyReason.INCOMPATIBLE)
        src_plugs, _ = self._child_plugs_and_types(src_parent)
        dst_plugs, _ = self._child_plugs_and_types(dst_parent)
        if any(
            self._scene.get_connections(dst_plugs[di]).sources for _, di in check.pairs
        ):
            return CopyResult(False, CopyReason.DST_CONNECTED)
        if not force and any(
            self._scene.is_locked(dst_plugs[di]) for _, di in check.pairs
        ):
            return CopyResult(False, CopyReason.DST_LOCKED)
        with self._scene.undo_chunk():
            for si, di in check.pairs:
                self._do_set_value(
                    dst_plugs[di], self._scene.get_value(src_plugs[si]), force=force
                )
        return CopyResult(True, CopyReason.OK)
