#!/usr/bin/env python3
# =============================================================================
# sim.py — ノード/エッジ分離配置を1プロセスで模擬する仮想サーバ（任意 K = Kn + Ke）
#
#   実サーバ multi/server.py と同じパーティショナ・同じスコア式を入れて、
#   既存の仮想サーバ core/virtual_server_sim_reuse_score.py を回す。
#   ヒット率は分散に依存しないので、Kn/Ke の掃引はまずここで回すのが速い
#   （実機は実時間・RTT の測定用）。
#
#   使い方（リポジトリルートから）:
#     python3 base/proposed_cache/core/multi/sim.py \
#       --edges dataset/Louvain/graph/karate.gr \
#       --node-servers 2 --edge-servers 3 \
#       --alpha 0.1 --walks 100 --start-nodes 0 --score-w 100
#
#   Kn/Ke を掃引する:
#     --sweep "1:1 2:2 2:3 4:4"      （"Kn:Ke" を空白区切り）
# =============================================================================
from __future__ import annotations

import argparse
import os
import statistics as st
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_THIS_DIR)
for _p in (_THIS_DIR, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import partition as part                              # noqa: E402
import score as sc                                    # noqa: E402
import server as multi_server                         # noqa: E402  (install を借りる)
import virtual_server_sim_reuse_score as vs           # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default=None)
    p.add_argument("--node-servers", type=int, default=1, help="Kn")
    p.add_argument("--edge-servers", type=int, default=1, help="Ke")
    p.add_argument("--sweep", default=None,
                   help='"Kn:Ke" を空白区切りで複数指定して一括比較 (例: "1:1 2:2 2:3")')
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--cache-capacity", type=int, default=100,
                   help="1台あたりの容量（重み単位）。総容量は K 倍になる")
    p.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    p.add_argument("--no-backtrack", action="store_true")
    p.add_argument("--policies", nargs="+",
                   default=["lru", "arc", "memo", "proposed"])
    # スコア
    p.add_argument("--score-w", type=float, default=100.0)
    p.add_argument("--score-theta", type=float, default=1.0)
    p.add_argument("--score-beta", type=float, default=1.0)
    p.add_argument("--score-gamma", type=float, default=1.0)
    p.add_argument("--score-cf", type=float, default=5.0)
    p.add_argument("--edge-use-degree", action="store_true")
    # seed
    p.add_argument("--seeds", type=int, nargs="+", default=None)
    p.add_argument("--n-seeds", type=int, default=1)
    p.add_argument("--disjoint", action="store_true")
    p.add_argument("--out-csv", default=None)
    return p.parse_args()


def resolve_seeds(a) -> List[int]:
    if a.seeds:
        return list(a.seeds)
    step = a.walks if a.disjoint else 1
    return [1 + i * step for i in range(a.n_seeds)]


def parse_sweep(spec: str) -> List[Tuple[int, int]]:
    out = []
    for tok in spec.split():
        kn, _, ke = tok.partition(":")
        out.append((int(kn), int(ke)))
    return out


def args_for_policy(a, seed: int) -> SimpleNamespace:
    return SimpleNamespace(
        cache_capacity=a.cache_capacity, cache_key_mode=a.cache_key_mode,
        alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes, seed=seed,
        max_hops=a.max_hops, no_backtrack=a.no_backtrack,
        # 提案スコアは multi/score.py 側が持つ。ここは cache の生成に必要な最小限。
        theta=a.score_theta, beta=a.score_beta, gamma=a.score_gamma,
        cf=a.score_cf, cd=10.0, rho=0.0, cr=10.0, reuse_w=a.score_w,
        hop_exp=1.0, deg_exp=1.0, delta=0.0,
        lambda_learn=0.0, cl=5.0,   # legacy 用。reuse_score では未使用
    )


def _sim_hop_prior(cluster, server, target, hops_done):
    """仮想サーバ側の構造項を score.py に差し替える。

    実サーバ multi/server.py の _hop_prior と同一の式・同一の統計更新にする
    （エッジには次数を与えず、deg の Welford も更新しない）。
    これを入れないと仮想と実分散でスコアがずれる。
    """
    is_edge = part.is_edge_id(target)
    deg = cluster.degree(target)
    return sc.structural_prior(
        hops_done, deg, multi_server._norm_of(server), multi_server._PARAMS, is_edge
    )


def run_one(a, kn: int, ke: int, seeds: List[int]) -> List[Dict[str, Any]]:
    k = kn + ke
    params = sc.ScoreParams(
        w=a.score_w, theta=a.score_theta, beta=a.score_beta,
        gamma=a.score_gamma, cf=a.score_cf, edge_use_degree=a.edge_use_degree,
    )
    # 実サーバと同じ差し替えを入れる（パーティショナ + スコア）
    multi_server.install(kn, ke, params)
    # 仮想サーバは import 時に `ModuloPartitioner = srv.ModuloPartitioner` で
    # 元クラスを自分の名前空間に束縛しているので、そちらも差し替える。
    vs.ModuloPartitioner = multi_server._SplitPartitionerShim
    # 仮想サーバの prior は PeerWalker._hop_prior ではなくモジュール関数
    # vs.hop_prior が担当している。ここを差し替えないと構造項だけ旧式のままになり、
    # 実サーバと数値がずれる（エッジに次数が入ってしまう）。
    vs.hop_prior = _sim_hop_prior

    edges = vs.load_edge_list(Path(a.edges))
    cluster = vs.VirtualCluster(edges, k, None)   # partition_dir なし = modulo 経路
    # VirtualCluster は ModuloPartitioner を使うが、install で差し替え済みなので
    # 実体は NodeEdgeSplitPartitioner になる。
    assert isinstance(cluster.partitioner, part.NodeEdgeSplitPartitioner), \
        "パーティショナの差し替えが効いていません"

    rows: List[Dict[str, Any]] = []
    for policy in a.policies:
        pol = "reuse_score" if policy == "proposed" else policy
        ov, nd, eg, acc, rem, loc = [], [], [], [], [], []
        for sd in seeds:
            r = vs.run_policy(cluster, pol, args_for_policy(a, sd))
            ov.append(100 * r["hit_rate"])
            nd.append(100 * r["node_hit_rate"])
            eg.append(100 * r["edge_hit_rate"])
            acc.append(r["accesses"])
            rem.append(r["remote_auth_calls"])
            loc.append(r["local_auth_calls"])

        def ms(xs):
            return st.mean(xs), (st.stdev(xs) if len(xs) > 1 else 0.0)

        rows.append({
            "graph": a.graph_name or Path(a.edges).stem,
            "kn": kn, "ke": ke, "k": k, "policy": policy,
            "overall": ms(ov)[0], "overall_sd": ms(ov)[1],
            "node": ms(nd)[0], "edge": ms(eg)[0],
            "accesses": ms(acc)[0],
            "remote_auth": ms(rem)[0], "local_auth": ms(loc)[0],
            # 越境率 = ミス時の認可のうちリモートに出た割合
            "cross_rate": 100.0 * ms(rem)[0] / max(1.0, ms(rem)[0] + ms(loc)[0]),
        })
        print(f"  Kn={kn} Ke={ke} K={k:<3} {policy:<10} "
              f"overall {rows[-1]['overall']:6.2f}±{rows[-1]['overall_sd']:.2f}  "
              f"node {rows[-1]['node']:6.2f}  edge {rows[-1]['edge']:6.2f}  "
              f"accesses {int(rows[-1]['accesses'])}  "
              f"越境率 {rows[-1]['cross_rate']:5.1f}%")
    return rows


def main() -> None:
    a = parse_args()
    seeds = resolve_seeds(a)
    combos = parse_sweep(a.sweep) if a.sweep else [(a.node_servers, a.edge_servers)]

    print("=" * 96)
    print("ノード/エッジ分離配置の仮想サーバ（1プロセス）")
    print(f"  graph={a.edges}  alpha={a.alpha} walks={a.walks} "
          f"starts={a.start_nodes} cap/台={a.cache_capacity}")
    print(f"  seeds={seeds}")
    print(f"  {sc.ScoreParams(w=a.score_w, theta=a.score_theta, beta=a.score_beta, gamma=a.score_gamma, cf=a.score_cf, edge_use_degree=a.edge_use_degree).describe()}")
    print("=" * 96)

    all_rows: List[Dict[str, Any]] = []
    for kn, ke in combos:
        all_rows.extend(run_one(a, kn, ke, seeds))
        print()

    if a.out_csv:
        import csv
        out = Path(a.out_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            wtr = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            wtr.writeheader()
            wtr.writerows(all_rows)
        print(f"[saved] {out}")


if __name__ == "__main__":
    main()
