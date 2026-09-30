#!/usr/bin/env python3
"""Summarize conditional T-curve separation across all graph conditions.

Run this after plot_total_count_conditional.py has written the 12 condition CSVs:
    python3 base/proposed_cache/figs/access_factors_distribution/06_conditional_total/plot_summary.py

Writes summary/conditional_total_summary.png, .pdf, and .csv. No HTML.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import analyze as common

np = common.np
plt = common.plt
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
ALPHAS = ("0.1", "0.01")
PAIRS = list(combinations(("D", "H", "O", "R"), 2))
DIRECTIONS = [(x, z) for first, second in PAIRS
              for x, z in ((first, second), (second, first))]
CONDITIONS = [(graph, alpha) for alpha in ALPHAS for graph in GRAPHS]


def weighted_median(values, weights):
    order = np.argsort(values)
    sorted_values = np.asarray(values)[order]
    sorted_weights = np.asarray(weights)[order]
    return float(sorted_values[np.searchsorted(np.cumsum(sorted_weights),
                                               sorted_weights.sum() / 2, side="left")])


def summarize_direction(rows, minimum_bins):
    by_xbin = defaultdict(dict)
    for row in rows:
        if row["displayed"].lower() == "true":
            by_xbin[int(row["x_bin"])][int(row["control_bin"])] = row
    differences, weights, matched_cells = [], [], 0
    for strata in by_xbin.values():
        if len(strata) < 2:
            continue
        low = strata[min(strata)]
        high = strata[max(strata)]
        low_t, high_t = float(low["t_median"]), float(high["t_median"])
        if low_t <= 0 or high_t <= 0:
            continue
        differences.append(float(np.log2(high_t / low_t)))
        weights.append(min(int(low["n"]), int(high["n"])))
        matched_cells += 1
    if matched_cells < minimum_bins:
        return dict(n_bins=matched_cells, matched_n=sum(weights), log2_ratio=float("nan"),
                    high_over_low=float("nan"), direction_reverses=False,
                    min_bin_log2=float("nan"), max_bin_log2=float("nan"))
    value = weighted_median(differences, weights)
    return dict(n_bins=matched_cells, matched_n=sum(weights), log2_ratio=value,
                high_over_low=float(2 ** value),
                direction_reverses=any(v > 0 for v in differences) and any(v < 0 for v in differences),
                min_bin_log2=min(differences), max_bin_log2=max(differences))


def read_all(input_dir, minimum_bins):
    results = []
    for graph, alpha in CONDITIONS:
        path = input_dir / f"{graph}_a{alpha}_conditional_total.csv"
        if not path.exists():
            raise SystemExit(f"入力がありません: {path}\n先に plot_total_count_conditional.py を実行してください。")
        with path.open() as file:
            rows = list(csv.DictReader(file))
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["kind"], row["horizontal"], row["controlled"]].append(row)
        for kind in ("node", "edge"):
            for x, control in DIRECTIONS:
                stats = summarize_direction(grouped[kind, x, control], minimum_bins)
                results.append(dict(graph=graph, alpha=alpha, kind=kind,
                                    horizontal=x, controlled=control, **stats))
    return results


def draw(results, output_dir, sort_columns=False):
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "conditional_total_summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)

    lookup = {(r["kind"], r["graph"], r["alpha"], r["horizontal"], r["controlled"]): r
              for r in results}
    matrix_by_kind = {}
    directions_by_kind = {}
    for kind in ("node", "edge"):
        directions_by_kind[kind] = (
            sorted(
                DIRECTIONS,
                key=lambda direction: -np.nanmean([
                    abs(lookup[kind, graph, alpha, direction[0], direction[1]]["log2_ratio"])
                    for graph, alpha in CONDITIONS
                ]),
            ) if sort_columns else DIRECTIONS
        )
        matrix_by_kind[kind] = np.array([
            [lookup[kind, graph, alpha, x, control]["log2_ratio"]
             for x, control in directions_by_kind[kind]]
            for graph, alpha in CONDITIONS], dtype=float)
    finite = np.concatenate([m[np.isfinite(m)] for m in matrix_by_kind.values()])
    limit = max(1., float(np.ceil(np.quantile(np.abs(finite), .95)))) if finite.size else 1.

    fig, axes = plt.subplots(2, 1, figsize=(19, 14))
    for ax, kind in zip(axes, ("node", "edge")):
        matrix = matrix_by_kind[kind]
        directions = directions_by_kind[kind]
        image = ax.imshow(matrix, cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto")
        ax.set_xticks(range(len(directions)),
                      [f"{x}固定\n{control}比較" for x, control in directions], fontsize=9)
        ax.set_yticks(range(len(CONDITIONS)),
                      [f"{graph}  α={alpha}" for graph, alpha in CONDITIONS], fontsize=9)
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=15)
        ax.axhline(5.5, color="#333", linewidth=1.3)
        if not sort_columns:
            for j in range(1, len(directions), 2):
                ax.axvline(j + .5, color="#777", linewidth=.8)
        for i, (graph, alpha) in enumerate(CONDITIONS):
            for j, (x, control) in enumerate(directions):
                row = lookup[kind, graph, alpha, x, control]
                effect = row["log2_ratio"]
                if not np.isfinite(effect):
                    label = "不足"
                else:
                    ratio = row["high_over_low"]
                    label = f"×{ratio:.2g}" if ratio >= 1 else f"÷{1 / ratio:.2g}"
                    if row["direction_reverses"]:
                        label += "*"
                ax.text(j, i, label, ha="center", va="center", fontsize=8,
                        color="white" if np.isfinite(effect) and abs(effect) > .65 * limit else "#111")
    fig.suptitle("片方をそろえても、もう片方で全アクセス回数Tは変わるか", fontsize=18, y=.985)
    subtitle = "各マス＝固定した要素の帯の中で、比較要素の高い層 ÷ 低い層のT中央値。"
    subtitle += ("列はノード・エッジ別に平均|log₂(比)|の大きい順。" if sort_columns
                 else "赤＝高い層ほどT大、青＝T小。")
    fig.text(.5, .955, subtitle,
             ha="center", fontsize=10)
    fig.subplots_adjust(left=.115, right=.91, top=.92, bottom=.125, hspace=.22)
    color_axis = fig.add_axes([.93, .26, .012, .49])
    fig.colorbar(image, cax=color_axis, label="log₂(T中央値の比)")
    fig.text(.02, .024,
             "例：『D固定 H比較』×2 は、同じ次数帯でH高群のT中央値がH低群の約2倍。"
             "『÷2』なら約半分。2方向を別々に表示。\n"
             "* は固定要素の帯によって差の向きが反転。『不足』は比較できる帯が少ない。"
             "帯ごとの差と件数はCSV。OとTは定義上重複するため、Oを含む列は予測力として解釈しない。\n"
             "アクセス1回を1票。各固定要素の帯で高/低層の中央値比を計算し、両群の小さい方の件数で加重した中央値。"
             "色の上限は全セルの絶対値の95%点から設定（超える値は色が飽和）。",
             fontsize=9, linespacing=1.4)
    plt.rcParams["pdf.fonttype"] = 42
    basename = "conditional_total_summary_sorted" if sort_columns else "conditional_total_summary"
    for extension in ("png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{extension}", dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=HERE)
    parser.add_argument("--out", type=Path, default=HERE / "summary")
    parser.add_argument("--min-bins", type=int, default=2,
                        help="比較可能な固定要素の帯が最低いくつ必要か")
    parser.add_argument("--sort-columns", action="store_true",
                        help="ノード・エッジ別に平均|log2(比)|の大きい順で列を並べる")
    args = parser.parse_args()
    draw(read_all(args.input, args.min_bins), args.out, sort_columns=args.sort_columns)
    basename = "conditional_total_summary_sorted" if args.sort_columns else "conditional_total_summary"
    print(args.out / f"{basename}.png")


if __name__ == "__main__":
    main()
