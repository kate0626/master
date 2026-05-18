
## 実装の全体像
新しいフォルダ base/auth-cache-strategies/ に2ファイルを作成しました。

Strategy A: BFSDistanceLRUCache
Cache-in（2経路）

経路	タイミング	呼び出し
proactive	walk開始時	prefetch_bfs_neighbors() → set_with_dist(ckey, allowed, bfs_dist=d+1)
reactive	キャッシュミス時	_authorize_candidate() → set_with_dist(ckey, allowed, bfs_dist=dist_map.get(..., 0))
proactive では BFS depth=2 以内のローカル所有エンティティを先読みする。リモートエンティティはwalk中にreactiveで補完される。_bfs_dist_map をサーバーに保持することで、reactive cache-in 時にも正確な dist を付与できる。

Cache-out
far group (bfs_dist >= far_threshold=6)  ← 先に追い出す（LRU順）
near group (bfs_dist < 6)               ← far が空になったら追い出す（LRU順）
Strategy C: DegreeAwareLRUCache
Cache-in

reactive のみ。投入時に neighbor_map を参照してエンティティのTierを決定する（追加コストはdegreeルックアップのみ）。

Cache-out（Tier順）
Tier 0: low-low エッジ  (両端degree < hub_threshold=10) ← 最初
Tier 1: hub-low エッジ  (片端だけ >= 10)
Tier 2: 低次数ノード    (degree < 10)
Tier 3: hub-hub エッジ  (両端とも >= 10)
Tier 4: ハブノード      (degree >= 10)               ← 最後
各Tier内はLRU順。

# 実験の走らせ方
splits.sh の設定を変えて実行：

## リモート実験
cd /path/to/master-progrem
zsh base/auth-cache-strategies/splits.sh

## ローカル単体テスト
python3 base/auth-cache-strategies/server.py \
  --server-id 0 --server-count 1 \
  --edges dataset/Louvain/graph/vldb.gr \
  --host 127.0.0.1 --port 3000 \
  --server-endpoints 127.0.0.1:3000 \
  --cache-policy bfs-lru --cache-capacity 100 \
  --cache-bfs-far-threshold 6 --cache-bfs-prefetch-depth 2
新しいポリシー固有のCLIオプションは --cache-hub-threshold（degree-lru）と --cache-bfs-far-threshold / --cache-bfs-prefetch-depth（bfs-lru）です。

## 新規作成
BFSDistanceLRUCache（Strategy A）
DegreeAwareLRUCache（Strategy C）
prefetch_bfs_neighbors()
build_authz_cache()（bfs-lru / degree-lru 対応）
PeerWalker._authorize_candidate() — set_with_dist() 対応版
EdgeAwareHandler._handle_walk_start() — プリフェッチ呼び出し追加版
EdgeAwareHandler._handle_cache_reset() — 新ポリシー対応版
GraphShardServer.__init__() — hub_threshold / bfs_far_threshold / bfs_prefetch_depth 追加版
parse_arguments() / main() — 新オプション追加版
base/auth-cache-strategies/splits.sh

bfs-lru / degree-lru / lru / none を順番に実行する実験スクリプト
ポリシー固有のオプション（--cache-bfs-far-threshold 等）を自動付与


