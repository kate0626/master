#!/usr/bin/env python3
"""Draw one four-group comparison summary per graph from existing CSVs.

Run after plot_pair_four_groups.py:
    python3 base/proposed_cache/figs/access_factors_distribution/08_pair_four_groups/plot_summary.py
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
PAIRS = list(combinations(("D", "H", "O", "R"), 2))
GROUPS = (("Low", "Low"), ("Low", "High"), ("High", "Low"), ("High", "High"))
LABELS = {
    "D": ("次数小", "次数大"),
    "H": ("歩数少", "歩数多"),
    "O": ("過去少", "過去多"),
    "R": ("間隔長", "間隔短"),
}


def load_rows(path):
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    return {(r["kind"], r["x"], r["y"], r["x_group"], r["y_group"]): r for r in rows}


def draw_panel(ax, lookup, kind, alpha):
    rates = np.full((len(PAIRS), 4), np.nan)
    counts = np.zeros((len(PAIRS), 4), dtype=int)
    for i, (x, y) in enumerate(PAIRS):
        for j, (x_group, y_group) in enumerate(GROUPS):
            row = lookup.get((kind, x, y, x_group, y_group))
            if row is None:
                continue
            counts[i, j] = int(row["n"])
            if counts[i, j]:
                rates[i, j] = 100 * int(row["reuses"]) / counts[i, j]

    image = ax.imshow(rates, cmap="YlGnBu", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(6), ["A小\nB小", "A小\nB大", "A大\nB小", "A大\nB大",
                              "最高−次点", "交互作用差"], fontsize=9)
    ax.set_yticks(range(len(PAIRS)), [f"{x} × {y}" for x, y in PAIRS], fontsize=10)
    ax.set_xlim(-.5, 5.5)
    ax.set_title(f"α={alpha} ／ {'ノード' if kind == 'node' else 'エッジ'}", fontsize=13)
    ax.set_xticks(np.arange(-.5, 6, 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(PAIRS), 1), minor=True)
    ax.grid(which="minor", color="#d8d8d8", linewidth=.8)
    ax.tick_params(which="minor", bottom=False, left=False)

    for i, (x, y) in enumerate(PAIRS):
        valid = np.flatnonzero(np.isfinite(rates[i]))
        if not len(valid):
            continue
        top = sorted(valid, key=lambda j: rates[i, j], reverse=True)
        best = top[0]
        if len(top) > 1:
            gap = rates[i, top[0]] - rates[i, top[1]]
            ax.text(4, i, f"{gap:.1f} pt", ha="center", va="center", fontsize=9)
        if len(valid) == 4:
            interaction = rates[i, 3] - rates[i, 2] - rates[i, 1] + rates[i, 0]
            ax.text(5, i, f"{interaction:+.1f} pt", ha="center", va="center", fontsize=9)
        for j in range(4):
            if j not in valid:
                ax.text(j, i, "不足", ha="center", va="center", fontsize=9)
                continue
            label = (f"{LABELS[x][GROUPS[j][0] == 'High']}\n"
                     f"{LABELS[y][GROUPS[j][1] == 'High']}\n"
                     f"{rates[i, j]:.1f}%\n(n={counts[i, j]:,})")
            ax.text(j, i, label, ha="center", va="center", fontsize=8,
                    color="white" if rates[i, j] >= 63 else "#17242e")
        ax.add_patch(plt.Rectangle((best - .49, i - .49), .98, .98,
                                   fill=False, edgecolor="#e34a33", linewidth=2.7))
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs", nargs="+", choices=list(common.GRAPHS),
                        default=list(common.GRAPHS))
    parser.add_argument("--alphas", nargs="+", default=["0.1", "0.01"])
    parser.add_argument("--horizon", type=int, default=100)
    args = parser.parse_args()
    if len(args.alphas) != 2:
        parser.error("図は2行構成のため --alphas には2値を指定してください")
    output_dir = HERE / "summary"
    output_dir.mkdir(exist_ok=True)
    for graph in args.graphs:
        fig, axes = plt.subplots(2, 2, figsize=(19, 13),
                                 gridspec_kw={"wspace": .21, "hspace": .26})
        image = None
        for row_index, alpha in enumerate(args.alphas):
            path = HERE / f"{graph}_a{alpha}_w1000_s8_h{args.horizon}_summary.csv"
            if not path.exists():
                raise SystemExit(f"集計CSVがありません: {path}")
            lookup = load_rows(path)
            for col_index, kind in enumerate(("node", "edge")):
                image = draw_panel(axes[row_index, col_index], lookup, kind, alpha)
        fig.suptitle(f"{graph}：2要素の4群ごとの再アクセス率", fontsize=18)
        fig.text(.5, .035,
                 "各行の赤枠＝再アクセス率が最高の群。A・Bは行に書いた2要素を左から順に指す。"
                 "Hは今回の歩数（少＝起点に近い側）、Rは前回からの間隔（短＝最近）。\n"
                 "最高−次点＝上位2群の率の差。交互作用差＝大大−大小−小大＋小小。"
                 "各アクセスから次の指定件数以内の再アクセスを集計。",
                 ha="center", fontsize=10)
        fig.subplots_adjust(top=.92, bottom=.10, right=.93)
        fig.colorbar(image, ax=axes.ravel().tolist(), fraction=.015, pad=.015,
                     label="再アクセス率 [%]")
        for extension in ("png", "pdf"):
            output = output_dir / f"{graph}_h{args.horizon}_four_group_summary.{extension}"
            fig.savefig(output, dpi=150)
        plt.close(fig)
        print(output_dir / f"{graph}_h{args.horizon}_four_group_summary.png")


if __name__ == "__main__":
    main()
