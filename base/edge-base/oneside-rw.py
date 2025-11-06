import networkx as nx
import random
import time

# ===== ファイルパス設定 =====
node_edge_file = "./../../dataset/edge-base/node_visibility.txt"
access_file = "./../../dataset/edge-base/access_control.txt"


# ===== データ構造 =====
node_visibility = {}  # node_visibility[node][edge_id] = True/False
access_rights = {}  # access_rights[edge_id] = set(allowed_start_nodes)
edge_connections = {}  # edge_connections[edge_id] = set(connected_nodes)


# ===== ファイル読み込み =====
def load_node_visibility(file_path):
    """ノードごとのエッジ可視性を読み込む"""
    visibility = {}
    current_node = None
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Node"):
                current_node = line.split()[1]
                visibility[current_node] = {}
            else:
                vis, edge_id = line.split()
                visibility[current_node][edge_id] = vis == "○"
    return visibility


def load_access_rights(file_path):
    """アクセス権制御ファイルを読み込む"""
    rights = {}
    with open(file_path, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            edge_id = parts[0]
            nodes = parts[1].split(",")
            rights[edge_id] = set(nodes)
    return rights


# ===== エッジ接続情報生成 =====
def build_edge_connections(node_visibility):
    """
    各edge_idがどのノードに現れるかをマッピングする。
    グラフ構築はせず、edge_id→node集合を作るだけ。
    """
    connections = {}
    for node, edge_dict in node_visibility.items():
        for edge_id in edge_dict:
            connections.setdefault(edge_id, set()).add(node)
    return connections


# ===== 認可チェック関数 =====
def is_authorized(current, edge_id):
    """エッジに対するアクセス認可を確認"""
    visible = node_visibility[current].get(edge_id, True)
    # print("通行可能か否か", visible)
    if visible:
        return True
    allowed_nodes = access_rights.get(edge_id, set())
    # print("allow-node", allowed_nodes)
    return current in allowed_nodes


# ===== ランダムウォーク =====
def random_walk(start_node, alpha=0.1):
    current = start_node
    path = [current]

    while random.random() > alpha:
        # 現在ノードから出ている全エッジIDを取得
        available_edges = list(node_visibility[current].keys())
        # print("av-dege", available_edges)
        # 迎えるノードがなかったら終了
        if not available_edges:
            break

        # エッジIDをランダムに1つ選択
        edge_id = random.choice(available_edges)
        # print("edge-id", edge_id)

        # 認可確認
        if not is_authorized(current, edge_id):
            continue  # 認可なしならスキップ

        # 移動先ノードを決定（edge_idで接続されているノードのうち、currentでない方）
        connected_nodes = edge_connections.get(edge_id, set())
        # print("connected-node", connected_nodes)

        # current以外を探す
        next_node = [n for n in connected_nodes if n != current][0]
        # print("next-node", next_node[0])

        # 移動
        path.append(next_node)
        current = next_node

    return path


# ===== メイン処理 =====
if __name__ == "__main__":
    node_visibility = load_node_visibility(node_edge_file)
    access_rights = load_access_rights(access_file)
    edge_connections = build_edge_connections(node_visibility)

    rw_count = 100
    start_node = "1"
    alpha = 0.1

    total_len = 0
    start_time = time.perf_counter()

    for _ in range(rw_count):
        path = random_walk(start_node, alpha)
        total_len += len(path)

    end_time = time.perf_counter()

    print(f"Average length: {total_len / rw_count:.2f}")
    print(f"Total time: {end_time - start_time}")
