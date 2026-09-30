#!/usr/bin/env python3
"""Compare each factor with total accesses T after stratifying another factor.

Default run from repository root:
    python3 base/proposed_cache/figs/access_factors_distribution/plot_total_count_conditional.py

By default processes all six graphs at alpha=0.1 and 0.01. Each condition
produces one PNG/PDF for nodes, one for edges, and one CSV. No HTML.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np
import analyze as common


HERE = Path(__file__).resolve().parent
FACTORS = ("D", "H", "O", "R")
PAIRS = list(combinations(FACTORS, 2))
COLORS = ("#0072b2", "#e69f00", "#009e73")


def replay_one_start(cluster, degree, start, config):
    sequence = common.source.run_walks(
        cluster, [start], config["walks"], float(config["alpha"]),
        config["seed"], config["max_hops"], config["no_backtrack"]
    )[start]
    totals = Counter(entity for entity, _ in sequence)
    first_hop = {}
    count = Counter()
    last = {}
    data = {name: np.empty(len(sequence), dtype=float) for name in (*FACTORS, "T", "edge")}
    for t, (entity, hop) in enumerate(sequence):
        data["D"][t] = degree[entity]
        data["H"][t] = hop
        data["O"][t] = count[entity]
        data["R"][t] = 1 / (1 + t - last[entity]) if entity in last else np.nan
        data["T"][t] = totals[entity]
        data["edge"][t] = not isinstance(entity, int)
        first_hop.setdefault(entity, hop)
        count[entity] += 1
        last[entity] = t
    return data, totals, first_hop, len(sequence)


def bins(values, n_bins):
    """Quantile boundaries; tied values stay in the same bin."""
    cuts = np.unique(np.quantile(values, np.linspace(0, 1, n_bins + 1)[1:-1]))
    labels = np.searchsorted(cuts, values, side="left")
    return cuts, labels


def interval(cuts, index):
    lower = -np.inf if index == 0 else float(cuts[index - 1])
    upper = np.inf if index == len(cuts) else float(cuts[index])
    return lower, upper


def summarize_pair(data, graph, alpha, kind, xname, zname, min_cell):
    finite = np.isfinite(data[xname]) & np.isfinite(data[zname])
    x = data[xname][finite]
    z = data[zname][finite]
    total = data["T"][finite]
    xcuts, xbins = bins(x, 6)
    zcuts, zbins = bins(z, 3)
    rows = []
    for zbin in range(len(zcuts) + 1):
        zlow, zhigh = interval(zcuts, zbin)
        for xbin in range(len(xcuts) + 1):
            selected = (zbins == zbin) & (xbins == xbin)
            if not selected.any():
                continue
            xlow, xhigh = interval(xcuts, xbin)
            sample = total[selected]
            rows.append(dict(graph=graph, alpha=alpha, kind=kind,
                             horizontal=xname, controlled=zname,
                             x_bin=xbin, x_lower_exclusive=xlow, x_upper_inclusive=xhigh,
                             control_bin=zbin, control_lower_exclusive=zlow,
                             control_upper_inclusive=zhigh, n=int(selected.sum()),
                             x_median=float(np.median(x[selected])),
                             t_p25=float(np.quantile(sample, .25)),
                             t_median=float(np.median(sample)),
                             t_p75=float(np.quantile(sample, .75)),
                             displayed=bool(selected.sum() >= min_cell)))
    return rows


def draw(kind, graph, alpha, data, out_dir, min_cell):
    fig, axes = common.plt.subplots(6, 2, figsize=(16, 23))
    all_rows = []
    type_mask = data["edge"] == (kind == "edge")
    subset = {name: values[type_mask] for name, values in data.items()}
    for row_index, (first, second) in enumerate(PAIRS):
        for col_index, (xname, zname) in enumerate(((first, second), (second, first))):
            ax = axes[row_index, col_index]
            result = summarize_pair(subset, graph, alpha, kind, xname, zname, min_cell)
            all_rows.extend(result)
            for zbin, color in enumerate(COLORS):
                series = sorted((r for r in result if r["control_bin"] == zbin and r["displayed"]),
                                key=lambda r: r["x_median"])
                if not series:
                    continue
                horizontal = np.array([r["x_median"] for r in series])
                median = np.array([r["t_median"] for r in series])
                p25 = np.array([r["t_p25"] for r in series])
                p75 = np.array([r["t_p75"] for r in series])
                ax.plot(horizontal, median, "o-", color=color,
                        label=f"{zname} 第{zbin+1}層（小→大）")
                ax.fill_between(horizontal, p25, p75, color=color, alpha=.12)
            ax.set_title(f"{xname} と T ｜ {zname} を層別", loc="left", fontsize=12)
            ax.set_xlabel(f"{xname} の値（各帯の中央値）")
            ax.set_ylabel("全アクセス回数 T の中央値")
            ax.set_yscale("log")
            if xname == "O":
                ax.set_xscale("symlog", linthresh=1)
            else:
                ax.set_xscale("log")
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
    fig.suptitle(f"{graph} / α={alpha} / {kind}：片方をそろえてもTとの関係が残るか", fontsize=17, y=.996)
    fig.text(.02, .012,
             "各行は同じ要素ペアを左右逆向きに比較。横軸＝要素X、色＝相手の要素Zの低・中・高、縦軸＝T。"
             "点＝セル内Tの中央値、帯＝25–75%、各セル20件以上を表示。\n"
             "アクセス1回を1票とし、始点をまとめる。Rが未定義の初回はRを含むパネルのみ除く。"
             "OはTと定義上重複。Tは今回以降のアクセスも含むため、予測力や因果を示す図ではない。",
             fontsize=9)
    fig.tight_layout(rect=(0, .035, 1, .985), h_pad=2.2)
    common.plt.rcParams["pdf.fonttype"] = 42
    for suffix in ("png", "pdf"):
        fig.savefig(out_dir / f"{graph}_a{alpha}_{kind}_conditional_total.{suffix}", dpi=140)
    common.plt.close(fig)
    return all_rows


def run_condition(graph, alpha, cluster, degree, out_dir, min_cell):
    tag = f"{graph}_a{alpha}_w1000_s8"
    original = common.PC / "results/cap" / f"accessfactors_{tag}_keys.csv"
    meta = json.loads(original.with_suffix(".meta.json").read_text())
    config = meta["config"]
    assert float(config["alpha"]) == float(alpha)
    assert config["partition_dir"] is None and config["owner_filter"] is None
    expected = {}
    with original.open() as file:
        for row in csv.DictReader(file):
            expected.setdefault(int(row["start"]), {})[row["entity"]] = (
                int(float(row["total"])), float(row["hop_walk"]), float(row["deg"]))

    starts = []
    for start in meta["starts"]:
        values, totals, first_hop, n_access = replay_one_start(cluster, degree, start, config)
        actual = {str(entity): (count, float(first_hop[entity]), degree[entity])
                  for entity, count in totals.items()}
        if actual != expected[start] or n_access != meta["per_start"][str(start)]["n_access"]:
            raise RuntimeError(f"Replayed accesses differ from saved source: {tag}, start={start}")
        starts.append(values)
    data = {name: np.concatenate([one[name] for one in starts]) for name in (*FACTORS, "T", "edge")}
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for kind in ("node", "edge"):
        results.extend(draw(kind, graph, alpha, data, out_dir, min_cell))
        print(out_dir / f"{graph}_a{alpha}_{kind}_conditional_total.png", flush=True)
    with (out_dir / f"{graph}_a{alpha}_conditional_total.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs", nargs="+", choices=list(common.GRAPHS),
                        default=list(common.GRAPHS), help="既定では全6グラフ")
    parser.add_argument("--alphas", nargs="+", choices=("0.1", "0.01"),
                        default=["0.1", "0.01"], help="既定では両方")
    parser.add_argument("--min-cell", type=int, default=20)
    parser.add_argument("--out", type=Path, default=HERE / "06_conditional_total")
    args = parser.parse_args()
    for graph in args.graphs:
        edges = common.source.srv.load_edge_list(
            common.ROOT / "dataset/Louvain/graph" / common.GRAPHS[graph])
        cluster = common.source.sim.VirtualCluster(edges, 2, None)
        cluster.build_servers(lambda: common.source.srv.build_authz_cache("none", 0), 1.0, 1.0)
        degree = common.source.build_degree_map(cluster)
        for alpha in args.alphas:
            run_condition(graph, alpha, cluster, degree, args.out, args.min_cell)
        del cluster, degree, edges


if __name__ == "__main__":
    main()
