#!/usr/bin/env python3
"""One correlation-line summary per fixed factor, using D/H/T/R (no O)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import analyze as common


FACTORS = ("D", "H", "T", "R")
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
ALPHAS = ("0.1", "0.01")
CONDITIONS = [(graph, alpha) for alpha in ALPHAS for graph in GRAPHS]
NAMES = {"D": "次数 D", "H": "今回の歩数 H", "T": "全アクセス回数 T", "R": "直近性 R"}
COLORS = {"D": "#0072b2", "H": "#d55e00", "T": "#009e73", "R": "#cc79a7"}


def load(path):
    with path.open() as file:
        return {(row["graph"], row["alpha"], row["kind"], row["pair"]): float(row["mean_rho"])
                for row in csv.DictReader(file)}


def render(fixed, lookup, output):
    other_factors = [factor for factor in FACTORS if factor != fixed]
    fig, axes = common.plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    for ax, kind in zip(axes, ("node", "edge")):
        for other in other_factors:
            left, right = sorted((fixed, other), key=FACTORS.index)
            pair = f"{left}×{right}"
            values = [lookup[graph, alpha, kind, pair] for graph, alpha in CONDITIONS]
            for first, last in ((0, 6), (6, 12)):
                ax.plot(range(first, last), values[first:last], "o-", color=COLORS[other],
                        linewidth=2, markersize=5,
                        label=NAMES[other] if first == 0 else None)
        ax.axhline(0, color="#666", linewidth=1)
        ax.axvline(5.5, color="#777", linestyle="--", linewidth=1)
        ax.set_ylim(-1, 1)
        ax.set_ylabel("Spearman順位相関 ρ")
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=13)
        ax.grid(alpha=.2)
        ax.legend(title="相関を比べる要素", ncol=3, fontsize=9)
    axes[-1].set_xticks(range(12), [f"{graph}\nα={alpha}" for graph, alpha in CONDITIONS], fontsize=9)
    axes[-1].set_xlabel("グラフ・停止確率 α")
    fig.suptitle(f"{NAMES[fixed]}と他の3要素の相関", fontsize=17)
    fig.text(.02, .016,
             "ρ=0付近は単調な相関が弱い。+1に近いほど一緒に増え、−1に近いほど片方が増えるともう片方が減る。"
             "線は同じαの6グラフ内だけを接続。\n"
             "アクセス1回を1票とし、Rが未定義の初回は全ペアから除外。Tは各始点の全アクセス回数で、"
             "同じ対象の各アクセスに同じ値を付ける。この図は要素同士の相関であり、追加価値は示さない。",
             fontsize=9)
    fig.tight_layout(rect=(0, .07, 1, .94))
    common.plt.rcParams["pdf.fonttype"] = 42
    for extension in ("png", "pdf"):
        fig.savefig(output / f"fixed_{fixed}_pair_correlations.{extension}", dpi=150)
    common.plt.close(fig)


def main():
    source = HERE / "total_count_pair_correlations_summary.csv"
    if not source.exists():
        raise SystemExit(f"集計CSVがありません: {source}")
    output = HERE / "by_fixed"
    output.mkdir(exist_ok=True)
    lookup = load(source)
    for fixed in FACTORS:
        render(fixed, lookup, output)
        print(output / f"fixed_{fixed}_pair_correlations.png")


if __name__ == "__main__":
    main()
