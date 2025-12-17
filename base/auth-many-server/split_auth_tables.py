#!/usr/bin/env python3
"""
auth_by_start.json と node_to_starts.json をサーバIDごとに事前分割するツール。
どちらか片方だけの分割でも利用可能（node_to_starts のみ等）。

例:
  # node_to_starts だけ分割する例
  python3 base/auth-many-server/split_auth_tables.py \
      --node-to-starts-file base/auth-many-server/node_to_starts.json \
      --server-count 3 \
      --out-dir base/auth-many-server/splits


  # METIS分割を使う例（graph.part.3 は 1列形式でパートIDを並べたファイル想定）
  python3 base/auth-many-server/split_auth_tables.py \
      --node-to-starts-file base/auth-many-server/node_to_starts.json \
      --server-count 2 \
      --partitioner-type metis \
      --metis-partition-file dataset/Louvain/community/graph.part.3 \
      --out-dir base/auth-many-server/splits

出力（out-dir配下）:
  auth_by_start_server0.json, node_to_starts_server0.json
  auth_by_start_server1.json, node_to_starts_server1.json
  auth_by_start_server2.json, node_to_starts_server2.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from collections import defaultdict

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]


class ModuloPartitioner:
    """remote_server.py と同じルールで所有サーバを決める。"""

    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign_node(self, node_id: int) -> int:
        return node_id % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        a, b = sorted((u, v))
        return (a * 1_000_003 + b) % self.server_count

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            _, raw_u, raw_v = entity_id.split("_", 2)
            return self.assign_edge(int(raw_u), int(raw_v))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


class CommunityPartitioner:
    """
    Louvain などの community ファイルを使って community % server_count で割当。
    端点が同一サーバならエッジも同一サーバ、それ以外はハッシュで分散。
    """

    def __init__(self, server_count: int, community_map: Dict[int, int]) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count
        self.community_map = community_map
        self._modulo = ModuloPartitioner(server_count)

    def assign_node(self, node_id: int) -> int:
        com = self.community_map.get(node_id)
        if com is None:
            return self._modulo.assign_node(node_id)
        return com % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        owner_u = self.assign_node(u)
        owner_v = self.assign_node(v)
        if owner_u == owner_v:
            return owner_u
        return self._modulo.assign_edge(u, v)

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            _, raw_u, raw_v = entity_id.split("_", 2)
            return self.assign_edge(int(raw_u), int(raw_v))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


class MetisPartitioner:
    """
    METIS の part ID をサーバIDとして利用（part が server_count 以上なら modulo）。
    端点が同じならエッジも同じサーバ、それ以外はハッシュで分散。
    """

    def __init__(
        self,
        server_count: int,
        part_map: Dict[int, int],
        edge_metis_map: Optional[Dict[str, int]] = None,
    ) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count
        self.part_map = part_map
        self.edge_metis_map = edge_metis_map or {}
        self._modulo = ModuloPartitioner(server_count)

    def assign_node(self, node_id: int) -> int:
        part = self.part_map.get(node_id)
        if part is None:
            return self._modulo.assign_node(node_id)
        return part % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        owner_u = self.assign_node(u)
        owner_v = self.assign_node(v)
        if owner_u == owner_v:
            return owner_u
        return self._modulo.assign_edge(u, v)

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            metis_id = self.edge_metis_map.get(entity_id)
            if metis_id is not None:
                part = self.part_map.get(metis_id)
                if part is not None:
                    return part % self.server_count
            _, raw_u, raw_v = entity_id.split("_", 2)
            return self.assign_edge(int(raw_u), int(raw_v))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


def load_auth_table(path: Optional[Union[str, Path]]) -> Dict[int, Dict[str, Set[Any]]]:
    if path is None:
        return {}
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[int, Dict[str, Set[Any]]] = {}
    for k, v in raw.items():
        try:
            start = int(k)
        except Exception:
            continue
        nodes = set(int(x) for x in v.get("n", []) if x is not None)
        edges = set(str(x) for x in v.get("e", []) if x is not None)
        out[start] = {"n": nodes, "e": edges}
    return out


def load_node_to_starts(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
    if path is None:
        return {}
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[NodeOrEdgeId, Set[int]] = {}
    for k, values in raw.items():
        try:
            entity_key: NodeOrEdgeId = int(k)
        except Exception:
            entity_key = str(k)
        starts = set()
        for v in values:
            try:
                starts.add(int(v))
            except Exception:
                continue
        out[entity_key] = starts
    return out


def load_community_map(path: Optional[Union[str, Path]]) -> Dict[int, int]:
    if path is None:
        return {}
    p = Path(path)
    raw: Dict[int, int] = {}
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            try:
                node = int(parts[0])
                com = int(parts[1])
            except Exception:
                continue
            raw[node] = com
    return raw


def load_metis_partition(
    path: Optional[Union[str, Path]], base_index: int = 0
) -> Dict[int, int]:
    if path is None:
        return {}
    p = Path(path)
    mapping: Dict[int, int] = {}
    with p.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) == 1:
                try:
                    part = int(parts[0])
                except Exception:
                    continue
                node_id = idx + base_index
            else:
                try:
                    node_id = int(parts[0])
                    part = int(parts[1])
                except Exception:
                    continue
            mapping[node_id] = part
    return mapping


def load_edge_list(path: Path) -> List[Tuple[int, int]]:
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


def build_edge_metis_map(
    edges: List[Tuple[int, int]], node_shift: int = 0
) -> Dict[str, int]:
    max_node = 0
    for u, v in edges:
        max_node = max(max_node, u + node_shift, v + node_shift)
    edge_map: Dict[str, int] = {}
    for idx, (u, v) in enumerate(edges):
        a, b = sorted((u, v))
        edge_id = f"edge_{a}_{b}"
        edge_map[edge_id] = max_node + idx + 1
    return edge_map


def filter_auth_table_for_shard(
    auth_table: Dict[int, Dict[str, Set[Any]]],
    partitioner: Any,
    server_id: int,
) -> Dict[int, Dict[str, Set[Any]]]:
    filtered: Dict[int, Dict[str, Set[Any]]] = {}
    for start, entries in auth_table.items():
        local_nodes = {
            n
            for n in entries.get("n", set())
            if partitioner.assign_entity(n) == server_id
        }
        local_edges = {
            e
            for e in entries.get("e", set())
            if partitioner.assign_entity(e) == server_id
        }
        if local_nodes or local_edges:
            filtered[start] = {"n": local_nodes, "e": local_edges}
    return filtered


def filter_node_to_starts_for_shard(
    node_to_starts: Dict[NodeOrEdgeId, Set[int]],
    partitioner: Any,
    server_id: int,
) -> Dict[NodeOrEdgeId, Set[int]]:
    filtered: Dict[NodeOrEdgeId, Set[int]] = {}
    for entity, starts in node_to_starts.items():
        try:
            owner = partitioner.assign_entity(entity)
        except Exception:
            continue
        if owner == server_id:
            filtered[entity] = set(starts)
    return filtered


def to_serializable_auth(
    table: Dict[int, Dict[str, Set[Any]]],
) -> Dict[str, Dict[str, List]]:
    return {
        str(start): {
            "n": sorted(int(x) for x in entries.get("n", set())),
            "e": sorted(str(x) for x in entries.get("e", set())),
        }
        for start, entries in table.items()
    }


def to_serializable_node_to_starts(
    table: Dict[NodeOrEdgeId, Set[int]],
) -> Dict[str, List[int]]:
    out: Dict[str, List[int]] = {}
    for k, starts in table.items():
        key = str(k)
        out[key] = sorted(int(x) for x in starts)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split auth_by_start/node_to_starts into per-server files."
    )
    parser.add_argument("--auth-file", type=str, help="Path to auth_by_start.json")
    parser.add_argument(
        "--node-to-starts-file", type=str, help="Path to node_to_starts.json"
    )
    parser.add_argument(
        "--server-count", required=True, type=int, help="Total number of servers."
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="base/auth-many-server/splits",
        help="Output directory for per-server files.",
    )
    parser.add_argument(
        "--partitioner-type",
        type=str,
        default="modulo",
        choices=["modulo", "community", "metis"],
        help="How to assign nodes/edges to servers for the split.",
    )
    parser.add_argument(
        "--community-file",
        type=str,
        default=None,
        help="Community file (node community) used when partitioner-type=community.",
    )
    parser.add_argument(
        "--metis-partition-file",
        type=str,
        default=None,
        help="METIS partition file used when partitioner-type=metis.",
    )
    parser.add_argument(
        "--metis-base",
        type=int,
        default=0,
        help="Base index for 1-column METIS partition files (default 0).",
    )
    parser.add_argument(
        "--metis-use-bipartite-edges",
        action="store_true",
        help="Assume METIS partition includes edge vertices (node-edge bipartite). edge_id is mapped to max_node+idx+1 (+node-shift).",
    )
    parser.add_argument(
        "--metis-node-shift",
        type=int,
        default=0,
        help="Shift applied to node ids when constructing edge vertex ids for METIS bipartite (use 1 if METIS input was 1-based).",
    )
    parser.add_argument(
        "--edges",
        type=str,
        default=None,
        help="Edge list path (required when metis-use-bipartite-edges is set).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.auth_file and not args.node_to_starts_file:
        raise SystemExit(
            "At least one of --auth-file or --node-to-starts-file is required."
        )
    if args.server_count <= 0:
        raise SystemExit("--server-count must be positive")

    edge_metis_map = None
    if args.partitioner_type == "community":
        if not args.community_file:
            raise SystemExit("--community-file is required for community partitioner")
        partitioner = CommunityPartitioner(
            args.server_count, load_community_map(Path(args.community_file))
        )
    elif args.partitioner_type == "metis":
        if not args.metis_partition_file:
            raise SystemExit("--metis-partition-file is required for metis partitioner")
        if args.metis_use_bipartite_edges:
            if not args.edges:
                raise SystemExit("--edges is required when metis-use-bipartite-edges is set")
            edges = load_edge_list(Path(args.edges))
            edge_metis_map = build_edge_metis_map(edges, node_shift=args.metis_node_shift)
        partitioner = MetisPartitioner(
            args.server_count,
            load_metis_partition(
                Path(args.metis_partition_file), base_index=args.metis_base
            ),
            edge_metis_map=edge_metis_map,
        )
    else:
        partitioner = ModuloPartitioner(args.server_count)
    auth_table = load_auth_table(args.auth_file)
    node_to_starts = load_node_to_starts(args.node_to_starts_file)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for sid in range(args.server_count):
        if auth_table:
            local_auth = filter_auth_table_for_shard(auth_table, partitioner, sid)
            auth_path = out_dir / f"auth_by_start_server{sid}.json"
            auth_path.write_text(
                json.dumps(
                    to_serializable_auth(local_auth), indent=2, ensure_ascii=False
                ),
                encoding="utf-8",
            )
            print(
                f"[server {sid}] auth_by_start: keep {len(local_auth)} starts -> {auth_path}"
            )

        if node_to_starts:
            local_n2s = filter_node_to_starts_for_shard(
                node_to_starts, partitioner, sid
            )
            n2s_path = out_dir / f"node_to_starts_server{sid}.json"
            n2s_path.write_text(
                json.dumps(
                    to_serializable_node_to_starts(local_n2s),
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(
                f"[server {sid}] node_to_starts: keep {len(local_n2s)} entities -> {n2s_path}"
            )


if __name__ == "__main__":
    main()
