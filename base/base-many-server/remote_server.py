#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlparse
from urllib import request as urllib_request
from urllib import parse as urllib_parse
from urllib import error as urllib_error


"""
    分散になるように変更した後のコード
"""


# --- dataclass for neighbor info ---
@dataclass(frozen=True)
class Neighbor:
    node_id: int
    server_id: int


# エッジリストの読み込み
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


def enumerate_nodes(edges: Sequence[Tuple[int, int]]) -> Iterable[int]:
    seen: Set[int] = set()
    for u, v in edges:
        if u not in seen:
            seen.add(u)
            yield u
        if v not in seen:
            seen.add(v)
            yield v


# ノードをサーバに分ける
class ModuloPartitioner:
    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    ## ノードIDをサーバ数で割った余りで担当サーバを決定
    def assign(self, node_id: int) -> int:
        return node_id % self.server_count


# --- Graph shard that stores local nodes and neighbor metadata ---
class GraphShard:
    """
    目的: 各サーバが担当する「部分グラフ」を保持するクラス。
        やっていること:
        与えられた全エッジから、自分の担当ノードを抽出。
        各ノードの隣接ノードを Neighbor オブジェクトとして保存。
        主要属性:
        self.local_nodes: このサーバが担当するノードIDの集合。
        self.neighbor_map: 各ノード → 隣接ノードリスト の辞書。
    """

    def __init__(
        self, edges: Sequence[Tuple[int, int]], server_id: int, server_count: int
    ) -> None:
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
        return list(self.neighbor_map.get(node_id, []))


# --- Server-side random walker used by /walk ---
@dataclass
class WalkResult:
    ## 一回あたりのRWの結果を保存
    path: List[int]
    servers: List[int]


## RWの実行クラス
class ServerSideRandomWalker:
    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        alpha: float,
        seed: Optional[int] = None,
        request_timeout: float = 5.0,
    ):
        self.shard = shard
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
        self.alpha = alpha
        self.rng = random.Random(seed)
        self.request_timeout = request_timeout

    def walk_once(self, start_node: int) -> WalkResult:
        current_node = start_node
        current_server = self.shard.partitioner.assign(start_node)
        path = [current_node]
        servers = [current_server]
        # 終了確率よりも大きかったら継続
        while self.rng.random() > self.alpha:
            neighbors = self._get_neighbors(current_server, current_node)
            if not neighbors:
                break
            next_neighbor = self.rng.choice(neighbors)
            current_node = next_neighbor["node_id"]
            current_server = next_neighbor["server_id"]
            path.append(current_node)
            servers.append(current_server)

        return WalkResult(path=path, servers=servers)

    def _get_neighbors(self, server_id: int, node_id: int) -> List[dict]:
        current_sid = self.shard.server_id
        # I自分のサーバにあるのなら同一サーバ内で移動
        if server_id == self.shard.server_id:
            neighs = self.shard.get_neighbors(node_id)
            if not neighs:
                print(f"[Server {current_sid}] Node {node_id}: No local neighbors.")
                return []
            neighbors_dict = [asdict(n) for n in neighs]
            print(
                f"[Server {current_sid}] Node {node_id}: "
                f"Fetched {len(neighbors_dict)} local neighbors → {[n['node_id'] for n in neighbors_dict]}"
            )
            return neighbors_dict
            # return [asdict(n) for n in neighs]

        # 同じサーバでなければ、異なるサーバへ送るための準備
        endpoint = self.endpoints[server_id].rstrip("/")
        query = urllib_parse.urlencode({"node": node_id})
        url = f"{endpoint}/neighbors?{query}"
        print(
            f"[Server {current_sid}] Querying remote server {server_id} for node {node_id} → {url}"
        )
        try:
            with urllib_request.urlopen(url, timeout=self.request_timeout) as resp:
                payload = resp.read()
        except urllib_error.URLError as exc:
            print(
                f"[Server {current_sid}] ERROR: Failed to contact server {server_id} ({endpoint}) - {exc}"
            )
            raise ConnectionError(
                f"Failed to contact server {server_id} at {endpoint}: {exc}"
            ) from exc

        data = json.loads(payload)
        if "neighbors" not in data:
            print(
                f"[Server {current_sid}] ERROR: Malformed response from server {server_id}: {data!r}"
            )
            raise ValueError(f"Malformed response from server {server_id}: {data!r}")
        neighbor_list = data["neighbors"]
        print(
            f"[Server {current_sid}] Received {len(neighbor_list)} neighbors from server {server_id} "
            f"for node {node_id} → {[n['node_id'] for n in neighbor_list]}"
        )
        print(data["neighbors"])
        # print(neighbor_list, "同じならOK")
        return neighbor_list
        # return data["neighbors"]


# --- HTTP handler ---
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
            self.send_error(
                404, f"Node {node_id} is not owned by server {self.server.server_id}"
            )
            return

        payload = {
            "node_id": node_id,
            "server_id": self.server.server_id,
            "neighbors": [asdict(neighbor) for neighbor in neighbors],
        }
        self._write_json(payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/walk":
            self.send_error(404, "Unknown path")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return

        body = self.rfile.read(content_length)
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        # Required fields
        start_node = params.get("start_node")
        alpha = params.get("alpha")
        walks = int(params.get("walks", 1))
        seed = params.get("seed", None)
        endpoints = params.get("endpoints", None)
        server_count = params.get("server_count", None)

        if (
            start_node is None
            or alpha is None
            or endpoints is None
            or server_count is None
        ):
            self.send_error(
                400,
                "Missing required parameters: start_node, alpha, endpoints, server_count",
            )
            return

        # instantiate walker and run requested number of walks
        try:
            walker = ServerSideRandomWalker(
                self.server.shard,
                endpoints=endpoints,
                alpha=float(alpha),
                seed=seed,
                request_timeout=getattr(self.server, "request_timeout", 5.0),
            )
        except Exception as exc:
            self.send_error(500, f"Failed to create walker: {exc}")
            return

        results = []
        try:
            for _ in range(walks):
                r = walker.walk_once(int(start_node))
                results.append({"path": r.path, "servers": r.servers})
        except Exception as exc:
            self.send_error(500, f"Error during walk execution: {exc}")
            return

        payload = {"walks": results}
        self._write_json(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return  # suppress default logging

    def _write_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class GraphShardServer(ThreadingHTTPServer):
    def __init__(
        self,
        host: str,
        port: int,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
    ) -> None:
        super().__init__((host, port), NeighborRequestHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout


# --- CLI / main ---
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
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for all servers in order (host:port). This server will use them to contact other shards.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=5.0,
        help="Timeout (sec) when this server queries other shards.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise FileNotFoundError(f"Edge list not found: {edge_path}")

    edges = load_edge_list(edge_path)
    shard = GraphShard(edges, server_id=args.server_id, server_count=args.server_count)
    server = GraphShardServer(
        host=args.host,
        port=args.port,
        shard=shard,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
    )

    print(
        f"[Server {server.server_id}] Serving {len(shard.local_nodes)} nodes on {args.host}:{args.port} (total servers: {args.server_count})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
