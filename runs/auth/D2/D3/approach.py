#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Dict, List, Tuple, Any


"""
    半径からのカバー率を求める
    RWの始点からの半径において、どの程度カバーしているのかを求める→カバー率が高ければ、その半径の中はより予測の的中可能性が高くなるということ
"""


# ===============================
# ここを直接編集する
# ===============================

GRAPH_NAME = "karate"  # "karate", "dolphins", "polbooks", "amazon0601"
EDGE_FILE = "./../../../../dataset/Louvain/graph/" + GRAPH_NAME + ".gr"
# LOG_FILE = "./../1-approach/0.3/karate/*per_walk_access.json"

LOG_PATTERN = "*per_walk_access.json"
LOG_DIR = Path("./../1-approach/0.3/" + GRAPH_NAME)

LOG_FILE = list(LOG_DIR.glob(LOG_PATTERN))[0]

START_NODE = 0
RMAX = 4
# ===============================


def load_edge_list(path: Path) -> List[Tuple[int, int]]:
    edges = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            u, v = map(int, s.split())
            edges.append((u, v))
    return edges


def build_adj(edges: List[Tuple[int, int]]) -> Dict[int, List[int]]:
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    return adj


def bfs_upto_r(adj: Dict[int, List[int]], start: int, rmax: int) -> Dict[int, int]:
    dist = {start: 0}
    q = deque([start])
    while q:
        x = q.popleft()
        dx = dist[x]
        if dx >= rmax:
            continue
        for y in adj.get(x, []):
            if y not in dist:
                dist[y] = dx + 1
                q.append(y)
    return dist


def is_edge(ent: Any) -> bool:
    return isinstance(ent, str) and ent.startswith("edge_")


def parse_edge_id(ent: str) -> Tuple[int, int]:
    _, a, b = ent.split("_")
    return int(a), int(b)


def cover_from_access(access: Dict[str, int], dist: Dict[int, int], r: int):
    node_in = node_total = 0
    edge_in = edge_total = 0
    INF = 10**9

    for ent, c in access.items():
        c = int(c)

        if is_edge(ent):
            u, v = parse_edge_id(ent)
            du = dist.get(u, INF)
            dv = dist.get(v, INF)
            de = min(du, dv)
            edge_total += c
            if de <= r:
                edge_in += c
        else:
            try:
                n = int(ent)
            except Exception:
                continue
            dn = dist.get(n, INF)
            node_total += c
            if dn <= r:
                node_in += c

    cov_v = node_in / node_total if node_total else 0.0
    cov_e = edge_in / edge_total if edge_total else 0.0
    return cov_v, cov_e


def main():
    print("=== Radius Cover Computation ===")
    print(f"Edges file : {EDGE_FILE}")
    print(f"Log file   : {LOG_FILE}")
    print(f"Start node : {START_NODE}")
    print(f"RMAX       : {RMAX}")
    print()

    edges = load_edge_list(Path(EDGE_FILE))
    adj = build_adj(edges)

    print("Running local BFS...")
    dist = bfs_upto_r(adj, START_NODE, RMAX)

    data = json.loads(Path(LOG_FILE).read_text(encoding="utf-8"))
    per_walk = data["per_walk_access"]

    agg = Counter()
    for w in per_walk:
        agg.update({str(k): int(v) for k, v in w.get("access", {}).items()})

    print("=== Aggregated Cover(r) ===")
    for r in range(RMAX + 1):
        cov_v, cov_e = cover_from_access(agg, dist, r)
        print(f"r={r}: Cover_V={cov_v:.4f}  Cover_E={cov_e:.4f}")


if __name__ == "__main__":
    main()
