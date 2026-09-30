#!/usr/bin/env python3
"""Plot all four reuse rates for every graph, factor pair, alpha and entity type.

Reads the existing *_summary.csv files and writes one PNG. It does not replay
walks or modify the earlier best-group summary.
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

np = common.np
plt = common.plt
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
PAIRS = list(combinations(("D", "H", "O", "R"), 2))
GROUPS = (("Low", "Low"), ("Low", "High"), ("High", "Low"), ("High", "High"))
GROUP_LABELS = ("小×小", "小×大", "大×小", "大×大")


def read_condition(graph, alpha, horizon):
    path = HERE / f"{graph}_a{alpha}_w1000_s8_h{horizon}_summary.csv"
    if not path.exists():
        raise SystemExit(f"集計CSVがありません: {path}")
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return {(row["kind"], row["x"], row["y"], row["x_group"], row["y_group"]): row
            for row in rows}


def draw_panel(ax, tables, alpha, kind):
    rates = np.full((len(GRAPHS) * len(PAIRS), len(GROUPS)), np.nan)
    labels = []
    for graph_index, graph in enumerate(GRAPHS):
        condition = tables[graph, alpha]
        for pair_index, (x, y) in enumerate(PAIRS):
            row_index = graph_index * len(PAIRS) + pair_index
            labels.append(f"{graph}  {x}×{y}" if pair_index == 0 else f"    {x}×{y}")
            for group_index, (x_group, y_group) in enumerate(GROUPS):
                cell = condition.get((kind, x, y, x_group, y_group))
                n = int(cell["n"]) if cell else 0
                if n:
                    rates[row_index, group_index] = 100 * int(cell["reuses"]) / n

    image = ax.imshow(rates, cmap="YlGnBu", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(GROUPS)), GROUP_LABELS, fontsize=10)
    ax.tick_params(axis="x", top=True, labeltop=True, bottom=False, labelbottom=False)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.set_title(f"α={alpha} ／ {'ノード' if kind == 'node' else 'エッジ'}", fontsize=13, pad=30)
    ax.set_xticks(np.arange(-.5, len(GROUPS), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=.7)
    ax.tick_params(which="minor", bottom=False, left=False)
    for graph_index in range(1, len(GRAPHS)):
        ax.axhline(graph_index * len(PAIRS) - .5, color="#363636", linewidth=1.4)
    for (i, j), value in np.ndenumerate(rates):
        ax.text(j, i, f"{value:.1f}%" if np.isfinite(value) else "不足",
                ha="center", va="center", fontsize=7.5,
                color="white" if np.isfinite(value) and value >= 63 else "#17242e")
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=100)
    parser.add_argument("--alphas", nargs=2, default=["0.1", "0.01"])
    args = parser.parse_args()
    tables = {(graph, alpha): read_condition(graph, alpha, args.horizon)
              for graph in GRAPHS for alpha in args.alphas}

    fig, axes = plt.subplots(2, 2, figsize=(16, 28))
    for i, alpha in enumerate(args.alphas):
        for j, kind in enumerate(("node", "edge")):
            image = draw_panel(axes[i, j], tables, alpha, kind)
    fig.suptitle("全グラフ：2要素の4群すべての再アクセス率", fontsize=18, y=.992)
    fig.subplots_adjust(left=.13, right=.94, top=.965, bottom=.055, wspace=.30, hspace=.09)
    color_axis = fig.add_axes([.955, .20, .012, .60])
    fig.colorbar(image, cax=color_axis, label="再アクセス率 [%]")
    fig.text(.5, .018,
             "各行の左側に書いた2要素の値を、小×小・小×大・大×小・大×大の順で表示。"
             "Hは今回の歩数（小＝起点に近い側）。Rは直近性（大＝前回からの間隔が短い）。\n"
             "率＝次の指定アクセス件数以内に同じ対象が再出現した件数 ÷ その群のアクセス件数。"
             "色の尺度は全パネル共通。",
             ha="center", fontsize=10)

    output_dir = HERE / "summary"
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"all_graphs_h{args.horizon}_four_group_rates.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main()
