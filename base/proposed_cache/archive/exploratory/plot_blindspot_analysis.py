#!/usr/bin/env python3
# =============================================================================
# plot_blindspot_analysis.py
#   「LRUが取り逃す再利用(=長間隔 cross-walk 再訪)」をトレースから分析し、
#   hop/degree との関係・価値あるノードの分布などを図にする。
#
#   位置づけ（重要）:
#     - トレースは cache=none の実走(=実測)。総アクセス・総再利用は実測値。
#     - LRUの hit/miss 分類は「トレース上のオフライン LRU シミュレーション」。
#     - 既定は node-only・単一始点(start=0)。実分散キャッシュの実測ではない。
#
#   使い方:
#     python3 base/proposed_cache/archive/exploratory/plot_blindspot_analysis.py
#     # 条件を変える:
#     GR=dataset/Louvain/graph/vldb.gr START=0 CAP=100 NLIMIT=120000 \
#       python3 base/proposed_cache/archive/exploratory/plot_blindspot_analysis.py
#
#   出力: base/proposed_cache/figs/*.png
# =============================================================================
import os, json, math, statistics
from collections import OrderedDict, deque, Counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------- config (環境変数で上書き可) ----------
ROOT   = os.path.dirname(os.path.abspath(__file__))          # base/proposed_cache
REPO   = os.path.abspath(os.path.join(ROOT, "..", ".."))     # repo root
GR     = os.environ.get("GR",    os.path.join(REPO, "dataset/Louvain/graph/vldb.gr"))
TRACE  = os.environ.get("TRACE", os.path.join(ROOT,
         "results/alpha0.01_walks_10000_capa_100/vldb_nobt/none_100_h-0/"
         "start=0_walks=10000_alpha=0.01_seed=42_none_100_h-0_per_walk_access.json"))
START  = int(os.environ.get("START", "0"))
CAP    = int(os.environ.get("CAP", "100"))
NLIMIT = int(os.environ.get("NLIMIT", "120000"))   # node-only 分析で使うアクセス数
FIGS   = os.path.join(ROOT, "figs")
TAG    = os.environ.get("TAG", "")   # 出力ファイル名のサフィックス (例: _amazon)
os.makedirs(FIGS, exist_ok=True)
def figpath(name):
    return os.path.join(FIGS, name.replace(".png", f"{TAG}.png"))

DBINS = [2, 4, 8, 16, 32, 64, 128]
DLAB  = ["1-2", "3-4", "5-8", "9-16", "17-32", "33-64", "65-128", ">128"]
def dbin(g):
    for i, t in enumerate(DBINS):
        if g <= t: return i
    return 7


# ---------- loaders ----------
def load_graph(path, start):
    adj = {}
    for line in open(path):
        p = line.split()
        if len(p) < 2: continue
        try: u, v = int(p[0]), int(p[1])
        except ValueError: continue
        adj.setdefault(u, set()).add(v); adj.setdefault(v, set()).add(u)
    deg = {k: len(vs) for k, vs in adj.items()}
    hop = {start: 0}; q = deque([start])
    while q:
        x = q.popleft()
        for y in adj.get(x, ()):
            if y not in hop: hop[y] = hop[x] + 1; q.append(y)
    return deg, hop

def load_stream(path, node_only=True, limit=None):
    d = json.load(open(path)); s = []
    for w in d.get("per_walk_access", []):
        for e, c in w.get("access", {}).items():
            if node_only:
                if e.startswith("edge_"): continue
                try: s += [int(e)] * c
                except ValueError: pass
            else:
                s += [e] * c
    return s[:limit] if limit else s


# ---------- LRU 分類 ----------
def lru_classify(stream, cap, weight_based=False):
    """各エンティティの missed(=LRU取り逃した再訪回数) と reuse を返す。
    weight_based: edge を重み2 として重み予算 cap を使う（node+edge 混在時）。"""
    is_edge = lambda x: isinstance(x, str) and x.startswith("edge_")
    od = OrderedDict(); seen = set(); wsum = 0
    missed = Counter(); reuse = Counter()
    for x in stream:
        ir = x in seen; hit = x in od
        if ir:
            reuse[x] += 1
            if not hit: missed[x] += 1
        if x in od:
            od.move_to_end(x)
        else:
            wt = 2 if (weight_based and is_edge(x)) else 1
            od[x] = wt; wsum += wt
            if weight_based:
                while wsum > cap and od:
                    _, wv = od.popitem(last=False); wsum -= wv
            else:
                if len(od) > cap: od.popitem(last=False)
        seen.add(x)
    return missed, reuse

def spearman(keys, fa, fb):
    a = [fa(k) for k in keys]; b = [fb[k] for k in keys]; n = len(keys)
    def rk(v):
        o = sorted(range(n), key=lambda i: v[i]); r = [0] * n
        for j, i in enumerate(o): r[i] = j
        return r
    ra, rb = rk(a), rk(b); ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(n)))
    return num / (da * db) if da * db else 0.0


# ---------- figures ----------
def fig_corr(deg, hop, missed, reuse, out):
    ks = [k for k in reuse if k in deg and k in hop]
    ht = spearman(ks, lambda k: -hop[k], reuse); hm = spearman(ks, lambda k: -hop[k], missed)
    dt = spearman(ks, lambda k: deg[k], reuse);  dm = spearman(ks, lambda k: deg[k], missed)
    fig, ax = plt.subplots(figsize=(8, 6))
    x = range(2); wd = 0.36
    ax.bar([i - wd/2 for i in x], [ht, dt], wd, color="#95a5a6", label="vs TOTAL reuse")
    ax.bar([i + wd/2 for i in x], [hm, dm], wd, color="#e67e22", label="vs LRU-MISSED reuse")
    for i, (t, m) in enumerate([(ht, hm), (dt, dm)]):
        ax.text(i - wd/2, t + .01, f"{t:.2f}", ha="center")
        ax.text(i + wd/2, m + .01, f"{m:.2f}", ha="center", weight="bold")
    ax.set_xticks(list(x)); ax.set_xticklabels(["hop (near)", "degree"])
    ax.set_ylabel("Spearman corr"); ax.set_ylim(0, 0.7); ax.legend()
    ax.set_title("Feature corr with reuse: TOTAL vs LRU-MISSED\n(missed > total = not redundant with recency)")
    plt.tight_layout(); plt.savefig(out, dpi=120); plt.close()
    return dict(hop=(ht, hm), degree=(dt, dm))

def fig_lru_vs_memo(deg, hop, stream, cap, out):
    """hop/degree 別: memo(=total reuse) を LRU-captured/ missed に分解 + miss rate."""
    od = OrderedDict(); seen = set()
    reuH = Counter(); misH = Counter(); reuD = Counter(); misD = Counter()
    for x in stream:
        h = min(hop.get(x, 99), 9); db = dbin(deg.get(x, 1))
        ir = x in seen; hit = x in od
        if ir:
            reuH[h] += 1; reuD[db] += 1
            if not hit: misH[h] += 1; misD[db] += 1
        if x in od: od.move_to_end(x)
        else:
            od[x] = 1
            if len(od) > cap: od.popitem(last=False)
        seen.add(x)
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.5))
    fig.suptitle("LRU vs memo by hop/degree: bar top = memo(=100% reuse); RED = LRU misses",
                 fontsize=11, weight="bold")
    def panel(a, keys, lab, reu, mis, cmis, title, xl):
        cap_ = [reu[k] - mis[k] for k in keys]; ms = [mis[k] for k in keys]
        a.bar(lab, cap_, color="#27ae60", label="LRU captures")
        a.bar(lab, ms, bottom=cap_, color=cmis, label="LRU MISSES")
        a.set_ylabel("reuse count (top=memo)"); a.set_xlabel(xl); a.set_title(title)
        a2 = a.twinx()
        a2.plot(lab, [mis[k] / reu[k] * 100 if reu[k] else 0 for k in keys], "-o",
                color="#8e44ad", lw=2, label="miss rate %")
        a2.set_ylim(0, 100); a2.set_ylabel("miss rate %", color="#8e44ad")
        h1, l1 = a.get_legend_handles_labels(); h2, l2 = a2.get_legend_handles_labels()
        a.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    H = list(range(10)); labH = [str(h) if h < 9 else "9+" for h in H]
    panel(ax[0], H, labH, reuH, misH, "#c0392b", "(1) by HOP", "hop from start")
    D = list(range(8)); panel(ax[1], D, DLAB, reuD, misD, "#2471a3", "(2) by DEGREE", "degree bucket")
    ax[1].tick_params(axis='x', rotation=30)
    plt.tight_layout(rect=[0, 0, 1, 0.95]); plt.savefig(out, dpi=120); plt.close()

def fig_cacheable(deg, hop, missed, out):
    """hop/degree 別: 取り逃し量を tier(1 / 2-4 / 5+) で色分け + cacheableノード数."""
    def collect(keyfn, nb):
        v1, v24, v5, nc = Counter(), Counter(), Counter(), Counter()
        for e, m in missed.items():
            b = keyfn(e)
            if m == 1: v1[b] += m
            elif m <= 4: v24[b] += m
            else: v5[b] += m
            if m >= 2: nc[b] += 1
        return v1, v24, v5, nc
    Hv1, Hv24, Hv5, Hnc = collect(lambda e: min(hop.get(e, 99), 9), 10)
    Dv1, Dv24, Dv5, Dnc = collect(lambda e: dbin(deg.get(e, 1)), 8)
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.5))
    fig.suptitle("Where are the nodes WORTH caching? (missed once=grey / 2-4=orange / 5+=red)\n"
                 "line = # nodes worth caching (missed>=2)", fontsize=10, weight="bold")
    def panel(a, keys, lab, v1, v24, v5, nc, xl):
        o = [v1[k] for k in keys]; t = [v24[k] for k in keys]; fv = [v5[k] for k in keys]
        a.bar(lab, o, color="#bdc3c7", label="missed once (NOT worth)")
        a.bar(lab, t, bottom=o, color="#e67e22", label="missed 2-4 (worth)")
        a.bar(lab, fv, bottom=[o[i] + t[i] for i in range(len(keys))], color="#c0392b", label="missed 5+ (high value)")
        a.set_ylabel("LRU-missed reuse volume"); a.set_xlabel(xl)
        a2 = a.twinx(); a2.plot(lab, [nc[k] for k in keys], "-D", color="#2c3e50", lw=2, label="# nodes worth caching")
        a2.set_ylabel("# cacheable nodes", color="#2c3e50")
        h1, l1 = a.get_legend_handles_labels(); h2, l2 = a2.get_legend_handles_labels()
        a.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    H = list(range(10)); labH = [str(h) if h < 9 else "9+" for h in H]
    panel(ax[0], H, labH, Hv1, Hv24, Hv5, Hnc, "hop from start"); ax[0].set_title("(1) by HOP")
    D = list(range(8)); panel(ax[1], D, DLAB, Dv1, Dv24, Dv5, Dnc, "degree bucket"); ax[1].set_title("(2) by DEGREE")
    ax[1].tick_params(axis='x', rotation=30)
    plt.tight_layout(rect=[0, 0, 1, 0.90]); plt.savefig(out, dpi=120); plt.close()

TIERS = [("1", lambda m: m == 1), ("2", lambda m: m == 2), ("3", lambda m: m == 3),
         ("4", lambda m: m == 4), ("5-9", lambda m: 5 <= m <= 9), ("10+", lambda m: m >= 10)]

def fig_tier_node_edge(missed, out):
    """再訪(取り逃し)回数 tier ごとに node vs edge の数・回収ヒット."""
    is_edge = lambda x: isinstance(x, str) and x.startswith("edge_")
    rows = []
    for lab, cond in TIERS:
        nn = ne = hn = he = 0
        for e, m in missed.items():
            if not cond(m): continue
            if is_edge(e): ne += 1; he += m
            else: nn += 1; hn += m
        rows.append((lab, nn, ne, hn, he))
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))
    x = range(len(rows)); wd = 0.38; labs = [r[0] for r in rows]
    ax[0].bar([i - wd/2 for i in x], [r[1] for r in rows], wd, color="#2980b9", label="node")
    ax[0].bar([i + wd/2 for i in x], [r[2] for r in rows], wd, color="#e67e22", label="edge")
    ax[0].set_xticks(list(x)); ax[0].set_xticklabels(labs); ax[0].set_ylabel("# entities")
    ax[0].set_xlabel("LRU-missed count"); ax[0].set_title("(1) # cacheable entities"); ax[0].legend()
    ax[1].bar([i - wd/2 for i in x], [r[3] for r in rows], wd, color="#2980b9", label="node")
    ax[1].bar([i + wd/2 for i in x], [r[4] for r in rows], wd, color="#e67e22", label="edge")
    ax[1].set_xticks(list(x)); ax[1].set_xticklabels(labs); ax[1].set_ylabel("recoverable hits")
    ax[1].set_xlabel("LRU-missed count"); ax[1].set_title("(2) recoverable hits"); ax[1].legend()
    fig.suptitle("Cacheable entities by missed count: NODE vs EDGE (weight-cap)", fontsize=11, weight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.93]); plt.savefig(out, dpi=120); plt.close()

def fig_profile_by_tier(deg, hop, missed, out):
    """tier ごとの degree 分布・hop 分布 (node-only)."""
    fig, ax = plt.subplots(len(TIERS), 2, figsize=(14, 15))
    fig.suptitle("Node profile by LRU-missed count: degree & hop distribution per tier",
                 fontsize=12, weight="bold")
    HB = list(range(10))
    for r, (lab, cond) in enumerate(TIERS):
        ents = [e for e, m in missed.items() if cond(m)]
        dc = Counter(dbin(deg.get(e, 1)) for e in ents)
        hc = Counter(min(hop.get(e, 99), 9) for e in ents)
        ax[r, 0].bar(DLAB, [dc.get(i, 0) for i in range(8)], color="#2980b9")
        ax[r, 0].set_ylabel(f"missed={lab}\n(n={len(ents)})", fontsize=9)
        ax[r, 1].bar([str(h) if h < 9 else "9+" for h in HB], [hc.get(h, 0) for h in HB], color="#c0392b")
        if r == 0: ax[r, 0].set_title("degree distribution"); ax[r, 1].set_title("hop distribution")
        if r == len(TIERS) - 1:
            ax[r, 0].set_xlabel("degree bucket"); ax[r, 0].tick_params(axis='x', rotation=30)
            ax[r, 1].set_xlabel("hop from start")
        else:
            ax[r, 0].set_xticklabels([])
        if ents:
            ax[r, 0].text(0.97, 0.85, f"mean deg={statistics.mean(deg.get(e,1) for e in ents):.0f}",
                          transform=ax[r, 0].transAxes, ha="right", fontsize=8, color="#1a5276")
            ax[r, 1].text(0.97, 0.85, f"mean hop={statistics.mean(hop.get(e,99) for e in ents):.1f}",
                          transform=ax[r, 1].transAxes, ha="right", fontsize=8, color="#922b21")
    plt.tight_layout(rect=[0, 0, 1, 0.96]); plt.savefig(out, dpi=110); plt.close()


def fig_profile_overlay(deg, hop, missed, out):
    """全 tier の degree/hop 分布を1枚に、正規化(%)して色分けで重ねる（コンパクト版）。"""
    cols = plt.cm.viridis([i / (len(TIERS) - 1) for i in range(len(TIERS))])
    fig, ax = plt.subplots(1, 2, figsize=(15, 6))
    fig.suptitle("Node profile shift by LRU-missed count (normalized per tier)",
                 fontsize=11, weight="bold")
    HB = list(range(10))
    for (lab, cond), c in zip(TIERS, cols):
        ents = [e for e, m in missed.items() if cond(m)]
        if not ents: continue
        n = len(ents)
        dc = Counter(dbin(deg.get(e, 1)) for e in ents)
        ax[0].plot(DLAB, [dc.get(i, 0) / n * 100 for i in range(8)], "-o", color=c,
                   lw=2, label=f"missed={lab} (n={n})")
        hc = Counter(min(hop.get(e, 99), 9) for e in ents)
        ax[1].plot([str(h) if h < 9 else "9+" for h in HB],
                   [hc.get(h, 0) / n * 100 for h in HB], "-o", color=c, lw=2,
                   label=f"missed={lab} (n={n})")
    ax[0].set_title("degree distribution (%)"); ax[0].set_xlabel("degree bucket")
    ax[0].set_ylabel("% of tier's nodes"); ax[0].tick_params(axis='x', rotation=30); ax[0].legend(fontsize=8)
    ax[1].set_title("hop distribution (%)"); ax[1].set_xlabel("hop from start")
    ax[1].set_ylabel("% of tier's nodes"); ax[1].legend(fontsize=8)
    plt.tight_layout(rect=[0, 0, 1, 0.93]); plt.savefig(out, dpi=120); plt.close()


def main():
    print(f"[cfg] GR={GR}\n      TRACE={TRACE}\n      START={START} CAP={CAP} NLIMIT={NLIMIT}")
    deg, hop = load_graph(GR, START)
    print(f"[graph] nodes={len(deg)}")

    # node-only 分析
    s_node = load_stream(TRACE, node_only=True, limit=NLIMIT)
    missed_n, reuse_n = lru_classify(s_node, CAP, weight_based=False)
    print(f"[node-only] accesses={len(s_node)} reuse-nodes={len(reuse_n)} missed-nodes={len(missed_n)}")

    fig_corr(deg, hop, missed_n, reuse_n, figpath("corr_total_vs_missed.png"))
    fig_lru_vs_memo(deg, hop, s_node, CAP, figpath("lru_vs_memo_by_hop_degree.png"))
    fig_cacheable(deg, hop, missed_n, figpath("cacheable_by_hop_degree.png"))
    fig_profile_by_tier(deg, hop, missed_n, figpath("profile_by_tier.png"))
    fig_profile_overlay(deg, hop, missed_n, figpath("profile_overlay.png"))

    # node+edge (weight cap) 分析
    s_all = load_stream(TRACE, node_only=False, limit=NLIMIT * 2 if NLIMIT else None)
    missed_a, _ = lru_classify(s_all, CAP, weight_based=True)
    fig_tier_node_edge(missed_a, figpath("tier_node_vs_edge.png"))

    print(f"[done] figures -> {FIGS}")
    print("  corr_total_vs_missed / lru_vs_memo_by_hop_degree / cacheable_by_hop_degree")
    print("  profile_by_tier / tier_node_vs_edge")


if __name__ == "__main__":
    main()
