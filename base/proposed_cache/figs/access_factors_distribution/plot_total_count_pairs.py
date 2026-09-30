#!/usr/bin/env python3
"""Six pairwise Spearman correlations with full access count T instead of cumulative O."""
from __future__ import annotations

import csv
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import analyze as common


HERE = Path(__file__).resolve().parent
OUT = HERE / "05_total_count_pairs"
GRAPHS = ("er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron")
ALPHAS = ("0.1", "0.01")
FACTORS = ("D", "H", "T", "R")
PAIRS = list(combinations(FACTORS, 2))


def observed_factors(sequence, degree):
    events, _, total, first = common.records(sequence, degree)
    r = 1.0 / (1.0 + events["A"])
    return {"D": events["D"], "H": events["H"], "T": events["T"], "R": r,
            "is_edge": events["edge"]}, total, first


def collect():
    data = []
    for graph in GRAPHS:
        print(f"Loading {graph}", flush=True)
        edges = common.source.srv.load_edge_list(common.ROOT / "dataset/Louvain/graph" / common.GRAPHS[graph])
        cluster = common.source.sim.VirtualCluster(edges, 2, None)
        cluster.build_servers(lambda: common.source.srv.build_authz_cache("none", 0), 1.0, 1.0)
        degree = common.source.build_degree_map(cluster)
        for alpha in ALPHAS:
            tag = f"{graph}_a{alpha}_w1000_s8"
            original = common.PC / "results/cap" / f"accessfactors_{tag}_keys.csv"
            meta = json.loads(original.with_suffix(".meta.json").read_text())
            config = meta["config"]
            assert config["partition_dir"] is None and config["owner_filter"] is None
            expected = {}
            with original.open() as file:
                for row in csv.DictReader(file):
                    expected.setdefault(int(row["start"]), {})[row["entity"]] = (
                        int(float(row["total"])), float(row["hop_walk"]), float(row["deg"]))
            for start in meta["starts"]:
                sequence = common.source.run_walks(cluster, [start], config["walks"], float(alpha),
                                                   config["seed"], config["max_hops"], config["no_backtrack"])[start]
                factors, total, first = observed_factors(sequence, degree)
                actual = {str(entity): (count, float(first[entity]), degree[entity])
                          for entity, count in total.items()}
                if actual != expected[start] or len(sequence) != meta["per_start"][str(start)]["n_access"]:
                    raise RuntimeError(f"Access trace differs from saved data: {tag} start={start}")
                for kind, is_edge in (("node", False), ("edge", True)):
                    # Same event population for all six pairs: R is undefined at first access.
                    mask = (factors["is_edge"] == is_edge) & np.isfinite(factors["R"])
                    ranked = np.column_stack([common.source.rankdata(factors[factor][mask])
                                              for factor in FACTORS])
                    matrix = np.corrcoef(ranked, rowvar=False)
                    for left, right in PAIRS:
                        data.append(dict(graph=graph, alpha=alpha, kind=kind, start=start,
                                         pair=f"{left}×{right}", n=int(mask.sum()),
                                         rho=float(matrix[FACTORS.index(left), FACTORS.index(right)])))
            print(f"  {graph} α={alpha}: 8 starts matched saved trace", flush=True)
        del cluster, degree, edges
    return data


def plot(data):
    common.plt.rcParams["pdf.fonttype"] = 42
    OUT.mkdir(exist_ok=True)
    with (OUT / "total_count_pair_correlations_by_start.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)
    labels = [f"{left}×{right}" for left, right in PAIRS]
    summary = []
    fig, axes = common.plt.subplots(1, 2, figsize=(18, 10))
    for ax, kind in zip(axes, ("node", "edge")):
        matrix = np.full((12, 6), np.nan)
        for i, (alpha, graph) in enumerate((alpha, graph) for alpha in ALPHAS for graph in GRAPHS):
            for j, pair in enumerate(labels):
                values = [row["rho"] for row in data
                          if row["graph"] == graph and row["alpha"] == alpha
                          and row["kind"] == kind and row["pair"] == pair]
                matrix[i, j] = float(np.mean(values))
                summary.append(dict(graph=graph, alpha=alpha, kind=kind, pair=pair,
                                    mean_rho=matrix[i, j], sd_rho=float(np.std(values)), n_starts=len(values)))
        image = ax.imshow(matrix, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(6), labels, fontsize=11)
        ax.set_yticks(range(12), [f"{graph}  α={alpha}" for alpha in ALPHAS for graph in GRAPHS], fontsize=10)
        ax.axhline(5.5, color="#333", linewidth=1.4)
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=17, pad=12)
        for i in range(12):
            for j in range(6):
                value = matrix[i, j]
                ax.text(j, i, f"{value:+.2f}", ha="center", va="center",
                        color="white" if abs(value) > .6 else "#111", fontsize=11)
    with (OUT / "total_count_pair_correlations_summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    fig.suptitle("D・H・全アクセス回数T・Rの要素間相関", fontsize=20, y=.97)
    fig.text(.5, .927, "Spearman順位相関：赤＝正、青＝負。各セルは8始点で求めた相関の平均。",
             ha="center", fontsize=12)
    fig.subplots_adjust(left=.11, right=.9, top=.85, bottom=.21, wspace=.37)
    color_axis = fig.add_axes([.925, .27, .014, .52])
    fig.colorbar(image, cax=color_axis, label="順位相関 ρ")
    fig.text(.025, .045,
             "T＝各始点の全アクセス列で、その対象がアクセスされた総回数。同じ対象の各アクセスに同じTを付与。"
             "D＝次数、H＝今回の歩数、R＝アクセス直前の直近性。\n"
             "アクセス1回を1票として集計。Rが定義できない初回アクセスは、全6ペアで共通して除外。"
             "Tは今回のアクセスと以後のアクセスを含むため、予測可能な情報ではない。\n"
             "相関は値の順位に基づくため、元の値の差や非単調な関係は別途分布図で確認する。",
             fontsize=10, linespacing=1.7)
    for extension in ("png", "pdf"):
        fig.savefig(OUT / f"total_count_pair_correlations.{extension}", dpi=150)
    common.plt.close(fig)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-csv", action="store_true", help="既存の始点別CSVから図だけ作り直す")
    args = parser.parse_args()
    if args.from_csv:
        with (OUT / "total_count_pair_correlations_by_start.csv").open() as file:
            data = list(csv.DictReader(file))
        for row in data:
            row["rho"] = float(row["rho"])
    else:
        data = collect()
    plot(data)
    print(OUT / "total_count_pair_correlations.png")
