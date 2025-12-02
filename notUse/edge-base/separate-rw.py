import networkx as nx
import random
import time

# ===== ファイルパス =====
node_file = "./../../dataset/edge-base/s-test-node.txt"
edge_file = "./../../dataset/edge-base/s-test-edge.txt"

# ===== ノードの読み込み =====
nodes = []
with open(node_file, "r") as f:
    for line in f:
        node = line.strip()
        if node:
            nodes.append(node)

# ===== エッジ情報の読み込み =====
edges = {}  # (node1, node2): {"visible": bool, "allowed": set()}
graph = {node: [] for node in nodes}

edges = {}
graph = {node: [] for node in nodes}

with open(edge_file, "r") as f:
    for line in f:
        parts = line.split()
        if len(parts) < 2:
            continue

        n1, n2 = parts[0].split("-")
        visible = parts[1] == "○"
        allowed_nodes = set(parts[2].split(",")) if len(parts) > 2 else set()

        # ソートコスト削減のため、手動で順序統一
        key = (n1, n2) if n1 < n2 else (n2, n1)
        edges[key] = {"visible": visible, "allowed": allowed_nodes}

        # グラフ登録（無向）
        graph[n1].append(n2)
        graph[n2].append(n1)


# ===== 認可判定関数 =====
def is_authorized(current_node, next_node):

    e = edges.get((current_node, next_node))
    if e is None:
        return False
    if e["visible"]:
        return True  # 公開エッジ
    # 非公開なら認可要否チェック
    if current_node in e["allowed"]:
        return True
    return False


# ===== ランダムウォーク =====
def random_walk(start_node, alpha=0.1):
    current = start_node
    path = [current]
    # print(f"=== Start random walk from {start_node} ===")

    while random.random() > alpha:
        # 現在ノードを含むエッジを抽出
        connected_edges = [
            (u, v) for (u, v) in edges.keys() if u == current or v == current
        ]
        # print("connected-edges:", connected_edges)
        if not connected_edges:
            # print(f"No edges connected to {current}")
            break

        moved = False
        remaining_edges = connected_edges.copy()

        # 候補が残っている限り試す
        while remaining_edges:
            next_edge = random.choice(remaining_edges)
            # print(next_edge)
            remaining_edges.remove(next_edge)

            u, v = next_edge
            edge_info = edges[next_edge]

            # 現在ノードから見た次ノードを決定
            next_node = v if u == current else u
            # print("next-node", next_node)

            # 認可判定
            if edge_info["visible"]:
                authorized = True
            else:
                authorized = current in edge_info["allowed"]

            # print(
            #     f"Check edge {u}-{v}, next={next_node}, visible={edge_info['visible']}, allowed={edge_info['allowed']}, authorized={authorized}"
            # )

            if authorized:
                # 認可成功 → 移動
                path.append(next_node)
                # print(f"→ Move: {current} → {next_node}")
                current = next_node
                moved = True
                break  # 認可成功したのでこのステップ終了

        # 候補を全て試しても通れなかった場合
        if not moved:
            # print(f"No authorized edges from {current}, stop.")
            break

    # print(f"=== End walk. Path: {path} ===\n")
    return path


# ===== 実行 =====
alpha = 0.1
start_node = "1"
rw_count = 100
total_length = 0

start_time = time.perf_counter()
for _ in range(rw_count):
    path = random_walk(start_node, alpha)
    total_length += len(path)
end_time = time.perf_counter()

print(f"Average length: {total_length / rw_count}")
print(f"Total time: {end_time - start_time}")
