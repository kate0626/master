#!/usr/bin/env python3
# =============================================================================
# collect_time_stats.py — 実行時間モデル用に「回数」を測って CSV に落とす。
#
#   図 lru_base_comparison.png はヒット率しか持っていないが、時間を出すには
#   ヒット率ではなく「遷移回数」と「ミス回数（= 遠隔認可の回数）」が要る。
#   本スクリプトは同じ条件・同じ seed で 4 方式を回し、その回数を記録する。
#
#     none  認可なし         キャッシュ無し（全アクセスがミス）
#     lru   既存手法         共有 LRU
#     prop  提案手法         run_loo_ablation の「提案 (全要素)」= 図の赤棒
#     memo  理想的キャッシュ 容量無制限（上限）
#
#   出力 CSV 1 行 = (graph, policy, seed) で、
#     hops        総遷移回数
#     accesses    認可判定の総回数（ノード + エッジ）
#     hits/misses キャッシュヒット / ミス（ミス = 遠隔問い合わせ = RTT を払う回数）
#     remote      ミスのうち他サーバ所有だったもの（RTT を遠隔のみに課す場合用）
#
#   使い方:
#     python3 base/proposed_cache/figs/collect_time_stats.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_FIGS = Path(__file__).resolve().parent
_PC = _FIGS.parent
_REPO = _PC.parent.parent
_RES = _PC / "results"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load("virtual_server_sim_reuse_score", _PC / "core" / "virtual_server_sim_reuse_score.py")
loo = _load("run_loo_ablation", _PC / "experiments" / "run_loo_ablation.py")


def cfg(alpha, walks, cap, seed):
    """run_loo_ablation の既定値（= 論文条件）に合わせた引数。"""
    return SimpleNamespace(
        cache_capacity=cap, cache_key_mode="full", alpha=alpha, walks=walks,
        start_nodes=[0], seed=seed, max_hops=100000, no_backtrack=False,
        theta=1.0, lambda_learn=0.0, delta=0.0, beta=1, gamma=1,
        cf=5.0, cl=5, cd=10, rho=0, cr=10, reuse_w=1.0,
        hop_exp=1.0, deg_exp=1.0,
        cap_node=int(round(0.4 * cap)), cap_edge=cap - int(round(0.4 * cap)),
        w_node=100.0, w_edge=300.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--graphs", nargs="+",
                   default=[f"VLDB={_REPO}/dataset/Louvain/graph/vldb.gr",
                            f"Amazon={_REPO}/dataset/Louvain/graph/amazon0601.gr"])
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--cap", type=int, default=100)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 7, 13, 99, 123])
    p.add_argument("--out", default=str(_RES / "time_stats_paperseed.csv"))
    a = p.parse_args()

    rows = []
    for spec in a.graphs:
        name, path = spec.split("=", 1)
        edges = base.load_edge_list(Path(path))
        cl = base.VirtualCluster(edges, 2, None)
        print(f"[graph] {name} nodes={len(cl.nodes)} edges={len(edges)}")

        for seed in a.seeds:
            ar = cfg(a.alpha, a.walks, a.cap, seed)

            # --- 既存方式 (共有プール): none / LRU / memo ---
            hops = None
            for pol, key in (("none", "none"), ("lru", "lru"), ("memo", "memo")):
                r = base.run_policy(cl, pol, ar)
                hops = r["total_hops"]          # 軌跡は方式によらず同一
                rows.append(dict(
                    graph=name, policy=key, seed=seed, hops=r["total_hops"],
                    accesses=r["accesses"], hits=r["hits"], misses=r["misses"],
                    remote=r["remote_auth_calls"], hit_rate=r["hit_rate"]))
                print(f"  seed={seed} {key:5s} hit={100*r['hit_rate']:5.2f}% "
                      f"hops={r['total_hops']} miss={r['misses']}")

            # --- 提案手法 (図の赤棒と同じ構成: 提案 (全要素)) ---
            full = dict(theta=1.0, hop_exp=1.0, deg_exp=1.0, edge_hop=True,
                        pool="shared", base_mode="lru",
                        w_node=ar.w_node, w_edge=ar.w_node)
            r = loo.run_case(cl, ar, seed=seed, **full)
            acc = r["acc_node"] + r["acc_edge"]
            hits = round(acc * r["hit_rate"])
            rows.append(dict(
                graph=name, policy="prop", seed=seed, hops=hops,
                accesses=acc, hits=hits, misses=acc - hits,
                remote=r["remote_auth_calls"], hit_rate=r["hit_rate"]))
            print(f"  seed={seed} {'prop':5s} hit={100*r['hit_rate']:5.2f}% "
                  f"hops={hops} miss={acc - hits}")

    cols = ["graph", "policy", "seed", "hops", "accesses", "hits", "misses",
            "remote", "hit_rate"]
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"[saved] {a.out}  ({len(rows)} rows)")


if __name__ == "__main__":
    main()
