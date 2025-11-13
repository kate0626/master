#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import List, Optional
from urllib import request as urllib_request

"""
    python3 base/auth-many-server/controller.py --servers 2 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --start-node 1 --walks 3 --alpha 0.5 --seed 42
"""


def parse_arguments() -> argparse.Namespace:
    import argparse

    parser = argparse.ArgumentParser(
        description="Distributed random walk controller (peer-transfer model)."
    )
    parser.add_argument("--servers", default=2, type=int, help="Number of servers.")
    parser.add_argument(
        "--alpha", default=0.1, type=float, help="Stopping probability."
    )
    parser.add_argument("--start-node", default=1, type=int, help="Start node.")
    parser.add_argument("--walks", default=1, type=int, help="Number of walks.")
    parser.add_argument("--seed", default=None, type=int, help="Optional seed.")
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for servers in order (host:port).",
    )
    parser.add_argument(
        "--request-timeout", default=10.0, type=float, help="HTTP timeout seconds."
    )
    return parser.parse_args()


def start_walk_on_server(endpoint: str, payload: dict, timeout: float) -> dict:
    """
    URLを http://{endpoint}/walk で作成
    （例：localhost:8000 → http://localhost:8000/walk）

    payload（辞書）をJSON形式でエンコードしてPOST送信。

    サーバの返答を受け取ってJSONデコードし、Pythonの辞書にして返す。
    """
    url = f"http://{endpoint.rstrip('/')}/walk"
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> None:
    args = parse_arguments()
    # TODO: スタートノードのあるサーバからの開始になっているか確認
    start_server = args.start_node % args.servers
    endpoint = args.server_endpoints[start_server]

    payload = {
        "start_node": int(args.start_node),
        "alpha": float(args.alpha),
        "walks": int(args.walks),
        "seed": args.seed,
        "endpoints": args.server_endpoints,
        "server_count": args.servers,
    }

    t0 = time.perf_counter()
    print(
        f"[Controller] Sending /walk to server {start_server} ({endpoint}) with payload: start_node={args.start_node}, alpha={args.alpha}, walks={args.walks}"
    )
    # リクエスト送信
    res = start_walk_on_server(endpoint, payload, timeout=args.request_timeout)
    t1 = time.perf_counter()
    walks = res.get("walks", [])
    duration = res.get("duration")
    total_steps = sum(len(w.get("path", [])) for w in walks)
    avg_len = total_steps / max(1, len(walks))
    print(
        f"[Controller] Received {len(walks)} walks in {t1-t0:.3f}s. Avg length: {avg_len:.3f}, total steps: {total_steps}"
    )
    if duration is not None:
        try:
            duration_val = float(duration)
        except (TypeError, ValueError):
            duration_val = None
        if duration_val is not None:
            print(f"[Controller] duration {duration_val:.6f}")

    # サーバー訪問回数のカウント
    server_visits = defaultdict(int)
    for w in walks:
        for s in w.get("servers", []):
            server_visits[s] += 1
    print("Server visit counts:")
    for sid in range(args.servers):
        print(f"  Server {sid}: {server_visits.get(sid, 0)}")

    # optionally print each walk
    for i, w in enumerate(walks):
        print(f"Walk[{i}] path: {w.get('path')}")
        print(f"Walk[{i}] servers: {w.get('servers')}")


if __name__ == "__main__":
    main()
