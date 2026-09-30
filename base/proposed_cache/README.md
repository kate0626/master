# proposed_cache — 提案手法「再利用スコア認可キャッシュ」

異なる管理主体下にあるグラフのランダムウォーク解析で、**越境時の認可通信をキャッシュで減らす**手法の
実装・実験・作図一式。

## どこに何があるか

```
proposed_cache/
├── README.md          ← このファイル（索引）
├── RUNBOOK.md         ← 実行手順（コマンドをそのままコピペできる）
│
├── core/              中核実装。ここだけが「手法そのもの」,　こちらがキャッシュの方式を変える前のもの
├── dist/              実際の K 台のマシンで回すための起動まわり
├── experiments/       論文の図表を作る実験ドライバ
├── sweeps/            パラメータ探索と結果の集計
├── figs/              作図スクリプト + 出力された図
├── results/           実験結果データ（CSV / JSON / ログ）
├── docs/              設計メモ・結果まとめ・論文ドラフト
└── archive/           旧実験・探索的分析（もう使っていない）
```

---

## core/ — 中核実装

互いに import し合うので同じフォルダに置いてある。**この4つが手法の本体**。

| ファイル | 役割 |
|---|---|
| `split_remote_server_reuse_score.py` | **実サーバ本体**（各マシンで常駐する HTTP サーバ）。グラフのシャード保持・認可判定・キャッシュ・RW の実行・他サーバへのハンドオフ。エンドポイント `/health` `/walk` `/continue_walk` `/authorize` `/access_stats` |
| `split_controller_reuse_score.py` | **コントローラ**（手元で1つだけ動く指揮役）。`/walk` を投げて結果を回収する |
| `virtual_server_sim_reuse_score.py` | **仮想サーバ**。上の実サーバを import して、1プロセス内に K 台を模擬。ヒット率だけを高速に測る |
| `virtual_server_dualpool.py` | 旧デュアルプール版（参考）。現在の提案手法は `experiments/run_loo_ablation.py --pool shared` で回す |

**提案手法 = 単一プール + スコアリング式**（案A、2026-09-08 決定）。

```
退去優先度 H(e) = last_access(e) + w · V(e)
価値        V(e) = O^θ · L^λ · H^β · D^γ    （観測・学習・距離・次数。各因子 [1,2) に正規化）
                    ただし D（次数）はノードにのみ与える
動作点      w = 100  （VLDB / Amazon ともに最良。グラフ別チューニング不要）
```

デュアルプール（node/edge を別プールに分ける機構）は**手法から外した**。寄与が
Shapley で +0.12/+0.54 pt（誤差レベル）で、実分割では −5.77 pt と有害だったため。
旧版は `archive/dualpool/`。経緯は [docs/LOO_ABLATION_RESULTS.md](docs/LOO_ABLATION_RESULTS.md) 追記3・追記4。

---

## dist/ — 実機で回す

| ファイル | 役割 |
|---|---|
| `run_local.sh` | **localhost に K プロセス**立てて回す。SSH 不要。まずこれで確認 |
| `run_cluster.sh` | **servers.conf の K 台**へ SSH で配って回す |
| `servers.conf` | サーバ定義。`host id ip port` を1行1台。**行数 = 台数** |
| `splits_reuse_score.sh` | 既存のスイープ用ランナー（ポリシー×パラメータのループ） |
| `verify_vs_virtual.py` | 仮想サーバと結果が一致するか突き合わせる |
| `aggregate_stats.py` | 全サーバの統計を合計して node/edge/overall にする |

提案手法は実サーバにフラグ3つを渡すだけで動く（**専用ラッパは不要**）。

```
--size-neutral --reuse-edge-no-deg --reuse-w 100
```

条件を揃えれば実分散と仮想サーバは**完全に一致する**（VLDB で4ポリシー全て Δ=0.00 を確認）。
詳細は [`dist/README.md`](dist/README.md)。

---

## experiments/ — 論文の図表を作る

| ファイル | 何を測るか | 出力 |
|---|---|---|
| `run_loo_ablation.py` | **要素別寄与**。Base / Freq / Close / Deg を1つずつ外す（`--pool shared` が既定） | `results/loo_*.csv` |
| `run_shapley_decomposition.py` | **寄与の厳密分解**（Shapley 値、2^4=16セル） | `results/shapley_*.csv` |
| `run_startnode_sweep.py` | **始点依存性** | `results/startsweep_*.csv` |
| `run_param_sensitivity.py` | **パラメータ感度**（OAT 1D、7軸） | `results/sens_*.csv` |
| `run_w_analysis.py` | 最適 w の分析 | `results/wanalysis_*.csv` |
| `run_onoff_ablation.sh` + `analyze_onoff_maineffects.py` | ON/OFF 主効果 | `results/all_*_settings.csv` |
| `run_singlepool_selection.py` | **単一プール版の構成選定**（plain / shared / degstar / dual を比較） | `results/singlepool_*.csv` |
| `reproduce_all.sh` | 上の主要4つを一括実行して図を4枚再生成 | `figs/*.png` |
| `analyze_access_factors.py` | **4要素とアクセス回数の関係**（次数/hop/過去回数/直近性 × 将来アクセス）。複数視点 RW | `results/accessfactors_*`, `figs/accessfactors_*` |
| `aggregate_access_factors.py` | 上の条件横断比較表 | 標準出力 |
| `run_access_factors_graphs.sh` + `graphs_access_factors.conf` | 次数の不均一さで並べた**6グラフ一括**実行 | 同上 |
| `deploy_access_factors.sh` | cap サーバへ配って収集だけ回す（**numpy 不要**）/ 結果回収 | `results/cap/` |
| `analyze_fetched_keys.sh` | 回収した keys CSV をまとめて統計・作図 | `results/`, `figs/` |
| `analyze_pair_correlations.py` | **4要素から2つ選ぶ6ペアの相関の有無**（ρ/τ/NMI/偏相関/ブートストラップCI） | `results/paircorr_*`, `figs/paircorr_*` |
| `analyze_factor_interplay.py` | 要素同士の重なり（冗長/相乗）と Shapley 分解 | `results/interplay_*`, `figs/interplay_*` |
| `run_factor_analysis_all.sh` + `aggregate_pair_correlations.py` | **全条件の keys CSV に対して上2つを一括**＋横断比較表 | 標準出力 |
| `figs/plot_access_factors_overview.py` | **俯瞰図3枚**（12条件を1枚に畳む）。索引は `figs/access_factors/README.md` | `figs/access_factors/00_overview/` |
| `analyze_score_form.py` | **価値式の形の比較**（積 vs 和 vs 交互作用、D を抜く / R を抜く） | `results/scoreform_*.csv` |

これらは `core/` の仮想サーバを `importlib` で読み込んで動く（実機不要・数秒〜数十秒）。

---

## sweeps/ — パラメータ探索と集計

| ファイル | 役割 |
|---|---|
| `run_reuse_score_sweep_rtt.sh` | **削減率のメインスイープ**。baseline → θ/λ/β/γ/lw の OAT → 集計 → RTT 比較 |
| `run_all_reuse_score_experiments.sh` | reuse_score 実験の一括実行 |
| `sweep_ppr_params.sh` / `run_bgl_sweep.sh` | 旧 ppr_demand 系のスイープ |
| `generate_results_csv.py` | 散らばったログを1本の CSV に集約 |
| `analyze_param_sweep_oat.py` / `analyze_param_stability.py` | スイープ結果の分析 |
| `compare_policy_results.py` | ポリシー間比較 |
| `plot_best_vs_baseline.py` / `rtt_compare_reuse_score.py` | 最良手法 vs ベースライン（ヒット率版 / RTT版） |
| `rtt_time_compare.py` / `create_amazon_nobt_test.py` | 補助 |

これらは `dist/splits_reuse_score.sh` を呼ぶので **SSH でサーバに繋がる環境が要る**。

---

## figs/ — 作図

作図スクリプト（`plot_*.py`）と、出力された PNG / PDF が同居している。
データは `../results/` から読む。主なもの:

| スクリプト | 図 |
|---|---|
| `plot_loo_ablation.py` | 要素別寄与 |
| `plot_shapley_decomposition.py` | Shapley 分解 |
| `plot_startnode_dependence.py` | 始点依存性 |
| `plot_param_sensitivity.py` | パラメータ感度 |
| `plot_proposed_win.py` | 提案 vs LRU/ARC |
| `plot_sweeps.py` | alpha / cap / walks スイープ |
| `plot_challenge_memo_vs_lru.py` | ②課題（memo と LRU の差 = 取りこぼし） |
| `plot_hitrate_and_time.py` + `collect_time_stats.py` | ヒット率と実行時間 |
| `plot_w_analysis.py` / `plot_w_ratio_law.py` / `plot_wsweep_*.py` | w の分析 |

引数なしで実行できるものが多い（既定パスが埋め込んである）。

---

## docs/ — 読み物

| ファイル | 内容 |
|---|---|
| `MOTIVATION.md` | なぜこの問題を解くのか |
| `PROPOSED_METHOD.md` | 手法の定義とパラメータの決め方 |
| `RESEARCH_STORY.md` | 研究のストーリー |
| `LOO_ABLATION_RESULTS.md` | 要素別寄与の全数値と考察 |
| `ADDITIONAL_EXPERIMENTS_PLAN.md` | 追加実験の設計と論文との対応 |
| `EXPERIMENT_SUMMARY.md` | 実験結果の要約 |
| `README_SUMMARY*.md` / `abstract_v*.md` | 論文用の下書き |
| `README_legacy.md` | 旧 README（bfs_prefetch 時代の記述。内容は古い） |

---

## archive/ — もう使っていない

| フォルダ | 中身 |
|---|---|
| `legacy_proposed/` | 旧 proposed 系（`split_*_proposed.py`・`splits.sh`・localhost ランナー群）。bfs_prefetch / ppr_demand 時代の実装 |
| `exploratory/` | 探索的な分析・作図（`analyze_*.py` / `plot_*.py`）。手法を決めるまでに使ったもの |
| `dualpool/` | 旧デュアルプール版の実サーバラッパと起動スクリプト（案A で手法から外した） |
| `notuse/` / `output_compare/` | 旧出力 |

パスは新構成に合わせて直してあるが、動作確認はしていない。

---

## 最初に何を動かすか

```bash
cd /Users/maiko/Documents/GitHub/master-progrem

# 1) 仮想サーバで手法を1回まわす（数秒、karate）
python3 base/proposed_cache/core/virtual_server_sim_reuse_score.py \
  --edges dataset/Louvain/graph/karate.gr --alpha 0.1 --walks 100 --policies lru arc memo

# 2) 論文の要素別寄与を再現する（VLDB は数分）
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --pool shared --w-node 100 \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5

# 3) 実分散（localhost K プロセス）で動かす
bash base/proposed_cache/dist/run_local.sh
```

詳しい手順は [RUNBOOK.md](RUNBOOK.md)。
