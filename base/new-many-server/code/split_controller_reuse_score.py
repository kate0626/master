#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union
from urllib import request as urllib_request

NodeOrEdgeId = Union[int, str]


def write_access_by_distance(
    out_dir: Path,
    prefix: str,
    node_dist: Dict[int, int],
    edge_dist: Dict[int, int],
    graph_label: str = "graph",
    meta_label: str = "",
) -> None:
    """距離 (bipartite hop) ごとのアクセス回数を CSV + PNG で出力。

    - x 軸 = start からの bipartite hop 距離 (node=偶数 hop, edge=奇数 hop)。
    - y 軸 = その距離にあるエンティティへの総アクセス回数。
    - node_logical_distance = hop // 2 (= 元グラフ上の論理ホップ数, node 行のみ意味を持つ)。
    距離においてアクセス局所性がどう変わるか (近いほど多く踏まれるか) を見るための図。
    """
    hops = sorted(set(node_dist) | set(edge_dist))
    csv_path = out_dir / f"{prefix}_access_by_distance.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "hop",
                "node_logical_distance",
                "node_access",
                "edge_access",
                "total_access",
            ]
        )
        for h in hops:
            n = int(node_dist.get(h, 0))
            e = int(edge_dist.get(h, 0))
            writer.writerow([h, h // 2, n, e, n + e])
    print(f"[Controller] Saved access-by-distance CSV to {csv_path}")

    if not hops:
        print("[Controller] No access-by-distance data; skipping PNG.")
        return

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - matplotlib 無い環境向け
        print(f"[Controller] matplotlib unavailable, skipping PNG ({exc})")
        return

    node_y = [int(node_dist.get(h, 0)) for h in hops]
    edge_y = [int(edge_dist.get(h, 0)) for h in hops]
    total_y = [n + e for n, e in zip(node_y, edge_y)]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.bar(hops, total_y, width=0.8, color="#cfd8dc", label="total", zorder=1)
    ax.plot(hops, node_y, marker="o", color="#1565c0", label="node access", zorder=3)
    ax.plot(hops, edge_y, marker="s", color="#ef6c00", label="edge access", zorder=2)
    ax.set_xlabel("distance from start (bipartite hop;  node=even, edge=odd)")
    ax.set_ylabel("access count")
    ax.set_title(f"Access locality vs distance — {graph_label}\n{meta_label}")
    # 局所性 (急速減衰) を見やすくするため、正の値があれば対数軸も併記
    if any(v > 0 for v in total_y):
        ax.set_yscale("log")
        ax.set_ylabel("access count (log)")
    ax.legend()
    ax.grid(True, which="both", axis="y", alpha=0.3)
    fig.tight_layout()
    png_path = out_dir / f"{prefix}_access_by_distance.png"
    fig.savefig(png_path, dpi=130)
    plt.close(fig)
    print(f"[Controller] Saved access-by-distance PNG to {png_path}")


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
        choices=["none", "bfs_prefetch", "bfs_score", "ppr_gdsf"],
        default="none",
        help="提案手法のプリフェッチモード。bfs_prefetch=BFS K-hop球, bfs_score=BFS×freq上位N, "
        "ppr_gdsf=PPR局所近似(1−α)^dist×deg で seed + GDSF 容量有界 (本命)",
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
    # ★学習フェーズ
    parser.add_argument(
        "--learning-walks",
        type=int,
        default=0,
        help="学習フェーズの walk 数 (0=無効, デフォルト動作)。"
        "> 0 のとき最初の N walks で実アクセス頻度を観測し、"
        "キャッシュリセット後にその頻度を w_prior として注入してから残りを実行。"
        "ppr_demand ポリシー専用 (他ポリシーでは注入が no-op になる)。",
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
        print(
            f"[Controller] Reset OK {endpoint}: policy={resp.get('policy')}, capacity={resp.get('capacity')}"
        )
        return True
    except Exception as e:
        print(f"[Controller] *** RESET FAILED {endpoint}: {e} ***")
        return False


def refresh_priors_on_server(
    endpoint: str,
    start_node: int,
    access_counts: dict,
    scale: float,
    timeout: float,
) -> dict:
    """学習フェーズの実測アクセス頻度を w_prior として注入する (ppr_demand 専用)。"""
    try:
        resp = post_json(
            endpoint,
            "/cache/refresh_priors",
            {
                "start_node": start_node,
                "access_counts": access_counts,
                "scale": scale,
            },
            timeout=timeout,
        )
        return resp
    except Exception as e:
        print(f"[Controller] *** refresh_priors FAILED {endpoint}: {e} ***")
        return {}


def prefetch_cache(
    endpoint: str,
    mode: str,
    start_node: int,
    K: int,
    capacity: int,
    decay: float,
    attempts: Optional[dict],
    timeout: float,
    alpha: float = 0.0,
) -> Optional[dict]:
    """提案手法: /cache/prefetch を呼ぶ。返り値に round_trip_sec (controller 側測定) を含む。"""
    import time as _time

    payload = {
        "start_node": int(start_node),
        "mode": mode,
        "K": int(K),
        "capacity": int(capacity),
        "decay": float(decay),
        "alpha": float(alpha),  # ppr_gdsf: (1−α)^dist の減衰に使用
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


def estimate_decay_from_baseline(
    path: Optional[str], start_node: int
) -> Optional[float]:
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
        print(
            f"[Controller] data_fit: baseline JSON not found ({path}, start={start_node})"
        )
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
            print(
                f"[Controller] no attempts hint file for start={start_node} under {p}"
            )
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

    # ===================================================================
    # 学習フェーズ (--learning-walks N > 0 のとき)
    #   Phase1: 最初のN walksを通常通り実行して実アクセス頻度を観測
    #   Phase2: キャッシュリセット後に学習済みw_priorを注入して残りを実行
    # ===================================================================
    phase1_saved_stats: dict = {}  # ep -> stats (Phase1からサーバ統計を保存)
    phase1_walk_results: list = []  # Phase1 walkパス一覧 (per_walk_access用)
    phase1_duration_sec: float = 0.0
    learning_phase_meta: dict = {}  # 出力JSONに埋め込むメタデータ

    learn_n = int(args.learning_walks) if args.learning_walks else 0
    total_n = int(args.walks)

    if learn_n > 0 and learn_n < total_n:
        remaining_n = total_n - learn_n
        scale = total_n / learn_n  # phase1頻度を全walk数に外挿するスケール係数

        print(
            f"[LearningPhase] 学習フェーズ開始: {learn_n}/{total_n} walks "
            f"(scale={scale:.1f}x, 残り={remaining_n} walks)"
        )

        # --- Phase 1: 学習walk ---
        phase1_payload = {
            "start_node": int(args.start_node),
            "alpha": float(args.alpha),
            "walks": learn_n,
            "seed": args.seed,
            "endpoints": args.server_endpoints,
            "server_count": int(args.servers),
        }
        p1_res = start_walk_on_server(
            endpoint, phase1_payload, timeout=args.request_timeout
        )
        phase1_walk_results = p1_res.get("walks", [])
        p1_metrics = p1_res.get("metrics", {})
        phase1_duration_sec = float(
            p1_metrics.get("duration_sec", p1_res.get("duration", 0.0)) or 0.0
        )
        print(
            f"[LearningPhase] Phase1 完了: {phase1_duration_sec:.3f}s, "
            f"walks={len(phase1_walk_results)}"
        )

        # Phase1 stats を全サーバから回収・保存
        phase1_access_merged: dict = {}
        for ep in args.server_endpoints:
            s1 = fetch_access_stats(ep, timeout=args.request_timeout)
            if s1:
                phase1_saved_stats[ep] = s1
                for entity, cnt in s1.get("access", {}).items():
                    phase1_access_merged[entity] = phase1_access_merged.get(
                        entity, 0
                    ) + int(cnt)

        n_observed = len(phase1_access_merged)
        print(
            f"[LearningPhase] 観測エンティティ数: {n_observed}, "
            f"スケール係数: {scale:.1f}x -> new_prior = count * {scale:.1f}"
        )

        # キャッシュリセット (Phase2用の新鮮なキャッシュ)
        print("[LearningPhase] キャッシュリセット (Phase2用)...")
        for ep in args.server_endpoints:
            reset_cache(ep, timeout=args.request_timeout)

        # 学習済み w_prior を注入
        print(f"[LearningPhase] empirical w_prior 注入中 ({n_observed} entities)...")
        total_updated = 0
        for ep in args.server_endpoints:
            result = refresh_priors_on_server(
                ep,
                int(args.start_node),
                phase1_access_merged,
                scale,
                args.request_timeout,
            )
            updated = result.get("updated_priors", 0)
            total_updated += updated
            print(
                f"[LearningPhase]   {ep}: updated={updated}, "
                f"recalculated={result.get('recalculated_scores', 0)}"
            )
        print(
            f"[LearningPhase] 注入完了 (total_updated={total_updated}). "
            f"Phase2 開始: {remaining_n} walks"
        )

        # Phase2 用に payload を上書き (残りwalk数・シードをずらす)
        phase2_seed = (args.seed + learn_n) if args.seed is not None else None
        payload["walks"] = remaining_n
        payload["seed"] = phase2_seed

        learning_phase_meta = {
            "enabled": True,
            "learning_walks": learn_n,
            "remaining_walks": remaining_n,
            "scale_factor": scale,
            "phase1_entities_observed": n_observed,
            "phase1_duration_sec": phase1_duration_sec,
            "phase1_seed": args.seed,
            "phase2_seed": phase2_seed,
            "total_priors_injected": total_updated,
        }
    else:
        learning_phase_meta = {"enabled": False, "learning_walks": 0}

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
    if args.prefetch_mode in ("bfs_prefetch", "bfs_score", "ppr_gdsf"):
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
                alpha=float(args.alpha),
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

    # Phase1 walk結果を先頭に連結 (学習フェーズ有効時)
    walks = phase1_walk_results + res.get("walks", [])
    metrics = res.get("metrics", {})
    phase2_duration = float(
        metrics.get("duration_sec", res.get("duration", 0.0)) or 0.0
    )
    duration = phase1_duration_sec + phase2_duration

    total_steps = sum(len(w.get("path", [])) for w in walks)
    avg_len = total_steps / max(1, len(walks))
    print(f"Avg length: {avg_len:.3f}, total steps: {total_steps}")
    if learn_n > 0:
        print(
            f"[Controller] duration {duration:.6f}s "
            f"(phase1={phase1_duration_sec:.3f}s + phase2={phase2_duration:.3f}s)"
        )
    else:
        if duration > 0:
            print(f"[Controller] duration {duration:.6f}s")

    # サーバー訪問回数のカウント
    server_visits = defaultdict(int)
    for w in walks:
        for s in w.get("servers", []):
            server_visits[int(s)] += 1
    print("Server visit counts:")
    for sid in range(args.servers):
        print(f"  Server {sid}: {server_visits.get(sid, 0)}")

    # walk ごとのアクセス集計（per_walk_access）
    per_walk_access = []
    cumulative_access: Dict[str, int] = defaultdict(int)
    cumulative_total_visits = 0
    cumulative_series = []
    for idx, w in enumerate(walks, start=1):
        per_walk: Dict[str, int] = defaultdict(int)
        for ent in w.get("path", []):
            per_walk[str(ent)] += 1
        per_walk_total = sum(per_walk.values())
        per_walk_access.append(
            {
                "walk_index": idx,
                "access": dict(per_walk),
                "total_visits": per_walk_total,
                "unique_entities": len(per_walk),
            }
        )
        for ent, count in per_walk.items():
            cumulative_access[ent] += count
        cumulative_total_visits += per_walk_total
        cumulative_series.append(
            {
                "walk_index": idx,
                "total_visits": cumulative_total_visits,
                "unique_entities": len(cumulative_access),
            }
        )

    # 統計統合 + memory（RSS/テーブル規模）も回収
    print("\n[Controller] Collecting access statistics from all servers...")
    global_access = defaultdict(int)
    global_authorized = defaultdict(int)
    global_auth_attempts = defaultdict(int)
    global_auth_denied = defaultdict(int)
    global_transition = defaultdict(int)
    # 距離 (bipartite hop) 別アクセス回数 (node/edge 別)
    global_node_dist = defaultdict(int)
    global_edge_dist = defaultdict(int)

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
        print(
            f"[Controller] Merging stats from server {sid} ({ep})  actual_policy={reported_policy}"
        )

        # 学習フェーズが有効な場合: Phase1のサーバ統計を Phase2統計に加算する。
        # Phase2開始前のリセットでカウンタがクリアされるため、合算しないと
        # Phase1分が失われる。
        p1 = phase1_saved_stats.get(ep, {})
        if p1:

            def _add(key: str) -> float:
                return float(stats.get(key, 0.0)) + float(p1.get(key, 0.0))

            def _add_int(key: str) -> int:
                return int(stats.get(key, 0)) + int(p1.get(key, 0))

            # access カウンタ合算
            merged_access = dict(p1.get("access", {}))
            for k, v in stats.get("access", {}).items():
                merged_access[k] = merged_access.get(k, 0) + int(v)
            # authorized / attempts / denied / transition 合算
            merged_authorized = dict(p1.get("authorized", {}))
            for k, v in stats.get("authorized", {}).items():
                merged_authorized[k] = merged_authorized.get(k, 0) + int(v)
            merged_attempts = dict(p1.get("authorization_attempts", {}))
            for k, v in stats.get("authorization_attempts", {}).items():
                merged_attempts[k] = merged_attempts.get(k, 0) + int(v)
            merged_denied = dict(p1.get("authorization_denied", {}))
            for k, v in stats.get("authorization_denied", {}).items():
                merged_denied[k] = merged_denied.get(k, 0) + int(v)
            merged_transition = dict(p1.get("transition", {}))
            for k, v in stats.get("transition", {}).items():
                merged_transition[k] = merged_transition.get(k, 0) + int(v)
            merged_node_dist = dict(p1.get("node_access_by_distance", {}))
            for k, v in stats.get("node_access_by_distance", {}).items():
                merged_node_dist[k] = merged_node_dist.get(k, 0) + int(v)
            merged_edge_dist = dict(p1.get("edge_access_by_distance", {}))
            for k, v in stats.get("edge_access_by_distance", {}).items():
                merged_edge_dist[k] = merged_edge_dist.get(k, 0) + int(v)
            stats = {
                **stats,
                "access": merged_access,
                "authorized": merged_authorized,
                "authorization_attempts": merged_attempts,
                "authorization_denied": merged_denied,
                "transition": merged_transition,
                "node_access_by_distance": merged_node_dist,
                "edge_access_by_distance": merged_edge_dist,
                "auth_time_total": _add("auth_time_total"),
                "auth_calls": _add_int("auth_calls"),
                "walk_time_total": _add("walk_time_total"),
                "walk_calls": _add_int("walk_calls"),
                "auth_cache_hit": _add_int("auth_cache_hit"),
                "auth_cache_miss": _add_int("auth_cache_miss"),
            }

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
        for k, v in stats.get("node_access_by_distance", {}).items():
            global_node_dist[int(k)] += int(v)
        for k, v in stats.get("edge_access_by_distance", {}).items():
            global_edge_dist[int(k)] += int(v)

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
    if args.prefetch_mode in ("bfs_prefetch", "bfs_score", "ppr_gdsf"):
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
            # 学習フェーズ メタデータ
            "learning_phase": learning_phase_meta,
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

    # ===== アクセス局所性: 距離 (bipartite hop) 別アクセス回数を CSV / PNG 出力 =====
    write_access_by_distance(
        out_dir=out_dir,
        prefix=prefix,
        node_dist=global_node_dist,
        edge_dist=global_edge_dist,
        graph_label=out_dir.parent.name or "graph",
        meta_label=(
            f"start={int(args.start_node)} walks={int(args.walks)} "
            f"alpha={float(args.alpha)} policy={args.cache_policy}"
        ),
    )

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

    per_walk_path = out_dir / f"{prefix}_per_walk_access.json"
    per_walk_payload = {
        "per_walk_access": per_walk_access,
        "cumulative_series": cumulative_series,
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
            "prefetch_mode": str(args.prefetch_mode),
        },
    }
    per_walk_path.write_text(json.dumps(per_walk_payload, indent=2), encoding="utf-8")
    print(f"[Controller] Saved per-walk access stats to {per_walk_path}")


if __name__ == "__main__":
    main()
