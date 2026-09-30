#!/usr/bin/env python3
# =============================================================================
# plot_weights.py — learned の重みが walk を重ねるごとにどう動いたかを、グラフ間で比べる
#
#   入力: sim.py --trace-walks N --out-json X.json の出力（複数可。グラフ・α が混ざってよい）
#   出力: α ごとに1枚
#     行 = 型（node / edge）、列 = 特徴量（H 距離 / O 回数 / D 次数 / R 経過時間）
#     線 = グラフ。値は「サーバ平均 → seed 平均」、帯は seed 間の ±1 標準偏差
#     最下段 = learned の区間ヒット率（trace 間隔ごと）
#   重みの読み方: 正 = その値が大きいほど次のアクセスが遠い（追い出しやすい）
#
#   使い方:
#     python3 base/proposed_cache/adaptive/plot_weights.py results/weights/wt_*.json \
#       --out-dir base/proposed_cache/adaptive/results/weights
# =============================================================================
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

_AVAILABLE = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP"):
    if _f in _AVAILABLE:
        matplotlib.rcParams["font.family"] = _f
        break

# 項の表示名（structured の項も含む）。未知の項は名前そのまま
LABELS = {"H": "距離 H（hop）", "O": "回数 O", "D": "次数 D", "R": "経過時間 R",
          "H_node": "距離 H のノード追加分", "D_cond": "次数 D（条件付き）"}
TERM_ORDER = ["O", "R", "H", "H_node", "D", "D_cond"]
TYPE_LABELS = {"node": "ノード", "edge": "エッジ", "all": "共通"}
META = {"graph", "alpha", "seed", "policy", "walks_done", "sid", "type", "updates",
        "cum_hits", "cum_misses", "b"}
# 次数の不均一さ（CV）の小さい順。色はこの順で固定（グラフに付く色が図ごとに変わらないように）
GRAPH_ORDER = ["er_mid", "amazon", "ba_mid", "vldb", "slashdot", "enron"]
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"


def load(paths):
    rows = []
    for p in paths:
        d = json.loads(Path(p).read_text())
        rows.extend(d.get("weight_trace", []))
    return rows


def color_of(graph, graphs):
    ordered = [g for g in GRAPH_ORDER if g in graphs] + sorted(g for g in graphs if g not in GRAPH_ORDER)
    i = ordered.index(graph)
    return PALETTE[i % len(PALETTE)], MARKERS[i % len(MARKERS)], ordered


def series(rows, graph, typ, feat):
    """walks_done -> [seed ごとのサーバ平均]"""
    per = defaultdict(lambda: defaultdict(list))   # walks -> seed -> [server 値]
    for r in rows:
        if r["graph"] == graph and r["type"] == typ and feat in r:
            per[r["walks_done"]][r["seed"]].append(r[feat])
    xs = sorted(per)
    mean, sd = [], []
    for x in xs:
        vals = [st.mean(v) for v in per[x].values()]
        mean.append(st.mean(vals))
        sd.append(st.stdev(vals) if len(vals) > 1 else 0.0)
    return xs, mean, sd


def hit_series(rows, graph):
    """trace 間隔ごとの区間ヒット率（seed 平均）"""
    per = defaultdict(dict)   # seed -> walks -> (hits, misses)
    for r in rows:
        if r["graph"] == graph:
            per[r["seed"]][r["walks_done"]] = (r["cum_hits"], r["cum_misses"])
    by_x = defaultdict(list)
    for seed, pts in per.items():
        prev = (0, 0)
        for x in sorted(pts):
            h, m = pts[x]
            dh, dm = h - prev[0], m - prev[1]
            prev = (h, m)
            if dh + dm:
                by_x[x].append(100.0 * dh / (dh + dm))
    xs = sorted(by_x)
    return xs, [st.mean(by_x[x]) for x in xs]


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def plot_alpha(rows, alpha, out_dir: Path, policy: str):
    rows = [r for r in rows if abs(r["alpha"] - alpha) < 1e-12
            and r.get("policy", "learned") == policy]
    graphs = sorted({r["graph"] for r in rows})
    if not graphs:
        return None
    keys = set().union(*(r.keys() for r in rows))
    terms = [t for t in TERM_ORDER if t in keys] + \
        sorted(k for k in keys if k not in META and k not in TERM_ORDER)
    types = [t for t in ("node", "edge", "all") if any(r["type"] == t for r in rows)]
    ncol = len(terms)
    fig = plt.figure(figsize=(3.8 * ncol, 3.2 * len(types) + 3.2))
    gs = fig.add_gridspec(len(types) + 1, ncol, height_ratios=[1] * len(types) + [0.8],
                          hspace=0.5, wspace=0.28)
    _, _, ordered = color_of(graphs[0], graphs)

    for ri, typ in enumerate(types):
        typ_ja = TYPE_LABELS.get(typ, typ)
        for ci, feat in enumerate(terms):
            feat_ja = LABELS.get(feat, feat)
            ax = fig.add_subplot(gs[ri, ci])
            for g in ordered:
                c, mk, _ = color_of(g, graphs)
                xs, m, sd = series(rows, g, typ, feat)
                if not xs:
                    continue
                ax.fill_between(xs, [a - b for a, b in zip(m, sd)], [a + b for a, b in zip(m, sd)],
                                color=c, alpha=0.12, lw=0)
                ax.plot(xs, m, color=c, lw=2, marker=mk, ms=4, markevery=4, label=g)
            ax.axhline(0, color=MUTED, lw=1)
            ax.set_title(f"{typ_ja}：{feat_ja}", fontsize=10, color=INK, loc="left")
            if ci == 0:
                ax.set_ylabel("重み（z 値あたり）", fontsize=9, color=INK)
            if ri == len(types) - 1:
                ax.set_xlabel("walk 本数（累積）", fontsize=9, color=INK)
            style(ax)

    ax = fig.add_subplot(gs[len(types), :])
    for g in ordered:
        c, mk, _ = color_of(g, graphs)
        xs, hr = hit_series(rows, g)
        ax.plot(xs, hr, color=c, lw=2, marker=mk, ms=4, markevery=4, label=g)
    ax.set_title(f"{policy} の区間ヒット率（記録間隔ごと、seed 平均）", fontsize=10, color=INK, loc="left")
    ax.set_xlabel("walk 本数（累積）", fontsize=9, color=INK)
    ax.set_ylabel("ヒット率 [%]", fontsize=9, color=INK)
    style(ax)

    handles, labels = fig.axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, 1.0), title="グラフ（左ほど次数が均一）",
               title_fontsize=9)
    fig.suptitle(f"{policy} の重みの推移（α={alpha:g}）  正 = 値が大きいほど追い出しやすい／負 = 残しやすい",
                 fontsize=12, color=INK, y=1.02 + 0.18 / (len(types) + 1))
    suffix = "" if policy == "learned" else f"_{policy}"
    out = out_dir / f"weights_trajectory{suffix}_a{alpha:g}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def write_table(rows, out_dir: Path):
    """最終時点の重み（サーバ平均 → seed 平均）を CSV に書く（方策・グラフ・α・型ごと）"""
    last = defaultdict(lambda: defaultdict(list))
    maxw = defaultdict(int)
    for r in rows:
        k = (r.get("policy", "learned"), r["graph"], r["alpha"])
        maxw[k] = max(maxw[k], r["walks_done"])
    terms_of = {}
    for r in rows:
        pol = r.get("policy", "learned")
        k = (pol, r["graph"], r["alpha"])
        if r["walks_done"] == maxw[k]:
            terms = [t for t in r if t not in META]
            terms_of[pol] = terms
            for f in terms:
                last[(pol, r["graph"], r["alpha"], r["type"])][f].append(r[f])
    out = out_dir / "weights_final.csv"
    with out.open("w", encoding="utf-8") as fh:
        fh.write("policy,graph,alpha,type,terms...\n")
        for key in sorted(last, key=lambda k: (k[0], -k[2], GRAPH_ORDER.index(k[1]) if k[1] in GRAPH_ORDER else 99, k[3])):
            pol = key[0]
            vals = ",".join(f"{t}={st.mean(last[key][t]):.3f}" for t in terms_of[pol])
            fh.write(f"{pol},{key[1]},{key[2]},{key[3]},{vals}\n")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("json", nargs="+")
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load(a.json)
    for pol in sorted({r.get("policy", "learned") for r in rows}):
        for alpha in sorted({r["alpha"] for r in rows}, reverse=True):
            o = plot_alpha(rows, alpha, out_dir, pol)
            if o:
                print(f"[saved] {o}")
    print(f"[saved] {write_table(rows, out_dir)}")


if __name__ == "__main__":
    main()
