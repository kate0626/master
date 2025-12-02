#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib import request as urllib_request

"""
    2台
    python3 base/auth-many-server/controller.py --servers 2 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --start-node 1 --walks 100 --alpha 0.1 --seed 42
    1台
    python3 base/auth-many-server/controller_ppr.py \
    --servers 1 \
    --server-endpoints 10.58.60.5:3000 \
    --walks 100 \
    --alpha 0.1 \
    --node-to-starts-file  base/auth-many-server/node_to_starts.json
"""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PPR controller that runs random walks from every permitted start node."
    )
    parser.add_argument("--servers", type=int, default=2, help="Number of servers.")
    parser.add_argument(
        "--alpha", type=float, default=0.1, help="Stopping probability."
    )
    parser.add_argument(
        "--walks", type=int, default=1, help="Number of walks per start."
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional base seed.")
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for servers in order (host:port).",
    )
    parser.add_argument(
        "--request-timeout", type=float, default=10.0, help="HTTP timeout seconds."
    )
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        required=True,
        help="Path to node_to_starts.json (defines which start nodes exist).",
    )
    parser.add_argument(
        "--start-node",
        type=int,
        default=None,
        help="If specified, run PPR only from this start node instead of all nodes in node-to-starts file.",
    )
    return parser.parse_args()


def start_walk_on_server(endpoint: str, payload: dict, timeout: float) -> dict:
    url = f"http://{endpoint.rstrip('/')}/walk"
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_access_stats(endpoint: str, timeout: float) -> Optional[dict]:
    url = f"http://{endpoint.rstrip('/')}/access_stats"
    try:
        with urllib_request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # pragma: no cover - network failures are informational
        print(f"[ControllerPPR] Failed to fetch stats from {endpoint}: {exc}")
        return None


def load_start_nodes(mapping_path: Path) -> List[int]:
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    nodes_set = set()
    for starts in data.values():
        if isinstance(starts, Sequence):
            for val in starts:
                try:
                    nodes_set.add(int(val))
                except (TypeError, ValueError):
                    continue
    nodes = sorted(nodes_set)
    return nodes


def main() -> None:
    args = parse_arguments()
    mapping_path = Path(args.node_to_starts_file)
    if not mapping_path.exists():
        raise SystemExit(f"node_to_starts file not found: {mapping_path}")

    if args.start_node is not None:
        start_nodes = [int(args.start_node)]
    else:
        start_nodes = load_start_nodes(mapping_path)
        if not start_nodes:
            raise SystemExit(
                f"No valid start nodes were found in node_to_starts file: {mapping_path}"
            )

    total_walks = 0
    total_steps = 0
    server_visits = defaultdict(int)
    per_start_metrics: List[Dict[str, Any]] = []

    for start_node in start_nodes:
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

        wall_t0 = time.perf_counter()
        res = start_walk_on_server(endpoint, payload, timeout=args.request_timeout)
        wall_t1 = time.perf_counter()
        walks = res.get("walks", [])
        metrics = res.get("metrics", {})
        srv_duration = metrics.get("duration_sec", res.get("duration"))
        steps_this_run = sum(len(w.get("path", [])) for w in walks)
        avg_len = steps_this_run / max(1, len(walks))

        print(
            f"[ControllerPPR] Start {start_node}: {len(walks)} walks in {wall_t1-wall_t0:.3f}s "
            f"(server {srv_duration if srv_duration is not None else 'N/A'}s) avg_len={avg_len:.3f} steps={steps_this_run}"
        )

        total_walks += len(walks)
        total_steps += steps_this_run
        for w in walks:
            for sid in w.get("servers", []):
                server_visits[sid] += 1

        srv_dur_val = None
        if srv_duration is not None:
            try:
                srv_dur_val = float(srv_duration)
            except (TypeError, ValueError):
                srv_dur_val = None

        per_start_metrics.append(
            {
                "start_node": start_node,
                "wall_duration_sec": wall_t1 - wall_t0,
                "server_duration_sec": srv_dur_val,
                "walks_completed": len(walks),
                "total_steps": steps_this_run,
                "average_length": avg_len,
            }
        )

    if len(start_nodes) > 1:
        avg_all = total_steps / max(1, total_walks)
        print(
            f"[ControllerPPR] Aggregated {total_walks} walks across {len(start_nodes)} start nodes "
            f"(avg length {avg_all:.3f}, total steps {total_steps})"
        )

    print("Server visit counts:")
    for sid in range(args.servers):
        print(f"  Server {sid}: {server_visits.get(sid, 0)}")

    # collect aggregated stats from each server
    global_access = defaultdict(int)
    global_authorized = defaultdict(int)
    global_attempts = defaultdict(int)
    global_denied = defaultdict(int)
    global_transition = defaultdict(int)
    total_auth_time = 0.0
    total_auth_calls = 0

    for sid, endpoint in enumerate(args.server_endpoints):
        stats = fetch_access_stats(endpoint, timeout=args.request_timeout)
        if not stats:
            continue
        print(f"[ControllerPPR] Merging stats from server {sid} ({endpoint})")
        for k, v in stats.get("access", {}).items():
            global_access[k] += v
        for k, v in stats.get("authorized", {}).items():
            global_authorized[k] += v
        for k, v in stats.get("authorization_attempts", {}).items():
            global_attempts[k] += v
        for k, v in stats.get("authorization_denied", {}).items():
            global_denied[k] += v
        for k, v in stats.get("transition", {}).items():
            global_transition[k] += v
        total_auth_time += float(stats.get("auth_time_total", 0.0))
        total_auth_calls += int(stats.get("auth_calls", 0))

    print(
        f"Total authorization time: {total_auth_time:.6f}s over {total_auth_calls} calls"
    )

    failure_rates = {}
    for entity, attempts in global_attempts.items():
        if attempts <= 0:
            continue
        failures = global_denied.get(entity, 0)
        failure_rates[entity] = failures / attempts if attempts else 0.0

    # ファイルへの保存
    output_filename = f"PPR_{args.walks}_{args.alpha}_global_transition.json"
    aggregate_metrics = {
        "start_nodes": start_nodes,
        "start_node_count": len(start_nodes),
        "total_walks": total_walks,
        "total_steps": total_steps,
        "average_length": total_steps / max(1, total_walks),
    }
    out = {
        "access": dict(global_access),
        "authorized": dict(global_authorized),
        "authorization_attempts": dict(global_attempts),
        "authorization_denied": dict(global_denied),
        "authorization_failure_rate": {k: v for k, v in failure_rates.items()},
        "transition": dict(global_transition),
        "auth_time_total": total_auth_time,
        "auth_calls": total_auth_calls,
        "start_node_metrics": per_start_metrics,
        "aggregate_metrics": aggregate_metrics,
    }

    total_visits = sum(global_access.values())
    if total_visits > 0:
        ppr_scores = {
            entity: count / total_visits for entity, count in global_access.items()
        }
        out["total_visits"] = total_visits
        out["ppr_scores"] = ppr_scores
        print(
            f"[ControllerPPR] Computed PPR for {len(ppr_scores)} entities (total visits={total_visits})"
        )
    else:
        out["total_visits"] = 0
        out["ppr_scores"] = {}
        print("[ControllerPPR] No visits recorded; cannot derive PPR.")

    out_path = Path(output_filename)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[ControllerPPR] Saved aggregated transition stats with PPR to {out_path}")


if __name__ == "__main__":
    main()
