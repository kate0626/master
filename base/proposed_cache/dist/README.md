# dist — 提案手法を実際の複数サーバで回す

## 提案手法の定義（案A、2026-09-08 決定）

**提案手法 = 単一プール + スコアリング式。** デュアルプールは落とした。

```
退去優先度 H(e) = last_access(e) + w · V(e)
価値        V(e) = O^θ · L^λ · H^β · D^γ      （各因子は z標準化 → logistic で [1,2) に正規化）
                    ただし D（次数）はノードにのみ与える
動作点      w = 100  （VLDB / Amazon ともに最良。グラフ別チューニング不要）
```

実サーバ `../core/split_remote_server_reuse_score.py` にフラグ3つを渡すだけで提案手法になる。
専用のラッパは**要らなくなった**（旧デュアルプール版は `../archive/dualpool/`）。

| フラグ | 意味 | 外すとどうなるか |
|---|---|---|
| `--size-neutral` | `H = last_access + w·V`（`/size` を外す） | 占有量による型バイアスが残る。VLDB −2.3 pt |
| `--reuse-edge-no-deg` | エッジのスコアから次数 D を外す | VLDB −0.68 / Amazon −1.02 pt |
| `--reuse-w 100` | スコアの重み w | w=0 で厳密に LRU |

### なぜデュアルプールを落としたか

Shapley 分解でのプール分離の寄与は **+0.12 pt (VLDB) / +0.54 pt (Amazon)** で誤差レベル。
さらに実分割（METIS）だと各サーバがノードかエッジに偏るため、40/60 に割った片方が遊んで
**−5.77 pt と有害**になる。単一プールに戻しても性能は変わらない（実測 +0.06 / −0.49 pt）一方で、

- パラメータが4つ（cap_node / cap_edge / w_node / w_edge）→ 1つ（w）に減る
- w=100 が両グラフ共通なのでグラフ別チューニングが不要になる
- 配置方法（modulo / METIS）に依存しなくなる
- 実サーバにラッパが不要になる

詳細は [`../docs/LOO_ABLATION_RESULTS.md`](../docs/LOO_ABLATION_RESULTS.md) の追記3・追記4。

---

## ファイル

| ファイル | 役割 |
|---|---|
| `run_local.sh` | **localhost に K プロセス**立てて回す。SSH 不要。まずこれで確認する |
| `run_cluster.sh` | **servers.conf の K 台**へ SSH で配って回す |
| `servers.conf` | サーバ定義。`host id ip port` を1行1台。**行数 = 台数** |
| `splits_reuse_score.sh` | 既存のスイープ用ランナー（ポリシー×パラメータのループ） |
| `verify_vs_virtual.py` | 仮想サーバと結果が一致するか突き合わせる |
| `aggregate_stats.py` | 全サーバの統計を合計して node/edge/overall にする |

---

## 使い方

### 1. localhost で動かす

```bash
cd /Users/maiko/Documents/GitHub/master-progrem
bash base/proposed_cache/dist/run_local.sh
```

```
RESULT	lru	overall=78.63	node=84.60	edge=73.22	accesses=805	...
RESULT	arc	overall=79.38	node=84.60	edge=74.64	accesses=805	...
RESULT	memo	overall=85.34	node=89.03	edge=81.99	accesses=805	...
RESULT	proposed	overall=80.12	node=87.47	edge=73.46	accesses=805	...
```

`accesses` が全ポリシーで一致するのが正常（キャッシュは歩行経路を変えない）。
karate は34ノードのおもちゃなので、評価は vldb / amazon0601 で行う。

```bash
GRAPH=vldb WALKS=1000 ALPHA=0.1 bash base/proposed_cache/dist/run_local.sh
K=4 POLICIES="lru proposed" bash base/proposed_cache/dist/run_local.sh
W=300 bash base/proposed_cache/dist/run_local.sh          # w を変える
```

deny テーブル込み（論文§5.3 の認可設定）:

```bash
PARTITION_DIR=base/auth-many-server/data/splits/vldb/0.3 \
GRAPH=vldb WALKS=1000 bash base/proposed_cache/dist/run_local.sh
```

### 2. 実際の K 台で回す

```bash
# 分割（deny テーブル + shard を K 個作る）
bash base/new-many-server/partition.sh --graph vldb -k 4 --starts "0"

# 各ホストへ配布（★フォルダ整理と実サーバのフラグ追加があったので必須）
bash base/new-many-server/deploy.sh

# 実行
SERVERS_CONF=base/new-many-server/servers.conf \
GRAPH=vldb WALKS=1000 ALPHA=0.1 \
PARTITION_DIR=base/new-many-server/data/splits/vldb/0.3 \
bash base/proposed_cache/dist/run_cluster.sh
```

台数を増やす = `servers.conf` に行を足して `partition.sh -k <台数>` を回し直すだけ。
`--servers` / `--server-endpoints` は自動生成される。

### 3. 仮想サーバと一致することを確認する

条件（グラフ・K・alpha・walks・cap・start・seed・`NO_BACKTRACK`・partition-dir・w）を
揃えれば、実分散の結果は仮想サーバと **完全に一致する**。

```bash
python3 base/proposed_cache/dist/verify_vs_virtual.py \
  --edges dataset/Louvain/graph/vldb.gr --server-count 2 \
  --alpha 0.1 --walks 1000 --start-nodes 0 --seed 42 --cache-capacity 100 --w 100 \
  --summary base/proposed_cache/dist/results/vldb_K2_a0.1_w1000_cap100/summary.tsv
```

実測（VLDB, K=2, modulo, α=0.1, walks=1000, cap=100, seed=42, w=100）:

| policy | 仮想 | 実分散 | Δ | accesses |
|---|---|---|---|---|
| lru | 45.36 | 45.36 | 0.00 | 9273 / 9273 |
| arc | 49.97 | 49.97 | 0.00 | 9273 / 9273 |
| memo | 74.70 | 74.70 | 0.00 | 9273 / 9273 |
| **proposed** | **51.75** | **51.75** | **0.00** | 9273 / 9273 |

node/edge 内訳も一致（proposed: node 68.14 / edge 36.95）。
ずれたときはまず `accesses` を見る。ここが違えば歩行条件が揃っていない。

---

## 注意点

1. **`NO_BACKTRACK` の既定値が既存スクリプトと違う。** ここは `0`（= 論文の LOO/表1 と同条件）。
   `splits_reuse_score.sh` / `new-many-server/run_rw.sh` は `1`。揃えないと歩行そのものが変わり、
   VLDB の LRU で 45.4% ⇄ 21.1% 動く。
2. **deny テーブルの有無で絶対値が大きく変わる。** `PARTITION_DIR` を渡さないと
   modulo 分割・deny なしになる。論文の条件と揃えるなら必ず渡す。
3. **`node_to_starts_serverX.json` は拒否リスト（deny）として解釈される。** allow 形式の
   密なテーブルを入れると認可が反転して歩けなくなる。生成は `partition.sh --mode deny`（既定）。
4. **`/walk` を投げる先は start_node の所有者**（`START_SERVER_MODE=owner`、既定）。
   旧挙動の「常に server0」は `zero` で再現できるが、所有者でないサーバから始めると
   生存判定が二重に走って walk が短くなるため使わないこと。`start_node % K == 0` のとき
   （論文条件の start=0, K=2 など）は両者一致する。
5. **キャッシュ容量は重み単位。** node エントリ=2 / edge エントリ=3。`--cache-capacity 100`。
