#!/usr/bin/env python3
"""Plot the conditional T difference as lines, one figure per fixed factor."""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import analyze as common


FACTORS = ("D", "H", "O", "R")
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
ALPHAS = ("0.1", "0.01")
CONDITIONS = [(graph, alpha) for alpha in ALPHAS for graph in GRAPHS]
COLORS = {"D": "#0072b2", "H": "#d55e00", "O": "#009e73", "R": "#cc79a7"}
NAMES = {"D": "次数 D", "H": "今回の歩数 H", "O": "累積回数 O", "R": "直近性 R"}


def load(path):
    with path.open() as file:
        return {(r["graph"], r["alpha"], r["kind"], r["horizontal"], r["controlled"]): r
                for r in csv.DictReader(file)}


def render(fixed, lookup, output):
    import numpy as np

    compared = [factor for factor in FACTORS if factor != fixed]
    series = {}
    for kind in ("node", "edge"):
        for factor in compared:
            rows = [lookup[graph, alpha, kind, fixed, factor] for graph, alpha in CONDITIONS]
            values = np.array([float(row["log2_ratio"]) for row in rows])
            reverse = np.array([row["direction_reverses"] == "True" for row in rows])
            series[kind, factor] = (values, reverse)
    finite = np.concatenate([values[np.isfinite(values)] for values, _ in series.values()])
    limit = max(2.0, math.ceil(float(np.max(np.abs(finite))) + .5)) if finite.size else 2.0

    fig, axes = common.plt.subplots(2, 1, figsize=(16, 10), sharex=True)
    for ax, kind in zip(axes, ("node", "edge")):
        for factor in compared:
            values, reverse = series[kind, factor]
            color = COLORS[factor]
            for first, last in ((0, 6), (6, 12)):
                ax.plot(range(first, last), values[first:last], "o-", linewidth=2,
                        markersize=6, color=color,
                        label=NAMES[factor] if first == 0 else None)
            marked = np.where(reverse & np.isfinite(values))[0]
            if len(marked):
                ax.scatter(marked, values[marked], marker="*", s=135,
                           color="#111", edgecolors="white", linewidths=.6, zorder=5)
        ax.axhline(0, color="#555", linewidth=1)
        ax.axvline(5.5, color="#777", linestyle="--", linewidth=1)
        ax.set_ylim(-limit, limit)
        ax.set_ylabel("log₂(T中央値の比)")
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=13)
        ax.grid(alpha=.2)
        ax.legend(title="比較する要素", fontsize=9, ncol=3)
    axes[-1].set_xticks(range(12),
                        [f"{graph}\nα={alpha}" for graph, alpha in CONDITIONS],
                        fontsize=9)
    axes[-1].set_xlabel("グラフ・停止確率 α")
    fig.suptitle(f"{NAMES[fixed]}をそろえたとき、他の要素と全アクセス回数Tの関係は残るか", fontsize=17)
    fig.text(.02, .015,
             "縦軸0＝T中央値が同じ。+1＝比較要素の高い群でTが2倍、−1＝半分。"
             "固定した要素の各帯で求めた比を、比較人数で加重して中央値を取った値。\n"
             "★＝固定要素の帯によって差の向きが反転。線は同じαの6グラフ内だけを接続。"
             "OとTには定義上の重複があるため、Oを含む比較を予測力とは解釈しない。",
             fontsize=10)
    fig.tight_layout(rect=(0, .065, 1, .94), h_pad=1.2)
    common.plt.rcParams["pdf.fonttype"] = 42
    for extension in ("png", "pdf"):
        fig.savefig(output / f"fixed_{fixed}.{extension}", dpi=150)
    common.plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=HERE / "conditional_total_summary.csv")
    parser.add_argument("--out", type=Path, default=HERE / "by_fixed")
    args = parser.parse_args()
    if not args.input.exists():
        raise SystemExit(f"サマリCSVがありません: {args.input}")
    lookup = load(args.input)
    args.out.mkdir(parents=True, exist_ok=True)
    for fixed in FACTORS:
        render(fixed, lookup, args.out)
        print(args.out / f"fixed_{fixed}.png")


if __name__ == "__main__":
    main()
