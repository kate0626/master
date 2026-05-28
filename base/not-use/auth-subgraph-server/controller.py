#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib import request as urllib_request

"""
    auth-many-serverのものと全く同じ
    python3 base/auth-many-server/controller.py --servers 2 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --start-node 1 --walks 100 --alpha 0.1 --seed 42
    
    
    [PPRコマンド]
    python3 base/auth-subgraph-server/controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.5:3000 10.58.60.11:3000 \
    --edges dataset/Louvain/graph/test.gr \
    --walks 1 \
    --alpha 0.1 \
    --seed 42 \
    --start-node-all 

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
    parser.add_argument(
        "--start-node-all",
        action="store_true",
        help="Run walks for every node listed in --subgraph-file instead of only --start-node.",
    )
    parser.add_argument(
        "--edges",
        type=str,
        default=None,
        help="Path to subgraph_index.json (required when --start-node-all is given).",
    )
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


# === 追加ここから ===
def fetch_access_stats(endpoint: str, timeout: float) -> Optional[dict]:
    """
    各リモートサーバのアクセス統計（access_stats_serverX.json）を
    HTTP経由で取得するための補助関数。

    ※ リモートサーバ側で /access_stats GET を追加している前提。
      もし /access_stats が未実装なら、この関数は失敗し、
      各サーバのJSONを手動で統合する運用でも問題ありません。
    """
    url = f"http://{endpoint.rstrip('/')}/access_stats"
    try:
        with urllib_request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[Controller] Failed to fetch stats from {endpoint}: {e}")
        return None


# === 追加ここまで ===


def load_start_nodes_from_graph(edge_path: Path) -> List[int]:

    nodes: Set[int] = set()
    with edge_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            u, v = map(int, line.split())
            nodes.add(u)
            nodes.add(v)
    return sorted(nodes)


def main() -> None:
    args = parse_arguments()
    if args.start_node_all:
        graph_path = Path(args.edges)  # 例: dataset/test.gr
        start_nodes = load_start_nodes_from_graph(graph_path)
        print(f"[Controller] start nodes from graph: {start_nodes}")
    else:
        start_nodes = [int(args.start_node)]

    walk_phase_wall_start = time.perf_counter()
    walk_phase_wall_start_epoch = time.time()
    total_walks = 0
    total_steps = 0
    server_visits = defaultdict(int)
    start_metrics: List[Dict[str, Any]] = []

    for start_node in start_nodes:
        # TODO: スタートノードのあるサーバからの開始になっているか確認
        start_server = start_node % args.servers
        endpoint = args.server_endpoints[start_server]

        payload = {
            "start_node": int(start_node),
            "alpha": float(args.alpha),
            "walks": int(args.walks),
            "seed": args.seed,
            "endpoints": args.server_endpoints,
            "server_count": args.servers,
        }

        t0 = time.perf_counter()
        res = start_walk_on_server(endpoint, payload, timeout=args.request_timeout)
        t1 = time.perf_counter()
        walks = res.get("walks", [])
        metrics = res.get("metrics", {})
        duration = metrics.get("duration_sec", res.get("duration"))
        steps_this_run = sum(len(w.get("path", [])) for w in walks)
        avg_len = steps_this_run / max(1, len(walks))

        total_walks += len(walks)
        total_steps += steps_this_run
        for w in walks:
            for s in w.get("servers", []):
                server_visits[s] += 1

        start_prefix = f"Start {start_node}: " if len(start_nodes) > 1 else ""
        print(
            f"[Controller] {start_prefix}Received {len(walks)} walks in {t1-t0:.3f}s. "
            f"Avg length: {avg_len:.3f}, total steps: {steps_this_run}"
        )
        duration_val = None
        if duration is not None:
            try:
                duration_val = float(duration)
            except (TypeError, ValueError):
                duration_val = None
            if duration_val is not None:
                print(f"[Controller] {start_prefix}duration {duration_val:.6f}s")

        start_metrics.append(
            {
                "start_node": start_node,
                "wall_duration_sec": t1 - t0,
                "server_duration_sec": duration_val,
                "walks_completed": len(walks),
                "total_steps": steps_this_run,
                "average_length": avg_len,
            }
        )

    walk_phase_wall_end = time.perf_counter()
    walk_phase_wall_end_epoch = time.time()

    if len(start_nodes) > 1:
        overall_avg = total_steps / max(1, total_walks)
        print(
            f"[Controller] Aggregated {total_walks} walks across {len(start_nodes)} start nodes. "
            f"Avg length: {overall_avg:.3f}, total steps: {total_steps}"
        )

    print("Server visit counts:")
    for sid in range(args.servers):
        print(f"  Server {sid}: {server_visits.get(sid, 0)}")

    server_duration_sum = sum(
        float(m["server_duration_sec"])
        for m in start_metrics
        if m.get("server_duration_sec") is not None
    )
    walk_phase_wall_duration = walk_phase_wall_end - walk_phase_wall_start
    avg_wall_per_start = walk_phase_wall_duration / max(1, len(start_nodes))
    print(
        "[Controller] PPR timing: "
        f"wall={walk_phase_wall_duration:.3f}s, "
        f"per_start={avg_wall_per_start:.3f}s, "
        f"server_sum={server_duration_sum:.3f}s "
        f"(start_nodes={len(start_nodes)})"
    )

    # optionally print each walk
    # for i, w in enumerate(walks):
    #     print(f"Walk[{i}] path: {w.get('path')}")
    #     print(f"Walk[{i}] servers: {w.get('servers')}")

    # === 追加ここから ===
    # 各サーバのアクセス統計を取得し、統合する
    print("\n[Controller] Collecting access statistics from all servers...")
    global_access = defaultdict(int)
    global_authorized = defaultdict(int)
    global_auth_attempts = defaultdict(int)
    global_auth_denied = defaultdict(int)
    global_transition = defaultdict(int)
    # ★ 認可時間・回数
    total_auth_time = 0.0
    total_auth_calls = 0

    for sid, endpoint in enumerate(args.server_endpoints):
        stats = fetch_access_stats(endpoint, timeout=args.request_timeout)
        if not stats:
            continue
        print(f"[Controller] Merging stats from server {sid} ({endpoint})")
        for k, v in stats.get("access", {}).items():
            global_access[k] += v
        for k, v in stats.get("authorized", {}).items():
            global_authorized[k] += v
        for k, v in stats.get("authorization_attempts", {}).items():
            global_auth_attempts[k] += v
        for k, v in stats.get("authorization_denied", {}).items():
            global_auth_denied[k] += v
        for k, v in stats.get("transition", {}).items():
            global_transition[k] += v

        # ★ 認可時間（秒）と回数を加算
        total_auth_time += float(stats.get("auth_time_total", 0.0))
        total_auth_calls += int(stats.get("auth_calls", 0))

    print(f"Total authorization time (sum over all servers): {total_auth_time:.6f} s")
    if total_auth_calls > 0:
        avg_ms = (total_auth_time / total_auth_calls) * 1000.0
        print(
            f"Average authorization time per call: {avg_ms:.3f} ms  (calls={total_auth_calls})"
        )
    else:
        print("No authorization calls recorded.")

    total_attempts = sum(global_auth_attempts.values())
    total_denied = sum(global_auth_denied.values())
    failure_rates = {}
    for entity, attempts in global_auth_attempts.items():
        if attempts <= 0:
            continue
        failures = global_auth_denied.get(entity, 0)
        failure_rates[entity] = failures / attempts if attempts else 0.0

    if total_attempts:
        failure_rate = total_denied / total_attempts
        print(
            f"[Controller] Authorization totals: attempts={total_attempts}, "
            f"failures={total_denied} ({failure_rate:.2%})"
        )
        sorted_entities = sorted(
            failure_rates.items(), key=lambda kv: kv[1], reverse=True
        )
        if sorted_entities:
            print("[Controller] Failure rate per entity (降順、全件):")
            for entity, rate in sorted_entities:
                attempts = global_auth_attempts.get(entity, 0)
                failures = global_auth_denied.get(entity, 0)
                print(f"  {entity}: {failures}/{attempts} failures ({rate:.2%})")

    # 結果をファイル保存
    output_filename = f"{args.walks}_{args.alpha}_global_transition.json"
    aggregate_metrics = {
        "start_nodes": start_nodes,
        "total_walks": total_walks,
        "total_steps": total_steps,
        "average_length": (total_steps / max(1, total_walks)),
        "start_node_count": len(start_nodes),
    }

    timing_summary = {
        "start_node_count": len(start_nodes),
        "walk_phase_wall_time_sec": walk_phase_wall_duration,
        "walk_phase_wall_start_epoch": walk_phase_wall_start_epoch,
        "walk_phase_wall_end_epoch": walk_phase_wall_end_epoch,
        "per_start_wall_time_sec": avg_wall_per_start,
        "server_duration_sum_sec": server_duration_sum,
    }

    out = {
        "access": dict(global_access),
        "authorized": dict(global_authorized),
        "authorization_attempts": dict(global_auth_attempts),
        "authorization_denied": dict(global_auth_denied),
        "authorization_failure_rate": {k: v for k, v in failure_rates.items()},
        "transition": dict(global_transition),
        # ★ 認可時間の集計
        "auth_time_total": total_auth_time,
        "auth_calls": total_auth_calls,
        "start_node_metrics": start_metrics,
        "aggregate_metrics": aggregate_metrics,
        "timing_summary": timing_summary,
    }

    total_visits = sum(global_access.values())
    if total_visits > 0:
        ppr_scores = {
            entity: count / total_visits for entity, count in global_access.items()
        }
        top_ppr = sorted(ppr_scores.items(), key=lambda kv: kv[1], reverse=True)[
            : min(10, len(ppr_scores))
        ]
        print(
            f"[Controller] PPR computed for {len(ppr_scores)} entities (total visits={total_visits})"
        )
        if top_ppr:
            print("[Controller] Top PPR entities:")
            for entity, score in top_ppr:
                visits = global_access.get(entity, 0)
                print(f"  {entity}: PPR={score:.6f} (visits={visits})")
    else:
        ppr_scores = {}
        print("[Controller] No visits recorded; PPR scores unavailable.")
    out["total_visits"] = total_visits
    out["ppr_scores"] = ppr_scores
    out_path = Path(output_filename)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[Controller] Saved aggregated transition stats (with PPR) to {out_path}")
    # === 追加ここまで ===


if __name__ == "__main__":
    main()
