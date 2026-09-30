#!/usr/bin/env python3
# =============================================================================
# plot_cause_distribution.py  ―  ③アプローチ 3-2 用（②根拠2の詳細版）
#   再利用tier別に、hop（始点距離）と degree（次数）の「分布(%)」を node/edge 別に描く。
#   平均版(plot_cause_structure.py)が「向き」を示すのに対し、こちらは
#   「偏りがどれだけ強い/集中しているか＝使える信号か」を示す。
#     node : hop=BFS距離, degree=無向次数
#     edge : hop=min(両端BFS距離), degree=両端次数の和
#   数値は実測（cache=none 実走トレース）＋グラフ計算。α等はトレース側で固定。
# =============================================================================
from __future__ import annotations
import argparse, json
from collections import deque, Counter, defaultdict
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

TIERS = [(1, 1, "1"), (2, 2, "2"), (3, 3, "3"), (4, 4, "4"), (5, 9, "5-9"), (10, 10**9, "10+")]
DEG_TH = [2, 4, 8, 16, 32, 64, 128]
DEG_LAB = ["1-2", "3-4", "5-8", "9-16", "17-32", "33-64", "65-128", ">128"]
HOP_LAB = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9+"]

def load_graph(path):
    adj = defaultdict(set)
    for line in open(path):
        p = line.split()
        if len(p) < 2: continue
        try: u, v = int(p[0]), int(p[1])
        except ValueError: continue
        adj[u].add(v); adj[v].add(u)
    deg = {k: len(vs) for k, vs in adj.items()}
    return adj, deg

def bfs_hop(adj, start):
    hop = {start: 0}; q = deque([start])
    while q:
        x = q.popleft()
        for y in adj[x]:
            if y not in hop:
                hop[y] = hop[x] + 1; q.append(y)
    return hop

def tier_of(reuse):
    for lo, hi, lab in TIERS:
        if lo <= reuse <= hi: return lab
    return None

def deg_bucket(g):
    for i, t in enumerate(DEG_TH):
        if g <= t: return i
    return len(DEG_TH)  # >128

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--graph-name", default="graph")
    ap.add_argument("--alpha-label", default="0.01")
    ap.add_argument("--out-prefix", default=None)
    a = ap.parse_args()

    adj, deg = load_graph(Path(a.edges))
    hop = bfs_hop(adj, a.start)
    FAR = max(hop.values()) + 1

    acc = Counter()
    d = json.load(open(a.trace))
    for w in d.get("per_walk_access", []):
        for e, c in w.get("access", {}).items():
            acc[e] += c

    def ent(e):
        s = str(e)
        if s.startswith("edge_"):
            _, u, v = s.split("_", 2); u, v = int(u), int(v)
            return "edge", min(hop.get(u, FAR), hop.get(v, FAR)), deg.get(u, 0) + deg.get(v, 0)
        n = int(e)
        return "node", hop.get(n, FAR), deg.get(n, 0)

    labs = [lab for _, _, lab in TIERS]
    # kind -> tier -> histogram(list of counts)
    hop_hist = {k: {l: [0]*len(HOP_LAB) for l in labs} for k in ("node", "edge")}
    deg_hist = {k: {l: [0]*len(DEG_LAB) for l in labs} for k in ("node", "edge")}
    for e, c in acc.items():
        reuse = c - 1
        if reuse < 1: continue
        t = tier_of(reuse)
        if t is None: continue
        kind, h, dg = ent(e)
        hop_hist[kind][t][min(h, 9)] += 1
        deg_hist[kind][t][deg_bucket(dg)] += 1

    def pct(hist):
        s = sum(hist)
        return [100.0*x/s if s else 0.0 for x in hist]

    cmap = plt.get_cmap("viridis")
    colors = [cmap(i/(len(labs)-1)) for i in range(len(labs))]

    def draw(metric, hist_by_kind, xlab, xlabels, out):
        fig, ax = plt.subplots(1, 2, figsize=(14, 5.2), sharey=True)
        x = list(range(len(xlabels)))
        for pi, kind in enumerate(("node", "edge")):
            for li, l in enumerate(labs):
                n = sum(hist_by_kind[kind][l])
                ax[pi].plot(x, pct(hist_by_kind[kind][l]), "-o", ms=4, lw=1.8,
                            color=colors[li], label=f"reuse {l} (n={n})")
            ax[pi].set_title(f"{kind}")
            ax[pi].set_xlabel(xlab); ax[pi].set_xticks(x); ax[pi].set_xticklabels(xlabels, rotation=30)
            ax[pi].grid(True, alpha=0.25)
        ax[0].set_ylabel("% of entities in that reuse tier")
        ax[1].legend(fontsize=8, title="reuse-count tier")
        fig.suptitle(f"{metric} distribution by reuse tier  —  {a.graph_name} (alpha={a.alpha_label}, start={a.start}, measured)",
                     fontsize=12)
        fig.tight_layout()
        fig.savefig(out, dpi=130); print(f"[saved] {out}")

    pre = a.out_prefix or str(Path(__file__).resolve().parent / f"cause_dist_{a.graph_name.lower()}_a{a.alpha_label.replace('.','')}")
    draw("hop (distance from start)", hop_hist, "hop from start", HOP_LAB, pre + "_hop.png")
    draw("degree", deg_hist, "degree bucket", DEG_LAB, pre + "_deg.png")

if __name__ == "__main__": main()
