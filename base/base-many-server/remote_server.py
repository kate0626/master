#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Any
from urllib.parse import parse_qs, urlparse
from urllib import request as urllib_request
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from datetime import datetime, timezone


"""
    これはノードのみを想定したもの
    基本的に使用しない
"""


# ----------------------------
# データ構造・ユーティリティ
# ----------------------------
@dataclass(frozen=True)
class Neighbor:
    node_id: int
    server_id: int


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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


class ModuloPartitioner:
    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign(self, node_id: int) -> int:
        return node_id % self.server_count


class GraphShard:
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


# ----------------------------
# RNG state の JSON 直列化/復元
# random.getstate() はネストされたタプルを返す -> JSON安全な構造へ変換して送る
# ----------------------------
def _to_jsonable(obj: Any) -> Any:
    # タプルをリストに変換して再帰的に処理
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    # ints, floats, str はそのまま
    return obj


def _from_jsonable(obj: Any) -> Any:
    # リストをタプルに戻す（random.setstate はタプルを期待する）
    if isinstance(obj, list):
        return tuple(_from_jsonable(x) for x in obj)
    return obj


def serialize_rng_state(rng: random.Random) -> Any:
    return _to_jsonable(rng.getstate())


def deserialize_rng_state(jsonable_state: Any) -> tuple:
    return _from_jsonable(jsonable_state)


# ----------------------------
# Walk 続行ロジック（分散・対等）
# ----------------------------
@dataclass
class WalkResult:
    path: List[int]
    servers: List[int]


class PeerWalker:
    """
    サーバ内での Walk 続行ロジックを持つクラス。
    - /walk で開始されたとき、初期状態を受け取りここから処理を開始。
    - ホップごとに次ノードの担当サーバが自サーバでない場合、
      /continue_walk に POST して状態を移譲する（制御移譲）。
    """

    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
    ):
        self.shard = shard
        # endpoints は "host:port" の文字列リスト。内部で "http://" を付加して使う。
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
        self.request_timeout = request_timeout
        self.max_hops = max_hops

    def _get_local_neighbors(self, node_id: int) -> List[Dict[str, int]]:
        neighs = self.shard.get_neighbors(node_id)
        if not neighs:
            return []
        return [asdict(n) for n in neighs]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        """
        他サーバに walker 状態を POST して続きを任せる。
        state は JSON 直列化可能な dict（rng_state は JSON 表現済み）。
        """
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def continue_from_state(self, state: dict) -> dict:
        """
        state を受けて可能な限りこのサーバで処理を進め、
        次のサーバへ制御を移す必要が出たら移譲する（再帰的に）。
        state の構成（例）:
        {
            "current_node": int,
            "path": [...],
            "servers": [...],
            "alpha": float,
            "rng_state": <JSONable RNG state>,
            "hops_done": int
        }
        戻り値:
            {"finished": bool, "path": [...], "servers": [...], "hops_done": int}
        """
        current_sid = self.shard.server_id

        # 復元 RNG
        rng_state_json = state.get("rng_state")
        rng = random.Random()
        if rng_state_json is not None:
            rng.setstate(deserialize_rng_state(rng_state_json))
        else:
            # 異常系: seed が来ていたら使う
            seed = state.get("seed")
            rng = random.Random(seed)

        current_node = int(state["current_node"])
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))

        print(
            f"[Server {current_sid}] continue_from_state: starting from node {current_node} (hops_done={hops_done})"
        )

        # メインループ（このサーバでできる分だけ進める）
        while hops_done < self.max_hops and rng.random() > alpha:
            hops_done += 1
            # 自サーバ担当ノードかをチェック（state が渡されるのは呼び出し側が next_server を決めて送るため、
            # ここでは current_node が自分の担当であることが想定されるが一応チェック）
            node_owner = self.shard.partitioner.assign(current_node)
            if node_owner != current_sid:
                # state が別サーバから来たが current_node の担当がここではない -> 即座に移譲
                print(
                    f"[Server {current_sid}] Notice: current_node {current_node} is not local (owner={node_owner}). Delegating."
                )
                state_out = {
                    "current_node": current_node,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                return self._post_continue(node_owner, state_out)

            # ローカル近傍を取得
            neighbors = self._get_local_neighbors(current_node)
            if not neighbors:
                print(
                    f"[Server {current_sid}] Node {current_node}: no local neighbors -> finishing here."
                )
                return {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                }

            # ランダムに次を選ぶ
            next_choice = rng.choice(neighbors)
            next_node = int(next_choice["node_id"])
            next_server = int(next_choice["server_id"])

            print(
                f"[Server {current_sid}] Hop {hops_done}: Node {current_node} -> {next_node} (next_server={next_server})"
            )

            # 経路更新
            path.append(next_node)
            servers.append(next_server)
            current_node = next_node

            # 次が自サーバで続けられるならループ継続
            if next_server != current_sid:
                # 制御移譲：次サーバに状態を渡して続行を任せる
                state_out = {
                    "current_node": current_node,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                print(
                    f"[Server {current_sid}] Delegating to server {next_server} with current_node {current_node}"
                )
                return self._post_continue(next_server, state_out)

            # else: same server => ループで続行

        # ループ外に到達：終了判定（alphaにより終了）、または max_hops 超過
        if hops_done >= self.max_hops:
            print(f"[Server {current_sid}] Max hops {self.max_hops} reached -> finish.")
            return {
                "finished": True,
                "path": path,
                "servers": servers,
                "hops_done": hops_done,
            }

        # rng.random() <= alpha で終了
        print(
            f"[Server {current_sid}] Local walk stopped by alpha after {hops_done} hops; finishing here."
        )
        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }


# ----------------------------
# HTTPハンドラ
# /neighbors  : 近傍情報を返す（従来どおり）
# /walk       : コントローラが起点で RW を要求（このサーバが起点となり continue_from_state を呼ぶ）
# /continue_walk : 他サーバから状態を受け取り処理を続行（PeerWalker.continue_from_state を呼ぶ）
# ----------------------------
class NeighborRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
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

        # ここで「受信確認」ログを出す
        print(
            f"[Server {self.server.server_id}] Received neighbor request for node {node_id} from {self.client_address}"
        )

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

        print(
            f"[Server {self.server.server_id}] Sending {len(neighbors)} neighbors for node {node_id}"
        )
        self._write_json(payload)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/walk":
            # コントローラが初めに /walk を投げる（起点）
            self._handle_walk_start()
            return

        if parsed.path == "/continue_walk":
            # 他サーバから状態を受け取って続行するエンドポイント
            self._handle_continue_walk()
            return

        self.send_error(404, "Unknown path")

    def _handle_walk_start(self) -> None:
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

        print(
            f"[Server {self.server.server_id}] Received /walk start from controller: start_node={start_node}, alpha={alpha}, seed={seed}, walks={walks}"
        )

        # PeerWalker を作り、walks 回だけ開始（各 walk は最終的に finish 結果を得る）
        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
        )
        start_ts = time.perf_counter()
        wall_start_epoch = time.time()
        wall_start_iso = now_iso()
        results = []
        for i in range(walks):
            # 初期 RNG を seed から作る（seed が None の場合はランダム）
            rng = random.Random(seed if seed is None else (seed + i))
            initial_state = {
                "current_node": int(start_node),
                "path": [int(start_node)],
                "servers": [self.server.server_id],
                "alpha": float(alpha),
                "rng_state": serialize_rng_state(rng),
                "hops_done": 0,
            }
            # このサーバで処理を進め、必要なら他サーバへ委譲される
            res = walker.continue_from_state(initial_state)
            results.append(res)
            # 少し待つことでログ順がわかりやすくなる（任意）
            time.sleep(0.01)

        wall_end_epoch = time.time()
        wall_end_iso = now_iso()
        duration = time.perf_counter() - start_ts
        payload = {
            "walks": results,
            "metrics": {
                "server_id": self.server.server_id,
                "mode": getattr(self.server, "mode", "node"),
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
            f"[Server {self.server.server_id}] Finished processing /walk start in {duration:.3f}s; returning {len(results)} walks to controller."
        )
        self._write_json(payload)

    def _handle_continue_walk(self) -> None:
        # 他サーバから状態を受け取り、このサーバで可能な限り続行する
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return
        body = self.rfile.read(content_length)
        try:
            state = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        print(
            f"[Server {self.server.server_id}] Received continue_walk from {self.client_address} (hops_done={state.get('hops_done', 0)})"
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

        # 結果を呼び出し元に返す（finished または再委譲を経て戻ってきた結果）
        self._write_json(res)

    def log_message(self, format: str, *args) -> None:
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
        self.mode = "node"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed graph peer shard server (control-transfer)."
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
    ## ここで時間の計測を開始
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
        ## ここで終了時間を計測


if __name__ == "__main__":
    main()
