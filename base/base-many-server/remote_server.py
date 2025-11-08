from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class Neighbor:
    node_id: int
    server_id: int


class ModuloPartitioner:
    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign(self, node_id: int) -> int:
        return node_id % self.server_count


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    edges: List[Tuple[int, int]] = []
    with edge_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) != 2:
                raise ValueError(f"Edge list line is malformed: {line!r}")
            u, v = map(int, parts)
            edges.append((u, v))
    return edges


def resolve_edge_path(edge_arg: str) -> Path:
    candidate = Path(edge_arg).expanduser()

    if candidate.is_absolute() and candidate.exists():
        return candidate

    search_paths = []
    if not candidate.is_absolute():
        search_paths.append((Path.cwd() / candidate).resolve())
        search_paths.append((Path(__file__).resolve().parent / candidate).resolve())

    for path in search_paths:
        if path.exists():
            return path

    return search_paths[0] if search_paths else candidate


def enumerate_nodes(edges: Sequence[Tuple[int, int]]) -> Iterable[int]:
    seen: Set[int] = set()
    for u, v in edges:
        if u not in seen:
            seen.add(u)
            yield u
        if v not in seen:
            seen.add(v)
            yield v


class GraphShard:
    """Stores the portion of the graph owned by a single server."""

    def __init__(self, edges: Sequence[Tuple[int, int]], server_id: int, server_count: int) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")

        self.server_id = server_id
        self.partitioner = ModuloPartitioner(server_count)
        self.local_nodes: Set[int] = set()
        self.neighbor_map: Dict[int, List[Neighbor]] = defaultdict(list)

        for node in enumerate_nodes(edges):
            if self.partitioner.assign(node) == self.server_id:
                self.local_nodes.add(node)
                self.neighbor_map.setdefault(node, [])

        for u, v in edges:
            owner_u = self.partitioner.assign(u)
            owner_v = self.partitioner.assign(v)

            if owner_u == self.server_id:
                self.neighbor_map[u].append(Neighbor(node_id=v, server_id=owner_v))

            if owner_v == self.server_id:
                self.neighbor_map[v].append(Neighbor(node_id=u, server_id=owner_u))

    def get_neighbors(self, node_id: int) -> Optional[List[Neighbor]]:
        if node_id not in self.local_nodes:
            return None
        return self.neighbor_map.get(node_id, [])


class NeighborRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._write_json({"status": "ok", "server_id": self.server.server_id})
            return

        if parsed.path != "/neighbors":
            self.send_error(404, "Unknown path")
            return

        query = parse_qs(parsed.query)
        raw_node = query.get("node", [None])[0]
        if raw_node is None:
            self.send_error(400, "Missing 'node' query parameter")
            return

        try:
            node_id = int(raw_node)
        except ValueError:
            self.send_error(400, "'node' must be an integer")
            return

        neighbors = self.server.shard.get_neighbors(node_id)
        if neighbors is None:
            self.send_error(404, f"Node {node_id} is not owned by server {self.server.server_id}")
            return

        payload = {
            "node_id": node_id,
            "server_id": self.server.server_id,
            "neighbors": [asdict(neighbor) for neighbor in neighbors],
        }
        self._write_json(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # Suppress default logging noise

    def _write_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class GraphShardServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, shard: GraphShard) -> None:
        super().__init__((host, port), NeighborRequestHandler)
        self.shard = shard
        self.server_id = shard.server_id


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Distributed graph shard server.")
    parser.add_argument(
        "--edges",
        default="./../../dataset/Louvain/graph/karate.gr",
        help="Path to the shared edge list file.",
    )
    parser.add_argument(
        "--server-id",
        type=int,
        required=True,
        help="Unique id of this server within the cluster (0-indexed).",
    )
    parser.add_argument(
        "--server-count",
        type=int,
        required=True,
        help="Total number of servers participating in the cluster.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host/IP address to bind the shard server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to expose the shard server.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    edge_path = resolve_edge_path(args.edges)
    edges = load_edge_list(edge_path)
    shard = GraphShard(edges, server_id=args.server_id, server_count=args.server_count)
    server = GraphShardServer(args.host, args.port, shard)

    print(
        f"[Server {args.server_id}] Serving {len(shard.local_nodes)} nodes "
        f"on {args.host}:{args.port} (total servers: {args.server_count})"
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {args.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
