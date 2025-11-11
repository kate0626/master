#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import List, Optional
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from typing import Sequence, Tuple


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


def parse_arguments() -> argparse.Namespace:
    """
    --edges: グラフファイルのパス（今はサーバ側で使用）
    --servers: サーバの総数
    --alpha: ランダムウォークを止める確率
    --start-node: スタートノード
    --walks: 何回ウォークするか
    --mode: "remote"または"local"
    --server-endpoints: サーバのアドレス一覧
    """
    parser = argparse.ArgumentParser(description="Distributed random walk controller.")
    parser.add_argument(
        "--edges",
        default="./../../dataset/Louvain/graph/karate.gr",
        type=str,
        help="Path to the edge list file (used for partitioning decisions locally if needed).",
    )
    parser.add_argument(
        "--servers",
        default=2,
        type=int,
        help="Number of servers to partition the graph across.",
    )
    parser.add_argument(
        "--alpha",
        default=0.1,
        type=float,
        help="Stopping probability for the random walk (0 < alpha < 1).",
    )
    parser.add_argument(
        "--start-node",
        default=1,
        type=int,
        help="Node id to start each random walk from.",
    )
    parser.add_argument(
        "--walks",
        default=1,
        type=int,
        help="Number of random walks to request from server.",
    )
    parser.add_argument(
        "--seed",
        default=None,
        type=int,
        help="Optional random seed for reproducibility (passed to server).",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "remote"),
        default="remote",
        help="Execution mode. 'remote' sends /walk to a shard server.",
    )
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        help="Endpoints for remote graph servers in order (e.g., host1:8000 host2:8001). Required in remote mode.",
    )
    parser.add_argument(
        "--request-timeout",
        default=10.0,
        type=float,
        help="HTTP request timeout in seconds when contacting remote servers.",
    )
    return parser.parse_args()


## 始点を持つサーバにRWの開始を依頼
def start_remote_walk(
    endpoints: List[str],
    server_count: int,
    start_node: int,
    alpha: float,
    walks: int,
    seed: Optional[int],
    timeout: float,
) -> dict:
    # Simple modulo partitioner logic: node -> server_id
    start_server = start_node % server_count
    endpoint = endpoints[start_server]
    url = f"http://{endpoint.rstrip('/')}/walk"

    payload = {
        "start_node": start_node,
        "alpha": alpha,
        "walks": walks,
        "seed": seed,
        "server_count": server_count,
        "endpoints": endpoints,  # server-side may need the list to contact other shards
    }
    data = json.dumps(payload).encode("utf-8")
    print("start RW from controller")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )

    with urllib_request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


def main() -> None:
    args = parse_arguments()

    start_time = time.perf_counter()

    if args.mode == "remote":
        if not args.server_endpoints:
            raise ValueError("Remote mode requires --server-endpoints")
        # send single POST to the server that owns start_node
        result = start_remote_walk(
            endpoints=args.server_endpoints,
            server_count=args.servers,
            start_node=args.start_node,
            alpha=args.alpha,
            walks=args.walks,
            seed=args.seed,
            timeout=args.request_timeout,
        )

        elapsed = time.perf_counter() - start_time
        ## 以下で結果の出力を行う
        # result expected: { "walks": [ { "path": [...], "servers":[...] }, ... ] }
        walks = result.get("walks", [])
        total_steps = sum(len(w["path"]) for w in walks)
        average_length = total_steps / max(1, len(walks))

        print(f"Requested {len(walks)} walks from server.")
        print(f"Average walk length: {average_length:.3f}")
        print(f"Total steps taken: {total_steps}")
        print(f"Completed in: {elapsed:.6f}s")

        # server visit counts across all walks
        server_visits = defaultdict(int)
        for w in walks:
            for sid in w.get("servers", []):
                server_visits[sid] += 1

        print("Server visit counts:")
        for sid in range(args.servers):
            print(f"  Server {sid}: {server_visits.get(sid, 0)}")

        # Optionally print each path (comment out if too verbose)
        for i, w in enumerate(walks):
            print(f"Walk[{i}] path: {w.get('path')}")
            print(f"Walk[{i}] servers: {w.get('servers')}")
    else:
        raise NotImplementedError(
            "Local mode not implemented in this controller (use previous local code)."
        )


if __name__ == "__main__":
    main()
