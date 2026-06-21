# fake-connection-editor

Autodesk Maya 標準の Connection Editor を再実装したツールです。属性ツリーを左右に並べ、
ポート間のドラッグや選択ペアの操作で接続・切断・値コピーを行います。テスト可能性を最優先に、
Maya / Qt に依存しないドメインロジックと UI を分離した 4 層アーキテクチャで構成しています。

## 特徴

- 左右 2 ツリー + 中央オーバーレイによる接続線・ポートの可視化
- ポート間ドラッグで接続、空白へのドロップで切断、Alt+Shift 横断で一括切断
- アクションバーからの接続 / leaf 接続（子属性ごと）/ 値コピー（方向トグル準拠）
- 型チップ・テキスト・表示オプションによる左右独立フィルタ
- マルチ属性のゴースト要素表示と接続時の実体化
- シーンの外部変更（接続・属性追加・ロック・Undo/Redo）へのライブ追従

## 動作環境

- Autodesk Maya 2023 以降（Python 3.9 以上）
- PySide2 / PySide6 の両対応（Maya 同梱の Qt を利用）

## インストール

### Maya で使う（リリース版）

1. [Releases](../../releases) から `fake-connection-editor_vX.Y.Z.zip` をダウンロード
2. 展開し、`fake_connection_editor` フォルダを Maya のスクリプトパスに置く
   （例: `<ユーザー>/Documents/maya/scripts/`）
3. Maya を再起動

### ソースから（開発者向け）

```bash
pip install -e ".[dev]"        # ruff / pytest
pip install -e ".[dev,qt]"     # Maya なしで UI を目視確認する場合（PySide2）
```

## 使い方

### Maya 内で起動

Maya のスクリプトエディタ（Python）から:

```python
import fake_connection_editor
fake_connection_editor.launch()
```

ノードを選択して左右の Load / Add ボタンで読み込みます。シーンの外部変更には
自動で追従します。

### Maya なしで UI を確認（dev）

```bash
python -m fake_connection_editor.dev          # サンプルシーン（pSphere1 / pSphere2）
python -m fake_connection_editor.dev tall     # 縦長シーン（スクロール / 画面外矢印）
python -m fake_connection_editor.dev multi    # 複数ノード（セクション / 束出し）
```

いずれにも `dark` を足すとダークパレットで起動します（テーマ非依存の確認用）。

## アーキテクチャ

依存方向は `ui → viewmodel → core → scene_access` の一方向です。

| 層 | 役割 | 依存 |
|---|---|---|
| `core` | 型互換・接続可否・フィルタ・ツリー構築などの純粋ロジック | Maya / Qt 非依存 |
| `viewmodel` | エディタ状態の保持と操作の合成 | Maya / Qt 非依存 |
| `ui` | PySide による描画・入力（PySide2/6 両対応） | Qt |
| `scene_access` | シーンへの読み書きの抽象 IF と Maya / Fake 実装 | 実装のみ Maya |

`core` と `viewmodel` は Maya にも Qt にも依存しないため、すべて `FakeSceneAccess` 上で
pytest 検証できます。

## テスト

```bash
pytest
```

`core` / `viewmodel` は Maya / Qt 非依存のため、実機なしで全ロジックを検証できます。

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。
