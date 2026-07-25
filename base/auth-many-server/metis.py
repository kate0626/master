"""
エッジリスト (.gr) から METIS 用の 2部グラフ (node <-> edge_vertex <-> node) を生成する。

出力した .metis ファイルを `gpmetis <file> K` に掛けると <file>.part.K が得られ、
それを only_split_auth_tables.py / split_auth_tables.py の --partitioner-type metis に
渡すことで node_to_starts を K サーバへ分割できる。

エッジ頂点の採番規約 (重要):
  metis 頂点ID = max_node(node_shift 適用後) + idx + 1   (idx = エッジのファイル出現順, 0始まり)
  → build_edge_metis_map (split 側) と必ず一致させること。
  → node_shift はここと split 側の --metis-node-shift を一致させる (0始まりグラフなら 1)。

使い方:
  # 単体 (従来どおり GRAPH_NAME をコード内で決める使い方も可)
  python3 base/auth-many-server/metis.py \
      --graph karate \
      --out  base/auth-many-server/karate/bipartite.metis \
      --node-shift 1
  gpmetis base/auth-many-server/karate/bipartite.metis 3
  # → base/auth-many-server/karate/bipartite.metis.part.3

  通常は partition_metis.sh が上記 3 ステップ (bipartite → gpmetis → split) をまとめて実行する。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from collections import defaultdict


def build_bipartite_metis(edge_path: Path, out_path: Path, node_shift: int = 1) -> tuple[int, int, int]:
    edges = []
    with edge_path.open() as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            u, v = map(int, s.split())
            edges.append((u + node_shift, v + node_shift))

    if not edges:
        raise SystemExit(f"no edges found in {edge_path}")

    max_node = max(max(u, v) for u, v in edges)
    adj = defaultdict(set)
    for idx, (u, v) in enumerate(edges):
        e_id = max_node + idx + 1  # エッジ頂点ID (split 側と一致させること)
        adj[u].add(e_id)
        adj[v].add(e_id)
        adj[e_id].add(u)
        adj[e_id].add(v)

    total_v = max_node + len(edges)
    m = sum(len(nbs) for nbs in adj.values()) // 2
    lines = [f"{total_v} {m}"]
    for vid in range(1, total_v + 1):
        lines.append(" ".join(str(x) for x in sorted(adj.get(vid, []))))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    return total_v, m, len(edges)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a bipartite (node<->edge) METIS graph from an edge list."
    )
    # 従来の GRAPH_NAME ハードコード運用も壊さないよう、--graph 未指定時のデフォルトを残す
    p.add_argument(
        "--graph",
        type=str,
        default="fb-pages-food",
        help="Graph name. Uses dataset/Louvain/graph/<graph>.gr when --edges is omitted.",
    )
    p.add_argument(
        "--edges",
        type=str,
        default=None,
        help="Explicit edge list path. Overrides --graph.",
    )
    p.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output .metis path. Default: base/auth-many-server/<graph>/bipartite.metis",
    )
    p.add_argument(
        "--node-shift",
        type=int,
        default=1,
        help="Shift added to node ids (1 if the graph is 0-based, 0 if already 1-based). "
        "Must match --metis-node-shift on the split side.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    edge_path = Path(args.edges) if args.edges else Path(f"dataset/Louvain/graph/{args.graph}.gr")
    if not edge_path.exists():
        raise SystemExit(f"edge file not found: {edge_path}")
    out_path = Path(args.out) if args.out else Path(f"base/auth-many-server/{args.graph}/bipartite.metis")

    total_v, m, n_edges = build_bipartite_metis(edge_path, out_path, node_shift=args.node_shift)
    print(
        f"wrote {out_path} (V={total_v}, E={m}, n_edges={n_edges}, node_shift={args.node_shift})"
    )


if __name__ == "__main__":
    main()
