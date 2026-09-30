#!/usr/bin/env python3
# =============================================================================
# plot_blindspot_analysis.py
#   「LRU が取り逃す再利用（＝キャッシュの死角）」を、実走トレース + オフライン
#   LRU シミュレーションから分析し、図を再生成する。
#
#   ■ データの位置づけ（重要・図にも注記される）
#     - 交通量(total access) / 再利用(total reuse=memo上限) : 実測（cache=none 実走トレース）
#     - LRU が拾う/取り逃す の分割                         : オフライン sim（トレース上でLRU再現）
#     - hop / degree                                     : グラフから計算（BFS距離・無向次数）
#   ＝「交通量・再利用は実測、LRUの取り逃し分割はシミュレーション」。
#
#   ■ 既定の環境（下の CONFIG で変更可）
#     graph=VLDB, start=0, alpha=0.01, node-only, 先頭 N=120,000 アクセス, LRU cap=100
#
#   使い方:
#     cd <repo root>
#     python3 base/proposed_cache/figs/plot_blindspot_analysis.py
#   出力: base/proposed_cache/figs/*.png
# =============================================================================
import json, math, os
from collections import OrderedDict, deque, Counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- CONFIG（ここを変えれば別条件で再生成できる） ----------------
GRAPH_PATH = "dataset/Louvain/graph/vldb.gr"
TRACE_PATH = ("base/proposed_cache/results/alpha0.01_walks_10000_capa_100/vldb_nobt/"
              "none_100_h-0/start=0_walks=10000_alpha=0.01_seed=42_none_100_h-0_"
              "per_walk_access.json")
START      = 0
N_ACCESS   = 120_000      # 先頭アクセス数（速度のため）
CAP        = 100          # LRU シミュレーションの容量（=「取り逃し」の定義）
CAPS_MULTI = [100, 500, 1000]   # cap 依存図で使う
OUTDIR     = "base/proposed_cache/figs"
# hop=0 の memo(LRU=22%)/上限(57%) は walks=10000 実測ベース（テンプレ図用）
LRU_REF, MEMO_REF = 22.0, 57.19
# ---------------------------------------------------------------------------

DBIN_TH = [2, 4, 8, 16, 32, 64, 128]
DLAB    = ["1-2", "3-4", "5-8", "9-16", "17-32", "33-64", "65-128", ">128"]


def load_graph(path):
    adj = {}
    for line in open(path):
        p = line.split()
        if len(p) < 2:
            continue
        try:
            u, v = int(p[0]), int(p[1])
        except ValueError:
            continue
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    deg = {k: len(vs) for k, vs in adj.items()}
    hop = {START: 0}
    q = deque([START])
    while q:
        x = q.popleft()
        for y in adj.get(x, ()):
            if y not in hop:
                hop[y] = hop[x] + 1
                q.append(y)
    return deg, hop


def load_stream(path, n):
    d = json.load(open(path))
    s = []
    for w in d.get("per_walk_access", []):
        for e, c in w.get("access", {}).items():
            if isinstance(e, str) and e.startswith("edge_"):
                continue          # node-only
            try:
                s += [int(e)] * c
            except (ValueError, TypeError):
                pass
    return s[:n]


def dbin(g):
    for i, t in enumerate(DBIN_TH):
        if g <= t:
            return i
    return 7


def spearman(keys, fa, fb):
    a = [fa(k) for k in keys]
    b = [fb[k] for k in keys]
    n = len(keys)
    def rank(v):
        o = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        for j, i in enumerate(o):
            r[i] = j
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(n)))
    db = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(n)))
    return num / (da * db) if da * db else 0.0


def lru_classify(stream, deg, hop, cap):
    """LRU cap を再現し、per-node/per-bin の 再利用・取り逃し を返す。"""
    od = OrderedDict(); seen = set()
    reuse_cnt = Counter(); missed_cnt = Counter()
    reuH = Counter(); misH = Counter(); reuD = Counter(); misD = Counter()
    lru_hit = 0; tot_reuse = 0
    for x in stream:
        ir = x in seen; hit = x in od
        h = min(hop.get(x, 99), 9); db = dbin(deg.get(x, 1))
        if ir:
            tot_reuse += 1; reuse_cnt[x] += 1; reuH[h] += 1; reuD[db] += 1
            if hit:
                lru_hit += 1
            else:
                missed_cnt[x] += 1; misH[h] += 1; misD[db] += 1
        if x in od:
            od.move_to_end(x)
        else:
            od[x] = 1
            if len(od) > cap:
                od.popitem(last=False)
        seen.add(x)
    return dict(reuse_cnt=reuse_cnt, missed_cnt=missed_cnt,
                reuH=reuH, misH=misH, reuD=reuD, misD=misD,
                lru_hit=lru_hit, tot_reuse=tot_reuse, N=len(stream))


# ============================ FIGURES ============================
def fig_corr(deg, hop, cls):
    ks = [k for k in cls["reuse_cnt"] if k in deg and k in hop]
    ht = spearman(ks, lambda k: -hop[k], cls["reuse_cnt"])
    hm = spearman(ks, lambda k: -hop[k], cls["missed_cnt"])
    dt = spearman(ks, lambda k: deg[k], cls["reuse_cnt"])
    dm = spearman(ks, lambda k: deg[k], cls["missed_cnt"])
    fig, ax = plt.subplots(figsize=(9, 6))
    g = ["hop (near)", "degree"]; tot = [ht, dt]; mis = [hm, dm]; x = range(2); wd = 0.36
    ax.bar([i - wd / 2 for i in x], tot, wd, color="#95a5a6", label="corr with TOTAL reuse")
    ax.bar([i + wd / 2 for i in x], mis, wd, color="#e67e22", label="corr with LRU-MISSED reuse")
    for i in x:
        ax.text(i - wd / 2, tot[i] + .01, f"{tot[i]:.2f}", ha="center")
        ax.text(i + wd / 2, mis[i] + .01, f"{mis[i]:.2f}", ha="center", weight="bold")
    ax.set_xticks(list(x)); ax.set_xticklabels(g); ax.set_ylabel("Spearman corr"); ax.set_ylim(0, .7)
    ax.set_title("Feature corr with reuse: TOTAL vs LRU-MISSED\n"
                 f"{os.path.basename(GRAPH_PATH)}, start={START}, N={N_ACCESS}, cap={CAP} (offline)")
    ax.legend()
    _save(fig, "corr_total_vs_missed.png")


def fig_lru_vs_memo(cls):
    fig, ax = plt.subplots(1, 2, figsize=(16, 6.5))
    fig.suptitle("LRU vs memo by hop/degree: bar top=memo(100% reuse), RED=LRU misses, purple=miss rate\n"
                 "(traffic&reuse=measured, LRU split=offline sim)", fontsize=11, weight="bold")

    def panel(a, keys, lab, reu, mis, cmis, title):
        cap = [reu[k] - mis[k] for k in keys]; ms = [mis[k] for k in keys]
        a.bar(lab, cap, color="#27ae60", label="LRU captures")
        a.bar(lab, ms, bottom=cap, color=cmis, label="LRU MISSES (memo gets)")
        a.plot(lab, [reu[k] for k in keys], "k_", ms=22, mew=2, label="memo ceiling (=reuse)")
        a.set_ylabel("reuse count (bar top=memo)"); a.set_title(title)
        a2 = a.twinx()
        a2.plot(lab, [mis[k] / reu[k] * 100 if reu[k] else 0 for k in keys], "-o",
                color="#8e44ad", lw=2, label="LRU miss rate %")
        a2.set_ylabel("miss rate % (vs memo)", color="#8e44ad"); a2.set_ylim(0, 100)
        h1, l1 = a.get_legend_handles_labels(); h2, l2 = a2.get_legend_handles_labels()
        a.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)

    H = list(range(10)); labH = [str(h) if h < 9 else "9+" for h in H]
    panel(ax[0], H, labH, cls["reuH"], cls["misH"], "#c0392b", "(1) by HOP")
    ax[0].set_xlabel("hop from start")
    D = list(range(8))
    panel(ax[1], D, DLAB, cls["reuD"], cls["misD"], "#2471a3", "(2) by DEGREE")
    ax[1].set_xlabel("degree bucket"); ax[1].tick_params(axis="x", rotation=30)
    _save(fig, "lru_vs_memo_by_hop_degree.png")


def fig_cap_dependence(deg, hop, stream):
    memo = None; res = {}
    for cap in CAPS_MULTI:
        cls = lru_classify(stream, deg, hop, cap)
        lru = cls["lru_hit"] / cls["N"] * 100
        miss = (cls["tot_reuse"] - cls["lru_hit"]) / cls["N"] * 100
        memo = cls["tot_reuse"] / cls["N"] * 100
        ks = [k for k in cls["reuse_cnt"] if k in deg and k in hop]
        ch = spearman(ks, lambda k: -hop[k], cls["missed_cnt"])
        cd = spearman(ks, lambda k: deg[k], cls["missed_cnt"])
        res[cap] = (lru, miss, ch, cd)
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    fig.suptitle("Cap-dependence: blind spot (upside) & feature correlation", fontsize=11, weight="bold")
    x = range(len(CAPS_MULTI)); lru = [res[c][0] for c in CAPS_MULTI]; miss = [res[c][1] for c in CAPS_MULTI]
    ax[0].bar(x, lru, color="#3498db", label="LRU captures")
    ax[0].bar(x, miss, bottom=lru, color="#e74c3c", label="LRU-missed (upside)")
    ax[0].axhline(memo, color="k", ls="--", label=f"memo={memo:.0f}%")
    for i, c in enumerate(CAPS_MULTI):
        ax[0].text(i, lru[i] + miss[i] / 2, f"{miss[i]:.0f}%", ha="center", color="white", weight="bold")
    ax[0].set_xticks(list(x)); ax[0].set_xticklabels([f"cap={c}" for c in CAPS_MULTI])
    ax[0].set_ylabel("hit rate %"); ax[0].set_title("(A) upside shrinks with cap"); ax[0].legend(fontsize=8)
    ch = [res[c][2] for c in CAPS_MULTI]; cd = [res[c][3] for c in CAPS_MULTI]; wd = .35
    ax[1].bar([i - wd / 2 for i in x], ch, wd, color="#c0392b", label="hop")
    ax[1].bar([i + wd / 2 for i in x], cd, wd, color="#2980b9", label="degree")
    ax[1].set_xticks(list(x)); ax[1].set_xticklabels([f"cap={c}" for c in CAPS_MULTI])
    ax[1].set_ylabel("corr with LRU-missed"); ax[1].set_title("(B) correlation stable across cap"); ax[1].legend()
    _save(fig, "cap_dependence_vldb.png")


def fig_wsweep_template():
    ws = [0, 50, 140, 400, 1000, 2000]
    hop = [LRU_REF, 28, 34, 33, 27, 20]   # ILLUSTRATIVE (not measured)
    deg = [LRU_REF, 25, 29, 28, 24, 18]
    fig, ax = plt.subplots(figsize=(9, 6))
    ax.axhline(LRU_REF, color="#2980b9", ls="--", label=f"LRU (w=0) = {LRU_REF:.0f}%")
    ax.axhline(MEMO_REF, color="#7f8c8d", ls="--", label=f"memo = {MEMO_REF:.0f}%")
    ax.fill_between([0, 2000], LRU_REF, MEMO_REF, color="#f1c40f", alpha=0.08)
    ax.plot(ws, hop, "-o", color="#c0392b", lw=2, label="hop")
    ax.plot(ws, deg, "-s", color="#27ae60", lw=2, label="degree")
    ax.set_xlabel("w (structural weight); w=0 is LRU"); ax.set_ylabel("hit rate %")
    ax.set_title("TEMPLATE (schematic, NOT measured): hit rate vs w"); ax.set_ylim(15, 60)
    ax.set_xticks(ws); ax.legend()
    _save(fig, "wsweep_template.png")


def _save(fig, name):
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    path = os.path.join(OUTDIR, name)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("saved:", path)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    deg, hop = load_graph(GRAPH_PATH)
    stream = load_stream(TRACE_PATH, N_ACCESS)
    cls = lru_classify(stream, deg, hop, CAP)
    fig_corr(deg, hop, cls)
    fig_lru_vs_memo(cls)
    fig_cap_dependence(deg, hop, stream)
    fig_wsweep_template()
    print("DONE")


if __name__ == "__main__":
    main()
