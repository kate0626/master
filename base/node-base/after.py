import networkx as nx
import random
import time

# ここから引用したので、元Verは以下のURlから参照
# https://github.com/Maikooon/school_lab-b/blob/main/test/test/RW/main.py
# エッジリストファイルとコミュニティファイルのパス
edges_file = "./../dataset/Louvain/graph/karate.gr"
property_file = "./../dataset/node-base/karate.txt"
# communities_file = "a.tcm"

# グラフの定義
G = nx.Graph()
# 終了確率の設定
alpha = 0.1
# 経路朝を示す
total = 0
# スタートノードを設定
start_node = 1
# RWの実行回数
rw_count = 100

# エッジリストの読み込み
with open(edges_file, "r") as f:
    for line in f:
        if line.strip():  # 空行でないことを確認
            edge_data = line.strip().split()
            if len(edge_data) == 2:
                node1, node2 = map(int, edge_data)
                G.add_edge(node1, node2)
            else:
                raise ValueError(
                    "Each line in the edges file should contain two integers representing an edge."
                )
# 属性ありリストの読み込み
private_nodes = set()

# ファイルを開いて読み込み
with open(property_file, "r") as f:
    for line in f:
        if line.strip():
            node_id_str, label = line.strip().split()
            node_id = int(node_id_str)
            if label == "Private":
                private_nodes.add(node_id)
# print(private_nodes)


def random_walk(graph, start_node, alpha):
    current_node = start_node
    path = [current_node]

    # 終了確率よりも大きかったら継続
    while random.random() > alpha:
        # 隣接ノードを取得
        neighbors = list(graph.neighbors(current_node))
        # 隣接ノードから行き先をランダムに指定
        next_node = random.choice(neighbors)
        # 選択したノードが害がないことを確かめる
        if next_node in private_nodes:
            # Privateノードを引いた場合 → スキップ or 再抽選
            # ここでは再抽選する方式にします
            while True:
                safe_neighbors = [n for n in neighbors if n not in private_nodes]
                if not safe_neighbors:
                    # 全部Privateなら終了
                    break
                next_node = random.choice(safe_neighbors)
                if next_node in private_nodes:
                    continue
                else:
                    break
        # 指定したら、行き先をPathに追加
        path.append(next_node)
        current_node = next_node
    return path


start_time = time.perf_counter()
# 実行によるブレをなくすために、複数回同じ始点で実験
for i in range(start_node, rw_count):
    # ランダムウォークの実行
    path = random_walk(G, start_node, alpha)
    length = len(path)
    # print(f"Random walk path: {path}")
    total += length
end_time = time.perf_counter()
total_time = end_time - start_time

# 平均経路朝が終了確率の逆数なら成功
ave = total / rw_count
print(f"Average length: {ave}")
print(f"Total length: {total}")
print(f"Total time: {total_time}")
