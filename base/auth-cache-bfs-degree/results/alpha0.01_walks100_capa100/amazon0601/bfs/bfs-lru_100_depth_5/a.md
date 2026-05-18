# ====== 設定 ======
GRAPH=amazon0601
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
RW_WALKS=100
ALPHA=0.01
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)

CACHE_CAPACITY=100
####
BFS_FAR_THRESHOLD=5      # bfs-lru: この距離以上を far group として優先追い出し
BFS_PREFETCH_DEPTH=2     # bfs-lru: walk開始時にプリフェッチするBFS深さ
HUB_THRESHOLD=10      