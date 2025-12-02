# import networkx as nx
# import random

# # --- 入力ファイル ---
# edge_file = (
#     "./../../dataset/Louvain/graph/karate.gr"  # 各行: A B（ノード名は文字でもOK）
# )

# # --- グラフ構築 ---
# G = nx.Graph()
# with open(edge_file, "r") as f:
#     for line in f:
#         u, v = line.strip().split()
#         G.add_edge(u, v)
# all_nodes = sorted(G.nodes())  # 全ノード一覧

# # --- ノード出力 ---
# with open("./../../dataset/edge-base/[separate]nodes.txt", "w") as f:
#     for node in sorted(G.nodes()):
#         f.write(f"{node}\n")

# # --- エッジリスト作成 ---
# edges = list(G.edges())

# # --- 可視性設定（全体の10%を非可視に） ---
# invisible_ratio = 0.1
# num_invisible = max(1, int(len(edges) * invisible_ratio))  # 少なくとも1つ非可視に
# invisible_edges = set(random.sample(edges, num_invisible))

# # --- エッジ可視性出力 ---
# with open("./../../dataset/edge-base/[separate]edges_visibility.txt", "w") as f:
#     for u, v in sorted(edges):
#         visible = "×" if (u, v) in invisible_edges or (v, u) in invisible_edges else "○"
#         f.write(f"{u}-{v}　　{visible}\n")

# with open("./../../dataset/edge-base/[separate]access_control.txt", "w") as f:
#     for u, v in invisible_edges:
#         allowed_nodes = "　".join(all_nodes)  # 全ノードを全角スペース区切りで
#         f.write(f"{u}-{v}      {allowed_nodes}\n")

import string
import random

# ====== 入力・出力ファイルパス ======
input_edge_file = "./../../dataset/Louvain/graph/karate.gr"
output_node_file = "./../../dataset/edge-base/s-karate-node.txt"
output_edge_file = "./../../dataset/edge-base/s-karate-edge.txt"

# ====== 非可視(×)の割合設定 ======
x_ratio = 0.1  # 30%を非可視にする場合

# ====== ノード・エッジ読み込み ======
edges = []
nodes = set()

with open(input_edge_file, "r") as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        n1, n2 = parts[0], parts[1]
        edges.append((n1, n2))
        nodes.update([n1, n2])

# ====== nodeファイル出力 ======
with open(output_node_file, "w") as nf:
    for node in sorted(nodes):
        nf.write(f"{node}\n")

# ====== edgeファイル出力 ======
with open(output_edge_file, "w") as ef:
    all_nodes_str = ",".join(sorted(nodes))  # 全ノードを文字列に
    for n1, n2 in edges:
        # ランダムに○か×を決める
        if random.random() < x_ratio:
            # ×の場合は両端ノードを許可ノードとして記載
            # ef.write(f"{n1}-{n2} × {n1},{n2}\n")
            ef.write(f"{n1}-{n2} × {all_nodes_str}\n")
        else:
            ef.write(f"{n1}-{n2} ○\n")
