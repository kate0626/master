# proposed_cache — 提案手法の実装・分析

`base/auth-baseline-cache/` をベースに、**BFS 距離ベースのキャッシュ戦略 (bfs_prefetch / bfs_score / ppr_demand)** を
分散サーバ上で実機実行するための実装と、その分析・可視化スクリプト群。

---

## ファイル一覧

### コア実行ファイル（実機実験に使うもの）

| ファイル | 役割 |
|---|---|
| `split_controller_proposed.py` | コントローラ本体。prefetch 呼び出し・walk 制御・結果 JSON 出力 |
| `0613_split_controller_proposed.py` | 上記の 6/13 更新版（フォーマット整理＋微修正）。**こちらが最新** |
| `split_remote_server_proposed.py` | サーバ本体。提案ポリシー（bfs_prefetch/bfs_score/ppr_demand）と `/cache/prefetch` エンドポイントを追加 |
| `proposed_bfs_cache_uni.py` | シミュレータ（実機不要）。baseline の transition.json からヒット率・理論時間を試算 |
| `splits.sh` | 全ポリシー一括実行スクリプト。none/memo/lru/arc/提案手法 を順番に走らせる |

### 分析スクリプト（`analyze_*.py`）

| ファイル | 何を調べるか |
|---|---|
| `analyze_access_frequency.py` | ノード・エッジ別のアクセス頻度分布・ランキング CSV を出力 |
| `analyze_capacity_locality.py` | K 本 walk で作ったキャッシュが「全 100 本最適」にどれだけ収束するかを Jaccard / coverage で評価 |
| `analyze_opt_lru.py` | OPT(Belady) / LRU / Memo のヒット率を比較して「LRU の改善余地」を定量化 |
| `analyze_param_stability.py` | パラメータ (t/d/l/b/g/lw) を動かしたときの実行時間・始点間ばらつきを集計 |
| `analyze_reuse_features.py` | one-hit / multi-hit 割合・Belady 容量-ヒット率曲線・次数/距離の予測力 (AUC) を分析 |
| `analyze_rtt_time.py` | splits.sh 出力の JSON から RTT 考慮時間を**後付けで**計算する（実測ではなく計算式で合成） |
| `analyze_rwer_coverage.py` | 先頭 K 本の RW で全アクセス集合の何割をカバーできるか（観測窓長の妥当性確認） |
| `analyze_walk_events.py` | イベント列から one-hit/multi-hit・昇格タイミング・inter/intra reuse・probation 取りこぼしを検証 |

### 可視化スクリプト（`plot_*.py` / `access_*.py`）

| ファイル | 何をプロットするか |
|---|---|
| `plot_proposed_vs_baseline.py` | 提案手法 vs none/memo/lru/arc の実時間（walk+auth+prefetch）積み上げ棒グラフ |
| `plot_access_by_distance.py` | 始点からの距離別アクセス回数を start 単位で重ね描き |
| `plot_time_vs_capacity.py` | 容量(cap=100/200/300)を横軸に LRU vs 提案手法の実時間を折れ線＋削減率で比較 |
| `access_density_by_distance.py` | 距離別アクセス回数をシェル幅 N(d) で正規化した「アクセス密度」をプロット（グラフ構造の影響を除去） |

### 比較・集計スクリプト

| ファイル | 役割 |
|---|---|
| `compare_policy_results.py` | splits.sh 出力を読みポリシー横断でヒット率を比較・CSV 出力 |
| `rtt_time_compare.py` | `compare_policy_results.py` が出す summary CSV から RTT 考慮時間を policy 別に計算 |

### ユーティリティ

| ファイル | 役割 |
|---|---|
| `create_amazon_nobt_test.py` | `amazon0601_nobt/test/` にモックデータを生成（動作確認・CI 用） |
| `collect_per_walk_access.sh` | ウォーク別アクセスデータを収集するシェルスクリプト |

---

## 混乱しやすいファイルの関係

### 二重管理になっているペア（日付 prefix）

`0613_` prefix は 6/13 の更新版で、**実質的に同じファイルのフォーマット整理版**。

| 古い版（使わない） | 新しい版（こちらを使う） | 差分の内容 |
|---|---|---|
| `split_controller_proposed.py` | `0613_split_controller_proposed.py` | フォーマット整理 + コメント追記（`scale` 変数の説明など） |
| `plot_proposed_vs_baseline.py` | `0613_plot_proposed_vs_baseline.py` | フォーマット整理のみ、ロジック変更なし |

> 将来的には古い版を削除して `0613_` prefix も除去することを推奨（後述のファイル名変更案を参照）。

### 似た名前の分析スクリプト

| ファイル | 入力 | 主目的 |
|---|---|---|
| `analyze_rtt_time.py` | splits.sh 出力の JSON | 各 start の walk_time / auth_calls から RTT 時間を**直接計算** |
| `rtt_time_compare.py` | `compare_policy_results.py` の summary CSV | 集計済み CSV をもとにポリシー間の RTT 時間を**比較・グラフ化** |

| ファイル | x 軸 | y 軸 | 正規化 |
|---|---|---|---|
| `plot_access_by_distance.py` | 距離(hop数) | アクセス回数 | なし（生の回数） |
| `access_density_by_distance.py` | 距離(hop数) | アクセス密度 A(d)/N(d) | シェル幅で正規化 |

---

## ファイル名変更案

### 案2（naming convention 統一）：カテゴリ prefix を統一

意図が分かりやすい命名に統一する。

| 現在のファイル名 | 変更案 | 理由 |
|---|---|---|
| `0613_split_controller_proposed.py` | `split_controller_proposed.py` | prefix 不要、古い版は削除 |
| `0613_plot_proposed_vs_baseline.py` | `plot_proposed_vs_baseline.py` | 同上 |
| `access_density_by_distance.py` | `plot_access_density_by_distance.py` | `plot_` prefix で可視化スクリプトと統一 |
| `rtt_time_compare.py` | `compare_rtt_time.py` | `compare_` prefix で集計スクリプトと統一 |
| `proposed_bfs_cache_uni.py` | `simulate_bfs_cache.py` | 「シミュレータ」と分かる名前に |

変更後のカテゴリ対応：

```
split_controller_proposed.py       # コア: コントローラ
split_remote_server_proposed.py    # コア: サーバ
splits.sh                          # コア: 一括実行

analyze_*.py                       # 分析スクリプト (8本)
plot_*.py                          # 可視化スクリプト (4本, access_density含む)
compare_*.py                       # 比較・集計 (2本)
simulate_bfs_cache.py              # シミュレータ
create_amazon_nobt_test.py         # ユーティリティ
```

---

## 既存からの追加点（baseline との差分）

### 新キャッシュポリシー
- **`bfs_prefetch`** — start_node から BFS 距離 ≤ K のノードを walk 開始前に prefetch
- **`bfs_score`** — `score = attempts(v) × γ^dist(v)` 上位 N をキャッシュ
- **`ppr_demand`** — PPR（個人化 PageRank）スコアで優先順位付け（オンデマンド更新）

### 新エンドポイント（サーバ側）
- `POST /cache/prefetch` — `{start_node, mode, K|capacity|decay, attempts}` を受け取り BFS で対象を選定 → 事前認可してキャッシュに投入
- `POST /cache/freeze` — prefetch cache を明示的に freeze

### 新コントローラ引数
- `--prefetch-mode {none,bfs_prefetch,bfs_score}` — デフォルト `none`
- `--prefetch-k INT` — BFS 距離 K（bfs_prefetch 用）
- `--prefetch-capacity INT` — 上位 N（bfs_score 用）
- `--prefetch-decay FLOAT` — 距離減衰率 γ（bfs_score 用）
- `--prefetch-attempts-source PATH` — baseline の none_100/ からアクセス頻度ヒントを読み込む

### 新出力メトリクス（JSON）
- `auth_cache_hit_prefetched` — prefetch 由来のヒット数
- `prefetch_size` — prefetch でキャッシュに入れたエントリ数
- `prefetch_build_time_sec` — prefetch の実行時間

---

## 実行方法

### 全ポリシー一括実行（推奨）
```bash
cd /Users/maiko/Documents/GitHub/master-progrem
GRAPH_OVERRIDE=amazon0601 zsh base/proposed_cache/splits.sh
GRAPH_OVERRIDE=vldb       zsh base/proposed_cache/splits.sh
```

出力先：
```
base/proposed_cache/results/alpha0.01_walks_100_capa_100/{graph}/
  ├── none_100/
  ├── memo_100/
  ├── lru_100/
  ├── arc_100/
  ├── bfs_prefetch_K10/
  └── bfs_score_N100_d0.7/
```

### 分析・比較グラフ生成
```bash
# ポリシー横断ヒット率比較
python3 base/proposed_cache/compare_policy_results.py \
  --input base/proposed_cache/results/alpha0.01_walks_100_capa_100/vldb_nobt

# 提案 vs ベースライン 実時間グラフ
python3 base/proposed_cache/0613_plot_proposed_vs_baseline.py \
  --baseline-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
  --proposed-dir base/proposed_cache/results/alpha0.01_walks_100_capa_100 \
  --out-dir base/proposed_cache/output_compare
```

---

## 注意点

1. **prefetch cache は frozen** — walk 中の miss では新エントリは追加されない（memo との違い）
2. **BFS は各サーバが独立実行** — 全グラフを各サーバが持っているため、`owner_sid` でフィルタして局所エンティティのみをローカル判定
3. **prefetch 時間も計測に含まれる** — `prefetch_build_time_sec` を別記録しているのでフェアな比較が可能
