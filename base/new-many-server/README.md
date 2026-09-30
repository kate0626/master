# new-many-server — 任意K台の分散RW (自己完結版)

既存の `base/proposed_cache/` `base/auth-many-server/` を**一切変更せず**、必要なコードをこのフォルダに
新規コピー＋新規ラッパーで再構成したもの。**servers.conf の行数を変えるだけで任意K台**に対応する。

## フォルダ構成

```
new-many-server/
├── servers.conf        # サーバ定義 (1行1台 "host id ip port")。台数はここで決まる
├── partition.sh        # 【分割フェーズ】METISで任意K分割 → shard生成
├── run_rw.sh           # 【実行フェーズ】servers.conf駆動でK台起動 → controller実行
├── code/               # コピーした本体 (既存からの複製。原本は無変更)
│   ├── split_remote_server_reuse_score.py   # リモートRWサーバ (K対応済)
│   ├── split_controller_reuse_score.py      # コントローラ (K対応済)
│   ├── metis.py                             # エッジリスト→2部METISグラフ
│   ├── only_split_auth_tables.py            # node_to_starts をK分割 (off-by-one修正済)
│   ├── create_json_table.py                 # 認可表生成 (+--out-dir を追加)
│   └── create_json_table_auth_table.py      # deny-direct 用 (参考)
└── data/               # 生成物 (実行時作成、自己完結)
    ├── tables/<graph>/node_to_starts_<ng>.json
    ├── metis/<graph>/bipartite.metis[.part.K]
    └── splits/<graph>/<ng>/node_to_starts_server{0..K-1}.json
```

## 分散環境

| 項目 | 値 |
|---|---|
| ホスト | `cap11` 〜 `cap26` (最大16台) |
| IP | `10.58.59.11` 〜 `10.58.59.26` |
| User | `kate` (HOME=`/home/kate`) / SSH Port 22 |
| RWサーバport | `3000` (endpoints は `10.58.59.<n>:3000`) |
| リモート配置 | `/home/kate/base/new-many-server/` `/home/kate/dataset/` (repo相対) |

## 前提

- `dataset/Louvain/graph/<graph>.gr` が repo ルートに存在
- `gpmetis` が PATH にある (`brew install metis`)
- 各 `capNN` へ鍵認証SSHが通ること (`ssh cap11` で入れる状態)
- ローカル(mac)から `10.58.59.<n>:3000` へ到達可能 (controller/health用)

## 使い方

### 1) 分割フェーズ (ローカルで完結)

```bash
cd /Users/maiko/Documents/GitHub/master-progrem
bash base/new-many-server/partition.sh --graph karate --server-count 3          # deny(既定)
bash base/new-many-server/partition.sh --graph vldb   -k 4 --starts "0"         # 大規模は starts 限定
```

- **`--mode deny` が既定**（本番と同じ意味論・省メモリ）。各 entity → 「NG(拒否)にする start の疎集合」。
  比較用に `--mode allow`（許可の密集合）も可能だが、**現行サーバは deny 解釈なので allow を入れると認可が反転**する。
- `--starts`: deny 生成の対象 start（`all`/`0`/`1-10`/`1,3,7`）。大規模グラフは `all` だと爆発するので
  実験で使う start に限定推奨。
- `--node-shift`: 0始まりグラフは `1`(既定)、1始まりは `0`。metis と split で自動一致。
- 出力 shard は `data/splits/<graph>/<ng>/node_to_starts_server{0..K-1}.json`。

### 2) servers.conf を用意 (任意K)

手書きでも、生成スクリプトでも可 (id は 0 連番、cap11起点):

```bash
bash base/new-many-server/gen_servers_conf.sh --count 4     # cap11..cap14 の4台
```

### 3) 配布 (全ホストへ code + dataset + 全shard)

```bash
bash base/new-many-server/deploy.sh --graph vldb            # servers.conf 全ホストへ rsync
bash base/new-many-server/deploy.sh --graph vldb --dry-run  # 送信内容の確認だけ
```

- **全K個**の shard を各リモートの `base/new-many-server/data/splits/<graph>/<ng>/` に置く
  (owner_map は同一 dir の全 shard を glob して作るため、各ホストに全K個が必要)。
- `code/` と `dataset/<graph>.gr` も各リモートへ (deploy.sh が一括で配る)。
- **K を変えたら**: `gen_servers_conf.sh --count K` → `partition.sh --server-count K` →
  `deploy.sh` の順で作り直す (conf の台数と分割Kは必ず一致させる)。

### 4) 実行フェーズ

```bash
cd /Users/maiko/Documents/GitHub/master-progrem
GRAPH_OVERRIDE=karate zsh base/new-many-server/run_rw.sh
GRAPH_OVERRIDE=vldb CACHE_POLICIES_OVERRIDE="reuse_score" zsh base/new-many-server/run_rw.sh
```

- `--servers` / エンドポイントは servers.conf から自動生成 (2台ハードコードを排除)。
- 主な環境変数: `GRAPH_OVERRIDE` `CACHE_POLICIES_OVERRIDE` `CACHE_CAPACITY_OVERRIDE`
  `ALPHA_OVERRIDE` `START_NODES_OVERRIDE` `REMOTE_REPO_DIR` `SERVERS_CONF`。
- 結果は `results/alpha.../<graph>/<policy>/` に出力。

## 対応ポリシー

`none / memo / lru / arc / ppr_demand / reuse_score` (demand系)。
prefetch系 (`bfs_prefetch / bfs_score / ppr_gdsf`) はこの簡易ドライバでは未対応
(選ぶと SKIP される。必要なら元 `base/proposed_cache/dist/splits_reuse_score.sh` を使用)。

## 設計メモ

- **任意K化の実体**は run_rw.sh (servers.conf 読取 + controller の `--servers`/endpoints 動的化)。
  RWサーバ・コントローラ本体は元々K対応済みのためコピーのみ。
- **認可の意味論は deny**（重要）: サーバ (`_is_locally_allowed` / `/authorize`) は
  `node_to_starts[entity]` を **拒否リスト**として解釈する (`start ∈ denied` なら不許可、空なら許可)。
  本番データも deny（疎リスト = 省メモリ。vldb で平均1.8件/entity）。
  → 分割は `--mode deny`（既定）で生成すること。allow データを入れると認可が反転する。
- **off-by-one 修正済**: `build_edge_metis_map`（deny/allow 両生成器）は
  METIS頂点ID = `max_node + idx`（`load_metis_partition` の「行i→頂点i+1」規約と整合）。
- 現状、各サーバは近傍を**全グラフから構築**する (RWサーバ本体の仕様)。エッジ実体まで
  サーバ別に分割したい場合は別途改修が必要 (owner_map=ルーティング表は小さい/グラフ実体が大きい)。
```
