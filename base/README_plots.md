# Plot scripts カタログ

`base/auth-baseline-cache/` と `base/auth-cache-bfs-degree/` の下にある「画像を出力するスクリプト全リスト」と「それぞれが出すファイル名」のリファレンス。

スクリプトを追加・改名したときはこのファイルも更新すること。

---

## 1. `base/auth-baseline-cache/`

| # | スクリプト | 出力する画像 | 入力データ | 役割 |
|---|---|---|---|---|
| 1 | `results/plot_cache_results.py` | `cache_comparison_auth_time.png`<br>`cache_comparison_walk_time.png`<br>`cache_comparison_hitrate.png`<br>`cache_comparison_combined.png` | 各 policy ディレクトリ内の `*_global_transition.json` | **policy 別の棒グラフ比較**（none / memo / lru / arc）。`--results-dir <alphaXX_walks_NNN_capa_M>` で実行先 dir を切り替え可能。Length=1 はスクリプト内で除外、per-start 平均で集計。 |
| 2 | `analyze_cache_causes.py` | `cause1_gini_vs_hitrate.png`<br>`cause3_saturation_curve.png`<br>`bfs_dist_vs_access_boxplot.png`<br>`node_degree_vs_access_boxplot.png`<br>`bfs_dist_vs_node_degree_boxplot.png`<br>`edge_<feature>_vs_access_boxplot.png` (avg_degree / max_degree など複数)<br>`edge_degree_type_vs_access_boxplot.png` | `*_global_transition.json`（access count）<br>`dataset/Louvain/graph/<graph>.gr`（辺定義） | **「hit rate がなぜ伸びないか」の原因分析**: Gini、BFS 距離、次数、辺特徴ごとの access 分布 boxplot。`--results-dir` / `--out-dir` 指定可。 |

出力ファイルは原則として **`--results-dir` で指定したディレクトリの直下** に書き出される（同じ dir に既存ファイルがあれば上書き）。

---

## 2. `base/auth-cache-bfs-degree/`

| # | スクリプト | 出力する画像 | 入力データ | 役割 |
|---|---|---|---|---|
| 3 | `analyze_time.py` | `time_amazon0601.png`<br>`time_karate.png`<br>`time_vldb.png` | `results/<alphaXX_walksNN_capaMM>/<graph>/all_policies_summary.log`<br>欠落分は baseline 側の生 `.log` を再パース | **graph ごとの policy 比較**（none / lru / bfs-lru / degree-lru / hybrid-lru、memo は baseline から補完）。Length=1 と Traceback を除外したうえで `sum / n_valid` で per-start 平均。 |
| 4 | `plot_bfs_threshold.py` | `bfs_threshold_time.png`<br>`bfs_threshold_hitrate.png` | 各 `bfs-lru_far<F>_depth<D>_<cap>/<graph>.log` | **far_threshold sweep** に対する walk_time / hit_rate のラインプロット。 |
| 5 | `plot_bfs_hitrate_breakdown.py` | `bfs_hitrate_breakdown.png` | 各 `bfs-lru_far<F>_depth<D>_<cap>/<graph>.log` 中の<br>`Auth cache hit_rate [BFS-prefetched nodes]` / `[non-prefetched nodes]` 行<br>＋ baseline / bfs-degree 側の `lru` 生 `.log` | **3 系列のヒット率内訳**: LRU baseline / BFS-prefetched / non-prefetched の比較。プリフェッチが効いているのか・追い出されているのかを切り分け。 |
| 6 | `plot_bfs_cache_usage.py` | `bfs_cache_usage_ratio.png` | 各 run dir の `<graph>.log`（`[BFS_PREFETCH] nodes_cached=N` / `Total auth cache lookups`）<br>＋ `start=*_memory_summary.json`（最終 `cache_entries` / `cache_capacity`） | **キャッシュ量の比率分析**: `pref/cap`, `end/cap`, `pref/end`, `pref_lookup_frac` を 3 本バー + 赤い `cap=1.0` 水平線で可視化。 |

出力先はどれも `base/auth-cache-bfs-degree/results/alpha0.01_walks100_capa100/` 直下（スクリプト内ハードコード）。

---

## 3. 補助スクリプト（画像は出さない・参考）

| スクリプト | 役割 |
|---|---|
| `base/aggregate_results.py` | `*_global_transition.json` から `base/results_combined.csv` / `base/results_summary.csv` を作成。Length=1 / failed run を除外して集計。 |
| `base/regenerate_summaries.py` | 既存の `all_policies_summary.log` を Length=1 除外したフォーマットで再生成（`.bak` 残し）。 |

---

## 4. 更新コマンド早見表

```bash
# baseline (alpha=0.01 walks=100)
python3 base/auth-baseline-cache/results/plot_cache_results.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100

# baseline 原因分析（各 alpha dir に対して）
python3 base/auth-baseline-cache/analyze_cache_causes.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
  --graph-dir   dataset/Louvain/graph

# bfs-degree: policy 比較 (3 graphs)
cd base/auth-cache-bfs-degree && python3 analyze_time.py

# bfs-degree: far sweep
cd base/auth-cache-bfs-degree && python3 plot_bfs_threshold.py

# bfs-degree: hit rate 内訳
python3 base/auth-cache-bfs-degree/plot_bfs_hitrate_breakdown.py

# bfs-degree: キャッシュ量比率
python3 base/auth-cache-bfs-degree/plot_bfs_cache_usage.py

# CSV 集約（参考、画像なし）
python3 base/aggregate_results.py
```

---

## 5. 退避済みの古い PNG （生成元スクリプトが repo に残っていないもの）

Length=1 除外前の値を反映している可能性が高く信頼すべきでないため、`_deprecated_old_plots/` に隔離済み。データは消していないので必要なら戻せる。

| 退避先 | 中身 |
|---|---|
| `auth-baseline-cache/results/karate/_deprecated_old_plots/` | `fig1_capacity_vs_cache_entries.png` ～ `fig7_capacity_vs_walk_time_total.png` (7 ファイル) |
| `auth-baseline-cache/results/alpha0.1_walks_100_capa_100/_deprecated_old_plots/` | `degree_bfs_dist_vs_access.png` |
| `auth-baseline-cache/results/alpha0.1_walks_1000_capa_100/_deprecated_old_plots/` | `degree_bfs_dist_vs_access.png`, `node_degree_00.png`, `node_degree_01.png`, `bfs_dist_vs_access_boxplot_{100,500,base}.png` |

`auth-cache-bfs-degree/results/alpha0.01_walks100_capa100/time_vldb-6.png` は退避処理時点で既に存在しなかったため、退避先 dir は作成していない（`analyze_time.py` の再実行で上書き消失していたと思われる）。
