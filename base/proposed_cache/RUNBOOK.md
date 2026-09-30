# 実行手順（追加実験まとめ）

論文 `IN_KishiMaiko.pdf`（dual-pool 系列）の**目的2「性能向上がどの構造情報に由来するか」**および
**パラメータの妥当性検証**のために追加したスクリプトと、その実行コマンド。

- **すべてリポジトリルートから実行する**（`cd /Users/maiko/Documents/GitHub/master-progrem`）
- 既存ファイル（`core/virtual_server_dualpool.py` / `core/virtual_server_sim_reuse_score.py` /
  `core/split_remote_server_reuse_score.py`）は**一切変更していない**。import して
  `dp.authorize_dual` を差し替え、`_h_value` をインスタンス単位で patch しているだけ。
  歩行ロジック（`dp._run_walk` / `dp._select_next`）は本家をそのまま使うので rng 消費も同一。
- 必要なもの：Python 3.10 / `matplotlib`（作図のみ）。それ以外は標準ライブラリ。
- 所要時間の目安：グラフのロードが VLDB ≈40秒 / Amazon ≈90秒。以降は 1構成あたり数秒。

```bash
cd /Users/maiko/Documents/GitHub/master-progrem
```

> **提案手法の定義が変わりました**（案A、2026-09-08）：**単一プール + スコアリング式**に一本化し、
> デュアルプールを外しました。`run_loo_ablation.py` は既定で `--pool shared`（単一プール）です。
> 旧定義（デュアルプール）を再現するには `--pool split` を付けてください。
> 経緯と数値は [docs/LOO_ABLATION_RESULTS.md](docs/LOO_ABLATION_RESULTS.md) の追記4。
>
> **フォルダ構成**（2026-09 に整理）：中核実装は `core/`、実機起動は `dist/`、
> 実験ドライバは `experiments/`、パラメータ探索は `sweeps/`、旧実験は `archive/` に分けた。
> 全体の索引は [README.md](README.md)。
> 実機（K台）で提案手法を回す手順は [`archive/dualpool/README.md`](archive/dualpool/README.md)。

---

## 0. ファイル一覧

### 実験（新規）

| ファイル | 役割 | 出力 |
|---|---|---|
| `experiments/run_loo_ablation.py` | **leave-one-out 対照実験**。Base / Pool / Freq / Close / Deg を1つずつ外す。LRU / ARC / memo / 分離LRU も同時に測る | `results/loo_*.csv`, `results/loo_*_raw.csv` |
| `experiments/run_shapley_decomposition.py` | **足し合わせが厳密に成立する寄与分解（Shapley 値）**。2^4=16 セル全部を測る | `results/shapley_*.csv`, `*_raw.csv` |
| `experiments/run_startnode_sweep.py` | **始点依存性**。始点ごとに 提案 / LRU / ARC / memo を比較 | `results/startsweep_*.csv` |
| `experiments/run_param_sensitivity.py` | **パラメータ感度（OAT 1D）**。w / w比 / θ / 容量配分 / C_F / β / γ を1軸ずつ振る | `results/sens_*.csv` |

### 作図（新規）

| ファイル | 図 | 備考 |
|---|---|---|
| `figs/plot_loo_ablation.py` | `figs/loo_ablation.png` | **引数なしで実行可**（既定パス埋め込み済み） |
| `figs/plot_shapley_decomposition.py` | `figs/shapley_decomposition.png` | 同上 |
| `figs/plot_startnode_dependence.py` | `figs/startnode_dependence.png` | 同上 |
| `figs/plot_param_sensitivity.py` | `figs/param_sensitivity.png` | 同上 |

### 結果まとめ（読み物）

| ファイル | 内容 |
|---|---|
| `docs/LOO_ABLATION_RESULTS.md` | 全数値・条件の切り分け・論文への反映案 |
| `docs/ADDITIONAL_EXPERIMENTS_PLAN.md` | 実装と論文の対応、設計上の論点 |

---

## 1. leave-one-out 対照実験（要素を1つずつ外す）

```bash
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 \
  --out-csv base/proposed_cache/results/loo_VLDB_a0.1.csv \
  --out-raw base/proposed_cache/results/loo_VLDB_a0.1_raw.csv
```

```bash
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 \
  --out-csv base/proposed_cache/results/loo_Amazon_a0.1.csv \
  --out-raw base/proposed_cache/results/loo_Amazon_a0.1_raw.csv
```

```bash
python3 base/proposed_cache/figs/plot_loo_ablation.py
```

**主なオプション**

| オプション | 意味 |
|---|---|
| `--disjoint --n-seeds 5` | seed を walks 以上離して自動生成（**独立反復**。誤差棒を出すなら必須） |
| `--seeds 1 7 13 99 123` | 論文と同じ seed（ウォーク集合が99%重複するので誤差棒は過小） |
| `--partition-dir <dir>` | 実グラフ分割 + deny テーブルを読み込む |
| `--deny-mode off` | partition-dir を指定しても deny だけ無効化（要因切り分け用） |
| `--owner-mode modulo` | partition-dir を指定しても所有はモジュロ（要因切り分け用） |
| `--w-node / --w-edge / --cap-node / --cap-edge / --theta / --cf` | 動作点の変更 |

---

## 2. 寄与の分解（合計が総改善に厳密一致する Shapley 値）

論文の seed を使うと合計が表1 の **+6.56 / +7.05 pt** に厳密一致する。

```bash
python3 base/proposed_cache/experiments/run_shapley_decomposition.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --seeds 1 7 13 99 123 \
  --out-csv base/proposed_cache/results/shapley_VLDB.csv \
  --out-raw base/proposed_cache/results/shapley_VLDB_raw.csv
```

```bash
python3 base/proposed_cache/experiments/run_shapley_decomposition.py \
  --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
  --seeds 1 7 13 99 123 \
  --out-csv base/proposed_cache/results/shapley_Amazon.csv \
  --out-raw base/proposed_cache/results/shapley_Amazon_raw.csv
```

```bash
python3 base/proposed_cache/figs/plot_shapley_decomposition.py
```

出力に `v(なし) 共有+スコアなし` と `LRU との残差` が出る。**残差が +0.00 であること**が
分解の前提（アンカーが LRU と一致していること）の確認になる。

---

## 3. 始点依存性

```bash
python3 base/proposed_cache/experiments/run_startnode_sweep.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --starts 0 1 2 3 4 --alpha 0.1 --walks 1000 --n-seeds 5 \
  --out-csv base/proposed_cache/results/startsweep_VLDB.csv
```

```bash
python3 base/proposed_cache/experiments/run_startnode_sweep.py \
  --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
  --starts 0 1 2 3 4 5 6 7 8 9 --alpha 0.1 --walks 1000 --n-seeds 5 \
  --out-csv base/proposed_cache/results/startsweep_Amazon.csv
```

```bash
python3 base/proposed_cache/figs/plot_startnode_dependence.py
```

グラフに存在しない始点は自動でスキップされる（VLDB は 0〜4 のみ存在）。

---

## 4. パラメータ感度（動作点が最適か・平坦か）

```bash
python3 base/proposed_cache/experiments/run_param_sensitivity.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --start-nodes 0 --n-seeds 5 \
  --out-csv base/proposed_cache/results/sens_VLDB.csv
```

```bash
python3 base/proposed_cache/experiments/run_param_sensitivity.py \
  --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
  --start-nodes 0 --n-seeds 5 \
  --out-csv base/proposed_cache/results/sens_Amazon.csv
```

```bash
python3 base/proposed_cache/figs/plot_param_sensitivity.py
```

軸を絞りたいときは `--axes w cap_node theta`（既定は7軸すべて）。

---

## 5. 実験条件の切り分け（deny / パーティション）

論文§5.1 の記述（30% deny・2サーバ配置）が表1 の条件と一致していないことの検証。
`--partition-dir` を渡すと **実グラフ分割** と **deny テーブル** が同時に有効になるので、
`--deny-mode` / `--owner-mode` で片方ずつ無効化して切り分ける。

```bash
# 実分割 + deny 30%（論文の記述に最も近い条件）
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB-part-deny \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 \
  --partition-dir base/auth-many-server/data/splits/vldb/0.3 \
  --out-raw base/proposed_cache/results/loo_VLDB_partdeny_raw.csv
```

```bash
# 実分割のみ（deny を無効化）
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB-part-only \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 \
  --partition-dir base/auth-many-server/data/splits/vldb/0.3 --deny-mode off \
  --out-raw base/proposed_cache/results/loo_VLDB_partOnly_raw.csv
```

```bash
# deny のみ（所有はモジュロのまま）
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB-deny-only \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 \
  --partition-dir base/auth-many-server/data/splits/vldb/0.3 --owner-mode modulo \
  --out-raw base/proposed_cache/results/loo_VLDB_denyOnly_raw.csv
```

**注意**：deny テーブルが拒否している始点は **{1, 2, 3, 4, 10}** のみで、
**start=0 には一件も適用されない**。deny を実際に効かせたいときは `--start-nodes 1` にする。
Amazon の deny は `base/auth-many-server/data/splits/amazon0601/0.3` にある。

---

## 6. 論文の表1 を再現する

```bash
python3 base/proposed_cache/experiments/run_loo_ablation.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --pool shared --w-node 100 \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5
```

先頭に出る `LRU (共有)` / `ARC (共有)` / `提案 (全要素)` / `memo (上限)` が新しい表1 になる。
**単一プール（案A）での確定値**（独立 seed 5本、alpha=0.1、walks=1000、cap=100、K=2 modulo）:

| | LRU | ARC | 提案 | memo | 提案−LRU | 提案−ARC |
|---|---|---|---|---|---|---|
| VLDB | 45.49 | 51.43 | **52.47** | 73.86 | +6.98 | +1.04 |
| Amazon | 45.05 | 52.36 | **52.10** | 65.04 | +7.04 | −0.26 |

旧デュアルプール定義の数値（VLDB 45.29 / 51.85 / 74.84、Amazon 44.33 / 51.38 / 64.33）は
`--pool split --seeds 1 7 13 99 123` で再現できるが、その seed 群はウォーク集合が99%重複する
ため誤差棒が過小になる点に注意。

---

## 7. 一括実行

```bash
bash base/proposed_cache/experiments/reproduce_all.sh
```

1〜4 をすべて実行して4枚の図を再生成する（VLDB と Amazon を並列実行。合計 15〜25 分程度）。
環境変数で条件を変えられる。

```bash
ALPHA=0.05 WALKS=3000 bash base/proposed_cache/experiments/reproduce_all.sh   # 条件を変える
GRAPHS="vldb" bash base/proposed_cache/experiments/reproduce_all.sh           # Amazon を外す
RES=/tmp/out  bash base/proposed_cache/experiments/reproduce_all.sh           # 既存結果を上書きしない
```

---

## 8. 既知の注意点

1. **seed の独立性**：`random.Random(seed + i)`（i = walk 番号）という規則なので、
   seed の差が `walks` 未満だとウォーク集合が重複する。論文 seed（1/7/13/99/123, walks=1000）は
   99% 重複しており、誤差棒を 2〜3 倍過小評価する。誤差棒を載せるときは `--disjoint` を使う。
2. **共有プールの扱い**：`Priority = last_access + w·V/size`（占有量 node=2 / edge=3）なので、
   共有プールで型別 w を使うとスコア全 OFF でも型バイアスが残る。本スクリプトでは
   共有プール時に単一 w を使い prior に占有量を掛けて `/size` を打ち消しているため、
   **スコア全 OFF の共有プール = 厳密に共有 LRU** になる（Shapley 分解の前提）。
3. **ローカルの `vldb.gr` は 317,080ノード / 1,049,866エッジ**で、論文 表3 の記載
   （2,856,664 / 13,511,731）とは一致しない。表1 の数値を再現するのはこのファイル。
   Amazon0601（403,394 / 3,387,388）は記載どおり。
