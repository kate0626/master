#!/usr/bin/env python3
"""generate_auth_from_edges.py with NG ratio option
Usage:
python3 scripts/generate_auth_from_edges.py edges.txt -o auth_by_start.json --ng-ratio 0.2

python3 base/auth-many-server/create_json_table.py ./dataset/Louvain/graph/karate.gr -o auth_by_start.json --ng-ratio 0.3
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple


def make_edge_id(u: int, v: int) -> str:
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def read_edge_list(path: Path) -> List[Tuple[int, int]]:
    edges = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            u, v = int(parts[0]), int(parts[1])
            edges.append((u, v))
    return edges


# 隣接ノードを全て入れる時を１とした場合のコード
# def build_auth_table1(
#     edges: List[Tuple[int, int]], ng_ratio: float = 0.0
# ) -> Dict[str, Dict[str, List]]:
#     """
#     Build mapping: start_node_int -> {"n": [nodes], "e": [edge_ids]}
#     ng_ratio: probability to exclude a neighbor/edge (NG)
#     """
#     by_start = defaultdict(lambda: {"n": set(), "e": set()})
#     all_nodes: Set[int] = set()

#     for u, v in edges:
#         all_nodes.add(u)
#         all_nodes.add(v)
#         edge_id = make_edge_id(u, v)

#         for start, neighbor in ((u, v), (v, u)):
#             entry = by_start[start]

#             # decide if neighbor is NG
#             if random.random() >= ng_ratio:
#                 entry["n"].add(neighbor)
#             # start node itself is always included
#             entry["n"].add(start)

#             # decide if edge is NG
#             if random.random() >= ng_ratio:
#                 entry["e"].add(edge_id)

#     # guarantee each node has at least itself registered (even if isolated)
#     for node in all_nodes:
#         by_start[node]["n"].add(node)

#     out = {}
#     for start, sets in by_start.items():
#         ns = sorted(int(x) for x in sets["n"])
#         es = sorted(str(x) for x in sets["e"])
#         out[str(start)] = {"n": ns, "e": es}
#     return out


"""
    隣接関係なく許可不許可を決めるのは、始点ノード
    つまり、始点ノードからた時には全てのノードへのアクセスが許可されている状態を１とする
"""


def build_auth_table2(
    edges: List[Tuple[int, int]], ng_ratio: float = 0.0
) -> Dict[str, Dict[str, List]]:
    """
    Build mapping: start_node_int -> {"n": [nodes], "e": [edge_ids]}
    - ng_ratio=0.0 → 自分以外の全ノード・全エッジを許可
    - ng_ratio>0.0 → ランダムに一部除外
    """
    by_start = defaultdict(lambda: {"n": set(), "e": set()})
    all_nodes: Set[int] = set()
    all_edges: Set[str] = set()
    incident_edges: Dict[int, List[str]] = defaultdict(list)
    edge_endpoints: Dict[str, Tuple[int, int]] = {}

    # 全ノード・エッジ集合を作る
    for u, v in edges:
        all_nodes.update([u, v])
        edge_id = make_edge_id(u, v)
        all_edges.add(edge_id)
        incident_edges[u].append(edge_id)
        incident_edges[v].append(edge_id)
        edge_endpoints[edge_id] = (u, v)

    # 各ノードについて許可リストを作成
    for start in all_nodes:
        # --- ノード認可リスト ---
        for node in all_nodes:
            if node == start:
                continue
            if random.random() >= ng_ratio:
                by_start[start]["n"].add(node)
        # --- エッジ認可リスト ---
        for edge_id in all_edges:
            if random.random() >= ng_ratio:
                by_start[start]["e"].add(edge_id)
        # 自分自身は常に含める
        by_start[start]["n"].add(start)

    # --- 許可集合を補正して必ず遷移先が残るようにする ---
    for start in all_nodes:
        nodes_set = by_start[start]["n"]
        edges_set = by_start[start]["e"]

        # ノードごとに少なくとも1つの incident edge を許可
        for node in list(nodes_set):
            incident = incident_edges.get(node)
            if not incident:
                continue
            if not any(edge in edges_set for edge in incident):
                edges_set.add(random.choice(incident))

        # 許可されたエッジの端点は必ず許可ノードに含める
        for edge_id in list(edges_set):
            u, v = edge_endpoints[edge_id]
            nodes_set.add(u)
            nodes_set.add(v)

    # 出力整形
    out = {}
    for start, sets in by_start.items():
        ns = sorted(int(x) for x in sets["n"])
        es = sorted(str(x) for x in sets["e"])
        out[str(start)] = {"n": ns, "e": es}
    return out


def build_node_to_starts(
    auth_table: Dict[str, Dict[str, List]],
) -> Dict[str, List[int]]:
    mapping: Dict[str, Set[int]] = defaultdict(set)
    for start_str, entries in auth_table.items():
        try:
            start_node = int(start_str)
        except Exception:
            continue
        for node in entries.get("n", []):
            mapping[str(node)].add(start_node)
        for edge_id in entries.get("e", []):
            mapping[str(edge_id)].add(start_node)
    return {node: sorted(starts) for node, starts in mapping.items()}


def main():
    p = argparse.ArgumentParser(
        description="Generate auth_by_start.json from edge list grouped by start node, with NG ratio."
    )
    p.add_argument("edges", type=str, help="Path to edge list file (u v per line).")
    p.add_argument(
        "-o",
        "--out",
        type=str,
        default="auth_by_start.json",
        help="Output JSON path.",
    )
    p.add_argument(
        "--ng-ratio",
        type=float,
        default=0.0,
        help="Ratio of neighbors/edges to exclude (0.0–1.0).",
    )
    p.add_argument(
        "--emit-auth-table",
        action="store_true",
        help="Also write auth_by_start.json (not needed for current remote_server).",
    )
    args = p.parse_args()

    if not 0.0 <= args.ng_ratio <= 1.0:
        raise SystemExit("ng-ratio must be between 0.0 and 1.0")

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise SystemExit(f"Edge list not found: {edge_path}")

    edges = read_edge_list(edge_path)
    auth = build_auth_table2(edges, ng_ratio=args.ng_ratio)
    node_to_starts = build_node_to_starts(auth)

    # ★ 固定出力先フォルダ（プロジェクトルートからの相対パス）
    out_dir = Path("./base/auth-many-server/")

    # out_path = Path(args.out)
    if args.emit_auth_table:
        out_path = out_dir / "auth_by_start.json"
        out_path.write_text(
            json.dumps(auth, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        out_path = None
    node_to_starts_path = out_dir / "node_to_starts.json"
    node_to_starts_path.write_text(
        json.dumps(node_to_starts, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if out_path is not None:
        print(
            f"Wrote {out_path} and {node_to_starts_path} with {len(auth)} start entries (NG ratio={args.ng_ratio})."
        )
    else:
        print(
            f"Wrote {node_to_starts_path} with {len(auth)} start entries (NG ratio={args.ng_ratio})."
        )


if __name__ == "__main__":
    main()
