#!/usr/bin/env python3
# =============================================================================
# run_startnode_sweep.py  —  始点ごとに「提案 vs LRU / ARC / memo」を測る。
#
#   論文の表1 は始点1点 (start=0) の結果。始点を変えても優位が保たれるかを確認する。
#   グラフのロードは1回だけ行い、始点をループするので安い。
#
#   使い方:
#     python3 base/proposed_cache/run_startnode_sweep.py \
#       --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
#       --starts 0 1 2 3 4 --alpha 0.1 --walks 1000 --n-seeds 5
#     # deny/実分割を有効にする場合
#       --partition-dir base/auth-many-server/data/splits/vldb/0.3
# =============================================================================
from __future__ import annotations

import argparse
import csv
import importlib.util
import statistics as st
from pathlib import Path
from types import SimpleNamespace

_DIR = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load("virtual_server_sim_reuse_score")
dp = _load("virtual_server_dualpool")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default="graph")
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--starts", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cap-node", type=int, default=40)
    p.add_argument("--cap-edge", type=int, default=60)
    p.add_argument("--w-node", type=float, default=100.0)
    p.add_argument("--w-edge", type=float, default=300.0)
    p.add_argument("--theta", type=float, default=1.0)
    p.add_argument("--n-seeds", type=int, default=5)
    p.add_argument("--out-csv", default=None)
    a = p.parse_args()

    seeds = [1 + k * (a.walks + 1) for k in range(a.n_seeds)]

    edges = base.load_edge_list(Path(a.edges))
    cluster = base.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None)
    ndeny = sum(len(d) for d in cluster._denied_by_sid.values())
    print(f"[graph] {a.graph_name} nodes={len(cluster.nodes)} edges={len(edges)}")
    print(f"[part ] {type(cluster.partitioner).__name__}  deny総件数={ndeny}")
    print(f"[cond ] alpha={a.alpha} walks={a.walks} cap={a.cache_capacity}"
          f" (node {a.cap_node}/edge {a.cap_edge}) w={a.w_node}/{a.w_edge}"
          f" seeds={seeds}")
    print()

    def ar(start, seed):
        return SimpleNamespace(
            cache_capacity=a.cache_capacity, cache_key_mode="full",
            alpha=a.alpha, walks=a.walks, start_nodes=[start], seed=seed,
            max_hops=100000, no_backtrack=False, theta=a.theta,
            lambda_learn=0.0, delta=0.0, beta=1, gamma=1, cf=5, cl=5, cd=10,
            rho=0, cr=10, reuse_w=1.0, hop_exp=1.0, deg_exp=1.0,
            cap_node=a.cap_node, cap_edge=a.cap_edge,
            w_node=a.w_node, w_edge=a.w_edge)

    rows = []
    hdr = (f"{'start':>6}{'LRU':>9}{'ARC':>9}{'PROP':>9}{'memo':>9}"
           f"{'PROP−LRU':>12}{'PROP−ARC':>12}")
    print(hdr)
    print("-" * len(hdr))
    for s0 in a.starts:
        if s0 not in cluster.nodes:
            print(f"{s0:>6}   (グラフに存在しない始点なのでスキップ)")
            continue
        acc = {k: [] for k in ("lru", "arc", "memo", "prop")}
        for sd in seeds:
            A = ar(s0, sd)
            acc["lru"].append(100 * base.run_policy(cluster, "lru", A)["hit_rate"])
            acc["arc"].append(100 * base.run_policy(cluster, "arc", A)["hit_rate"])
            acc["memo"].append(100 * base.run_policy(cluster, "memo", A)["hit_rate"])
            acc["prop"].append(100 * dp.run_dualpool(cluster, A)["hit_rate"])
        m = {k: st.mean(v) for k, v in acc.items()}
        dl = [p - l for p, l in zip(acc["prop"], acc["lru"])]
        da = [p - x for p, x in zip(acc["prop"], acc["arc"])]
        print(f"{s0:>6}{m['lru']:>9.2f}{m['arc']:>9.2f}{m['prop']:>9.2f}"
              f"{m['memo']:>9.2f}"
              f"{st.mean(dl):>+9.2f}±{st.stdev(dl):<3.2f}"
              f"{st.mean(da):>+9.2f}±{st.stdev(da):<3.2f}")
        rows.append(dict(graph=a.graph_name, start=s0, **{k: m[k] for k in m},
                         d_lru=st.mean(dl), d_lru_sd=st.stdev(dl),
                         d_arc=st.mean(da), d_arc_sd=st.stdev(da)))

    if rows:
        print("-" * len(hdr))
        print(f"{'平均':>6}{st.mean([r['lru'] for r in rows]):>9.2f}"
              f"{st.mean([r['arc'] for r in rows]):>9.2f}"
              f"{st.mean([r['prop'] for r in rows]):>9.2f}"
              f"{st.mean([r['memo'] for r in rows]):>9.2f}"
              f"{st.mean([r['d_lru'] for r in rows]):>+9.2f}    "
              f"{st.mean([r['d_arc'] for r in rows]):>+9.2f}")
        n_win = sum(1 for r in rows if r["d_lru"] > 0)
        print(f"\n提案が LRU を上回った始点: {n_win} / {len(rows)}")

    if a.out_csv:
        with open(a.out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"[CSV] {a.out_csv}")


if __name__ == "__main__":
    main()
