#!/usr/bin/env python3
"""Plot only the six pairwise correlations among D, H, O, and R."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np
import analyze as common


HERE = Path(__file__).resolve().parent
INPUT = HERE / "03_information_overlap"
OUTPUT = HERE / "07_factor_pair_summary"
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
ALPHAS = ("0.1", "0.01")
PAIRS = ("D×H", "D×O", "D×R", "H×O", "H×R", "O×R")
CONDITIONS = [(graph, alpha) for alpha in ALPHAS for graph in GRAPHS]


def main():
    grouped = defaultdict(list)
    for graph, alpha in CONDITIONS:
        filename = INPUT / f"{graph}_a{alpha}_pair_correlations.csv"
        if not filename.exists():
            raise SystemExit(f"入力がありません: {filename}")
        with filename.open() as file:
            for row in csv.DictReader(file):
                grouped[row["kind"], graph, alpha, row["pair"]].append(float(row["rho"]))

    fig, axes = common.plt.subplots(1, 2, figsize=(17, 9))
    for ax, kind in zip(axes, ("node", "edge")):
        matrix = np.array([[np.mean(grouped[kind, graph, alpha, pair]) for pair in PAIRS]
                           for graph, alpha in CONDITIONS])
        image = ax.imshow(matrix, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(6), PAIRS, fontsize=11)
        ax.set_yticks(range(12), [f"{graph}  α={alpha}" for graph, alpha in CONDITIONS], fontsize=10)
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=15)
        ax.axhline(5.5, color="#333", linewidth=1.4)
        for i in range(12):
            for j in range(6):
                value = matrix[i, j]
                ax.text(j, i, f"{value:+.2f}", ha="center", va="center",
                        color="white" if abs(value) > .6 else "#111", fontsize=10)
    fig.suptitle("D・H・O・R：要素同士の相関", fontsize=20, y=.98)
    fig.text(.5, .935, "全6ペアのSpearman順位相関。赤＝正、青＝負。数値は8始点の相関の平均。",
             ha="center", fontsize=11)
    fig.subplots_adjust(left=.12, right=.89, top=.86, bottom=.19, wspace=.36)
    color_axis = fig.add_axes([.915, .25, .013, .54])
    fig.colorbar(image, cax=color_axis, label="順位相関 ρ")
    fig.text(.025, .035,
             "D＝次数、H＝今回の歩数、O＝アクセス前の累積回数、R＝アクセス前の直近性。"
             "アクセス1回を1票。ノード・エッジ別。\n"
             "Rが未定義の初回と、次の100アクセスまで観測できない末尾を除く。"
             "この図は要素同士の相関のみを示し、全アクセス回数Tは使っていない。",
             fontsize=9)
    OUTPUT.mkdir(exist_ok=True)
    common.plt.rcParams["pdf.fonttype"] = 42
    for extension in ("png", "pdf"):
        fig.savefig(OUTPUT / f"factor_pair_correlations.{extension}", dpi=150)
    common.plt.close(fig)
    print(OUTPUT / "factor_pair_correlations.png")


if __name__ == "__main__":
    main()
