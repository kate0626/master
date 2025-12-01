#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
import atexit

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]
"""
    リモートサーバにおける一度グラフを作成してからのRWを行う方法
    実行サーバは次のよう
    コントローラの実行はそのまま行うことが可能
    
    # Server 0
    python3 base/auth-many-server/remote_server_visi.py \
    --edges ./dataset/Louvain/graph/karate.gr \
    --server-count 2 \
    --server-id 0 \
    --host 10.58.60.5 \
    --port 3000 \
    --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 \
    --auth-file base/auth-many-server/auth_by_start.json

    # Server 1
    python3 base/auth-many-server/remote_server_visi.py \
    --edges ./dataset/Louvain/graph/karate.gr \
    --server-count 2 \
    --server-id 1 \
    --host 10.58.60.6 \
    --port 3000 \
    --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 \
    --auth-file base/auth-many-server/auth_by_start.json
    
    
    python3 base/auth-many-server/remote_server_visi.py   --server-id 0 --server-count 1   --edges dataset/Louvain/graph/karate.gr   --host 10.58.60.5 --port 3000   --server-endpoints 10.58.60.5:3000 --node-to-starts-file base/auth-many-server/node_to_starts.json

    

"""


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """ログ・メトリクス用のタイムスタンプ"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_edge_id(u: int, v: int) -> str:
    """(u,v) をソートして "edge_u_v" 形式のIDにする"""
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    """エッジリスト（u v）を読み込む"""
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


@dataclass(frozen=True)
class Neighbor:
    """隣接エンティティ (node or edge) + 所有サーバID"""

    node_id: NodeId
    server_id: int


class ModuloPartitioner:
    """ノード/エッジを server_count 個に均等に割り振るためのパーティショナ"""

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
            try:
                _, raw_u, raw_v = entity_id.split("_", 2)
                return self.assign_edge(int(raw_u), int(raw_v))
            except ValueError as exc:
                raise ValueError(f"Malformed edge id: {entity_id!r}") from exc
        raise TypeError(f"Unsupported entity id type: {entity_id!r}")


class GraphShard:
    """
    node ↔ edge の二部グラフとして内部表現を持つシャード。
    """

    def __init__(
        self, edges: Sequence[Tuple[int, int]], server_id: int, server_count: int
    ) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")

        self.server_id = server_id
        self.partitioner = ModuloPartitioner(server_count)
        self.local_entities: Set[NodeId] = set()
        self.neighbor_map: Dict[NodeId, List[Neighbor]] = defaultdict(list)

        for u, v in edges:
            edge_id = make_edge_id(u, v)
            u_owner = self.partitioner.assign_node(u)
            v_owner = self.partitioner.assign_node(v)
            edge_owner = self.partitioner.assign_edge(u, v)

            if u_owner == self.server_id:
                self._ensure_entity(u)
            if v_owner == self.server_id:
                self._ensure_entity(v)
            if edge_owner == self.server_id:
                self._ensure_entity(edge_id)

            if u_owner == self.server_id:
                self.neighbor_map[u].append(Neighbor(edge_id, edge_owner))
            if v_owner == self.server_id:
                self.neighbor_map[v].append(Neighbor(edge_id, edge_owner))
            if edge_owner == self.server_id:
                self.neighbor_map[edge_id].append(Neighbor(u, u_owner))
                self.neighbor_map[edge_id].append(Neighbor(v, v_owner))

    def _ensure_entity(self, entity_id: NodeId) -> None:
        self.local_entities.add(entity_id)
        self.neighbor_map.setdefault(entity_id, [])

    def get_neighbors(self, entity_id: NodeId) -> Optional[List[Neighbor]]:
        if entity_id not in self.local_entities:
            return None
        return list(self.neighbor_map.get(entity_id, []))


# ---------------------------------------------------------------------------
# 認可テーブル → visible グラフ
# ---------------------------------------------------------------------------


def load_node_to_starts_table(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
    """
    node/edge -> 許可スタートノード集合。
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"node_to_starts file not found: {p}")
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


def build_visible_neighbor_map_for_start(
    shard: GraphShard,
    start_node: int,
    node_to_starts: Dict[NodeOrEdgeId, Set[int]],
    auth_table: Optional[Dict[int, Dict[str, Set[Any]]]] = None,
) -> Dict[NodeId, List[Neighbor]]:
    """
    start_node に対して「見えるノード・エッジだけ」を使った
    visible graph の隣接リストを構築する。

    ここでは auth_table を「許可リスト」として解釈し、
    ・ノード: allowed_nodes に含まれているものだけ採用
    ・エッジ: allowed_edges に含まれているものだけ採用

    NG リストで持っている場合は、この関数内で反転すればOK。
    """
    visible_map: Dict[NodeId, List[Neighbor]] = {}

    entry = auth_table.get(start_node) if auth_table else None

    def is_allowed(entity: NodeOrEdgeId) -> bool:
        allowed_starts = node_to_starts.get(entity)
        if allowed_starts is not None:
            return start_node in allowed_starts
        if entry:
            if isinstance(entity, int):
                return entity in entry.get("n", set())
            return entity in entry.get("e", set())
        return False

    for entity in shard.local_entities:
        neighbors = shard.get_neighbors(entity) or []
        filtered: List[Neighbor] = []
        for nb in neighbors:
            nid = nb.node_id
            if is_allowed(nid):
                filtered.append(nb)
        visible_map[entity] = filtered

    print(visible_map[0])
    return visible_map


# ---------------------------------------------------------------------------
# RNG のシリアライズ
# ---------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    return obj


def _from_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return tuple(_from_jsonable(x) for x in obj)
    return obj


def serialize_rng_state(rng: random.Random) -> Any:
    return _to_jsonable(rng.getstate())


def deserialize_rng_state(jsonable_state: Any) -> tuple:
    return _from_jsonable(jsonable_state)


# ---------------------------------------------------------------------------
# Visible グラフ専用 Walker
# ---------------------------------------------------------------------------


class VisibleWalker:
    """
    NG/OK リストから事前に作った visible グラフ上で RW する。
    RW 中には認可チェックは行わない。
    """

    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        visible_neighbor_map: Dict[NodeId, List[Neighbor]],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        stats_collector: Optional[Any] = None,
    ):
        self.shard = shard
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
        self.request_timeout = request_timeout
        self.max_hops = max_hops
        self.visible_neighbor_map = visible_neighbor_map
        self.stats_collector = stats_collector

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.visible_neighbor_map.get(entity_id, [])
        return [asdict(n) for n in neighbors]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def _bump_server_counter(self, attr: str, key: Any) -> None:
        if self.stats_collector is None:
            return
        counter = getattr(self.stats_collector, attr, None)
        if counter is None:
            return
        counter[key] += 1

    def _record_entity_visit(self, entity_id: Any) -> None:
        self._bump_server_counter("access_counter", entity_id)

    def continue_from_state(self, state: dict) -> dict:
        current_sid = self.shard.server_id
        rng_state_json = state.get("rng_state")
        rng = random.Random()
        if rng_state_json is not None:
            loaded = deserialize_rng_state(rng_state_json)
            if loaded:
                rng.setstate(loaded)
        else:
            rng = random.Random(state.get("seed"))

        current_entity: NodeId = state["current_node"]
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))

        self._record_entity_visit(current_entity)

        # 終了確率をクリアしている間、visible グラフ上を遷移
        while rng.random() > alpha:
            hops_done += 1
            owner = self.shard.partitioner.assign_entity(current_entity)
            if owner != current_sid:
                # 別サーバへ移動 → 状態を送り返す
                state_out = {
                    "start_node": state.get("start_node"),
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                return self._post_continue(owner, state_out)

            neighbors = self._get_local_neighbors(current_entity)
            if not neighbors:
                # visible グラフ上で隣接がない
                result = {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                    "denied": True,
                    "denied_reason": f"no neighbors (visible graph) from {current_entity}",
                }
                return result

            next_choice = rng.choice(neighbors)
            next_entity = next_choice["node_id"]
            next_server = int(next_choice["server_id"])

            self._record_entity_visit(next_entity)
            self._bump_server_counter(
                "transition_counter", f"{current_entity}->{next_entity}"
            )

            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            if next_server != current_sid:
                state_out = {
                    "start_node": state.get("start_node"),
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                return self._post_continue(next_server, state_out)

        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }


# ---------------------------------------------------------------------------
# HTTP ハンドラ
# ---------------------------------------------------------------------------


class EdgeAwareHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json({"status": "ok", "server_id": self.server.server_id})
            return
        if parsed.path == "/access_stats":
            payload = {
                "access": dict(self.server.access_counter),
                "authorized": dict(self.server.authorized_counter),
                "authorization_attempts": dict(
                    self.server.authorization_attempt_counter
                ),
                "authorization_denied": dict(self.server.authorization_denied_counter),
                "transition": dict(self.server.transition_counter),
                # visible 版では認可呼び出しはしないので 0 のまま
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
            }
            self._write_json(payload)
            return

        if parsed.path != "/neighbors":
            self.send_error(404, "Unknown path")
            return

        query = parse_qs(parsed.query)
        raw_entity = query.get("node", [None])[0]
        if raw_entity is None:
            self.send_error(400, "Missing 'node' query parameter")
            return

        if raw_entity.startswith("edge_"):
            entity: NodeId = raw_entity
        else:
            try:
                entity = int(raw_entity)
            except ValueError:
                self.send_error(
                    400, "'node' must be an integer id or an edge id (edge_u_v)"
                )
                return

        neighbors = self.server.shard.get_neighbors(entity)
        if neighbors is None:
            self.send_error(404, f"Entity {entity} not owned by this shard")
            return

        payload = {
            "node_id": entity,
            "server_id": self.server.server_id,
            "neighbors": [asdict(n) for n in neighbors],
        }
        self._write_json(payload)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/walk":
            self._handle_walk_start()
            return
        if parsed.path == "/continue_walk":
            self._handle_continue_walk()
            return
        self.send_error(404, "Unknown path")

    def _handle_walk_start(self) -> None:
        params = self._read_json_body()
        if params is None:
            return

        start_node = params.get("start_node")
        alpha = params.get("alpha")
        walks = int(params.get("walks", 1))
        seed = params.get("seed", None)
        endpoints = params.get("endpoints")
        server_count = params.get("server_count")

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

        start_node_int = int(start_node)

        print(
            f"[Server {self.server.server_id}] /walk(start={start_node_int}, alpha={alpha}, walks={walks}, seed={seed}) [visible_graph]"
        )

        # start_node ごとに visible graph を構築 or キャッシュから取得
        cache = self.server.visible_neighbor_cache
        if start_node_int not in cache:
            cache[start_node_int] = build_visible_neighbor_map_for_start(
                self.server.shard,
                start_node_int,
                self.server.node_to_starts,
                self.server.auth_table,
            )
            print(
                f"[Server {self.server.server_id}] built visible graph for start_node={start_node_int}"
            )
        visible_neighbors = cache[start_node_int]

        walker = VisibleWalker(
            self.server.shard,
            endpoints=endpoints,
            visible_neighbor_map=visible_neighbors,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            stats_collector=self.server,
        )

        start_ts = time.perf_counter()
        wall_start_epoch = time.time()
        wall_start_iso = now_iso()
        results = []
        for i in range(walks):
            rng = random.Random(seed if seed is None else (seed + i))
            initial_state = {
                "start_node": start_node_int,
                "current_node": start_node_int,
                "path": [start_node_int],
                "servers": [self.server.server_id],
                "alpha": float(alpha),
                "rng_state": serialize_rng_state(rng),
                "hops_done": 0,
            }
            res = walker.continue_from_state(initial_state)
            results.append(res)

        wall_end_epoch = time.time()
        wall_end_iso = now_iso()
        duration = time.perf_counter() - start_ts
        payload = {
            "walks": results,
            "metrics": {
                "server_id": self.server.server_id,
                "duration_sec": duration,
                "wall_start_epoch": wall_start_epoch,
                "wall_end_epoch": wall_end_epoch,
                "wall_start_time": wall_start_iso,
                "wall_end_time": wall_end_iso,
                "walks_requested": walks,
                "walks_completed": len(results),
                "alpha": float(alpha),
            },
        }
        print(
            f"[Server {self.server.server_id}] finished /walk in {duration:.3f}s; returning {len(results)} walks [visible_graph]"
        )
        self._write_json(payload)

    def _handle_continue_walk(self) -> None:
        state = self._read_json_body()
        if state is None:
            return

        start_node = state.get("start_node")
        if start_node is None:
            self.send_error(400, "Missing start_node in state")
            return
        start_node_int = int(start_node)

        cache = self.server.visible_neighbor_cache
        if start_node_int not in cache:
            cache[start_node_int] = build_visible_neighbor_map_for_start(
                self.server.shard,
                start_node_int,
                self.server.node_to_starts,
                self.server.auth_table,
            )
            print(
                f"[Server {self.server.server_id}] built visible graph (continue) for start_node={start_node_int}"
            )
        visible_neighbors = cache[start_node_int]

        walker = VisibleWalker(
            self.server.shard,
            endpoints=self.server.endpoints,
            visible_neighbor_map=visible_neighbors,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            stats_collector=self.server,
        )

        try:
            res = walker.continue_from_state(state)
        except Exception as exc:
            self.send_error(500, f"Error during continue_walk: {exc}")
            return

        self._write_json(res)

    def log_message(self, format: str, *args) -> None:
        # HTTP の標準ログは抑制
        return

    def _read_json_body(self) -> Optional[dict]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return None
        body = self.rfile.read(content_length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return None

    def _write_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


# ---------------------------------------------------------------------------
# サーバクラス & main
# ---------------------------------------------------------------------------


class GraphShardServer(ThreadingHTTPServer):
    def __init__(
        self,
        host: str,
        port: int,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
    ) -> None:
        super().__init__((host, port), EdgeAwareHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout
        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}

        # 各種カウンタ（コントローラ側と形式を合わせるため per-hop 版と同じ名前で用意）
        self.access_counter = Counter()
        self.authorized_counter = Counter()
        self.authorization_attempt_counter = Counter()
        self.authorization_denied_counter = Counter()
        self.transition_counter = Counter()
        # visible 版では 0 のまま
        self.auth_time_total = 0.0
        self.auth_calls = 0

        # start_node ごとの visible neighbor キャッシュ
        # visible_neighbor_cache[start_node][entity_id] = List[Neighbor]
        self.visible_neighbor_cache: Dict[int, Dict[NodeId, List[Neighbor]]] = {}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (visible-graph model)."
    )
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
        "--host", default="0.0.0.0", help="Host/IP address to bind the shard server."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to expose the shard server."
    )
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for all servers in order (host:port).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=5.0,
        help="Timeout (sec) when this server queries other shards.",
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default=None,
        help="JSON file path mapping start_node -> allowed entities (n/e).",
    )
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        default=None,
        help="JSON file mapping node/edge -> allowed start nodes.",
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

    # 認可テーブル読み込み
    # if args.auth_file:
    #     server.auth_table = load_entity_auth_table(Path(args.auth_file))
    if args.node_to_starts_file:
        server.node_to_starts = load_node_to_starts_table(
            Path(args.node_to_starts_file)
        )

    def dump_access_stats():
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "authorization_attempts": dict(server.authorization_attempt_counter),
            "authorization_denied": dict(server.authorization_denied_counter),
            "transition": dict(server.transition_counter),
            "auth_time_total": server.auth_time_total,
            "auth_calls": server.auth_calls,
        }
        out_path = Path(f"access_stats_server{server.server_id}.json")
        out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(
            f"[Server {server.server_id}] auth summary: "
            f"{server.auth_calls} calls, total {server.auth_time_total:.6f}s"
        )
        print(f"[Server {server.server_id}] Access stats saved to {out_path}")

    atexit.register(dump_access_stats)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
