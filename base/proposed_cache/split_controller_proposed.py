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
    # 提案手法用 prefetch パラメータ
    parser.add_argument(
        "--prefetch-mode",
        choices=["none", "bfs_prefetch", "bfs_score"],
        default="none",
        help="提案手法のプリフェッチモード。bfs_prefetch=BFS K-hop球, bfs_score=BFS×freq上位N",
    )
    parser.add_argument(
        "--prefetch-k",
        type=int,
        default=10,
        help="bfs_prefetch の BFS 距離 K",
    )
    parser.add_argument(
        "--prefetch-capacity",
        type=int,
        default=100,
        help="bfs_score の上位 N (= cache 容量に相当)",
    )
    parser.add_argument(
        "--prefetch-decay",
        type=float,
        default=0.7,
        help="bfs_score の距離減衰率γ (manual モード時に使用)",
    )
    parser.add_argument(
        "--prefetch-decay-mode",
        choices=["manual", "data_fit"],
        default="manual",
        help="γ の決定モード。"
        "manual=CLI 値を使う / "
        "data_fit=baseline JSON の BFS 距離別 attempts 分布を指数フィットして γ を自動推定",
    )
    parser.add_argument(
        "--prefetch-attempts-source",
        type=str,
        default=None,
        help="bfs_score 用の頻度ヒント JSON (start_node ごとの attempts dict)。"
        "data_fit モードでは BFS 距離推定にも使う。",
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
        resp = post_json(endpoint, "/cache/reset", payload={}, timeout=timeout)
        print(f"[Controller] Reset OK {endpoint}: policy={resp.get('policy')}, capacity={resp.get('capacity')}")
        return True
    except Exception as e:
        print(f"[Controller] *** RESET FAILED {endpoint}: {e} ***")
        return False


def prefetch_cache(
    endpoint: str,
    mode: str,
    start_node: int,
    K: int,
    capacity: int,
    decay: float,
    attempts: Optional[dict],
    timeout: float,
) -> Optional[dict]:
    """提案手法: /cache/prefetch を呼ぶ。返り値に round_trip_sec (controller 側測定) を含む。"""
    import time as _time

    payload = {
        "start_node": int(start_node),
        "mode": mode,
        "K": int(K),
        "capacity": int(capacity),
        "decay": float(decay),
        "attempts": attempts or {},
    }
    t0 = _time.perf_counter()
    try:
        resp = post_json(endpoint, "/cache/prefetch", payload=payload, timeout=timeout)
        rtt = _time.perf_counter() - t0
        resp = dict(resp) if isinstance(resp, dict) else {"raw": resp}
        resp["round_trip_sec"] = rtt  # controller 側で測定した端から端までの時間
        resp["endpoint"] = endpoint
        print(
            f"[Controller] Prefetch OK {endpoint}: mode={resp.get('mode')} "
            f"selected={resp.get('selected')} inserted={resp.get('inserted')} "
            f"server_build={resp.get('build_time_sec', 0):.3f}s "
            f"round_trip={rtt:.3f}s"
        )
        return resp
    except Exception as e:
        rtt = _time.perf_counter() - t0
        print(
            f"[Controller] *** PREFETCH FAILED {endpoint}: {e} "
            f"(after {rtt:.3f}s) ***"
        )
        return {"endpoint": endpoint, "round_trip_sec": rtt, "error": str(e)}


def _resolve_baseline_json(path: Optional[str], start_node: int) -> Optional[Path]:
    """attempts hint パスを start_node ごとの JSON ファイルに解決する。
    ディレクトリ指定なら start={N}_*.json を探す。"""
    if not path:
        return None
    p = Path(path)
    if p.is_dir():
        cands = list(p.glob(f"start={start_node}_*_global_transition.json"))
        if not cands:
            return None
        return cands[0]
    return p if p.exists() else None


def estimate_decay_from_baseline(path: Optional[str], start_node: int) -> Optional[float]:
    """
    baseline transition.json から BFS 距離別 attempts 分布を取り出し、
    指数 log(attempts(d)) = log(A) + d * log(γ) で γ を推定する。

    手順:
      1. JSON 読み込み (`authorization_attempts`, `transition`)
      2. `transition` の "src->edge_a_b" キーから無向隣接リストを構築
      3. start_node から BFS で各ノードの距離 d を求める
      4. 各 d について Σ attempts(v) を集計
      5. log-空間で線形回帰 → 傾き = log(γ) を得る → γ を返す

    返り値: 推定 γ (0,1) の値。失敗時 None。
    """
    import math
    import re as _re
    from collections import defaultdict, deque

    jf = _resolve_baseline_json(path, start_node)
    if jf is None:
        print(f"[Controller] data_fit: baseline JSON not found ({path}, start={start_node})")
        return None
    try:
        d = json.loads(jf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Controller] data_fit: parse failed {jf}: {e}")
        return None

    attempts = d.get("authorization_attempts", {})
    tr = d.get("transition", {})
    if not attempts or not tr:
        print(f"[Controller] data_fit: missing attempts/transition in {jf.name}")
        return None

    # 隣接リスト構築
    edge_pat = _re.compile(r"^(.+?)->edge_(.+?)_(.+)$")
    adj: Dict[str, Set[str]] = defaultdict(set)
    for key in tr:
        m = edge_pat.match(key)
        if not m:
            continue
        src, a, b = m.group(1), m.group(2), m.group(3)
        dst = b if src == a else a
        adj[src].add(dst)
        adj[dst].add(src)

    # BFS from start_node
    start = str(start_node)
    if start not in adj and start not in attempts:
        print(f"[Controller] data_fit: start_node {start} not in adjacency")
        return None
    dist: Dict[str, int] = {start: 0}
    q = deque([start])
    while q:
        u = q.popleft()
        for w in adj.get(u, ()):
            if w not in dist:
                dist[w] = dist[u] + 1
                q.append(w)

    # 距離別 attempts 合計
    sum_by_d: Dict[int, int] = defaultdict(int)
    for v, c in attempts.items():
        v_str = str(v)
        if v_str in dist:
            sum_by_d[dist[v_str]] += int(c)

    # 線形回帰 (log y vs x), d >= 1 のみ (start ノードは bias になる)
    xs = []
    ys = []
    for d_, s in sum_by_d.items():
        if d_ <= 0 or s <= 0:
            continue
        xs.append(d_)
        ys.append(math.log(s))
    if len(xs) < 3:
        print(
            f"[Controller] data_fit: too few BFS-distance buckets "
            f"(start={start}, n={len(xs)})"
        )
        return None

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den  # = log(γ)
    gamma = math.exp(slope)
    # 安全範囲にクリップ (0,1) でないと意味なし
    gamma = max(0.05, min(0.999, gamma))
    print(
        f"[Controller] data_fit: γ estimated = {gamma:.4f}  "
        f"(n_buckets={n}, distances={sorted(sum_by_d.keys())[:5]}...)"
    )
    return gamma


def load_attempts_hint(path: Optional[str], start_node: int) -> dict:
    """bfs_score 用の頻度ヒントを JSON から読み込む。

    対応フォーマット:
      (A) 既存 baseline の transition.json (`authorization_attempts` キーを持つ)
          → そのまま attempts dict を取り出す
      (B) {"<start_node>": {"<entity>": count, ...}, ...}
          → start_node 部分を取り出す
      (C) {"<entity>": count, ...}
          → そのまま使う

    path がディレクトリの場合は start_node に対応する JSON を自動探索。
      e.g.  start=0_walks=100_alpha=0.01_seed=42_cache=none_cap=100_global_transition.json
    """
    if not path:
        return {}
    p = Path(path)
    # ディレクトリ指定なら start_node 用ファイルを探す
    if p.is_dir():
        cands = list(p.glob(f"start={start_node}_*_global_transition.json"))
        if not cands:
            print(f"[Controller] no attempts hint file for start={start_node} under {p}")
            return {}
        p = cands[0]
    if not p.exists():
        print(f"[Controller] attempts hint not found: {p} → 空で続行")
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Controller] attempts hint parse failed: {e}")
        return {}
    # (A) 既存 baseline の transition.json
    if isinstance(d, dict) and "authorization_attempts" in d:
        att = d.get("authorization_attempts", {})
        return att if isinstance(att, dict) else {}
    # (B) start_node ごとの dict
    key = str(start_node)
    if isinstance(d, dict) and key in d and isinstance(d[key], dict):
        return d[key]
    # (C) フラットな {entity: count}
    return d if isinstance(d, dict) else {}


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

    # walk前にリセット：キャッシュ・統計を初期化してこのrunだけの値を計測する
    print("[Controller] Resetting caches on all servers before walk...")
    for ep in args.server_endpoints:
        reset_cache(ep, timeout=args.request_timeout)

    # 提案手法: /cache/prefetch を全サーバに呼ぶ
    prefetch_metrics: dict = {
        "mode": args.prefetch_mode,
        "k": args.prefetch_k,
        "capacity": args.prefetch_capacity,
        "decay": args.prefetch_decay,
        "decay_mode": args.prefetch_decay_mode,
        "decay_effective": None,
        "decay_source": None,
        "per_server": [],
        "wall_clock_sec": 0.0,
        "server_build_sum_sec": 0.0,
        "server_build_max_sec": 0.0,
        "client_round_trip_sum_sec": 0.0,
        "client_round_trip_max_sec": 0.0,
        "total_inserted": 0,
        "total_selected": 0,
    }
    if args.prefetch_mode in ("bfs_prefetch", "bfs_score"):
        import time as _time

        attempts_hint = load_attempts_hint(
            args.prefetch_attempts_source, int(args.start_node)
        )
        # γ の決定モード
        effective_decay = args.prefetch_decay
        decay_source = "manual"
        if args.prefetch_mode == "bfs_score" and args.prefetch_decay_mode == "data_fit":
            est = estimate_decay_from_baseline(
                args.prefetch_attempts_source, int(args.start_node)
            )
            if est is not None:
                effective_decay = est
                decay_source = "data_fit"
            else:
                print(
                    "[Controller] data_fit failed → fallback to manual γ="
                    f"{args.prefetch_decay}"
                )
                decay_source = "manual (data_fit_fallback)"
        prefetch_metrics["decay_effective"] = float(effective_decay)
        prefetch_metrics["decay_source"] = decay_source

        print(
            f"[Controller] Prefetch mode={args.prefetch_mode} "
            f"K={args.prefetch_k} N={args.prefetch_capacity} "
            f"γ={effective_decay:.4f} ({decay_source}) "
            f"(attempts hint: {len(attempts_hint)} entries)"
        )
        # 全体のウォールクロック時間を計測 (controller 視点での prefetch 完了までの時間)
        prefetch_start = _time.perf_counter()
        per_server_results = []
        for ep in args.server_endpoints:
            res = prefetch_cache(
                ep,
                mode=args.prefetch_mode,
                start_node=int(args.start_node),
                K=args.prefetch_k,
                capacity=args.prefetch_capacity,
                decay=effective_decay,
                attempts=attempts_hint,
                timeout=args.request_timeout,
            )
            per_server_results.append(res or {"endpoint": ep})
        prefetch_metrics["wall_clock_sec"] = _time.perf_counter() - prefetch_start
        prefetch_metrics["per_server"] = per_server_results

        # 集計
        for r in per_server_results:
            b = float(r.get("build_time_sec", 0.0) or 0.0)
            rt = float(r.get("round_trip_sec", 0.0) or 0.0)
            prefetch_metrics["server_build_sum_sec"] += b
            prefetch_metrics["server_build_max_sec"] = max(
                prefetch_metrics["server_build_max_sec"], b
            )
            prefetch_metrics["client_round_trip_sum_sec"] += rt
            prefetch_metrics["client_round_trip_max_sec"] = max(
                prefetch_metrics["client_round_trip_max_sec"], rt
            )
            prefetch_metrics["total_inserted"] += int(r.get("inserted", 0) or 0)
            prefetch_metrics["total_selected"] += int(r.get("selected", 0) or 0)

        print(
            f"[Controller] Prefetch DONE: "
            f"wall_clock={prefetch_metrics['wall_clock_sec']:.3f}s "
            f"(server_build max={prefetch_metrics['server_build_max_sec']:.3f}s, "
            f"sum={prefetch_metrics['server_build_sum_sec']:.3f}s) "
            f"inserted_total={prefetch_metrics['total_inserted']}"
        )

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
        reported_policy = stats.get("auth_cache_policy", "N/A")
        print(f"[Controller] Merging stats from server {sid} ({ep})  actual_policy={reported_policy}")
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

    # 提案手法時: prefetch 時間も明示し、合計 (prefetch + walk) を出力
    if args.prefetch_mode in ("bfs_prefetch", "bfs_score"):
        pf_wc = prefetch_metrics["wall_clock_sec"]
        pf_build_max = prefetch_metrics["server_build_max_sec"]
        pf_build_sum = prefetch_metrics["server_build_sum_sec"]
        print(
            f"Prefetch wall_clock (controller view): {pf_wc:.6f} s "
            f"(server BFS+auth max={pf_build_max:.3f}s, sum={pf_build_sum:.3f}s)"
        )
        # ベンチマーク的な合計時間 (walk_time は sum なのでサーバ並列を考慮しない素直な値)
        total_with_prefetch = pf_wc + total_walk_time
        print(
            f"Total time including prefetch: {total_with_prefetch:.6f} s "
            f"(= prefetch {pf_wc:.3f}s + walk_sum {total_walk_time:.3f}s)"
        )

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
            # 提案手法用 メタデータ
            "prefetch_mode": str(args.prefetch_mode),
            "prefetch_k": int(args.prefetch_k),
            "prefetch_capacity": int(args.prefetch_capacity),
            "prefetch_decay": float(args.prefetch_decay),
            "prefetch_decay_mode": str(args.prefetch_decay_mode),
        },
        # 提案手法 prefetch の時間計測
        "prefetch_metrics": prefetch_metrics,
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
