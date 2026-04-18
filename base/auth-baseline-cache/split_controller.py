#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union
from urllib import request as urllib_request

NodeOrEdgeId = Union[int, str]
"""
echo "=== [START_NODE] ${start_node} ==="
python3 base/auth-cache/split_controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
    --start-node 0 \
    --walks 10 \
    --alpha 0.1 \
    --seed 42 
    --node-to-starts-file "base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts.json"
    コマンドの一番下の部分が変わった瞬間エラーになる
"""


def parse_entity_id(raw) -> NodeOrEdgeId:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.startswith("edge_"):
        return raw
    try:
        return int(raw)
    except Exception:
        return str(raw)


def load_node_to_starts_table(path: Path) -> Dict[NodeOrEdgeId, Set[int]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[NodeOrEdgeId, Set[int]] = {}
    for k, values in raw.items():
        entity = parse_entity_id(k)
        starts: Set[int] = set()
        for v in values:
            try:
                starts.add(int(v))
            except Exception:
                continue
        out[entity] = starts
    return out


def build_owner_map_from_sibling_node_to_starts_files(
    base_path: Path,
) -> Dict[str, int]:
    """
    remote_server と同じ：同ディレクトリの node_to_starts_serverX.json を全部読んで
    「そのキー(entity)は server X が所有」とする owner_map を作る。
    """
    dir_path = base_path.parent
    owner_map: Dict[str, int] = {}
    pat = re.compile(r"node_to_starts_server(\d+)\.json$")

    for p in sorted(dir_path.glob("node_to_starts_server*.json")):
        m = pat.search(p.name)
        if not m:
            continue
        sid = int(m.group(1))
        tbl = load_node_to_starts_table(p)
        for ent in tbl.keys():
            owner_map[str(ent)] = sid
    return owner_map


def resolve_node_to_starts_path(base_path: Path, server_id: int) -> Path:
    """
    remote_server と同じ：node_to_starts_serverX.json / node_to_startsX.json を優先
    """
    stem = base_path.stem
    suffix = base_path.suffix
    candidates = [
        base_path.with_name(f"{stem}_server{server_id}{suffix}"),
        base_path.with_name(f"{stem}{server_id}{suffix}"),
        base_path,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"node_to_starts file not found. Tried: {[str(c) for c in candidates]}"
    )


def parse_arguments() -> argparse.Namespace:
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

    # remote_server と同じ node_to_starts を参照して開始サーバを正しく決めたい
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        default=None,
        help="(Optional) base node_to_starts.json path. If set, controller chooses start server using owner_map built from node_to_starts_serverX.json files.",
    )
    # デバッグ用に開始サーバ固定もできる
    parser.add_argument(
        "--force-start-server",
        type=int,
        default=None,
        help="(Optional) If set, always send /walk to this server id.",
    )

    # ★追加：集計結果の保存先
    parser.add_argument(
        "--out-dir",
        type=str,
        default=".",
        help="Output directory to save aggregated stats (default: current dir).",
    )
    # ★追加：ファイル名プレフィックス（複数start_nodeを回すときに便利）
    parser.add_argument(
        "--out-prefix",
        type=str,
        default=None,
        help="(Optional) Prefix for output filename. If omitted, auto-generated from params.",
    )
    parser.add_argument(
        "--cache-policy",
        type=str,
        default="unknown",
        help="Cache policy label to embed in output metadata/filenames.",
    )
    parser.add_argument(
        "--cache-capacity",
        type=int,
        default=None,
        help="Cache capacity label to embed in output metadata/filenames.",
    )
    return parser.parse_args()


def post_json(endpoint: str, path: str, payload: dict, timeout: float) -> dict:
    url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
    url = url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_json(endpoint: str, path: str, timeout: float) -> dict:
    url = endpoint if endpoint.startswith("http") else f"http://{endpoint}"
    url = url.rstrip("/") + path
    with urllib_request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def start_walk_on_server(endpoint: str, payload: dict, timeout: float) -> dict:
    return post_json(endpoint, "/walk", payload, timeout=timeout)


def fetch_access_stats(endpoint: str, timeout: float) -> Optional[dict]:
    try:
        return get_json(endpoint, "/access_stats", timeout=timeout)
    except Exception as e:
        print(f"[Controller] Failed to fetch stats from {endpoint}: {e}")
        return None


# def pick_start_server(
#     start_node: int,
#     servers: int,
#     endpoints: list[str],
#     node_to_starts_file: Optional[str],
#     force_start_server: Optional[int],
# ) -> int:
#     if force_start_server is not None:
#         if force_start_server < 0 or force_start_server >= servers:
#             raise ValueError(
#                 f"--force-start-server must be in [0, {servers-1}] but got {force_start_server}"
#             )
#         return force_start_server

#     if node_to_starts_file:
#         base = Path(node_to_starts_file)
#         if base.exists() or base.parent.exists():
#             owner_map = build_owner_map_from_sibling_node_to_starts_files(base)
#             sid = owner_map.get(str(start_node))
#             if sid is not None:
#                 if sid < 0 or sid >= servers:
#                     raise ValueError(
#                         f"owner_map says start_node {start_node} owned by server {sid}, but servers={servers}"
#                     )
#                 return sid


#     # フォールバック：0に投げるのが安全
#     return 0
def pick_start_server(
    start_node: int,
    servers: int,
    endpoints: list[str],
    node_to_starts_file: Optional[str],
    force_start_server: Optional[int],
) -> int:
    # 強制指定があればそれを優先（デバッグ用）
    if force_start_server is not None:
        if force_start_server < 0 or force_start_server >= servers:
            raise ValueError(
                f"--force-start-server must be in [0, {servers-1}] but got {force_start_server}"
            )
        return force_start_server
    # それ以外は常に server0
    return 0


# キャッシュをサーバ終了以外にリセットする関数
def reset_cache(endpoint: str, timeout: float) -> bool:

    try:
        _ = post_json(endpoint, "/cache/reset", payload={}, timeout=timeout)
        return True
    except Exception as e:
        print(f"[Controller] Failed to reset cache on {endpoint}: {e}")
        return False


def main() -> None:
    args = parse_arguments()

    if args.servers <= 0:
        raise ValueError("--servers must be positive")
    if len(args.server_endpoints) != args.servers:
        raise ValueError(
            f"len(--server-endpoints) must equal --servers. "
            f"servers={args.servers}, endpoints={len(args.server_endpoints)}"
        )

    start_server = pick_start_server(
        start_node=int(args.start_node),
        servers=int(args.servers),
        endpoints=args.server_endpoints,
        node_to_starts_file=args.node_to_starts_file,
        force_start_server=args.force_start_server,
    )
    endpoint = args.server_endpoints[start_server]

    payload = {
        "start_node": int(args.start_node),
        "alpha": float(args.alpha),
        "walks": int(args.walks),
        "seed": args.seed,
        "endpoints": args.server_endpoints,
        "server_count": int(args.servers),
    }

    print(f"[Controller] start_server={start_server}, endpoint={endpoint}")
    res = start_walk_on_server(endpoint, payload, timeout=args.request_timeout)

    walks = res.get("walks", [])
    metrics = res.get("metrics", {})
    duration = metrics.get("duration_sec", res.get("duration"))

    total_steps = sum(len(w.get("path", [])) for w in walks)
    avg_len = total_steps / max(1, len(walks))
    print(f"Avg length: {avg_len:.3f}, total steps: {total_steps}")
    if duration is not None:
        print(f"[Controller] duration {float(duration):.6f}s")

    # サーバー訪問回数のカウント
    server_visits = defaultdict(int)
    for w in walks:
        for s in w.get("servers", []):
            server_visits[int(s)] += 1
    print("Server visit counts:")
    for sid in range(args.servers):
        print(f"  Server {sid}: {server_visits.get(sid, 0)}")

    # キャッシュのリセットを行う
    print("[Controller] Resetting caches on all servers...")
    for ep in args.server_endpoints:
        reset_cache(ep, timeout=args.request_timeout)

    # 統計統合 + memory（RSS/テーブル規模）も回収
    print("\n[Controller] Collecting access statistics from all servers...")
    global_access = defaultdict(int)
    global_authorized = defaultdict(int)
    global_auth_attempts = defaultdict(int)
    global_auth_denied = defaultdict(int)
    global_transition = defaultdict(int)

    total_auth_time = 0.0
    total_auth_calls = 0
    total_walk_time = 0.0
    total_walk_calls = 0
    total_cache_hit = 0
    total_cache_miss = 0
    total_cache_size = 0  # これは合計/平均どっちでもOK

    per_server_stats: list[dict] = []

    for sid, ep in enumerate(args.server_endpoints):
        stats = fetch_access_stats(ep, timeout=args.request_timeout)
        if not stats:
            continue
        print(f"[Controller] Merging stats from server {sid} ({ep})")
        per_server_stats.append({"server_id": sid, "endpoint": ep, "stats": stats})

        for k, v in stats.get("access", {}).items():
            global_access[k] += int(v)
        for k, v in stats.get("authorized", {}).items():
            global_authorized[k] += int(v)
        for k, v in stats.get("authorization_attempts", {}).items():
            global_auth_attempts[k] += int(v)
        for k, v in stats.get("authorization_denied", {}).items():
            global_auth_denied[k] += int(v)
        for k, v in stats.get("transition", {}).items():
            global_transition[k] += int(v)

        total_auth_time += float(stats.get("auth_time_total", 0.0))
        total_auth_calls += int(stats.get("auth_calls", 0))
        total_walk_time += float(stats.get("walk_time_total", 0.0))
        total_walk_calls += int(stats.get("walk_calls", 0))
        total_cache_hit += int(stats.get("auth_cache_hit", 0))
        total_cache_miss += int(stats.get("auth_cache_miss", 0))
        total_cache_size += int(stats.get("auth_cache_size", 0))

    print(f"Total authorization time (sum over all servers): {total_auth_time:.6f} s")
    print(f"Total authorization calls (sum over all servers): {total_auth_calls}")
    print(f"Total walk time (sum over all servers): {total_walk_time:.6f} s")
    print(f"Total walk calls (sum over all servers): {total_walk_calls}")

    if total_auth_calls > 0:
        avg_ms = (total_auth_time / total_auth_calls) * 1000.0
        print(
            f"Average authorization time per call: {avg_ms:.3f} ms (calls={total_auth_calls})"
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

    # キャッシュの集計
    total_cache_lookups = total_cache_hit + total_cache_miss
    hit_rate = 0.0
    if total_cache_lookups > 0:
        hit_rate = total_cache_hit / total_cache_lookups
        print(f"Total auth cache lookups: {total_cache_lookups}")
        print(
            f"Auth cache hit: {total_cache_hit}, miss: {total_cache_miss}, hit_rate: {hit_rate:.3f}"
        )
    else:
        print("No auth cache stats recorded.")

    # 出力
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.out_prefix:
        prefix = args.out_prefix
    else:
        # 例: start=12_walks=10_alpha=0.1_seed=42_cache=lru_cap=100
        seed_str = "none" if args.seed is None else str(args.seed)
        cache_policy = str(args.cache_policy)
        cache_capacity = (
            "na" if args.cache_capacity is None else str(int(args.cache_capacity))
        )
        prefix = (
            f"start={int(args.start_node)}_walks={int(args.walks)}"
            f"_alpha={float(args.alpha)}_seed={seed_str}"
            f"_cache={cache_policy}_cap={cache_capacity}"
        )

    output_filename = f"{prefix}_global_transition.json"

    out = {
        "access": dict(global_access),
        "authorized": dict(global_authorized),
        "authorization_attempts": dict(global_auth_attempts),
        "authorization_denied": dict(global_auth_denied),
        "authorization_failure_rate": {k: v for k, v in failure_rates.items()},
        "transition": dict(global_transition),
        "auth_time_total": total_auth_time,
        "auth_calls": total_auth_calls,
        "walk_time_total": total_walk_time,
        "walk_calls": total_walk_calls,
        "controller": {
            "start_node": int(args.start_node),
            "start_server": int(start_server),
            "servers": int(args.servers),
            "alpha": float(args.alpha),
            "walks": int(args.walks),
            "seed": args.seed,
            "server_endpoints": list(args.server_endpoints),
            "cache_policy": str(args.cache_policy),
            "cache_capacity": args.cache_capacity,
        },
        # ★追加：サーバごとの /access_stats 生データ（memory含む）
        "per_server_access_stats": per_server_stats,
        "cache hit": total_cache_hit,
        "cache miss": total_cache_miss,
        "cache rate": hit_rate,
    }

    out_path = out_dir / output_filename
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[Controller] Saved aggregated transition stats to {out_path}")

    # ★追加：メモリだけ見やすいサマリJSONも別出力（RSS, table sizes）
    mem_summary = []
    for item in per_server_stats:
        sid = item["server_id"]
        ep = item["endpoint"]
        stats = item["stats"]
        mem = stats.get("memory", {})
        mem_summary.append(
            {
                "server_id": sid,
                "endpoint": ep,
                **mem,
            }
        )

    mem_path = out_dir / f"{prefix}_memory_summary.json"
    mem_path.write_text(json.dumps(mem_summary, indent=2), encoding="utf-8")
    print(f"[Controller] Saved memory summary to {mem_path}")


if __name__ == "__main__":
    main()
