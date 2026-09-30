#!/usr/bin/env python3
# =============================================================================
# verify_vs_virtual.py — 実分散の結果が仮想サーバと一致することを確認する
#
#   run_local.sh / run_cluster.sh が吐いた summary.tsv と、
#   同じ条件で回した仮想サーバ (virtual_server_sim_reuse_score /
#   run_loo_ablation の pool="shared") の結果を並べて差分を出す。
#
#   条件 (グラフ・K・alpha・walks・cap・start・seed・no-backtrack・partition-dir) を
#   揃えれば **完全一致する** のが正しい状態。ずれたら配線かパラメータの取り違え。
#
#   使い方 (リポジトリルートから):
#     python3 base/proposed_cache/dist/verify_vs_virtual.py \
#       --edges dataset/Louvain/graph/vldb.gr --server-count 2 \
#       --alpha 0.1 --walks 1000 --start-nodes 0 --seed 42 --cache-capacity 100 \
#       --no-backtrack \
#       --summary base/proposed_cache/dist/results/vldb_K2_a0.1_w1000_cap100/summary.tsv
#
#   --summary を省くと仮想側の値だけ出す (実分散を回す前の期待値の確認に使える)。
# =============================================================================
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(os.path.dirname(_THIS_DIR), "core")            # base/proposed_cache/core
_EXP = os.path.join(os.path.dirname(_THIS_DIR), "experiments")      # 実験ドライバ
for _p in (_CORE, _EXP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_loo_ablation as loo  # noqa: E402  (run_case を借りる)
import virtual_server_sim_reuse_score as vs  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    p.add_argument("--no-backtrack", action="store_true")
    # 提案手法(単一プール)の動作点。run_local.sh / run_cluster.sh の既定と合わせること
    p.add_argument("--w", type=float, default=100.0, help="スコアの重み w")
    p.add_argument("--theta", type=float, default=1.0)
    p.add_argument("--policies", nargs="+", default=["lru", "arc", "memo", "proposed"])
    p.add_argument("--summary", default=None,
                   help="実分散側の summary.tsv。指定すると差分を出す。")
    return p.parse_args()


def _sim_args(a: argparse.Namespace) -> SimpleNamespace:
    """run_policy / run_case が読む属性をまとめて作る。"""
    return SimpleNamespace(
        alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes, seed=a.seed,
        max_hops=a.max_hops, cache_capacity=a.cache_capacity,
        cache_key_mode=a.cache_key_mode, no_backtrack=a.no_backtrack,
        # reuse_score 系
        theta=a.theta, beta=1.0, gamma=1.0,
        hop_exp=1.0, deg_exp=1.0, rho=0.0, cf=5.0, cd=10.0, cr=10.0,
        lambda_learn=0.0, cl=5.0,   # legacy 用。reuse_score では未使用
        reuse_w=1.0, delta=0.0,
        # run_case が読む属性。単一プールなので cap は全量、w は1つ。
        cap_node=a.cache_capacity, cap_edge=0,
        w_node=a.w, w_edge=a.w,
        node_hop_exp=1.0, node_deg_exp=1.0, edge_use_hop=True,
    )


def read_summary(path: Path) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("RESULT\t"):
            continue
        cols = line.split("\t")
        policy = cols[1]
        fields = {}
        for c in cols[2:]:
            if "=" in c:
                k, v = c.split("=", 1)
                fields[k] = v
        out[policy] = fields
    return out


def main() -> None:
    a = parse_args()
    edges = vs.load_edge_list(Path(a.edges))
    cluster = vs.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None,
    )
    sa = _sim_args(a)

    print("=" * 88)
    print("仮想サーバ (期待値)")
    print(f"  graph={a.edges}  K={a.server_count}  "
          f"partition={'static:' + a.partition_dir if a.partition_dir else 'modulo'}")
    print(f"  alpha={a.alpha} walks={a.walks} cap={a.cache_capacity} "
          f"starts={a.start_nodes} seed={a.seed} no_backtrack={a.no_backtrack}")
    print(f"  提案手法: 単一プール cap={a.cache_capacity}  w={a.w}")
    print("=" * 88)

    virtual: Dict[str, Dict[str, Any]] = {}
    for policy in a.policies:
        if policy == "proposed":
            # 単一プール + スコア。実サーバの
            #   --cache-policy reuse_score --size-neutral --reuse-edge-no-deg --reuse-w W
            # と等価な構成 (run_loo_ablation の pool="shared")。
            loo.EDGE_PRIOR_MODE = "hop"
            r = loo.run_case(cluster, sa, theta=a.theta, hop_exp=1.0, deg_exp=1.0,
                             edge_hop=True, pool="shared", base_mode="lru",
                             w_node=a.w, w_edge=a.w, seed=a.seed)
            virtual[policy] = {
                "overall": 100.0 * r["hit_rate"],
                "node": 100.0 * r["node_hit_rate"],
                "edge": 100.0 * r["edge_hit_rate"],
                "accesses": r["acc_node"] + r["acc_edge"],
                "remote_auth": r["remote_auth_calls"],
            }
        else:
            r = vs.run_policy(cluster, policy, sa)
            virtual[policy] = {
                "overall": 100.0 * r["hit_rate"],
                "node": 100.0 * r["node_hit_rate"],
                "edge": 100.0 * r["edge_hit_rate"],
                "accesses": r.get("accesses"),
                "remote_auth": r.get("remote_auth_calls"),
            }
        v = virtual[policy]
        node = f"{v['node']:.2f}" if v["node"] is not None else "-"
        edge = f"{v['edge']:.2f}" if v["edge"] is not None else "-"
        print(f"  {policy:<12} overall={v['overall']:.2f}\tnode={node}\tedge={edge}\t"
              f"accesses={v['accesses']}\tremote_auth={v['remote_auth']}")

    if not a.summary:
        return

    dist = read_summary(Path(a.summary))
    print()
    print("=" * 88)
    print("実分散との差分  (|Δ| <= 0.01pt なら一致とみなす)")
    print("=" * 88)
    print(f"  {'policy':<12} {'仮想':>9} {'実分散':>9} {'Δ':>8}   accesses(仮想/実分散)")
    ok = True
    for policy in a.policies:
        if policy not in dist:
            print(f"  {policy:<12} {'-':>9} {'(未実行)':>9}")
            continue
        vov = virtual[policy]["overall"]
        try:
            dov = float(dist[policy]["overall"])
        except (KeyError, ValueError):
            print(f"  {policy:<12} 実分散側の overall を読めません: {dist[policy]}")
            ok = False
            continue
        d = dov - vov
        mark = "OK " if abs(d) <= 0.01 else "*** ずれ ***"
        va, da = virtual[policy]["accesses"], dist[policy].get("accesses")
        amark = "" if str(va) == str(da) else "  <- accesses 不一致 (歩行条件が違う)"
        if abs(d) > 0.01 or str(va) != str(da):
            ok = False
        print(f"  {policy:<12} {vov:9.2f} {dov:9.2f} {d:+8.2f}  {mark}  {va}/{da}{amark}")

    print()
    if ok:
        print("=> 一致。実分散の実装は仮想サーバと同じ結果を出しています。")
    else:
        print("=> 不一致。まず accesses を合わせること (no-backtrack / seed / start /")
        print("   partition-dir / walks が揃っていないと歩行そのものが変わる)。")
        sys.exit(1)


if __name__ == "__main__":
    main()
