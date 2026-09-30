#!/usr/bin/env python3
# =============================================================================
# plot_challenge_memo_vs_lru.py
#   課題セクション用のクリーンな 2 本線図 (memo 上限 vs LRU) を作る。
#   横軸 walks: memo(上限)は本数とともに伸び、LRU は頭打ち → 取りこぼしが拡大。
#   横軸 cap  : LRU は容量とともに正しく上がる(=正常動作)が、memo には届かない。
#   plot_sweeps.py と同じ virtual_server_sim_reuse_score を使い、数値は実測。
# =============================================================================
from __future__ import annotations
import argparse, importlib.util, statistics as st
from pathlib import Path
from types import SimpleNamespace
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

_DIR = Path(__file__).resolve().parent.parent / "core"   # 中核実装は core/ にある
def _load(n):
    s = importlib.util.spec_from_file_location(n, _DIR / f"{n}.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
base = _load("virtual_server_sim_reuse_score")

def args_for(alpha, walks, cap, seed):
    cn = int(round(0.4 * cap)); ce = cap - cn
    return SimpleNamespace(cache_capacity=cap, cache_key_mode="full", alpha=alpha,
        walks=walks, start_nodes=[0], seed=seed, max_hops=100000, no_backtrack=False,
        theta=1.0, lambda_learn=1.0, delta=0.0, beta=1, gamma=1, cf=5, cl=5, cd=10,
        rho=0, cr=10, reuse_w=1.0, hop_exp=1.0, deg_exp=1.0,
        cap_node=cn, cap_edge=ce, w_node=1.0*cap, w_edge=3.0*cap,
        node_hop_exp=1.0, node_deg_exp=0.0, edge_use_hop=True, arc_w=1.0*cap)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default="graph")
    p.add_argument("--xaxis", choices=["walks", "cap"], required=True)
    p.add_argument("--values", type=float, nargs="+", required=True)
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--cap", type=int, default=100)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 7])
    p.add_argument("--memo-walks", type=int, nargs="+", default=[100, 1000, 10000],
                   help="cap モードで LRU 曲線と memo 参照線を描く walk 本数")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    edges = base.load_edge_list(Path(a.edges)); cl = base.VirtualCluster(edges, 2, None)

    def hit(pol, al, wl, cp):
        ys = []
        for s in a.seeds:
            ar = args_for(al, wl, cp, s)
            ys.append(100 * base.run_policy(cl, {"LRU": "lru", "memo": "memo"}[pol], ar)["hit_rate"])
        return st.mean(ys)

    fig, ax = plt.subplots(figsize=(8.0, 5.2))

    if a.xaxis == "walks":
        # memo(上限) vs LRU の 2 本線。横軸=探索本数。
        memo = [hit("memo", a.alpha, int(v), a.cap) for v in a.values]
        lru  = [hit("LRU",  a.alpha, int(v), a.cap) for v in a.values]
        for v, mm, ll in zip(a.values, memo, lru):
            print(f"  walks={v:g}  memo={mm:.1f}  LRU={ll:.1f}  gap={mm-ll:.1f}")
        x = list(range(len(a.values))); xl = [f"{int(v):,}" for v in a.values]
        ax.plot(x, memo, "-o", color="#2e6db4", lw=2.4, ms=8, label="memo (upper bound = revisit rate)")
        ax.plot(x, lru,  "-s", color="#c0392b", lw=2.4, ms=8, label="LRU")
        for xi, yi in zip(x, memo): ax.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points", xytext=(0, 8), ha="center", color="#2e6db4", fontsize=9)
        for xi, yi in zip(x, lru):  ax.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points", xytext=(0, -14), ha="center", color="#c0392b", fontsize=9)
        ax.set_xticks(x); ax.set_xticklabels(xl)
        ax.set_xlabel("Number of walks (same start, log scale)")
        ax.set_title(f"Hit rate vs. number of walks ({a.graph_name}, cap={a.cap})")
    else:
        # figB スタイル: 横軸=容量。各 walk 本数ごとに LRU 曲線 + memo 水平参照線。
        # memo は容量非依存(無制限キャッシュ)なので walk 本数だけで決まる。
        walk_list = [int(w) for w in a.memo_walks]
        reds = ["#f1a9a0", "#c0392b", "#7b241c"]  # 薄→濃 (walk 少→多)
        x = list(range(len(a.values))); xl = [f"{int(v):,}" for v in a.values]
        for i, wl in enumerate(walk_list):
            c = reds[i % len(reds)]
            lru = [hit("LRU", a.alpha, wl, int(v)) for v in a.values]
            mm = hit("memo", a.alpha, wl, int(a.values[-1]))  # memo(容量非依存)
            print(f"  walks={wl}: memo={mm:.1f}  LRU@caps=" + " ".join(f"{v:.1f}" for v in lru))
            ax.plot(x, lru, "-s", color=c, lw=2.2, ms=7, label=f"LRU (walks={wl:,})")
            ax.axhline(mm, ls=":", color=c, lw=1.6, alpha=0.9)
            ax.annotate(f"memo {wl:,}={mm:.1f}%", (x[-1], mm), textcoords="offset points",
                        xytext=(-4, 4), ha="right", color=c, fontsize=9)
        ax.annotate("LRU rises with capacity at every walk count\n(= LRU functions correctly)",
                    (x[-1], min(hit("LRU", a.alpha, walk_list[-1], int(a.values[-1])), 30)),
                    textcoords="offset points", xytext=(-140, -30), ha="left", color="#555555", fontsize=9,
                    arrowprops=dict(arrowstyle="->", color="#888888"))
        ax.set_xticks(x); ax.set_xticklabels(xl)
        ax.set_xlabel("Cache capacity (entries, log scale)")
        ax.set_title(f"LRU hit rate vs. capacity at each walk count ({a.graph_name})")

    ax.set_ylabel("Cache hit rate (%)")
    leg_loc = "lower right" if a.xaxis == "walks" else "center left"
    ax.legend(loc=leg_loc, framealpha=0.95); ax.grid(True, alpha=0.25); ax.set_ylim(0, 100)
    fig.tight_layout()
    out = a.out or str(Path(__file__).resolve().parent / f"challenge_{a.xaxis}_{a.graph_name.lower()}.png")
    fig.savefig(out, dpi=130); print(f"[saved] {out}")
    # 論文貼り込み用にベクタ(PDF)も同時出力
    if out.lower().endswith(".png"):
        pdf = out[:-4] + ".pdf"
        fig.savefig(pdf); print(f"[saved] {pdf}")

if __name__ == "__main__": main()
