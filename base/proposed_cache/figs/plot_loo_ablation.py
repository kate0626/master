#!/usr/bin/env python3
# =============================================================================
# plot_loo_ablation.py  —  leave-one-out 対照実験の図。
#
#   run_loo_ablation.py --out-raw で出した seed 別 CSV を読み、
#     Δ = (提案) − (その要素を外した構成)   ※同一 seed の差 (対応あり)
#   を要素別・node/edge/overall 別に棒グラフ化する。
#
#   使い方:
#     python3 base/proposed_cache/figs/plot_loo_ablation.py \
#       --raw base/proposed_cache/results/loo_VLDB_a0.1_raw.csv \
#             base/proposed_cache/results/loo_Amazon_a0.1_raw.csv \
#       --out base/proposed_cache/figs/loo_ablation.png
# =============================================================================
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib

from pathlib import Path as _P
_FIGS = _P(__file__).resolve().parent
_RES = _FIGS.parent / "results"
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# CSV の構成名 -> 図の英語ラベル (leave-one-out の対象要素)
ELEMENTS = [
    ("−Base (LRU最終アクセス)", "LRU base"),
    ("−Pool (プール分離)",      "Pool split"),
    ("−Freq (使用回数)",        "Freq"),
    ("−Close (始点からの近さ)", "Close"),
    ("−Deg (ノード次数)",       "Deg"),
]
METRICS = [("node_hit", "node"), ("edge_hit", "edge"), ("overall_hit", "overall")]


def load(path):
    """path -> (graph_name, {name: {seed: {metric: value}}})"""
    per = defaultdict(dict)
    graph = "?"
    for row in csv.DictReader(open(path)):
        graph = row["graph"]
        per[row["name"]][int(row["seed"])] = {
            m: float(row[m]) for m, _ in METRICS}
    return graph, per


def paired_delta(per, name, metric):
    """同一 seed で (提案 − name) を取り、mean と sd を返す。"""
    full = per["提案 (全要素)"]
    tgt = per.get(name)
    if not tgt:
        return None, None
    d = [full[s][metric] - tgt[s][metric] for s in sorted(full) if s in tgt]
    return st.mean(d), (st.stdev(d) if len(d) > 1 else 0.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", nargs="+",
                   default=[str(_RES / "loo_VLDB_a0.1_raw.csv"),
                            str(_RES / "loo_Amazon_a0.1_raw.csv")])
    p.add_argument("--out", default=str(_FIGS / "loo_ablation.png"))
    a = p.parse_args()

    datasets = [load(Path(x)) for x in a.raw]
    n = len(datasets)
    fig, axes = plt.subplots(1, n, figsize=(6.2 * n, 4.4), squeeze=False)

    colors = ["#4C72B0", "#DD8452", "#55A868"]
    hatches = ["", "//", ".."]

    for ax, (graph, per) in zip(axes[0], datasets):
        xs = range(len(ELEMENTS))
        width = 0.26
        for k, (metric, mlabel) in enumerate(METRICS):
            means, sds = [], []
            for cname, _ in ELEMENTS:
                m, s = paired_delta(per, cname, metric)
                means.append(0.0 if m is None else m)
                sds.append(0.0 if s is None else s)
            off = (k - 1) * width
            ax.bar([x + off for x in xs], means, width, yerr=sds, capsize=3,
                   label=mlabel, color=colors[k], hatch=hatches[k],
                   edgecolor="white", linewidth=0.6)
            for x, m, s in zip(xs, means, sds):
                top = m + s if m >= 0 else m - s
                ax.annotate(f"{m:+.1f}", (x + off, top),
                            textcoords="offset points",
                            xytext=(0, 4 if m >= 0 else -12),
                            ha="center", fontsize=7.5)

        ax.axhline(0, color="black", linewidth=1.0)
        ax.set_xticks(list(xs))
        ax.set_xticklabels([lab for _, lab in ELEMENTS])
        ax.set_ylabel("Δ hit rate  [pt]   (proposed − without the element)")
        ax.set_title(f"{graph}")
        ax.grid(axis="y", alpha=0.3, linewidth=0.5)
        ax.legend(fontsize=8, framealpha=0.9)

    fig.suptitle("Leave-one-out contribution of each element "
                 "(α=0.1, 1,000 RWers, capacity 100)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(a.out, dpi=180)
    print(f"[saved] {a.out}")

    # --- 数値も表で吐く (論文の表にそのまま貼れる形) ---
    print()
    hdr = f"{'element':<12}"
    for graph, _ in datasets:
        hdr += f"{graph+' node':>14}{graph+' edge':>14}{graph+' overall':>16}"
    print(hdr)
    for cname, lab in ELEMENTS:
        line = f"{lab:<12}"
        for _, per in datasets:
            for metric, _m in METRICS:
                m, s = paired_delta(per, cname, metric)
                line += f"{m:>+9.2f}±{s:<4.2f}" if m is not None else f"{'-':>14}"
        print(line)


if __name__ == "__main__":
    main()
