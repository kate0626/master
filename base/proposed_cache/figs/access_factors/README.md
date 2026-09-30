# access_factors — 図の索引

RW のアクセス回数と 4 要素（**D 次数 / H 始点からの hop（歩数）/ O 過去アクセス回数 /
R 直近性**）の関係を調べた図。条件は **6 グラフ × α=0.1・0.01 = 12 条件**、
母集団は **all / node / edge** の 3 通りあるので枚数が多い。

## まず `00_overview/` の 3 枚を見る

| ファイル | 何がわかるか |
|---|---|
| `per_factor.png` | **各要素が将来アクセスをどれだけ予測するか**（Spearman ρ）。12 条件 × 4 要素 × 3 母集団を 1 枚に |
| `pair_correlation.png` | **要素どうしの相関**（4C2 = 6 ペア）。点線枠 = 95%CI が 0 をまたぐ＝相関ありと言えない |
| `shapley.png` | **予測寄与の配分**（Poisson 逸脱度 D² の Shapley 値）。青＝負＝入れると予測が悪化 |

いずれも **行 = 条件**（上半分 α=0.1 / 下半分 α=0.01、グラフは次数 CV の小さい順）、
**列 = 要素またはペア**、**パネル = 母集団**。

再生成: `python3 figs/plot_access_factors_overview.py`

## 個別に深掘りするとき

| フォルダ | 中身 | どんなときに見るか |
|---|---|---|
| `01_per_factor/` | `accessfactors_<g>_a<α>_..._univariate.png` | **ある条件で、各要素と将来アクセスの関係の形**（単調か U 字か）を見たいとき。次数の非線形性はここで分かる |
| `02_pairwise_response/` | `..._pairs.png` | **2 要素を組にしたときの将来アクセス**（ヒートマップ）。「hop を固定すると次数はどう効くか」など |
| `03_pair_correlation/` | `paircorr_<g>_a<α>_..._<kind>_hwalk.png` | **要素どうしの相関の形と強さ**。中央値＋IQR、ρ・95%CI・NMI つき |
| `04_interplay/` | `interplay_<g>_a<α>_..._<kind>.png` | **冗長か相乗か**（交互作用情報量）と Shapley 分解 |

命名: `<graph>` = `er_mid / ba_mid / slashdot / enron / vldb / amazon`、
`<α>` = `0.1`（局所）/ `0.01`（拡散）、`<kind>` = `all / node / edge`、
`hwalk` = H の定義が歩数（`hbfs` なら BFS 距離）。

## 数値と文章

- 数値: `../../results/{accessfactors,paircorr,interplay}_*.csv`
- 結論: `../../docs/ACCESS_FACTORS_RESULTS.md`
