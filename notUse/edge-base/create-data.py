import networkx as nx
import random

# ...
edge_file = "./../../dataset/Louvain/graph/karate.gr"

G = nx.Graph()
with open(edge_file, "r") as f:
    for line in f:
        u, v = map(int, line.strip().split())
        G.add_edge(u, v)

# エッジIDを付与
edges = list(G.edges())
edge_ids = {edge: f"{i+1:03d}" for i, edge in enumerate(edges)}

# 可視性の割合（例：30%を非可視）
invisible_ratio = 0.1
num_invisible = int(len(edges) * invisible_ratio)
invisible_edges = set(random.sample(edges, num_invisible))

# ノードごとの出力
with open("./../../dataset/edge-base/node_visibility.txt", "w") as f:
    for node in G.nodes():
        f.write(f"Node {node}\n")
        for edge in edges:
            if node in edge:
                eid = edge_ids[edge]
                visible = "×" if edge in invisible_edges else "○"
                f.write(f"{visible} {eid}\n")
        f.write("\n")

# 非可視エッジのアクセス権ファイル（全ノードがアクセス可能）
all_nodes = ",".join(str(n) for n in G.nodes())
with open("./../../dataset/edge-base/access_control.txt", "w") as f:
    for edge in invisible_edges:
        eid = edge_ids[edge]
        f.write(f"{eid} {all_nodes}\n")
