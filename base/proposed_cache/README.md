# proposed_cache — 提案手法の実機実装 (BFS prefetch + BFS-score)

`base/auth-baseline-cache/` をベースに、**BFS 距離ベースのキャッシュ戦略**を分散サーバ上で
実機実行できるよう拡張したもの。

## ディレクトリ

```
base/proposed_cache/
├── split_remote_server_proposed.py  # サーバ (新ポリシー + /cache/prefetch エンドポイント)
├── split_controller_proposed.py     # コントローラ (prefetch 呼び出し + 引数)
├── splits_proposed.sh               # 実行スクリプト (全ポリシー一括実行)
├── proposed_bfs_cache_uni.py        # 解析的シミュレータ (実機なし版・参考)
├── output_uni/                       # シミュレータ出力
└── results/                          # 実機実行の結果保存先 (自動生成)
```

## 既存からの追加点

### 1. 新キャッシュポリシー
- **`bfs_prefetch`** — start_node から BFS 距離 ≤ K のノードを事前 prefetch
- **`bfs_score`** — `score = attempts(v) × γ^BFS_dist(v)` 上位 N をキャッシュ

両者とも `FrozenPrefetchCache` を使用 (prefetch 後は freeze、walk 中の追加書き込み禁止)。

### 2. 新エンドポイント (サーバ側)
- `POST /cache/prefetch` — `{start_node, mode, K|capacity|decay, attempts}` を受け取り、
  サーバ内 BFS で対象ノードを選定 → auth 判定を事前計算 → 認可キャッシュに投入
- `POST /cache/freeze` — 明示的に prefetch cache を freeze (通常は自動)

### 3. コントローラの新引数
- `--prefetch-mode {none, bfs_prefetch, bfs_score}` — デフォルト `none`
- `--prefetch-k INT` — BFS 距離 K (bfs_prefetch 用, デフォルト 10)
- `--prefetch-capacity INT` — 上位 N (bfs_score 用, デフォルト 100)
- `--prefetch-decay FLOAT` — 距離減衰率 γ (bfs_score 用, デフォルト 0.7)
- `--prefetch-attempts-source PATH` — 既存 baseline (`none_100/` ディレクトリ等) から
  attempts ヒントを自動読み込み

### 4. 新メトリクス (出力 JSON)
- `auth_cache_hit_prefetched` — prefetch 由来のヒット回数 (全ヒットの内訳)
- `prefetch_size` — prefetch でキャッシュに入れたエントリ数
- `prefetch_build_time_sec` — prefetch の実行時間

## 実行方法

### 全ポリシー一括実行 (推奨)
```bash
cd /Users/maiko/Documents/GitHub/master-progrem
GRAPH_OVERRIDE=amazon0601 zsh base/proposed_cache/splits_proposed.sh
# または
GRAPH_OVERRIDE=vldb zsh base/proposed_cache/splits_proposed.sh
```

実行されるポリシー:
- 既存比較対象: `none / memo / lru(100) / arc(100)`
- 提案手法: `bfs_prefetch(K=10) / bfs_score(N=100, γ=0.7)`

出力先:
```
base/proposed_cache/results/alpha0.01_walks_100_capa_100/{graph}/
  ├── none_100/
  ├── memo_100/
  ├── lru_100/
  ├── arc_100/
  ├── bfs_prefetch_K10/
  └── bfs_score_N100_d0.7/
```

### 単独実行 (デバッグ用)
```bash
# サーバ起動 (各ホストで)
python3 base/proposed_cache/split_remote_server_proposed.py \
  --server-id 0 --server-count 2 --host 10.58.60.6 --port 3000 \
  --edges dataset/Louvain/graph/amazon0601.gr \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --cache-policy bfs_prefetch --cache-capacity 100 \
  --owned-hints-only \
  --node-to-starts-file base/auth-many-server/data/splits/amazon0601/0.3/node_to_starts_server0.json

# コントローラ実行
python3 base/proposed_cache/split_controller_proposed.py \
  --servers 2 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --start-node 0 --walks 100 --alpha 0.01 --seed 42 \
  --cache-policy bfs_prefetch --cache-capacity 100 \
  --prefetch-mode bfs_prefetch --prefetch-k 10 \
  --out-dir /tmp/proposed_test
```

### bfs_score 用の attempts hint
スクリプト内の `ATTEMPTS_HINT_DIR` が `base/auth-baseline-cache/results/.../{graph}/none_100/` を指している。
既存 baseline 実験を済ませてからこのスクリプトを実行すれば、各 start_node の
`authorization_attempts` を自動で頻度ヒントとして読み込む。

## パラメータの調整方法

`splits_proposed.sh` 内の以下を編集:
```bash
PREFETCH_K=10              # bfs_prefetch: BFS K-hop 範囲
PREFETCH_CAPACITY=100      # bfs_score: 上位 N (LRU(100) と同条件)
PREFETCH_DECAY=0.7         # bfs_score: γ
```

複数の K や N を試したい場合は、`CACHE_POLICIES` に同じポリシーを並べる代わりに、
スクリプトを複製して K 値を変えるか、`POLICY_TAG` 名で区別する形に拡張する。

## 期待される結果

シミュレータ (`proposed_bfs_cache_uni.py`) では、LRU(100) と比較して:
- **Amazon0601 / 遠距離 RTT=200ms**: bfs_score(top500) で −141s (16% 短縮)
- **VLDB / 遠距離 RTT=200ms**: bfs_prefetch(K=15) で −124s (3.6% 短縮)

実機では prefetch オーバーヘッド (`prefetch_build_time_sec`) と
ネットワーク遅延を含めた実時間で計測されるため、シミュレータ値より控えめになる可能性がある。
`prefetch_size` と `auth_cache_hit_prefetched` を見ながら、K / N を調整して
コスト/効果バランスを最適化する。

## 注意点 / 既知の制約

1. **prefetch cache は frozen** — walk 中の cache miss では新エントリは追加されない。
   memo (UnlimitedDecisionCache) との違いはこの点。
2. **prefetch 時間も実測される** — `/cache/prefetch` の呼び出しは controller の
   合計時間に含まれる。フェアな比較のため、`prefetch_build_time_sec` を別途記録。
3. **BFS は各サーバが独立に実行** — 各サーバは全グラフを知っている (edge list を
   共有しているため) ので、両サーバが同じ BFS 球を計算する。
   重複削減のため、実装では `owner_sid == self.server_id` のエンティティのみを
   ローカルで判定し、それ以外はリモート問い合わせする。
