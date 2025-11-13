#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone

NodeId = Union[int, str]


def now_iso() -> str:
    """Timezone-aware ISO8601 timestamp for logging/metrics."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def make_edge_id(u: int, v: int) -> str:
    """
    u, v をソートして昇順に並べ、f"edge_{a}_{b}" という文字列を返す。
    これにより (1,2) と (2,1) が同じエッジIDになる。副作用なし。
    """
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    """
    指定パスを UTF-8 で開き、空行をスキップしつつ各行を空白で分割して 2 要素でない行は ValueError を投げる。
    u, v を int に変換してタプルとして edges に追加し、最後にリストで返す。
    """
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
    """
    データクラスの返し方
    node_id（int か str）と server_id（int）を持つ不変オブジェクト。
    asdict() で辞書化できるため JSON レスポンス作成に便利である。
    """

    node_id: NodeId
    server_id: int


## TODO: この分け方をどの程度変えることができるのかも指標になりそう
class ModuloPartitioner:
    """
    ノードとエッジのサーバごとへの割り当て
    assign_node(node_id)：ノードを node_id % server_count で割り当てる（シンプル均等割り当て）。
    assign_edge(u, v)：エッジは (a * 1_000_003 + b) % server_count のハッシュで割り当てる。1_000_003 は大きな素数で衝突分散に寄与する。
    assign_entity(entity_id)：与えられた entity_id が整数ならノード割り当て、文字列で edge_* ならエッジ割当を行う。形式不正や未対応型なら例外を投げる。
    """

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
    Each shard owns two types of entities:
      * graph nodes (int id)
      * synthetic edge nodes (edge_u_v)
    Random walks traverse the bipartite expansion node -> edge -> node -> ...
    __init__(edges, server_id, server_count)：与えられた全エッジ列を走査し、各ノードと各合成エッジ（edge_u_v）の持ち主をパーティショナで判定する。

        自身が所有するエンティティは local_entities に追加し、neighbor_map に空リストを準備する（_ensure_entity）。
        ノード側から見て隣接にはエッジID（合成エンティティ）を追加し、エッジ側から見て隣接には両端ノードを追加する（各 Neighbor には相手のサーバID を記録）。
        これによりグラフは「二部展開（node ↔ edge）」として内部表現される。
        _ensure_entity(entity_id)：ローカルエンティティ集合に加え、neighbor_map のキーを確実に作る。副作用：集合とマップを更新する。
        get_neighbors(entity_id) -> Optional[List[Neighbor]]：entity_id がこのシャードのローカルエンティティなら、その隣接の Neighbor リスト（コピー）を返す。所有していないなら None を返す。
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
# RNG (de)serialization helpers
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
# Walk execution
# ---------------------------------------------------------------------------
@dataclass
class WalkResult:
    path: List[NodeId]
    servers: List[int]


class PeerWalker:
    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
    ):
        self.shard = shard
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
        self.request_timeout = request_timeout
        self.max_hops = max_hops

    def _get_local_neighbors(self, entity_id: NodeId) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def continue_from_state(self, state: dict) -> dict:
        # 現在のサーバIDを取得
        current_sid = self.shard.server_id
        rng_state_json = state.get("rng_state")
        # ランダムに隣接を取得
        rng = random.Random()
        if rng_state_json is not None:
            rng.setstate(deserialize_rng_state(rng_state_json))
        else:
            seed = state.get("seed")
            rng = random.Random(seed)

        current_entity: NodeId = state["current_node"]
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))

        print(
            f"[Server {current_sid}] continue_from_state: start entity={current_entity} hops_done={hops_done}"
        )
        # 終了確率より大きかったら継続
        while hops_done < self.max_hops and rng.random() > alpha:
            hops_done += 1
            owner = self.shard.partitioner.assign_entity(current_entity)
            # 次のエンティティが別サーバの場合
            if owner != current_sid:
                print(
                    f"[Server {current_sid}] entity {current_entity} not local (owner={owner}) → delegate"
                )
                state_out = {
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                ## メッセージ内容を保持して、次のサーバに送る
                return self._post_continue(owner, state_out)

            # 次のエンティティが同じサーバだった時
            neighbors = self._get_local_neighbors(current_entity)
            if not neighbors:
                print(
                    f"[Server {current_sid}] entity {current_entity} has no neighbors → finish"
                )
                return {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                }

            next_choice = rng.choice(neighbors)
            next_entity: NodeId = next_choice["node_id"]
            next_server = int(next_choice["server_id"])
            print(
                f"[Server {current_sid}] hop {hops_done}: {current_entity} -> {next_entity} (server {next_server})"
            )

            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            if next_server != current_sid:
                state_out = {
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                print(
                    f"[Server {current_sid}] delegating walk to server {next_server} (entity={current_entity})"
                )
                return self._post_continue(next_server, state_out)

        if hops_done >= self.max_hops:
            print(f"[Server {current_sid}] reached max hops {self.max_hops} → finish")
        else:
            print(
                f"[Server {current_sid}] walk stopped by alpha after {hops_done} hops"
            )
        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------
class EdgeAwareHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json({"status": "ok", "server_id": self.server.server_id})
            return

        if parsed.path != "/neighbors":
            self.send_error(404, "Unknown path")
            return

        query = parse_qs(parsed.query)
        raw_entity = query.get("node", [None])[0]
        if raw_entity is None:
            self.send_error(400, "Missing 'node' query parameter")
            return

        entity: NodeId
        if raw_entity.startswith("edge_"):
            entity = raw_entity
        else:
            try:
                entity = int(raw_entity)
            except ValueError:
                self.send_error(
                    400, "'node' must be an integer id or an edge id (edge_u_v)"
                )
                return

        print(
            f"[Server {self.server.server_id}] neighbor request for entity {entity} from {self.client_address}"
        )

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
        # コントローラによるここから N 回のウォークを始めてくれとの開始命令
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

        print(
            f"[Server {self.server.server_id}] /walk start node={start_node} alpha={alpha} walks={walks} seed={seed}"
        )

        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
        )
        start_ts = time.perf_counter()
        print("start time", start_ts)
        results = []
        for i in range(walks):
            rng = random.Random(seed if seed is None else (seed + i))
            initial_state = {
                "current_node": int(start_node),
                "path": [int(start_node)],
                "servers": [self.server.server_id],
                "alpha": float(alpha),
                "rng_state": serialize_rng_state(rng),
                "hops_done": 0,
            }
            res = walker.continue_from_state(initial_state)
            results.append(res)
            # time.sleep(0.01)

        wall_end_epoch = time.time()
        wall_end_iso = now_iso()
        duration = time.perf_counter() - start_ts
        print(duration)
        payload = {"walks": results, "duration": duration}
        print(
            f"[Server {self.server.server_id}] finished /walk in {duration:.3f}s; returning {len(results)} walks"
        )
        self._write_json(payload)

    # 他のサーバから来たRwerを受け取る
    def _handle_continue_walk(self) -> None:

        state = self._read_json_body()
        if state is None:
            return

        print(
            f"[Server {self.server.server_id}] /continue_walk from {self.client_address} hops_done={state.get('hops_done', 0)}"
        )
        walker = PeerWalker(
            self.server.shard,
            endpoints=self.server.endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
        )
        try:
            res = walker.continue_from_state(state)
        except Exception as exc:
            self.send_error(500, f"Error during continue_walk: {exc}")
            return

        self._write_json(res)

    def log_message(self, format: str, *args) -> None:
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (node + edge bipartite model)."
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
        f"[Server {server.server_id}] Serving {len(shard.local_entities)} entities (nodes + edges) on {args.host}:{args.port} / {args.server_count} servers"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
