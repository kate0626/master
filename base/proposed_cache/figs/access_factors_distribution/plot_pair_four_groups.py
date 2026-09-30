#!/usr/bin/env python3
"""Compare next-100-access reuse in Low/High groups for every factor pair.

Outputs CSV and six-panel figures under 08_pair_four_groups/. Factors are
measured at each access; the final 100 events of each walk are excluded.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import analyze as common

np = common.np
plt = common.plt
source = common.source
PAIRS = list(combinations(("D", "H", "O", "R"), 2))
GROUPS = ((0, 0), (0, 1), (1, 0), (1, 1))
OUT = common.HERE / "08_pair_four_groups"
GROUP_LABELS = {
    "D": ("次数小", "次数大"),
    "H": ("歩数少\n起点に近い", "歩数多\n起点から遠い"),
    "O": ("過去アクセス少", "過去アクセス多"),
    "R": ("前回から長い", "前回から短い"),
}


def split_at_tie(values):
    """Choose a boundary between distinct values nearest a balanced split."""
    unique, counts = np.unique(values, return_counts=True)
    if len(unique) < 2:
        return None
    low_counts = np.cumsum(counts)[:-1]
    cut = int(np.argmin(np.abs(low_counts - len(values) / 2)))
    return float(unique[cut])


def tally(events, start, horizon, pairs=None, eligibility_horizon=None):
    features = {name: events[name] for name in ("D", "H", "O")}
    features["R"] = 1 / (1 + events["A"])
    output = []
    for kind, edge in (("node", 0), ("edge", 1)):
        window = horizon if eligibility_horizon is None else eligibility_horizon
        if window < horizon:
            raise ValueError('eligibility_horizon must cover the prediction horizon')
        base = (events["edge"] == edge) & (events["remaining"] >= window)
        for x, y in (PAIRS if pairs is None else pairs):
            valid = base & np.isfinite(features[x]) & np.isfinite(features[y])
            xcut = split_at_tie(features[x][valid]) if valid.any() else None
            ycut = split_at_tie(features[y][valid]) if valid.any() else None
            if xcut is None or ycut is None:
                continue
            xhigh, yhigh = features[x] > xcut, features[y] > ycut
            reused = events["gap"] <= horizon
            for a, b in GROUPS:
                mask = valid & (xhigh == a) & (yhigh == b)
                output.append(dict(start=start, kind=kind, x=x, y=y,
                                   x_group="High" if a else "Low",
                                   y_group="High" if b else "Low",
                                   x_cut=xcut, y_cut=ycut,
                                   n=int(mask.sum()), reuses=int((mask & reused).sum())))
    return output


def plot_condition(rows, graph, alpha, kind, horizon, out, pairs=None, language="ja"):
    pairs = PAIRS if pairs is None else pairs
    english = language == "en"
    labels = {
        "D": ("Low degree", "High degree"),
        "H": ("Fewer steps", "More steps"),
        "O": ("Low past count", "High past count"),
        "R": ("Less recent", "More recent"),
    } if english else GROUP_LABELS
    lookup = {(r["x"], r["y"], r["x_group"], r["y_group"]): r for r in rows if r["kind"] == kind}
    nrows = (len(pairs) + 2) // 3
    fig, axes = plt.subplots(nrows, 3, figsize=(14, 4.25 * nrows),
                             squeeze=False, constrained_layout=True)
    colors = ["#b7c9d4", "#4f96c0", "#e2a065", "#be4b49"]
    for ax, (x, y) in zip(axes.flat, pairs):
        cells = [lookup[x, y, "High" if a else "Low", "High" if b else "Low"] for a, b in GROUPS]
        rates = [100 * int(r["reuses"]) / int(r["n"]) if int(r["n"]) else np.nan for r in cells]
        ax.bar(range(4), np.nan_to_num(rates), color=colors)
        ax.set_xticks(range(4),
                      [f"{labels[x][a]}\n×\n{labels[y][b]}"
                       for a, b in GROUPS], fontsize=8)
        ax.set_title(f"{x} × {y}", fontsize=12)
        ax.set_ylabel("Reuse rate [%]" if english else "再アクセス率 [%]")
        ax.set_ylim(0, 105)
        ax.grid(axis="y", alpha=.2)
        for i, (rate, row) in enumerate(zip(rates, cells)):
            label = f"{rate:.1f}%\nn={int(row['n']):,}" if int(row["n"]) else ("No data" if english else "データなし")
            ax.text(i, min(np.nan_to_num(rate) + 2, 95), label, ha="center", va="bottom", fontsize=8)
        if all(int(r["n"]) for r in cells):
            delta = rates[3] - rates[2] - rates[1] + rates[0]
            ax.text(.98, .96, (f"Interaction contrast {delta:+.1f} pp" if english else f"交互作用差 {delta:+.1f} pt"), transform=ax.transAxes,
                    ha="right", va="top", fontsize=9)
    for ax in list(axes.flat)[len(pairs):]:
        ax.set_visible(False)
    if english:
        title = f"{graph} / alpha={alpha} / {kind}: four-group comparison"
        note = (f"Reuse of the same entity within the next {horizon} accesses; "
                f"the final {horizon} accesses per start are excluded. R is undefined on first access.\n"
                "Cuts balance access counts per start/type/pair without splitting ties. "
                "Interaction contrast = HH - HL - LH + LL (percentage points).")
    else:
        title = f"{graph} / α={alpha} / {'ノード' if kind == 'node' else 'エッジ'}：2要素の4群比較"
        note = (f"各時点から次の{horizon}アクセス以内に同じ対象が再出現した割合。末尾{horizon}件を除外。"
                "初回アクセスではRが未定義。\n"
                "境界は始点・型・ペアごとに、同値を分割せず件数が半々に近い値。交互作用差＝高高−高低−低高＋低低（確率差の尺度）。")
    fig.suptitle(title, fontsize=15)
    fig.text(.5, -.015, note, ha="center", va="top", fontsize=9)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)


def write_csv(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graphs", nargs="+", choices=list(common.GRAPHS), default=list(common.GRAPHS))
    parser.add_argument("--alphas", nargs="+", default=["0.1", "0.01"])
    parser.add_argument("--horizon", type=int, default=100)
    parser.add_argument("--plot-only", action="store_true",
                        help="既存の集計CSVから図だけを再生成する")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.plot_only:
        for graph in args.graphs:
            for alpha in args.alphas:
                tag = f"{graph}_a{alpha}_w1000_s8"
                path = OUT / f"{tag}_h{args.horizon}_summary.csv"
                with path.open() as file:
                    summary = list(csv.DictReader(file))
                for kind in ("node", "edge"):
                    plot_condition(summary, graph, alpha, kind, args.horizon,
                                   OUT / f"{tag}_{kind}_h{args.horizon}.png")
                print(f"Updated {tag}", flush=True)
        return
    all_summary = []
    for graph in args.graphs:
        edge_path = common.ROOT / "dataset/Louvain/graph" / common.GRAPHS[graph]
        edges = source.srv.load_edge_list(edge_path)
        cluster = source.sim.VirtualCluster(edges, 2, None)
        cluster.build_servers(lambda: source.srv.build_authz_cache("none", 0), 1., 1.)
        deg_map = source.build_degree_map(cluster)
        for alpha in args.alphas:
            tag = f"{graph}_a{alpha}_w1000_s8"
            meta_path = common.PC / "results/cap" / f"accessfactors_{tag}_keys.meta.json"
            meta = json.loads(meta_path.read_text())
            cfg = meta["config"]
            detail = []
            for start in meta["starts"]:
                seq = source.run_walks(cluster, [start], cfg["walks"], float(alpha),
                                       cfg["seed"], cfg["max_hops"], cfg["no_backtrack"])[start]
                if len(seq) != meta["per_start"][str(start)]["n_access"]:
                    raise RuntimeError(f"Replay length differs: {tag}, start={start}")
                events, _, _, _ = common.records(seq, deg_map)
                detail.extend(tally(events, start, args.horizon))
            if not detail:
                continue
            write_csv(OUT / f"{tag}_h{args.horizon}_by_start.csv", detail)
            grouped = defaultdict(lambda: [0, 0, 0])
            for row in detail:
                key = (row["kind"], row["x"], row["y"], row["x_group"], row["y_group"])
                cell = grouped[key]
                cell[0] += row["n"]
                cell[1] += row["reuses"]
                cell[2] += row["n"] > 0
            summary = []
            for kind in ("node", "edge"):
                for x, y in PAIRS:
                    for a, b in GROUPS:
                        xg, yg = ("High" if a else "Low"), ("High" if b else "Low")
                        n, reuses, starts = grouped[kind, x, y, xg, yg]
                        summary.append(dict(graph=graph, alpha=alpha, kind=kind,
                                            x=x, y=y, x_group=xg, y_group=yg,
                                            n=n, n_starts=starts, reuses=reuses,
                                            rate=reuses / n if n else float("nan")))
            write_csv(OUT / f"{tag}_h{args.horizon}_summary.csv", summary)
            all_summary.extend(summary)
            for kind in ("node", "edge"):
                plot_condition(summary, graph, alpha, kind, args.horizon,
                               OUT / f"{tag}_{kind}_h{args.horizon}.png")
            print(f"Saved {tag}", flush=True)
    if all_summary:
        write_csv(OUT / f"all_conditions_h{args.horizon}_summary.csv", all_summary)


if __name__ == "__main__":
    main()
