#!/usr/bin/env python3
"""Plot the observed ranges of D/H/O/R for two graphs, by entity type.

Run from the repository root:
    python3 base/proposed_cache/figs/access_factors_distribution/plot_factor_ranges.py

Default: VLDB and Amazon, each at alpha=0.1 and 0.01. Each run writes one PNG,
one PDF, and one CSV to 04_factor_ranges/. No HTML is produced.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np
import analyze as common


HERE = Path(__file__).resolve().parent
DEFAULT_GRAPHS = ("vldb", "amazon")
FACTOR_LABELS = {
    "D": "D：次数",
    "H": "H：今回の歩数",
    "O": "O：今回より前の累積アクセス回数",
    "R": "R：直近性 1/(1+A)",
}


def observed_values(sequence, degree):
    """Record factor values immediately before each access updates O and R."""
    values = {factor: [] for factor in FACTOR_LABELS}
    counts = Counter()
    last_access = {}
    for index, (entity, hop) in enumerate(sequence):
        values["D"].append(degree[entity])
        values["H"].append(hop)
        values["O"].append(counts[entity])
        age = index - last_access[entity] if entity in last_access else None
        values["R"].append(1.0 / (1.0 + age) if age is not None else np.nan)
        counts[entity] += 1
        last_access[entity] = index
    is_edge = np.fromiter((not isinstance(entity, int) for entity, _ in sequence),
                          dtype=bool, count=len(sequence))
    return {factor: np.asarray(samples, dtype=float) for factor, samples in values.items()}, is_edge


def summarize(samples):
    samples = samples[np.isfinite(samples)]
    if not len(samples):
        return None
    q = np.quantile(samples, [0, .1, .25, .5, .75, .9, .99, 1])
    return dict(n=len(samples), minimum=q[0], p10=q[1], p25=q[2],
                median=q[3], p75=q[4], p90=q[5], p99=q[6], maximum=q[7])


def plot_condition(graph, alpha, cluster, degree, output_dir):
    tag = f"{graph}_a{alpha}_w1000_s8"
    meta_path = common.PC / "results" / "cap" / f"accessfactors_{tag}_keys.meta.json"
    meta = json.loads(meta_path.read_text())
    config = meta["config"]
    assert float(config["alpha"]) == float(alpha)
    assert config["owner_filter"] is None and config["partition_dir"] is None

    by_type = {kind: {factor: [] for factor in FACTOR_LABELS}
               for kind in ("node", "edge")}
    for start in meta["starts"]:
        sequence = common.source.run_walks(
            cluster, [start], config["walks"], float(alpha), config["seed"],
            config["max_hops"], config["no_backtrack"]
        )[start]
        if len(sequence) != meta["per_start"][str(start)]["n_access"]:
            raise RuntimeError(f"Access count differs from source data: {tag}, start={start}")
        factors, is_edge = observed_values(sequence, degree)
        for kind, mask in (("node", ~is_edge), ("edge", is_edge)):
            for factor, samples in factors.items():
                selected = samples[mask]
                selected = selected[np.isfinite(selected)]
                by_type[kind][factor].append(selected)

    fig, axes = common.plt.subplots(2, 4, figsize=(19, 8), sharey=True)
    csv_rows = []
    for row, kind in enumerate(("node", "edge")):
        for col, (factor, label) in enumerate(FACTOR_LABELS.items()):
            ax = axes[row, col]
            per_start = by_type[kind][factor]
            pooled = np.concatenate(per_start)
            stats = summarize(pooled)
            if stats is None:
                ax.text(.5, .5, "データなし", ha="center", va="center", transform=ax.transAxes)
                continue
            csv_rows.append(dict(graph=graph, alpha=alpha, kind=kind, factor=factor, **stats))

            # Thin lines show individual starts; the thick line is all observed accesses.
            # Quantile coordinates keep these curves small even for large access traces.
            fractions = np.linspace(0, 1, 501)
            for samples in per_start:
                if len(samples):
                    ax.plot(np.quantile(samples, fractions), fractions * 100,
                            color="#94a3b8", alpha=.4, linewidth=.8)
            ax.plot(np.quantile(pooled, fractions), fractions * 100,
                    color="#096b9b", linewidth=2.2)
            ax.axvspan(stats["p10"], stats["p90"], color="#f3be63", alpha=.22)
            ax.axvline(stats["median"], color="#be531e", linestyle="--", linewidth=1.5)
            if factor == "O":
                ax.set_xscale("symlog", linthresh=1)
            else:
                ax.set_xscale("log")
            ax.set_ylim(0, 100)
            ax.set_xlabel(label)
            ax.grid(alpha=.2)
            ax.text(.04, .96,
                    f"10–90%: {stats['p10']:.3g}–{stats['p90']:.3g}\n"
                    f"中央値: {stats['median']:.3g}\n"
                    f"99%: {stats['p99']:.3g}  最大: {stats['maximum']:.3g}",
                    transform=ax.transAxes, va="top", fontsize=9,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=.86))
            if row == 0:
                ax.set_title(label, fontsize=11)
            if col == 0:
                ax.set_ylabel(f"{kind}\n累積割合 [%]")

    fig.suptitle(f"{graph} / α={alpha}：各要素の値の分布", fontsize=17)
    fig.text(.025, .025,
             "横軸＝要素の元の値（対数目盛。Oのみ0を含むためsymlog）。縦軸＝その値以下のアクセスの割合。"
             "灰色＝始点ごと、青＝全始点のアクセスをまとめた分布。橙＝中央80%、破線＝中央値。\n"
             "アクセス1回を1票として集計。O・Rはアクセス直前の値。Rは前回アクセスのない初回を除く。"
             "Hは今回の歩数。次数Dはノードの隣接エッジ数／エッジの両端点次数の平均。",
             fontsize=9)
    fig.tight_layout(rect=(0, .085, 1, .94))
    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(output_dir / f"{tag}_ranges.{ext}", dpi=150)
    common.plt.close(fig)
    with (output_dir / f"{tag}_ranges.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs", nargs="+", choices=list(common.GRAPHS), default=list(DEFAULT_GRAPHS))
    parser.add_argument("--alphas", nargs="+", choices=("0.1", "0.01"), default=["0.1", "0.01"])
    parser.add_argument("--out", type=Path, default=HERE / "04_factor_ranges")
    args = parser.parse_args()
    for graph in args.graphs:
        edge_path = common.ROOT / "dataset/Louvain/graph" / common.GRAPHS[graph]
        edges = common.source.srv.load_edge_list(edge_path)
        cluster = common.source.sim.VirtualCluster(edges, 2, None)
        cluster.build_servers(lambda: common.source.srv.build_authz_cache("none", 0), 1.0, 1.0)
        degree = common.source.build_degree_map(cluster)
        for alpha in args.alphas:
            plot_condition(graph, alpha, cluster, degree, args.out)
            print(args.out / f"{graph}_a{alpha}_w1000_s8_ranges.png")


if __name__ == "__main__":
    main()
