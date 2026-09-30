#!/usr/bin/env python3
# =============================================================================
# plot_cause_structure.py  ―  ③原因の図（node vs edge × 近さ/次数 を再利用tier別に）
#   cache=none 実走トレース(per_walk_access.json)から、エンティティごとの再利用回数を
#   数え、再利用tierごとに「始点からの最短距離(hop)」「次数」の平均を node/edge 別に出す。
#     node : hop=BFS距離, degree=無向次数
#     edge : hop=min(両端のBFS距離), degree=両端の次数の合計
#   → 「よく再利用されるものほど始点に近い」／「次数はnodeで正・edgeで逆」を実測で示す。
#   数値は実測（トレース）＋グラフ計算（BFS/次数）。α等はトレース側で固定。
# =============================================================================
from __future__ import annotations
import argparse, json
from collections import deque, Counter, defaultdict
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

TIERS = [(1, 1, "1"), (2, 2, "2"), (3, 3, "3"), (4, 4, "4"), (5, 9, "5-9"), (10, 10**9, "10+")]

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--graph-name", default="graph")
    ap.add_argument("--alpha-label", default="0.01")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    adj, deg = load_graph(Path(a.edges))
    hop = bfs_hop(adj, a.start)
    FAR = max(hop.values()) + 1  # 到達不能は最遠+1 扱い

    # エンティティ総アクセス数を集計 → 再利用回数 = access-1
    acc = Counter()
    d = json.load(open(a.trace))
    for w in d.get("per_walk_access", []):
        for e, c in w.get("access", {}).items():
            acc[e] += c

    def ent_hop_deg(e):
        s = str(e)
        if s.startswith("edge_"):
            _, u, v = s.split("_", 2); u, v = int(u), int(v)
            return "edge", min(hop.get(u, FAR), hop.get(v, FAR)), deg.get(u, 0) + deg.get(v, 0)
        n = int(e)
        return "node", hop.get(n, FAR), deg.get(n, 0)

    # tier × kind → hop/deg リスト
    hops = defaultdict(lambda: defaultdict(list)); degs = defaultdict(lambda: defaultdict(list))
    for e, c in acc.items():
        reuse = c - 1
        if reuse < 1: continue
        t = tier_of(reuse)
        if t is None: continue
        kind, h, dg = ent_hop_deg(e)
        hops[kind][t].append(h); degs[kind][t].append(dg)

    labs = [lab for _, _, lab in TIERS]
    def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
    print(f"[{a.graph_name} alpha={a.alpha_label} start={a.start}]  tier: " + " ".join(labs))
    stats = {}
    for kind in ("node", "edge"):
        mh = [mean(hops[kind][l]) for l in labs]
        md = [mean(degs[kind][l]) for l in labs]
        nn = [len(hops[kind][l]) for l in labs]
        stats[kind] = (mh, md, nn)
        print(f"  {kind:4} mean_hop : " + " ".join(f"{v:6.1f}" for v in mh))
        print(f"  {kind:4} mean_deg : " + " ".join(f"{v:6.1f}" for v in md))
        print(f"  {kind:4} n_entity : " + " ".join(f"{v:6d}" for v in nn))

    x = list(range(len(labs)))
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.2))
    # (1) hop
    ax[0].plot(x, stats["node"][0], "-o", color="#2e6db4", lw=2.3, ms=8, label="node")
    ax[0].plot(x, stats["edge"][0], "-s", color="#e67e22", lw=2.3, ms=8, label="edge")
    ax[0].set_title("(1) mean hop from start (smaller = nearer)")
    ax[0].set_xlabel("reuse-count tier (right = reused more often)"); ax[0].set_ylabel("mean hop")
    # (2) degree
    ax[1].plot(x, stats["node"][1], "-o", color="#2e6db4", lw=2.3, ms=8, label="node (degree)")
    ax[1].plot(x, stats["edge"][1], "-s", color="#e67e22", lw=2.3, ms=8, label="edge (sum of endpoint deg)")
    ax[1].set_title("(2) mean degree  (node: NEARER & HIGHER,  edge: NEARER & LOWER)")
    ax[1].set_xlabel("reuse-count tier (right = reused more often)"); ax[1].set_ylabel("mean degree")
    for a_ in ax:
        a_.set_xticks(x); a_.set_xticklabels(labs); a_.grid(True, alpha=0.25); a_.legend()
    fig.suptitle(f"How node/edge change as they are reused more  —  {a.graph_name} (alpha={a.alpha_label}, start={a.start}, measured trace)",
                 fontsize=12)
    fig.tight_layout()
    out = a.out or str(Path(__file__).resolve().parent / f"cause_structure_{a.graph_name.lower()}_a{a.alpha_label.replace('.','')}.png")
    fig.savefig(out, dpi=130); print(f"[saved] {out}")

if __name__ == "__main__": main()
