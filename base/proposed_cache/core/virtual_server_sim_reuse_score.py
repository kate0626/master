#!/usr/bin/env python3
# =============================================================================
# virtual_server_sim_reuse_score.py
#
#   「仮想サーバ (virtual / pseudo server)」による単一プロセス・キャッシュ実験。
#
#   目的:
#     実分散 (split_remote_server_reuse_score.py を K 台の実サーバで走らせる) を
#     せずに、1 プロセス内に K 台の「仮想サーバ」を立て、それぞれに独立した
#     authz_cache を持たせて、ランダムウォークのアクセス列を owner サーバへ
#     ルーティングしながら **ヒット率・認可呼び出し回数・クロスサーバ通信回数**
#     といった「実時間に依存しない論理指標」を計測する。
#
#   なぜ正しいか (前提):
#     ヒット率はアクセス列 × パーティション割当 × キャッシュ方策の純関数であり、
#     ネットワークや台数と無関係に決定論的に決まる。よって同一 seed・同一割当・
#     同一方策なら、実分散で走らせた値と一致する (時間 = RTT 等だけが分散を要する)。
#
#   実装方針 (重要):
#     キャッシュ本体 (PPRBoundedCache = reuse_score / lru / arc / memo / none) と
#     グラフ・パーティション・キー生成ユーティリティは、既存の
#         split_remote_server_reuse_score.py
#     を **import して流用** する (再実装しない = ロジックの乖離を防ぐ)。
#     当該モジュールは純標準ライブラリで、実行コードは全て `__main__` ガード内に
#     あるため、import しても副作用 (サーバ起動・argv 解釈) は一切発生しない。
#
#     このシミュレータが自前で写経しているのは、キャッシュ「外側」にある
#         - PeerWalker._hop_prior   (reuse_score の H^β·D^γ prior)
#         - PeerWalker._reuse_norm_z (hop/deg のオンライン標準化 = Welford + sigmoid)
#         - PeerWalker._authorize_candidate のヒット/ミス計上と bump_prior 呼び出し順
#         - continue_from_state / _select_next_neighbor の歩行 + ハンドオフ
#     の 4 点のみ。いずれも本家の該当メソッドを 1:1 で移植している。
#
#   既存ファイルは一切変更しない (import するだけ)。
#
#   使い方 (例):
#     python3 base/proposed_cache/core/virtual_server_sim_reuse_score.py \
#       --edges dataset/Louvain/graph/vldb.gr \
#       --server-count 2 --alpha 0.01 --walks 100 --cache-capacity 100 \
#       --start-nodes 0 --seed 42 \
#       --policies none lru arc memo reuse_score
#
#   出力: 方策ごとのヒット率・認可呼び出し (local/remote)・通信削減率の表。
# =============================================================================
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# --- 既存モジュールを import (副作用なし。同ディレクトリを sys.path へ) ----------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import split_remote_server_reuse_score as srv  # noqa: E402  (本家実装)

# 流用する部品 (再実装しない)
build_authz_cache = srv.build_authz_cache
DecisionCacheSizer = srv.DecisionCacheSizer
PPRBoundedCache = srv.PPRBoundedCache
make_edge_id = srv.make_edge_id
cache_entity_key = srv.cache_entity_key
load_edge_list = srv.load_edge_list
ModuloPartitioner = srv.ModuloPartitioner
StaticPartitioner = srv.StaticPartitioner
load_node_to_starts_table = srv.load_node_to_starts_table
build_owner_map_from_sibling_node_to_starts_files = (
    srv.build_owner_map_from_sibling_node_to_starts_files
)
_sigmoid = srv._sigmoid

EntityId = Any  # int (node) or "edge_u_v" (str)


# =============================================================================
# 仮想サーバ
# =============================================================================
class VirtualServer:
    """1 台分の状態: 独立キャッシュ + 認可 (deny) テーブル + reuse_score 用の
    オンライン標準化状態 + 計数カウンタ。本家 GraphShardServer のうち、
    ヒット率計測に必要なフィールドだけを抜き出したもの。"""

    def __init__(self, sid: int, cache, denied: Dict[EntityId, Set[int]],
                 hop_exp: float, deg_exp: float) -> None:
        self.sid = sid
        self.cache = cache
        # entity -> {NGな start_node} (node_to_starts_serverX.json 由来。無ければ空)
        self.denied = denied
        # reuse_score の H^β·D^γ で使う β/γ (本家 server.ppr_prior_hop_exp/deg_exp)
        self.hop_exp = hop_exp
        self.deg_exp = deg_exp
        # 本家 _reuse_norm: 特徴量ごとの Welford 状態 (hop / deg)
        self.reuse_norm: Dict[str, Dict[str, float]] = {}
        # カウンタ (本家と同名)
        self.auth_cache_hit = 0
        self.auth_cache_miss = 0
        self.local_auth_calls = 0
        self.remote_auth_calls = 0
        # node / edge 別のヒット・ミス (エントリ種別ごとの取り代を見るため)
        self.hit_node = 0
        self.miss_node = 0
        self.hit_edge = 0
        self.miss_edge = 0

    # --- 本家 PeerWalker._reuse_norm_z の移植 (Welford + z 値, invert 対応) -----
    def reuse_norm_z(self, name: str, x: float, invert: bool) -> float:
        s = self.reuse_norm.get(name)
        if s is None:
            s = {"n": 0.0, "mean": 0.0, "M2": 0.0}
            self.reuse_norm[name] = s
        s["n"] += 1
        delta = x - s["mean"]
        s["mean"] += delta / s["n"]
        s["M2"] += delta * (x - s["mean"])
        if s["n"] < 2:
            return 0.0
        var = s["M2"] / (s["n"] - 1)
        if var <= 1e-12:
            return 0.0
        z = (x - s["mean"]) / math.sqrt(var)
        return -z if invert else z


# =============================================================================
# 仮想クラスタ (グラフ + パーティション + K 台のサーバ)
# =============================================================================
class VirtualCluster:
    def __init__(self, edges: List[Tuple[int, int]], server_count: int,
                 partition_dir: Optional[Path]) -> None:
        self.server_count = server_count

        # --- 二部隣接 (node <-> edge_u_v) を本家と同じ規則で構築 ---------------
        self.neighbor_map: Dict[EntityId, List[EntityId]] = {}
        self.nodes: Set[int] = set()
        for (u, v) in edges:
            eid = make_edge_id(u, v)
            self.neighbor_map.setdefault(u, []).append(eid)
            self.neighbor_map.setdefault(v, []).append(eid)
            self.neighbor_map.setdefault(eid, []).append(u)
            self.neighbor_map[eid].append(v)
            self.nodes.add(u)
            self.nodes.add(v)

        # --- パーティション (owner 割当) ------------------------------------
        # 既定: ModuloPartitioner (node%K / edge ハッシュ%K) = 本家の fallback と一致。
        # partition_dir 指定時: node_to_starts_serverX.json から静的 owner map +
        #                       各サーバの deny テーブルをロード (本家と同一挙動)。
        denied_by_sid: Dict[int, Dict[EntityId, Set[int]]] = {
            i: {} for i in range(server_count)
        }
        if partition_dir is not None:
            base = Path(partition_dir) / "node_to_starts_server0.json"
            owner_map = build_owner_map_from_sibling_node_to_starts_files(base)
            self.partitioner = StaticPartitioner(server_count, owner_map)
            for sid in range(server_count):
                p = Path(partition_dir) / f"node_to_starts_server{sid}.json"
                if p.exists():
                    denied_by_sid[sid] = load_node_to_starts_table(p)
        else:
            self.partitioner = ModuloPartitioner(server_count)

        self._denied_by_sid = denied_by_sid
        self._owner_cache: Dict[EntityId, int] = {}
        self.servers: List[VirtualServer] = []  # build_servers() で構築

    def owner(self, entity: EntityId) -> int:
        o = self._owner_cache.get(entity)
        if o is None:
            o = self.partitioner.assign_entity(entity)
            self._owner_cache[entity] = o
        return o

    def build_servers(self, cache_factory, hop_exp: float, deg_exp: float) -> None:
        """方策ごとに呼び直してキャッシュを差し替える (歩行は方策非依存なので
        クラスタ構造は使い回し、サーバのカウンタ/キャッシュだけ作り直す)。"""
        self.servers = [
            VirtualServer(sid, cache_factory(), self._denied_by_sid[sid],
                          hop_exp, deg_exp)
            for sid in range(self.server_count)
        ]

    def degree(self, entity: EntityId) -> int:
        return len(self.neighbor_map.get(entity, ())) or 1


# =============================================================================
# reuse_score の hop prior (本家 PeerWalker._hop_prior の reuse_score 分岐を移植)
# =============================================================================
def hop_prior(cluster: VirtualCluster, server: VirtualServer,
              target: EntityId, hops_done: int) -> float:
    hop = max(0, int(hops_done))
    deg = cluster.degree(target)
    zh = server.reuse_norm_z("hop", float(hop), invert=True)   # 近いほど高スコア
    if (getattr(server, "reuse_edge_no_deg", False)
            and isinstance(target, str) and target.startswith("edge_")):
        return (1.0 + _sigmoid(zh)) ** server.hop_exp
    zd = server.reuse_norm_z("deg", float(deg), invert=False)  # 高次数ほど高スコア
    h_base = 1.0 + _sigmoid(zh)   # ∈ [1, 2)
    d_base = 1.0 + _sigmoid(zd)   # ∈ [1, 2)
    return (h_base ** server.hop_exp) * (d_base ** server.deg_exp)


# =============================================================================
# 認可 (1 候補 = 1 キャッシュアクセス)。本家 _authorize_candidate の計数と
# bump_prior 呼び出し順を厳密に再現する。
# =============================================================================
def is_locally_allowed(server: VirtualServer, start_node: int,
                       entity: EntityId) -> bool:
    # deny テーブル: entity -> {NGな start}。空/未登録なら誰も拒否していない = OK。
    denied = server.denied.get(entity)
    if not denied:
        return True
    return int(start_node) not in denied


def authorize_candidate(cluster: VirtualCluster, server: VirtualServer,
                        start_node: int, target: EntityId, hops_done: int,
                        node_only: bool) -> bool:
    ekey = cache_entity_key(target, node_only=node_only)
    ckey = (int(start_node), ekey)
    observer = getattr(server, "access_observer", None)
    if observer is not None:
        observer(server.sid, ckey)
    cache = server.cache
    is_edge = isinstance(target, str) and target.startswith("edge_")

    # --- ヒット: 認可コストはゼロ (local/remote いずれも呼ばない) ---
    if ckey in cache:
        server.auth_cache_hit += 1
        if is_edge:
            server.hit_edge += 1
        else:
            server.hit_node += 1
        if isinstance(cache, PPRBoundedCache) and cache.use_hop_prior:
            cache.bump_prior(ckey, hop_prior(cluster, server, target, hops_done))
        return bool(cache[ckey])

    # --- ミス: owner に認可を問い合わせる (local か remote) ---
    server.auth_cache_miss += 1
    if is_edge:
        server.miss_edge += 1
    else:
        server.miss_node += 1
    owner_t = cluster.owner(target)
    if owner_t == server.sid:
        server.local_auth_calls += 1
        allowed = is_locally_allowed(server, start_node, target)
    else:
        server.remote_auth_calls += 1  # ← クロスサーバ通信 (/authorize 相当)
        allowed = is_locally_allowed(cluster.servers[owner_t], start_node, target)

    if isinstance(cache, PPRBoundedCache) and cache.use_hop_prior:
        cache.bump_prior(ckey, hop_prior(cluster, server, target, hops_done))
    cache[ckey] = bool(allowed)
    return bool(allowed)


# =============================================================================
# 歩行 (continue_from_state + _select_next_neighbor を単一プロセス化)
# =============================================================================
def select_next_neighbor(cluster: VirtualCluster, rng: random.Random,
                         current_entity: EntityId, start_node: int,
                         hops_done: int, node_only: bool, no_backtrack: bool,
                         path: List[EntityId]) -> Optional[EntityId]:
    neighbors = cluster.neighbor_map.get(current_entity, ())
    if not neighbors:
        return None
    # 歩行を実行しているのは「今いる実体の owner サーバ」。認可はそのキャッシュに当てる。
    server = cluster.servers[cluster.owner(current_entity)]

    indices = list(range(len(neighbors)))
    rng.shuffle(indices)  # 本家と同じ rng 消費 (一様ランダム順)
    try_last = None
    if (no_backtrack and isinstance(current_entity, str)
            and current_entity.startswith("edge_") and len(path) >= 2):
        try_last = path[-2]
        indices.sort(key=lambda i: 1 if neighbors[i] == try_last else 0)

    for idx in indices:
        cand = neighbors[idx]
        if authorize_candidate(cluster, server, start_node, cand, hops_done,
                               node_only):
            return cand  # 最初に認可された隣接へ進む
    return None  # 全隣接が拒否 = 行き止まり


def run_walk(cluster: VirtualCluster, start_node: int, alpha: float,
             rng: random.Random, max_hops: int, node_only: bool,
             no_backtrack: bool) -> int:
    """本家 continue_from_state を単一プロセスで再現。ハンドオフは owner を
    再計算するだけで表現される (状態 POST は不要)。戻り値 = 実際に歩いたホップ数。"""
    current_entity: EntityId = start_node
    path: List[EntityId] = [start_node]
    hops_done = 0
    while hops_done < max_hops and rng.random() > alpha:
        hops_done += 1
        nxt = select_next_neighbor(cluster, rng, current_entity, start_node,
                                   hops_done, node_only, no_backtrack, path)
        if nxt is None:
            break
        path.append(nxt)
        current_entity = nxt
    return hops_done


# =============================================================================
# 1 方策の実験を回して集計
# =============================================================================
def run_policy(cluster: VirtualCluster, policy: str, args) -> Dict[str, Any]:
    sizer = getattr(args, "cache_sizer", None) or DecisionCacheSizer()

    def cache_factory():
        return build_authz_cache(
            policy, args.cache_capacity, sizer=sizer,
            theta=args.theta, delta=args.delta, lambda_learn=args.lambda_learn,
            beta=args.beta, gamma=args.gamma,
            cf=args.cf, cl=args.cl, cd=args.cd,
            rho=args.rho, cr=args.cr, reuse_w=args.reuse_w,
            size_neutral=getattr(args, "size_neutral", False),
        )

    cluster.build_servers(cache_factory, args.hop_exp, args.deg_exp)
    for server in cluster.servers:
        server.reuse_edge_no_deg = getattr(args, "reuse_edge_no_deg", False)
        server.access_observer = getattr(args, "access_observer", None)

    node_only = (args.cache_key_mode == "node_only")
    total_hops = 0
    for start in args.start_nodes:
        for i in range(args.walks):
            rng = random.Random(args.seed + i)  # 本家 /walk ループと同じ seed 規則
            total_hops += run_walk(cluster, int(start), args.alpha, rng,
                                   args.max_hops, node_only, args.no_backtrack)

    hits = sum(s.auth_cache_hit for s in cluster.servers)
    miss = sum(s.auth_cache_miss for s in cluster.servers)
    local = sum(s.local_auth_calls for s in cluster.servers)
    remote = sum(s.remote_auth_calls for s in cluster.servers)
    hit_node = sum(s.hit_node for s in cluster.servers)
    miss_node = sum(s.miss_node for s in cluster.servers)
    hit_edge = sum(s.hit_edge for s in cluster.servers)
    miss_edge = sum(s.miss_edge for s in cluster.servers)
    acc_node = hit_node + miss_node
    acc_edge = hit_edge + miss_edge
    accesses = hits + miss
    return {
        "policy": policy,
        "accesses": accesses,
        "hits": hits,
        "misses": miss,
        "node_hit": hit_node, "node_acc": acc_node,
        "node_hit_rate": (hit_node / acc_node) if acc_node else 0.0,
        "edge_hit": hit_edge, "edge_acc": acc_edge,
        "edge_hit_rate": (hit_edge / acc_edge) if acc_edge else 0.0,
        "hit_rate": (hits / accesses) if accesses else 0.0,
        "local_auth_calls": local,
        "remote_auth_calls": remote,
        "auth_calls_total": local + remote,  # = miss (ミス毎に 1 認可)
        "total_hops": total_hops,
        "per_server": [
            {"sid": s.sid, "hit": s.auth_cache_hit, "miss": s.auth_cache_miss,
             "local": s.local_auth_calls, "remote": s.remote_auth_calls}
            for s in cluster.servers
        ],
    }


# =============================================================================
# CLI
# =============================================================================
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="仮想サーバ (単一プロセス) による reuse_score キャッシュのヒット率実験",
    )
    p.add_argument("--edges", required=True, help=".gr エッジリスト (u v 各行)")
    p.add_argument("--server-count", type=int, default=2, help="仮想サーバ台数 K")
    p.add_argument("--partition-dir", default=None,
                   help="node_to_starts_serverX.json のディレクトリ (省略時は modulo 割当)")
    p.add_argument("--alpha", type=float, default=0.01, help="停止確率 α")
    p.add_argument("--walks", type=int, default=100, help="start あたりの walk 本数")
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0],
                   help="始点ノード (複数可)")
    p.add_argument("--num-start-nodes", type=int, default=None,
                   help="指定時: グラフから seed 固定で N 個の始点を無作為抽出 (--start-nodes を上書き)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    p.add_argument("--no-backtrack", action="store_true")
    p.add_argument("--policies", nargs="+",
                   default=["none", "lru", "arc", "memo", "reuse_score"],
                   help="比較する方策")
    # reuse_score パラメータ (本家 build_authz_cache と同名・同デフォルト)
    p.add_argument("--theta", type=float, default=1.0, help="θ: 観測 O の指数")
    p.add_argument("--lambda-learn", type=float, default=0.0,
                   help="【legacy ppr_demand 専用】λ。reuse_score では未使用 "
                        "(学習項 L は 2026-09-08 に削除)")
    p.add_argument("--beta", type=float, default=1.0, help="β: 距離 H の指数 (cache 側, reuse では未使用)")
    p.add_argument("--gamma", type=float, default=1.0, help="γ: 次数 D の指数 (cache 側, reuse では未使用)")
    p.add_argument("--hop-exp", type=float, default=None,
                   help="実際に H^? に効く hop 指数 (本家 ppr_prior_hop_exp)。省略時は --beta")
    p.add_argument("--deg-exp", type=float, default=None,
                   help="実際に D^? に効く deg 指数 (本家 ppr_prior_deg_exp)。省略時は --gamma")
    p.add_argument("--rho", type=float, default=0.0, help="ρ: recency R の指数 (0=無効)")
    p.add_argument("--cf", type=float, default=5.0, help="C_F: 頻度飽和")
    p.add_argument("--cl", type=float, default=5.0,
                   help="【legacy ppr_demand 専用】C_L。reuse_score では未使用")
    p.add_argument("--cd", type=float, default=10.0, help="C_D: 次数飽和 (reuse では未使用)")
    p.add_argument("--cr", type=float, default=10.0, help="C_R: recency 飽和")
    p.add_argument("--reuse-w", type=float, default=1.0, help="w: LRU 土台への V/size 加点重み")
    p.add_argument("--delta", type=float, default=0.0, help="δ: add 方策用 (reuse では未使用)")
    p.add_argument("--json-out", default=None, help="結果 JSON の出力先")
    args = p.parse_args(argv)
    if args.hop_exp is None:
        args.hop_exp = args.beta
    if args.deg_exp is None:
        args.deg_exp = args.gamma
    return args


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    edges = load_edge_list(Path(args.edges))
    cluster = VirtualCluster(
        edges, args.server_count,
        Path(args.partition_dir) if args.partition_dir else None,
    )

    # --num-start-nodes 指定時は seed 固定で始点を抽出
    if args.num_start_nodes is not None:
        rng = random.Random(args.seed)
        pool = sorted(cluster.nodes)
        k = min(args.num_start_nodes, len(pool))
        args.start_nodes = rng.sample(pool, k)

    print("=" * 74)
    print("仮想サーバ・シミュレータ (単一プロセス / 分散なし)")
    print(f"  graph={args.edges}  nodes={len(cluster.nodes)}  edges={len(edges)}")
    print(f"  K(servers)={args.server_count}  partition="
          f"{'static:' + args.partition_dir if args.partition_dir else 'modulo'}")
    print(f"  alpha={args.alpha}  walks={args.walks}  cap={args.cache_capacity}"
          f"  starts={args.start_nodes}  seed={args.seed}")
    print(f"  reuse_score: theta={args.theta}"
          f" hop_exp={args.hop_exp} deg_exp={args.deg_exp} rho={args.rho}"
          f" reuse_w={args.reuse_w}")
    print("=" * 74)

    results = [run_policy(cluster, pol, args) for pol in args.policies]

    # 通信削減率は 'none' (キャッシュ無し) を基準にする
    base = next((r for r in results if r["policy"] == "none"), None)
    base_auth = base["auth_calls_total"] if base else None
    base_remote = base["remote_auth_calls"] if base else None

    print(f"\n{'policy':<12}{'hit%':>8}{'access':>10}{'hit':>9}{'miss':>9}"
          f"{'local':>8}{'remote':>8}{'auth削減%':>10}{'remote削減%':>12}")
    print("-" * 94)
    for r in results:
        auth_cut = (100.0 * (1 - r["auth_calls_total"] / base_auth)
                    if base_auth else float("nan"))
        rem_cut = (100.0 * (1 - r["remote_auth_calls"] / base_remote)
                   if base_remote else float("nan"))
        print(f"{r['policy']:<12}{100*r['hit_rate']:>8.1f}{r['accesses']:>10}"
              f"{r['hits']:>9}{r['misses']:>9}{r['local_auth_calls']:>8}"
              f"{r['remote_auth_calls']:>8}{auth_cut:>10.1f}{rem_cut:>12.1f}")

    if args.json_out:
        out = {"config": vars(args), "results": results}
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n[saved] {args.json_out}")


if __name__ == "__main__":
    main()
