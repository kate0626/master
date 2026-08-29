#!/usr/bin/env python3
# =============================================================================
# run_shapley_decomposition.py
#   「提案 − LRU」の総改善を、各要素の寄与に *厳密に足し合わせが成立する形* で分解する。
#
#   なぜ leave-one-out (LOO) では足し合わせられないか:
#     LOO の Δ_i = v(全部) − v(全部から i を外す) は「限界寄与」であり、
#     要素間に交互作用があると Σ Δ_i ≠ v(全部) − v(なし) になる。
#     実測では Σ Δ_i が総改善の 2〜3 倍になる（正の交互作用のため）。
#
#   厳密に足し合わせられる分解 = Shapley 値:
#     φ_i = Σ_{S ⊆ N\{i}}  |S|!(n−|S|−1)!/n! · [ v(S∪{i}) − v(S) ]
#     性質: Σ_i φ_i = v(N) − v(∅)  （効率性）。順序にも依存しない。
#
#   分解する4要素 (N):
#     Pool  : ノード/エッジ別キャッシュ   (OFF = 共有1プール)
#     Freq  : 使用回数            θ=1 / 0
#     Close : 始点からの近さ       β=1 / 0（エッジ側 hop も同時に ON/OFF）
#     Deg   : ノード次数          γ=1 / 0
#   ※ Base（LRU の最終アクセス時刻）は分解に含めない。これは *基準側*（LRU）に
#     もともと入っている要素であり、「LRU に何を足したか」の分解の対象ではない。
#     Base 自体の必要性は LOO（外すと 4〜9 pt 低下）で別途示す。
#
#   v(∅) は「共有プール + スコアなし」= 実質 LRU。実測の LRU との差は残差として表示する。
#
#   使い方:
#     python3 base/proposed_cache/run_shapley_decomposition.py \
#       --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
#       --seeds 1 7 13 99 123 --out-csv results/shapley_VLDB.csv
# =============================================================================
from __future__ import annotations

import argparse
import csv
import importlib.util
import statistics as st
from itertools import combinations
from math import factorial
from pathlib import Path
from types import SimpleNamespace

_DIR = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


loo = _load("run_loo_ablation")
base = loo.base
dp = loo.dp

ELEMS = ["Pool", "Freq", "Close", "Deg"]


def cfg_for(S):
    """要素集合 S（ON のもの）→ run_case の引数。"""
    return dict(
        pool="split" if "Pool" in S else "shared",
        theta=1.0 if "Freq" in S else 0.0,
        hop_exp=1.0 if "Close" in S else 0.0,
        edge_hop=("Close" in S),
        deg_exp=1.0 if "Deg" in S else 0.0,
        base_mode="lru",
    )


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
    p.add_argument("--cap-node", type=int, default=40)
    p.add_argument("--cap-edge", type=int, default=60)
    p.add_argument("--w-node", type=float, default=100.0)
    p.add_argument("--w-edge", type=float, default=300.0)
    p.add_argument("--cf", type=float, default=5.0)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 7, 13, 99, 123])
    p.add_argument("--out-csv", default=None)
    p.add_argument("--out-raw", default=None)
    a = p.parse_args()

    edges = base.load_edge_list(Path(a.edges))
    cluster = base.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None)
    print(f"[graph] {a.graph_name} nodes={len(cluster.nodes)} edges={len(edges)}")
    print(f"[cond ] alpha={a.alpha} walks={a.walks} starts={a.start_nodes} "
          f"cap={a.cache_capacity}({a.cap_node}/{a.cap_edge}) "
          f"w={a.w_node}/{a.w_edge} seeds={a.seeds}")
    print()

    A = SimpleNamespace(
        cache_capacity=a.cache_capacity, cache_key_mode="full", alpha=a.alpha,
        walks=a.walks, start_nodes=a.start_nodes, max_hops=100000,
        no_backtrack=False, cap_node=a.cap_node, cap_edge=a.cap_edge, cf=a.cf,
        theta=1.0, lambda_learn=0.0, delta=0.0, beta=1, gamma=1, cl=5, cd=10,
        rho=0, cr=10, reuse_w=1.0, hop_exp=1.0, deg_exp=1.0,
        w_node=a.w_node, w_edge=a.w_edge)

    # --- 全 2^4 = 16 セルを seed 別に測る -----------------------------------
    v = {}   # frozenset(S) -> [seed 別 hit rate %]
    for k in range(len(ELEMS) + 1):
        for S in combinations(ELEMS, k):
            key = frozenset(S)
            c = cfg_for(set(S))
            v[key] = [100 * loo.run_case(
                cluster, A, w_node=a.w_node, w_edge=a.w_edge, seed=s, **c)["hit_rate"]
                for s in a.seeds]
            print(f"  done {sorted(S) or ['(なし)']}  "
                  f"{st.mean(v[key]):6.2f}")
    lru = [100 * base.run_policy(
        cluster, "lru", SimpleNamespace(**{**vars(A), "seed": s}))["hit_rate"]
        for s in a.seeds]
    arc = [100 * base.run_policy(
        cluster, "arc", SimpleNamespace(**{**vars(A), "seed": s}))["hit_rate"]
        for s in a.seeds]
    memo = [100 * base.run_policy(
        cluster, "memo", SimpleNamespace(**{**vars(A), "seed": s}))["hit_rate"]
        for s in a.seeds]

    n = len(ELEMS)
    empty, full = frozenset(), frozenset(ELEMS)

    # --- Shapley 値（seed 別に計算 → mean ± sd）---------------------------
    def shapley(i, si):
        """要素 i の Shapley 値（seed index si）"""
        tot = 0.0
        others = [e for e in ELEMS if e != i]
        for k in range(len(others) + 1):
            for S in combinations(others, k):
                w = factorial(k) * factorial(n - k - 1) / factorial(n)
                tot += w * (v[frozenset(S) | {i}][si] - v[frozenset(S)][si])
        return tot

    phi = {e: [shapley(e, si) for si in range(len(a.seeds))] for e in ELEMS}

    # --- LOO（限界寄与）と 単独寄与 ---------------------------------------
    looΔ = {e: [v[full][si] - v[full - {e}][si] for si in range(len(a.seeds))]
            for e in ELEMS}
    solo = {e: [v[frozenset({e})][si] - v[empty][si] for si in range(len(a.seeds))]
            for e in ELEMS}

    m = lambda x: st.mean(x)
    s_ = lambda x: st.stdev(x) if len(x) > 1 else 0.0
    gain_vs_lru = [v[full][i] - lru[i] for i in range(len(a.seeds))]
    gain_vs_empty = [v[full][i] - v[empty][i] for i in range(len(a.seeds))]

    print("\n" + "=" * 78)
    print(f"寄与の分解   {a.graph_name}  (alpha={a.alpha}, RW {a.walks}本, "
          f"容量{a.cache_capacity}, 始点{a.start_nodes})")
    print("=" * 78)
    print(f"  LRU (実測)                = {m(lru):6.2f} ± {s_(lru):.2f}")
    print(f"  v(なし) 共有+スコアなし     = {m(v[empty]):6.2f} ± {s_(v[empty]):.2f}"
          f"   (LRU との残差 {m(v[empty])-m(lru):+.2f})")
    print(f"  v(全部) = 提案             = {m(v[full]):6.2f} ± {s_(v[full]):.2f}")
    print(f"  ARC                       = {m(arc):6.2f}")
    print(f"  memo (上限)                = {m(memo):6.2f}")
    print(f"  総改善 提案 − LRU          = {m(gain_vs_lru):+6.2f} ± {s_(gain_vs_lru):.2f}")
    print(f"  総改善 提案 − v(なし)       = {m(gain_vs_empty):+6.2f} ± {s_(gain_vs_empty):.2f}"
          f"   ← Shapley 値の合計はこれに厳密に一致")
    print()
    print(f"{'要素':<8}{'Shapley (足せる)':>22}{'LOO (限界寄与)':>20}{'単独ON':>16}")
    print("-" * 78)
    for e in ELEMS:
        print(f"{e:<8}{m(phi[e]):>+13.2f}±{s_(phi[e]):<7.2f}"
              f"{m(looΔ[e]):>+12.2f}±{s_(looΔ[e]):<6.2f}"
              f"{m(solo[e]):>+9.2f}±{s_(solo[e]):<5.2f}")
    print("-" * 78)
    print(f"{'合計':<8}{sum(m(phi[e]) for e in ELEMS):>+13.2f}       "
          f"{sum(m(looΔ[e]) for e in ELEMS):>+12.2f}      "
          f"{sum(m(solo[e]) for e in ELEMS):>+9.2f}")
    print(f"{'総改善':<8}{m(gain_vs_empty):>+13.2f}  ← Shapley だけが一致")
    print()
    print("交互作用の大きさ = Σ LOO − 総改善 = "
          f"{sum(m(looΔ[e]) for e in ELEMS) - m(gain_vs_empty):+.2f} pt "
          "（正なら要素同士が補い合っている）")

    if a.out_csv:
        with open(a.out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["graph", "element", "shapley", "shapley_sd",
                        "loo", "loo_sd", "solo", "solo_sd",
                        "lru", "v_empty", "v_full", "arc", "memo",
                        "gain_vs_lru", "gain_vs_empty"])
            for e in ELEMS:
                w.writerow([a.graph_name, e,
                            f"{m(phi[e]):.4f}", f"{s_(phi[e]):.4f}",
                            f"{m(looΔ[e]):.4f}", f"{s_(looΔ[e]):.4f}",
                            f"{m(solo[e]):.4f}", f"{s_(solo[e]):.4f}",
                            f"{m(lru):.4f}", f"{m(v[empty]):.4f}",
                            f"{m(v[full]):.4f}", f"{m(arc):.4f}", f"{m(memo):.4f}",
                            f"{m(gain_vs_lru):.4f}", f"{m(gain_vs_empty):.4f}"])
        print(f"\n[CSV] {a.out_csv}")

    if a.out_raw:
        with open(a.out_raw, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["graph", "subset", "seed", "hit_rate"])
            for key, vals in v.items():
                nm = "+".join(sorted(key)) or "(none)"
                for s, val in zip(a.seeds, vals):
                    w.writerow([a.graph_name, nm, s, f"{val:.4f}"])
            for nm, vals in (("LRU", lru), ("ARC", arc), ("memo", memo)):
                for s, val in zip(a.seeds, vals):
                    w.writerow([a.graph_name, nm, s, f"{val:.4f}"])
        print(f"[RAW] {a.out_raw}")


if __name__ == "__main__":
    main()
