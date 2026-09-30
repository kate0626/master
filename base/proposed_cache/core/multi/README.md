# core/multi — ノードとエッジを別サーバ群に置く（任意 K = Kn + Ke）

## これは何か

二部グラフとしての管理（`node ↔ edge_u_v ↔ node`、エッジもノードと対等な1エンティティ）
を前提に、**所有を型で分ける**配置を扱うファイルセット。

```
server id  0 .. Kn-1        → ノードのみ所有   (role=node)
server id  Kn .. Kn+Ke-1    → エッジのみ所有   (role=edge)
合計       K = Kn + Ke      （任意）
```

既存 `core/*.py` は**そのまま残してあります**。このフォルダは既存を import して
**2箇所だけ**差し替えます。サーバ間の流れ（`/walk` 1回 → `/continue_walk` で
state+rng_state をバケツリレー → `/authorize` はミスかつ他サーバ所有のときだけ）は
一切変えていません。

| 差し替え | ファイル |
|---|---|
| パーティショナ（ノードとエッジを別サーバ群へ） | `partition.py` |
| スコア式（`V = O^θ · H^β · D^γ`） | `score.py` |

---

## ファイル

| ファイル | 役割 |
|---|---|
| `partition.py` | `NodeEdgeSplitPartitioner`。`servers.conf` の読み込みと検証も。単体で `python3 partition.py` を叩くと自己検証が走る |
| `score.py` | **スコア式はここだけ**。式を変えるならこのファイルを触る。`python3 score.py` で自己検証 |
| `server.py` | RW サーバ。既存サーバを import して上2つを差し替え、本家 `main()` を呼ぶ |
| `sim.py` | 仮想サーバ。同じ差し替えを入れて1プロセスで K 台を模擬。Kn/Ke の掃引はまずこれで |
| `servers.conf` | `host id ip port role` を1行1台。**role 列が追加**されている |
| `run_local.sh` | localhost に K プロセス立てて回す。SSH 不要 |

---

## スコア式

```
退去優先度  H(e) = last_access(e) + w · V(e)
価値        V(e) = O^θ · H_^β · D^γ      （ノード）
                 = O^θ · H_^β            （エッジ）

  O  = 1 + freq/(freq + C_F)     観測（頻度）
  H_ = 1 + sigmoid(z_hop)        距離（始点に近いほど大）
  D  = 1 + sigmoid(z_deg)        次数

  既定: θ = β = γ = 1、w = 100、C_F = 5
```

`/size`（占有量）で割りません。割ると `V=1` でも node に `+w/2` / edge に `+w/3` の
定数差が付き、型バイアスが残ります。割らない形なら **w=0 が厳密に LRU** と一致します。

### ⚠️ 次数項の適用は「サーバの役割」ではなく「エンティティの型」で決める

型分離配置での非自明な帰結です。キャッシュに入るのは**そのサーバが所有するもの**ではなく、
**そのサーバが認可を問い合わせた候補**、つまり現在地の隣接 = **反対の型**です。

```
ノード担当サーバ（ノードを所有）→ 現在地はノード → 候補はエッジ → エッジをキャッシュする
エッジ担当サーバ（エッジを所有）→ 現在地はエッジ → 候補はノード → ノードをキャッシュする
```

役割で `use_degree` を決めると逆になります。`score.py` は `is_edge` 引数で型を見ています。

---

## 使い方

### 1. 仮想サーバで Kn/Ke を掃引する（速い）

```bash
cd /Users/maiko/Documents/GitHub/master-progrem

python3 base/proposed_cache/core/multi/sim.py \
  --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
  --sweep "1:1 2:2 2:3 3:2 4:4" \
  --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 3 \
  --out-csv base/proposed_cache/results/multi_split_VLDB.csv
```

### 2. localhost の K プロセスで実分散を回す

`servers.conf` の role 列で Kn / Ke が決まります。

```bash
bash base/proposed_cache/core/multi/run_local.sh
GRAPH=vldb WALKS=1000 bash base/proposed_cache/core/multi/run_local.sh
```

### 3. 台数を変える

`servers.conf` に行を足すだけです。**node 役をすべて先、edge 役をすべて後**に並べ、
id を 0 から連番にしてください（`partition.py` が検証して落とします）。

```
# host   id   ip            port   role
ab06     0    10.58.60.6    3000   node
ab07     1    10.58.60.7    3000   node
ab11     2    10.58.60.11   3000   edge
ab12     3    10.58.60.12   3000   edge
ab13     4    10.58.60.13   3000   edge
```

---

## この配置で起きること（実測）

### 越境率が 100% になる

二部グラフの歩行は `node → edge → node → …` と型が必ず交互します。型でサーバが
分かれているので、**ミス時の認可は必ずリモート**になります（実測 `local_auth=0`）。

型混在（modulo）配置では越境率 約50%。つまりこの配置では
**キャッシュ1ヒットが必ず1回のネットワーク往復を節約する**ことになり、
削減率の主張が最も強く出る条件です。同時に `/continue_walk` も毎ホップ発生するので、
実時間で測るとハンドオフのコストが効いてくる点には注意が必要です。

### デュアルプールの議論が構造的に解消する

各サーバのキャッシュには片方の型しか入りません。「node/edge を別プールに分けるか」
という問題自体が消え、案A（単一プール + スコア）と整合します。

### Kn : Ke は「型ごとのキャッシュ容量配分」で、しかも逆向き

上の帰結から、

```
ノード用キャッシュの総容量 = Ke × cap   （エッジ担当サーバがノードをキャッシュする）
エッジ用キャッシュの総容量 = Kn × cap   （ノード担当サーバがエッジをキャッシュする）
```

**Kn を増やすとエッジ側の容量が増えます。** 直感と逆なので注意してください。

---

## 検証

条件を揃えれば `sim.py` と `run_local.sh`（実分散）は**完全に一致します**。
karate, Kn=1, Ke=1, α=0.1, walks=100, seed=42, w=100:

| policy | 仮想 (sim.py) | 実分散 (run_local.sh) |
|---|---|---|
| lru | 83.98 | 83.98 |
| arc | 84.72 | 84.72 |
| memo | 90.19 | 90.19 |
| **proposed** | **84.97** | **84.97** |

`accesses` は全ポリシー 805 で一致（キャッシュは歩行経路を変えない）。
ずれたときはまず `accesses` を見てください。

> 実装メモ: 仮想サーバの構造項は `PeerWalker._hop_prior` ではなくモジュール関数
> `virtual_server_sim_reuse_score.hop_prior` が担当しています。`sim.py` はここも
> 差し替えています。忘れるとエッジに次数が入って実サーバと 0.1 pt 程度ずれます。

---

## 注意点

1. **karate では評価できません。** 34ノードしかないので K を増やすと総容量 `K × cap` が
   working set を超え、`lru = arc = memo = proposed` になります（実測 Kn=4:Ke=4 で全て同値）。
   評価は vldb / amazon0601 で行ってください。
2. **既存の型混在配置の結果とは直接比較できません。** 配置が違うので条件が異なります。
   両方測って「配置による違い」として並べる形になります。
3. **認可表（`node_to_starts_serverX.json`）を使う場合は型別に作り直しが必要です。**
   現状の `run_local.sh` は modulo 経路（deny なし）のみ対応です。deny 込みで回すには
   `base/auth-many-server/only_split_auth_tables.py` に同じパーティショナを入れて
   K 個の shard を作る必要があります（未実装）。
4. **実機（`run_cluster.sh`）は未作成です。** `dist/run_cluster.sh` と同じ構造で
   `--node-servers` / `--edge-servers` を渡す形になります。
