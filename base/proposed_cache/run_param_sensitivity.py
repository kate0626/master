#!/usr/bin/env python3
# =============================================================================
# run_param_sensitivity.py  —  各パラメータの 1D 感度分析（OAT）。
#
#   目的:
#     「提案が LRU を上回る」という結果が、パラメータの偶然の当たりではなく
#     (a) 動作点が各軸の最適付近にあり
#     (b) その周辺が平坦（多少ずらしても LRU を上回り続ける）
#     ことを示す。併せて「どのパラメータがどの程度効くか」を定量化する。
#
#   中心（動作点 = 論文の設定）:
#     w_node=100, w_edge=300 (=3·w_node), θ=1, β=1, γ=1,
#     cap_node=40 / cap_edge=60, C_F=5
#
#   掃引する軸:
#     w        : w_node を振る（w_edge = ratio·w_node を維持）
#     w_ratio  : w_edge / w_node
#     theta    : Freq の指数 θ
#     cap_node : 容量配分（cap_edge = 100 − cap_node）
#     cf       : C_F（Freq の飽和点）
#     beta     : Close の指数（ノード側。エッジ側 hop は常に有効）
#     gamma    : Deg の指数（ノードのみ）
#
#   使い方:
#     python3 base/proposed_cache/run_param_sensitivity.py \
#       --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
#       --start-nodes 0 --n-seeds 5 --out-csv results/sens_Amazon.csv
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


loo = _load("run_loo_ablation")   # run_case / authorize_flex を流用
base = loo.base
dp = loo.dp

CENTER = dict(w_node=100.0, w_ratio=3.0, theta=1.0, cap_node=40,
              cf=5.0, beta=1.0, gamma=1.0)

AXES = {
    "w":        ("w_node",   [0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0]),
    "w_ratio":  ("w_ratio",  [1.0, 2.0, 3.0, 5.0, 8.0]),
    "theta":    ("theta",    [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]),
    "cap_node": ("cap_node", [20, 30, 40, 50, 60, 70, 80]),
    "cf":       ("cf",       [1.0, 2.0, 5.0, 10.0, 20.0]),
    "beta":     ("beta",     [0.0, 0.5, 1.0, 1.5, 2.0]),
    "gamma":    ("gamma",    [0.0, 0.5, 1.0, 1.5, 2.0]),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default="graph")
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--n-seeds", type=int, default=5)
    p.add_argument("--axes", nargs="+", default=list(AXES.keys()))
    p.add_argument("--out-csv", default=None)
    a = p.parse_args()

    seeds = [1 + k * (a.walks + 1) for k in range(a.n_seeds)]

    edges = base.load_edge_list(Path(a.edges))
    cluster = base.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None)
    ndeny = sum(len(d) for d in cluster._denied_by_sid.values())
    print(f"[graph] {a.graph_name} nodes={len(cluster.nodes)} edges={len(edges)}")
    print(f"[part ] {type(cluster.partitioner).__name__} deny総件数={ndeny}")
    print(f"[cond ] alpha={a.alpha} walks={a.walks} cap={a.cache_capacity} "
          f"starts={a.start_nodes} seeds={seeds}")
    print(f"[中心 ] {CENTER}")
    print()

    def mkargs(cfg):
        return SimpleNamespace(
            cache_capacity=a.cache_capacity, cache_key_mode="full",
            alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes,
            max_hops=100000, no_backtrack=False,
            cap_node=int(cfg["cap_node"]),
            cap_edge=a.cache_capacity - int(cfg["cap_node"]),
            cf=cfg["cf"], theta=cfg["theta"],
            lambda_learn=0.0, delta=0.0, beta=1, gamma=1, cl=5, cd=10,
            rho=0, cr=10, reuse_w=1.0, hop_exp=1.0, deg_exp=1.0,
            w_node=cfg["w_node"], w_edge=cfg["w_node"] * cfg["w_ratio"])

    cache = {}

    def run(cfg):
        key = tuple(sorted((k, float(v)) for k, v in cfg.items()))
        if key in cache:
            return cache[key]
        A = mkargs(cfg)
        res = [loo.run_case(
            cluster, A, theta=cfg["theta"], hop_exp=cfg["beta"],
            deg_exp=cfg["gamma"], edge_hop=True, pool="split",
            base_mode="lru", w_node=A.w_node, w_edge=A.w_edge, seed=s)
            for s in seeds]
        out = {k: [100 * r[k] for r in res] for k in
               ("hit_rate", "node_hit_rate", "edge_hit_rate")}
        cache[key] = out
        return out

    # --- ベースライン ---
    A0 = mkargs(CENTER)
    bl = {}
    for pol in ("lru", "arc", "memo"):
        v = [100 * base.run_policy(
            cluster, pol, SimpleNamespace(**{**vars(A0), "seed": s}))["hit_rate"]
            for s in seeds]
        bl[pol] = v
        print(f"  {pol:5s} overall = {st.mean(v):6.2f} ± {st.stdev(v):.2f}")
    print()

    center = run(CENTER)
    cm = st.mean(center["hit_rate"])
    dlru = [p - l for p, l in zip(center["hit_rate"], bl["lru"])]
    print(f"  中心(動作点) overall = {cm:6.2f} ± {st.stdev(center['hit_rate']):.2f}"
          f"   vs LRU {st.mean(dlru):+.2f} ± {st.stdev(dlru):.2f}")
    print()

    rows = []
    for ax in a.axes:
        pname, values = AXES[ax]
        print(f"===== {ax} ({pname}) 中心={CENTER[pname]} " + "=" * 30)
        print(f"{'値':>8}{'overall':>16}{'node':>9}{'edge':>9}"
              f"{'vs LRU':>14}{'vs ARC':>9}")
        for v in values:
            cfg = dict(CENTER); cfg[pname] = v
            r = run(cfg)
            o = st.mean(r["hit_rate"]); osd = st.stdev(r["hit_rate"])
            dl = [x - y for x, y in zip(r["hit_rate"], bl["lru"])]
            da = [x - y for x, y in zip(r["hit_rate"], bl["arc"])]
            mark = "  ← 動作点" if v == CENTER[pname] else ""
            print(f"{v:>8}{o:>9.2f}±{osd:<5.2f}"
                  f"{st.mean(r['node_hit_rate']):>9.2f}"
                  f"{st.mean(r['edge_hit_rate']):>9.2f}"
                  f"{st.mean(dl):>+9.2f}±{sd(dl):<3.2f}"
                  f"{st.mean(da):>+9.2f}{mark}")
            rows.append(dict(graph=a.graph_name, axis=ax, param=pname, value=v,
                             overall=o, overall_sd=osd,
                             node=st.mean(r["node_hit_rate"]),
                             edge=st.mean(r["edge_hit_rate"]),
                             d_lru=st.mean(dl), d_lru_sd=sd(dl),
                             d_arc=st.mean(da),
                             is_center=int(v == CENTER[pname]),
                             lru=st.mean(bl["lru"]), arc=st.mean(bl["arc"]),
                             memo=st.mean(bl["memo"])))
        # 軸の「効き幅」= その軸を動かして得られる最大 − 最小
        vals = [r["overall"] for r in rows if r["axis"] == ax]
        print(f"  → この軸の効き幅 (最大−最小) = {max(vals)-min(vals):.2f} pt   "
              f"最良値 = {[r['value'] for r in rows if r['axis']==ax and r['overall']==max(vals)][0]}")
        print()

    print("=" * 72)
    print("[軸の効き幅ランキング] (大きいほどそのパラメータが結果を左右する)")
    for ax in sorted(a.axes,
                     key=lambda x: -(max(r["overall"] for r in rows if r["axis"] == x)
                                     - min(r["overall"] for r in rows if r["axis"] == x))):
        vals = [r["overall"] for r in rows if r["axis"] == ax]
        best = [r["value"] for r in rows if r["axis"] == ax
                and r["overall"] == max(vals)][0]
        cen = [r["overall"] for r in rows if r["axis"] == ax and r["is_center"]]
        gap = (max(vals) - cen[0]) if cen else float("nan")
        print(f"  {ax:<9} 効き幅 {max(vals)-min(vals):>6.2f} pt   "
              f"最良={best!s:<6} 動作点との差={gap:+.2f} pt")

    if a.out_csv and rows:
        with open(a.out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"\n[CSV] {a.out_csv}")


def sd(v):
    return st.stdev(v) if len(v) > 1 else 0.0


if __name__ == "__main__":
    main()
