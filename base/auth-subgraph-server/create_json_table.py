#!/usr/bin/env python3
"""
auth-many-serverのものと全く同じ
generate_auth_from_edges.py with NG ratio option
Usage:

NG比率も一緒に更新する場合
python3 base/auth-subgraph-server/create_json_table.py ./dataset/Louvain/graph/karate.gr --ng-ratio 0.0 --subgraph-out base/auth-subgraph-server/subgraph_index.json --subgraph-size 2 --seed 1


グループ分けのみ作る場合
python3 base/auth-subgraph-server/create_json_table.py \
    dataset/Louvain/graph/karate.gr \
    --ng-ratio 0.0 \
    --subgraph-out base/auth-subgraph-server/subgraph_index.json \
    --subgraph-size 5 \
    --seed 202

"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple, Optional, Union

NodeOrEdgeId = Union[int, str]


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


def build_connected_groups(
    edges: List[Tuple[int, int]], max_entities: int, seed: Optional[int] = None
) -> List[Set[NodeOrEdgeId]]:
    if max_entities <= 0:
        raise ValueError("subgraph size must be positive")

    rng = random.Random(seed)
    adjacency: Dict[NodeOrEdgeId, Set[NodeOrEdgeId]] = defaultdict(set)
    nodes: Set[int] = set()

    for u, v in edges:
        nodes.update([u, v])
        edge_id = make_edge_id(u, v)
        adjacency[u].add(edge_id)
        adjacency[v].add(edge_id)
        adjacency[edge_id].update([u, v])

    for node in nodes:
        adjacency.setdefault(node, set())

    unassigned: Set[NodeOrEdgeId] = set(adjacency.keys())
    groups: List[Set[NodeOrEdgeId]] = []

    while unassigned:
        start = rng.choice(tuple(unassigned))
        current_group: Set[NodeOrEdgeId] = set()
        queue: deque[NodeOrEdgeId] = deque([start])

        while queue and len(current_group) < max_entities:
            entity = queue.popleft()
            if entity not in unassigned:
                continue
            current_group.add(entity)
            unassigned.remove(entity)
            for nb in adjacency.get(entity, []):
                if nb in unassigned and len(current_group) < max_entities:
                    queue.append(nb)

        groups.append(current_group)

    return groups


def build_subgraph_index(
    edges: List[Tuple[int, int]], groups: List[Set[NodeOrEdgeId]]
) -> Dict[str, object]:
    node_to_group: Dict[str, int] = {}
    group_entries: List[Dict[str, object]] = []

    for gid, members in enumerate(groups):
        node_list = sorted(int(x) for x in members if isinstance(x, int))
        edge_list = sorted(str(x) for x in members if isinstance(x, str))
        for node in node_list:
            node_to_group[str(node)] = gid
        for edge_id in edge_list:
            node_to_group[edge_id] = gid
        group_entries.append({"id": gid, "nodes": node_list, "edges": edge_list})

    # カバーされなかったノード/エッジがあれば単独グループを作成
    covered = set(node_to_group.keys())
    for u, v in edges:
        for entity in (str(u), str(v), make_edge_id(u, v)):
            if entity not in covered:
                gid = len(group_entries)
                if entity.startswith("edge_"):
                    group_entries.append({"id": gid, "nodes": [], "edges": [entity]})
                else:
                    group_entries.append(
                        {"id": gid, "nodes": [int(entity)], "edges": []}
                    )
                node_to_group[entity] = gid
                covered.add(entity)

    return {"node_to_group": node_to_group, "groups": group_entries}


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
    p.add_argument(
        "--subgraph-out",
        type=str,
        default=None,
        help="Optional JSON path to write node/edge group definition.",
    )
    p.add_argument(
        "--subgraph-size",
        type=int,
        default=5,
        help="Maximum number of entities (nodes + edges) per group.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=2024,
        help="Random seed for NG selection and group generation.",
    )
    p.add_argument(
        "--node-to-starts-out",
        type=str,
        default=None,
        help="Optional JSON path to write node_to_starts. If omitted, no file is written.",
    )
    args = p.parse_args()

    if not 0.0 <= args.ng_ratio <= 1.0:
        raise SystemExit("ng-ratio must be between 0.0 and 1.0")

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise SystemExit(f"Edge list not found: {edge_path}")

    if args.seed is not None:
        random.seed(args.seed)

    edges = read_edge_list(edge_path)
    auth = build_auth_table2(edges, ng_ratio=args.ng_ratio)

    # ★ 固定出力先フォルダ（プロジェクトルートからの相対パス）
    out_dir = Path("./base/auth-subgraph-server/")

    # out_path = Path(args.out)
    if args.emit_auth_table:
        out_path = out_dir / "auth_by_start.json"
        out_path.write_text(
            json.dumps(auth, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        out_path = None

    if args.node_to_starts_out:
        node_to_starts = build_node_to_starts(auth)
        node_to_starts_path = Path(args.node_to_starts_out)
        node_to_starts_path.write_text(
            json.dumps(node_to_starts, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        node_to_starts_path = None
    if args.subgraph_out:
        groups = build_connected_groups(edges, args.subgraph_size, seed=args.seed)
        subgraph_index = build_subgraph_index(edges, groups)
        subgraph_path = Path(args.subgraph_out)
        subgraph_path.write_text(
            json.dumps(subgraph_index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        subgraph_path = None

    summary_parts = []
    if node_to_starts_path is not None:
        summary_parts.append(str(node_to_starts_path))
    if out_path is not None:
        summary_parts.append(str(out_path))
    if subgraph_path is not None:
        summary_parts.append(str(subgraph_path))
    if summary_parts:
        summary = "Wrote " + ", ".join(summary_parts)
    else:
        summary = "No JSON files emitted"
    summary += f" with {len(auth)} start entries (NG ratio={args.ng_ratio})."
    print(summary)


if __name__ == "__main__":
    main()
