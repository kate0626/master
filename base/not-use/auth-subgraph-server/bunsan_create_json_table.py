"""
- 新規：
    - 既存のNG比率＋サーバ番号が決まっているファイルを使用する
        - サブグラフ分割をサーバごとに行いたいため

    python3 base/auth-subgraph-server/bunsan_create_json_table.py \
    --edges dataset/Louvain/graph/test.gr \
    --node-to-starts base/auth-many-server/test/node_to_starts_server0.json \
    --group-size 10 \
    --seed 1

    python3 base/auth-subgraph-server/bunsan_create_json_table.py \
    --edges dataset/Louvain/graph/test.gr \
    --node-to-starts base/auth-many-server/test/node_to_starts_server1.json \
    --group-size 10 \
    --seed 1

"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Set, Tuple, Union, Optional
import re

NodeOrEdgeId = Union[int, str]


def make_edge_id(u: int, v: int) -> str:

    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def read_edge_list(path: Path) -> List[Tuple[int, int]]:
    edges: List[Tuple[int, int]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            edges.append((int(parts[0]), int(parts[1])))
    return edges


def parse_entity_id(raw: str) -> NodeOrEdgeId:
    # node_to_starts のキーは "1" や "edge_1_3" の文字列
    if raw.startswith("edge_"):
        return raw
    try:
        return int(raw)
    except Exception:
        return raw


def load_node_to_starts_keys(path: Path) -> Set[NodeOrEdgeId]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {parse_entity_id(k) for k in raw.keys()}


def build_adjacency_bipartite(
    edges: List[Tuple[int, int]],
) -> Dict[NodeOrEdgeId, Set[NodeOrEdgeId]]:
    """
    node ↔ edge の二部展開の隣接を作る
    node u  -- edge_u_v -- node v
    """
    adj: Dict[NodeOrEdgeId, Set[NodeOrEdgeId]] = defaultdict(set)
    for u, v in edges:
        e = make_edge_id(u, v)
        adj[u].add(e)
        adj[v].add(e)
        adj[e].add(u)
        adj[e].add(v)
    return adj


def build_connected_groups_restricted(
    adjacency: Dict[NodeOrEdgeId, Set[NodeOrEdgeId]],
    allowed_entities: Set[NodeOrEdgeId],
    max_entities: int,
    seed: Optional[int] = None,
) -> List[Set[NodeOrEdgeId]]:
    """
    build_connected_groups() の“機能だけ”を真似。
    ただし探索対象は allowed_entities の中だけに制限する。
    """
    if max_entities <= 0:
        raise ValueError("group size must be positive")

    rng = random.Random(seed)

    # allowed だけを unassigned にする（ここが“サーバ内に閉じる”コア）
    unassigned: Set[NodeOrEdgeId] = set(allowed_entities)
    groups: List[Set[NodeOrEdgeId]] = []

    while unassigned:
        start = rng.choice(tuple(unassigned))
        group: Set[NodeOrEdgeId] = set()
        q: deque[NodeOrEdgeId] = deque([start])

        while q and len(group) < max_entities:
            x = q.popleft()
            if x not in unassigned:
                continue
            group.add(x)
            unassigned.remove(x)

            for nb in adjacency.get(x, set()):
                # ★ ここが重要：allowedの中の隣接だけキューに入れる
                if nb in unassigned and len(group) < max_entities:
                    q.append(nb)

        groups.append(group)

    return groups


def build_subgraph_index_from_groups(
    groups: List[Set[NodeOrEdgeId]],
) -> Dict[str, object]:
    node_to_group: Dict[str, int] = {}
    group_entries: List[Dict[str, object]] = []

    for gid, members in enumerate(groups):
        node_list = sorted(int(x) for x in members if isinstance(x, int))
        edge_list = sorted(str(x) for x in members if isinstance(x, str))
        for n in node_list:
            node_to_group[str(n)] = gid
        for e in edge_list:
            node_to_group[e] = gid
        group_entries.append({"id": gid, "nodes": node_list, "edges": edge_list})

    return {"node_to_group": node_to_group, "groups": group_entries}


def extract_server_id(path: Path) -> int:
    """
    node_to_starts_server0.json
    node_to_starts0.json
    から server_id = 0 を取り出す
    """
    m = re.search(r"server(\d+)", path.stem)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)$", path.stem)
    if m:
        return int(m.group(1))
    raise ValueError(f"Cannot extract server_id from filename: {path.name}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Make subgraph_index per server from server-specific node_to_starts*.json (no cross-server groups)."
    )
    ap.add_argument("--edges", required=True, help="Edge list .gr path (u v per line)")
    ap.add_argument(
        "--node-to-starts",
        required=True,
        help="Path to node_to_starts_serverX.json (or node_to_startsX.json)",
    )
    # ap.add_argument("--out", required=True, help="Output JSON path for subgraph_index")
    ap.add_argument(
        "--group-size",
        type=int,
        default=10,
        help="Max entities (nodes+edges) per group",
    )
    ap.add_argument("--seed", type=int, default=2024, help="Random seed for grouping")
    args = ap.parse_args()

    edge_path = Path(args.edges).expanduser()
    nts_path = Path(args.node_to_starts).expanduser()

    edges = read_edge_list(edge_path)
    allowed = load_node_to_starts_keys(nts_path)
    adjacency = build_adjacency_bipartite(edges)

    # ★ 自動命名ここから
    graph_name = edge_path.stem  # e.g. test
    print(graph_name)
    server_id = extract_server_id(nts_path)  # e.g. 0
    group_size = args.group_size

    out_dir = Path("base/auth-subgraph-server")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = (
        out_dir
        / f"{graph_name}"
        / "bi"
        / f"subgraph_server{server_id}_size{group_size}.json"
    )
    # ★ 自動命名ここまで

    groups = build_connected_groups_restricted(
        adjacency=adjacency,
        allowed_entities=allowed,
        max_entities=args.group_size,
        seed=args.seed,
    )
    subgraph_index = build_subgraph_index_from_groups(groups)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(subgraph_index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Wrote {out_path} (entities={len(allowed)}, groups={len(groups)}, group_size<={args.group_size})"
    )


if __name__ == "__main__":
    main()
