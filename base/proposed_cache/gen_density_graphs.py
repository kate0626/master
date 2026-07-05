#!/usr/bin/env python3
"""粗密の異なる合成グラフを生成して dataset/Louvain/graph/ に .gr で出力する。

設計:
  - ノード数 N は固定。平均次数 <k> だけを変えて「密度」を振る。
  - BA(scale-free) と ER(一様次数) を同じ <k> で対にして出すことで、
    「密度」と「次数分布の裾(scale-free性)」を分離して観察できる。
  - 最大連結成分のみ残し、0始まりに振り直す(始点ノード0を必ず含めるため)。

出力例:
  ba_sparse.gr ba_mid.gr ba_dense.gr  er_sparse.gr er_mid.gr er_dense.gr

使い方:
  cd /Users/maiko/Documents/GitHub/master-progrem
  python3 base/proposed_cache/gen_density_graphs.py
"""
import networkx as nx
from pathlib import Path

N = 5000          # ノード数は固定(密度以外を交絡させない)
SEED = 42
OUT = Path("dataset/Louvain/graph")

# 目標平均次数 <k> = 2M/N。粗/中/密の3水準。
TARGET_K = {"sparse": 4, "mid": 10, "dense": 30}


def build(model: str, k: int) -> nx.Graph:
    if model == "ba":
        # BA: 新規ノードの接続数 m。<k> ≈ 2m なので m = k/2。
        m = max(1, round(k / 2))
        G = nx.barabasi_albert_graph(N, m, seed=SEED)
    elif model == "er":
        # ER: p = <k> / (N-1)
        p = k / (N - 1)
        G = nx.gnp_random_graph(N, p, seed=SEED)
    else:
        raise ValueError(model)
    # 最大連結成分のみ → 0始まりに再ラベル
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    G = nx.convert_node_labels_to_integers(G, first_label=0)
    return G


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"{'name':14} {'N':>6} {'M':>8} {'<k>':>6}")
    for model in ("ba", "er"):
        for level, k in TARGET_K.items():
            name = f"{model}_{level}"
            G = build(model, k)
            n, m = G.number_of_nodes(), G.number_of_edges()
            path = OUT / f"{name}.gr"
            with path.open("w") as f:
                for u, v in G.edges():
                    f.write(f"{u}\t{v}\n")
            print(f"{name:14} {n:>6} {m:>8} {2*m/n:>6.1f}  -> {path}")


if __name__ == "__main__":
    main()
