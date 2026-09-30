#!/usr/bin/env python3
"""Summarize the best observed four-group cell for all graphs in one figure.

Run after plot_pair_four_groups.py:
    python3 base/proposed_cache/figs/access_factors_distribution/08_pair_four_groups/plot_all_graphs_summary.py
"""
from __future__ import annotations

import argparse
import csv
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import analyze as common
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

np = common.np
plt = common.plt
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
PAIRS = list(combinations(("D", "H", "O", "R"), 2))
GROUPS = (("Low", "Low"), ("Low", "High"), ("High", "Low"), ("High", "High"))
SHORT = ("小・小", "小・大", "大・小", "大・大")
COLORS = ("#e0e0e0", "#7db9d3", "#e3ae79", "#8ec99c")


def read_rates(graph, alpha, horizon):
    path = HERE / f"{graph}_a{alpha}_w1000_s8_h{horizon}_summary.csv"
    if not path.exists():
        raise SystemExit(f"集計CSVがありません: {path}")
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    return {(row["kind"], row["x"], row["y"], row["x_group"], row["y_group"]): row
            for row in rows}


def draw_panel(ax, tables, alpha, kind):
    best_groups = np.full((len(GRAPHS), len(PAIRS)), np.nan)
    descriptions = {}
    for i, graph in enumerate(GRAPHS):
        table = tables[graph, alpha]
        for j, (x, y) in enumerate(PAIRS):
            rates = []
            for a, b in GROUPS:
                row = table.get((kind, x, y, a, b))
                n = int(row["n"]) if row else 0
                rates.append(100 * int(row["reuses"]) / n if n else np.nan)
            valid = [k for k, rate in enumerate(rates) if np.isfinite(rate)]
            if len(valid) < 2:
                descriptions[i, j] = "不足"
                continue
            order = sorted(valid, key=lambda k: rates[k], reverse=True)
            first, second = order[:2]
            best_groups[i, j] = first
            descriptions[i, j] = (f"{SHORT[first]}  {rates[first]:.1f}%\n"
                                  f"次点との差 {rates[first] - rates[second]:.1f}pt")

    cmap = ListedColormap(COLORS)
    cmap.set_bad("#ffffff")
    ax.imshow(best_groups, cmap=cmap, vmin=-.5, vmax=3.5, aspect="auto")
    ax.set_xticks(range(len(PAIRS)), [f"{x} × {y}" for x, y in PAIRS], fontsize=10)
    ax.set_yticks(range(len(GRAPHS)), GRAPHS, fontsize=10)
    ax.set_title(f"α={alpha} ／ {'ノード' if kind == 'node' else 'エッジ'}", fontsize=13)
    ax.set_xticks(np.arange(-.5, len(PAIRS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(GRAPHS), 1), minor=True)
    ax.grid(which="minor", color="#ffffff", linewidth=2)
    ax.tick_params(which="minor", bottom=False, left=False)
    for (i, j), label in descriptions.items():
        ax.text(j, i, label, ha="center", va="center", fontsize=8.5, color="#17242e")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=100)
    parser.add_argument("--alphas", nargs=2, default=["0.1", "0.01"])
    args = parser.parse_args()
    tables = {(graph, alpha): read_rates(graph, alpha, args.horizon)
              for graph in GRAPHS for alpha in args.alphas}
    fig, axes = plt.subplots(2, 2, figsize=(17, 13), layout="constrained")
    for i, alpha in enumerate(args.alphas):
        for j, kind in enumerate(("node", "edge")):
            draw_panel(axes[i, j], tables, alpha, kind)
    fig.suptitle("全グラフ：2要素の4群で再アクセス率が最も高い群", fontsize=18)
    fig.legend(handles=[Patch(facecolor=color, label=label)
                        for color, label in zip(COLORS, SHORT)],
               loc="lower center", ncol=4, bbox_to_anchor=(.5, -.005), title="色＝最高群の分類")
    # fig.text(.5, .035,
    #          "各マス：最高群の分類と再アクセス率、次点群との差。小・大はペア名の左・右の要素の値の大小。"
    #          "Hは今回の歩数（小＝近い側）、Rは直近性（大＝前回からの間隔が短い）。\n"
    #          "同率の場合は小・小→小・大→大・小→大・大の順で表示。"
    #          "最高群は観測上の最高であり、加算・乗算の優劣を示さない。",
    #          ha="center", fontsize=10)
    output = HERE / "summary"
    output.mkdir(exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(output / f"all_graphs_h{args.horizon}_four_group_summary.{extension}", dpi=150)
    plt.close(fig)
    print(output / f"all_graphs_h{args.horizon}_four_group_summary.png")


if __name__ == "__main__":
    main()
