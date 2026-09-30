#!/usr/bin/env python3
"""One grouped-bar figure for all factor pairs and graph conditions.

Rows are the six factor pairs. Columns are alpha × node/edge. Each subplot
compares graphs on x, with the four High/Low groups as bar colors. Reads
existing *_summary.csv files; does not replay access traces.
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
COLORS = ("#9aaab5", "#3182bd", "#e5a14e", "#bd4b46")


def read_condition(graph, alpha, horizon):
    path = HERE / f"{graph}_a{alpha}_w1000_s8_h{horizon}_summary.csv"
    if not path.exists():
        raise SystemExit(f"集計CSVがありません: {path}")
    with path.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return {(r["kind"], r["x"], r["y"], r["x_group"], r["y_group"]): r
            for r in rows}


def draw_panel(ax, lookups, pair, alpha, kind, graphs, language="ja"):
    english = language == "en"
    labels = ("Low x Low", "Low x High", "High x Low", "High x High") if english else GROUP_LABELS
    centers = np.arange(len(graphs))
    bar_width = .19
    x, y = pair
    for group_index, ((x_group, y_group), label, color) in enumerate(
            zip(GROUPS, labels, COLORS)):
        rates = []
        for graph in graphs:
            row = lookups[graph, alpha].get((kind, x, y, x_group, y_group))
            n = int(row["n"]) if row else 0
            rates.append(100 * int(row["reuses"]) / n if n else np.nan)
        positions = centers + (group_index - 1.5) * bar_width
        ax.bar(positions, rates, width=bar_width, color=color, label=label)
    ax.set_xticks(centers, graphs, fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Reuse rate [%]" if english else "再アクセス率 [%]")
    ax.set_title(f"alpha={alpha} / {kind}" if english else f"α={alpha} ／ {'ノード' if kind == 'node' else 'エッジ'}", fontsize=13)
    ax.grid(axis="y", alpha=.2)
    ax.set_axisbelow(True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs", nargs="+", choices=GRAPHS, default=list(GRAPHS))
    parser.add_argument("--alphas", nargs=2, default=["0.1", "0.01"])
    parser.add_argument("--horizon", type=int, default=100)
    args = parser.parse_args()
    output_dir = HERE / "summary" / "grouped_bars"
    output_dir.mkdir(parents=True, exist_ok=True)
    lookups = {(graph, alpha): read_condition(graph, alpha, args.horizon)
               for graph in args.graphs for alpha in args.alphas}
    fig, axes = plt.subplots(len(PAIRS), 4, figsize=(28, 25), sharey=True)
    for row_index, pair in enumerate(PAIRS):
        for alpha_index, alpha in enumerate(args.alphas):
            for kind_index, kind in enumerate(("node", "edge")):
                col_index = alpha_index * 2 + kind_index
                ax = axes[row_index, col_index]
                draw_panel(ax, lookups, pair, alpha, kind, args.graphs)
                if row_index:
                    ax.set_title("")
                if col_index:
                    ax.set_ylabel("")
                if col_index == 0:
                    ax.text(-.21, .5, f"{pair[0]} × {pair[1]}", transform=ax.transAxes,
                            rotation=90, ha="center", va="center", fontsize=14,
                            fontweight="bold")
    fig.suptitle("全要素ペア・全グラフ：4群の再アクセス率", fontsize=21)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5, .975),
               ncol=4, title="各ペアの左の要素 × 右の要素")
    fig.subplots_adjust(left=.075, right=.99, top=.945, bottom=.065,
                        wspace=.10, hspace=.42)
    fig.text(.5, .022,
             "小・大は要素の値の大小。Hは今回の歩数（小＝起点に近い側）。"
             "Rは直近性（大＝前回からの間隔が短い）。\n"
             f"各棒＝その群のアクセスのうち、次の{args.horizon}アクセス以内に同じ対象が再出現した割合。",
             ha="center", fontsize=11)
    path = output_dir / f"all_pairs_h{args.horizon}_grouped_bars.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(path)


if __name__ == "__main__":
    main()
