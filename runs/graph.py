import networkx as nx
import matplotlib.pyplot as plt

# === 入力ファイルパス ===
EDGE_FILE = "./dataset/Louvain/graph/karate.gr"  # あなたのファイル名に変更可

# === ファイルからエッジリストを読み込む ===
edges = []
with open(EDGE_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue  # 空行スキップ
        try:
            u, v = map(int, line.split())
            edges.append((u, v))
        except ValueError:
            print(f"⚠️ 無効な行をスキップしました: {line}")

# === グラフ構築 ===
G = nx.Graph()
G.add_edges_from(edges)

# === ノード数とエッジ数を出力 ===
print(f"✅ 読み込んだノード数: {G.number_of_nodes()}, エッジ数: {G.number_of_edges()}")

# === レイアウト（ノード位置）を計算 ===
pos = nx.spring_layout(G, seed=42)  # 力学モデルレイアウト

# === グラフを描画 ===
plt.figure(figsize=(8, 6))
nx.draw_networkx_nodes(
    G, pos, node_size=600, node_color="lightblue", edgecolors="black", linewidths=1.0
)
nx.draw_networkx_edges(G, pos, width=1.5, alpha=0.7)
nx.draw_networkx_labels(G, pos, font_size=10, font_color="black")

plt.title(f"Graph Visualization from {EDGE_FILE}", fontsize=14)
plt.axis("off")
plt.tight_layout()
plt.savefig("karate.png")
plt.show()
