"""
METISを使った認証テーブルを分割するスクリプト

2部グラフを生成する

"""

from pathlib import Path
from collections import defaultdict

## TODO: ここのグラフ種類は変更の必要
GRAPH_NAME = "fb-pages-food"
edge_path = Path(f"dataset/Louvain/graph/{GRAPH_NAME}.gr")
out_path = Path(f"base/auth-many-server/{GRAPH_NAME}/bipartite.metis")
node_shift = 1  # 元が0始まりなら+1して1始まりに揃える。既に1始まりなら0にする。

edges = []
with edge_path.open() as f:
    for line in f:
        s = line.strip()
        if not s:
            continue
        u, v = map(int, s.split())
        u += node_shift
        v += node_shift
        edges.append((u, v))

max_node = max(max(u, v) for u, v in edges)
adj = defaultdict(set)
for idx, (u, v) in enumerate(edges):
    e_id = max_node + idx + 1  # エッジ頂点ID
    adj[u].add(e_id)
    adj[v].add(e_id)
    adj[e_id].add(u)
    adj[e_id].add(v)

total_v = max_node + len(edges)
m = sum(len(nbs) for nbs in adj.values()) // 2
lines = [f"{total_v} {m}"]
for vid in range(1, total_v + 1):
    lines.append(" ".join(str(x) for x in sorted(adj.get(vid, []))))
out_path.write_text("\n".join(lines))
print(f"wrote {out_path} (V={total_v}, E={m})")
