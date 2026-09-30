#!/usr/bin/env python3
"""Whole-trace distributions and event-time conditional reuse; no half-trace split."""
from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PC = HERE.parent.parent
ROOT = PC.parent.parent
os.environ.setdefault("MPLCONFIGDIR", "/tmp/access-factor-distribution-mpl")
sys.dont_write_bytecode = True
sys.path.insert(0, str(PC / "experiments"))
import analyze_access_factors as source
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

fonts = {f.name for f in font_manager.fontManager.ttflist}
for font in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "IPAexGothic"):
    if font in fonts:
        plt.rcParams["font.family"] = font
        break
plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.unicode_minus": False})

GRAPHS = {"er_mid": "er_mid.gr", "ba_mid": "ba_mid.gr", "vldb": "vldb.gr",
          "amazon": "amazon0601.gr", "slashdot": "soc-slashdot.gr",
          "enron": "email-enron-connected.gr"}
NAMES = {"D": "D 次数", "H": "H 今回の歩数", "O": "O 今回より前の累積回数",
         "A": "前回アクセスからの間隔 A（小さいほど最近）"}
EDGES = np.array([0, 1, 2, 4, 8, 16, 32, 64, 128, 256, np.inf])
LABELS = ["0", "1", "2–3", "4–7", "8–15", "16–31", "32–63", "64–127", "128–255", "256+"]
PAIRS = [("O", "A"), ("O", "H"), ("O", "D"), ("H", "D"), ("H", "A"), ("D", "A")]
COLORS = ["#0072b2", "#d55e00", "#009e73"]


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def bucket(x):
    return np.searchsorted(EDGES[1:-1], x, side="right")


def summary(x):
    x = x[np.isfinite(x)]
    if not len(x):
        return None
    q = np.quantile(x, [.1, .25, .5, .75, .9])
    return dict(n=len(x), mean=float(x.mean()), p10=q[0], p25=q[1],
                median=q[2], p75=q[3], p90=q[4])


def distribution(ax, totals, values, title, ylabel, rows, kind, unit, factor):
    groups = bucket(totals)
    boxes, positions, ticks = [], [], []
    for b in range(1, len(LABELS)):
        s = summary(values[groups == b])
        if s is None:
            continue
        positions.append(b)
        ticks.append(f"{LABELS[b]}\nn={s['n']:,}")
        boxes.append(dict(med=s["median"], q1=s["p25"], q3=s["p75"],
                          whislo=s["p10"], whishi=s["p90"], fliers=[]))
        rows.append(dict(kind=kind, unit=unit, factor=factor, total_band=LABELS[b], **s))
    ax.bxp(boxes, positions=positions, showfliers=False, widths=.55,
           patch_artist=True, boxprops={"facecolor": "#d7e8f2"},
           medianprops={"color": "#005985", "linewidth": 2})
    ax.set_xticks(positions, ticks, fontsize=7)
    ax.set_title(title, loc="left", fontsize=11)
    ax.set_ylabel(ylabel)
    if len(values[np.isfinite(values)]) and np.nanmax(values) > 0:
        if factor == "O":
            ax.set_yscale("symlog", linthresh=1)
            ax.set_ylim(bottom=0)
        else:
            ax.set_yscale("log")
    ax.grid(axis="y", alpha=.2)
    ax.set_xlabel("全体のアクセス回数 T")


def records(seq, deg_map):
    entities = [e for e, _ in seq]
    totals = Counter(entities)
    n = len(seq)
    result = {k: np.empty(n) for k in ("D", "H", "O", "A", "T", "gap", "remaining", "edge")}
    last, count, first = {}, Counter(), {}
    for t, (e, h) in enumerate(seq):
        result["D"][t] = deg_map[e]
        result["H"][t] = h
        result["O"][t] = count[e]
        result["A"][t] = t - last[e] if e in last else np.nan
        result["T"][t] = totals[e]
        result["edge"][t] = not isinstance(e, int)
        result["remaining"][t] = n - 1 - t
        first.setdefault(e, h)
        last[e] = t
        count[e] += 1
    nxt = {}
    for t in range(n - 1, -1, -1):
        e = entities[t]
        result["gap"][t] = nxt[e] - t if e in nxt else np.inf
        nxt[e] = t
    keys = list(totals)
    key_data = {"T": np.array([totals[e] for e in keys]),
                "D": np.array([deg_map[e] for e in keys]),
                "H": np.array([first[e] for e in keys]),
                "edge": np.array([not isinstance(e, int) for e in keys])}
    return result, key_data, totals, first


def conditional(events, start, rows):
    for kind, edge in (("node", 0), ("edge", 1)):
        type_mask = events["edge"] == edge
        for x, z in PAIRS:
            finite = type_mask & np.isfinite(events[x]) & np.isfinite(events[z])
            # Boundaries use features only, never the response. Do not split tied values.
            if not finite.any():
                continue
            cuts = np.unique(np.quantile(events[z][finite], [1 / 3, 2 / 3]))
            strata = np.searchsorted(cuts, events[z], side="left")
            bins = bucket(events[x])
            for horizon in (10, 100, 1000):
                valid = finite & (events["remaining"] >= horizon)
                for level in range(len(cuts) + 1):
                    lo = -np.inf if level == 0 else cuts[level - 1]
                    hi = np.inf if level == len(cuts) else cuts[level]
                    for b in range(len(LABELS)):
                        m = valid & (strata == level) & (bins == b)
                        n = int(m.sum())
                        if not n:
                            continue
                        hits = int((events["gap"][m] <= horizon).sum())
                        rows.append(dict(start=start, kind=kind, x=x, control=z,
                                         level=level, levels=len(cuts) + 1,
                                         control_lower_exclusive=float(lo), control_upper_inclusive=float(hi),
                                         x_bin=b, x_band=LABELS[b], horizon=horizon,
                                         n=n, reuses=hits, rate=hits / n))


def plot_conditionals(rows, tag, kind, out, aggregated):
    fig, axes = plt.subplots(2, 3, figsize=(17, 10))
    for ax, (x, z) in zip(axes.flat, PAIRS):
        selected = [r for r in rows if r["kind"] == kind and r["x"] == x and
                    r["control"] == z and r["horizon"] == 100 and r["n"] >= 20]
        for level, color in enumerate(COLORS):
            # With tied quantiles a middle/high stratum can be absent; keep actual labels.
            by_bin = defaultdict(list)
            for r in selected:
                if r["level"] == level:
                    by_bin[r["x_bin"]].append(r)
            means, sds = np.full(10, np.nan), np.full(10, np.nan)
            for b, cells in by_bin.items():
                rates = np.array([r["rate"] for r in cells])
                aggregated.append(dict(kind=kind, x=x, control=z, level=level,
                                       x_band=LABELS[b], n_starts=len(cells), n=sum(r["n"] for r in cells),
                                       mean_rate=float(rates.mean()), sd_rate=float(rates.std())))
                if len(cells) >= 3:
                    means[b], sds[b] = rates.mean() * 100, rates.std() * 100
            ax.plot(range(10), means, "o-", color=color, label=f"{z}：第{level + 1}層（小→大）", markersize=4)
            ax.fill_between(range(10), np.maximum(0, means - sds), np.minimum(100, means + sds), color=color, alpha=.10)
        ax.set_title(f"同じ {x} 帯でも、{z} で再アクセス率が違うか", loc="left", fontsize=10)
        ax.set_xticks(range(10), LABELS, rotation=35, fontsize=8)
        ax.set_xlabel(NAMES[x])
        ax.set_ylabel("次の100アクセス以内の再アクセス率 [%]")
        ax.set_ylim(0, 102)
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.suptitle(f"{tag} / {kind} — 論点3：条件をそろえた比較", fontsize=16)
    fig.text(.02, .015, "点＝始点別の率の等重み平均、帯＝始点間の標準偏差（信頼区間ではない）。各セル20件以上・3始点以上を表示。\n"
             "層は始点・型別の三分位（同値は分割しない）。Aは直前までの間隔、R=1/(1+A)。末尾100件は除外。粗い層別なので独立性・因果の証明ではない。", fontsize=9)
    fig.tight_layout(rect=(0, .075, 1, .95))
    fig.savefig(out, dpi=135)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--graphs", nargs="+", choices=list(GRAPHS), default=list(GRAPHS))
    p.add_argument("--alphas", nargs="+", default=["0.1", "0.01"])
    args = p.parse_args()
    for d in ("01_total_distributions", "02_conditional_reuse", "data"):
        (HERE / d).mkdir(exist_ok=True)
    for graph in args.graphs:
        edge_path = ROOT / "dataset/Louvain/graph" / GRAPHS[graph]
        print(f"Loading {graph}", flush=True)
        edges = source.srv.load_edge_list(edge_path)
        cluster = source.sim.VirtualCluster(edges, 2, None)
        cluster.build_servers(lambda: source.srv.build_authz_cache("none", 0), 1., 1.)
        deg_map = source.build_degree_map(cluster)
        for alpha in args.alphas:
            tag = f"{graph}_a{alpha}_w1000_s8"
            csv_path = PC / "results/cap" / f"accessfactors_{tag}_keys.csv"
            meta_path = csv_path.with_suffix(".meta.json")
            meta = json.loads(meta_path.read_text())
            cfg = meta["config"]
            assert cfg["owner_filter"] is None and cfg["partition_dir"] is None
            assert cfg["server_count"] == 2
            expected = defaultdict(dict)
            with csv_path.open() as f:
                for r in csv.DictReader(f):
                    expected[int(r["start"])][r["entity"]] = (int(float(r["total"])), float(r["hop_walk"]), float(r["deg"]))
            all_events, all_keys, cond_rows, corr_rows, checks = [], [], [], [], []
            for start in meta["starts"]:
                seq = source.run_walks(cluster, [start], cfg["walks"], float(alpha),
                                       cfg["seed"], cfg["max_hops"], cfg["no_backtrack"])[start]
                ev, kd, totals, first = records(seq, deg_map)
                actual = {str(e): (n, float(first[e]), deg_map[e]) for e, n in totals.items()}
                if actual != expected[start] or len(seq) != meta["per_start"][str(start)]["n_access"]:
                    raise RuntimeError(f"Replay differs from source CSV: {tag} start={start}")
                checks.append(dict(start=start, n_access=len(seq), n_keys=len(totals), exact_match=True))
                for kind, edge in (("node", 0), ("edge", 1)):
                    for unit, data, factors in (("key", kd, ("D", "H")), ("event", ev, ("D", "H", "O", "A"))):
                        for factor in factors:
                            mask = (data["edge"] == edge) & np.isfinite(data[factor])
                            corr_rows.append(dict(start=start, kind=kind, unit=unit, factor=factor,
                                                  n=int(mask.sum()), rho_total=source.spearman(data[factor][mask], data["T"][mask])))
                conditional(ev, start, cond_rows)
                all_events.append(ev)
                all_keys.append(kd)
                print(f"  {tag} start={start}: {len(seq):,} accesses; exact replay OK", flush=True)
            ev = {k: np.concatenate([d[k] for d in all_events]) for k in all_events[0]}
            kd = {k: np.concatenate([d[k] for d in all_keys]) for k in all_keys[0]}
            distribution_rows, aggregated = [], []
            for kind, edge in (("node", 0), ("edge", 1)):
                km, em = kd["edge"] == edge, ev["edge"] == edge
                fig, axes = plt.subplots(2, 3, figsize=(18, 10))
                counts = np.bincount(bucket(kd["T"][km]), minlength=10)
                axes[0, 0].bar(range(1, 10), counts[1:], color="#5193b5")
                axes[0, 0].set_xticks(range(1, 10), LABELS[1:], rotation=30, fontsize=8)
                axes[0, 0].set_title("① どのアクセス回数帯に何個あるか", loc="left")
                axes[0, 0].set_ylabel("キー数（始点×実体）")
                axes[0, 0].set_xlabel("全体のアクセス回数 T")
                axes[0, 0].set_yscale("log")
                panels = [(axes[0, 1], kd, km, "D", "② よくアクセスされる対象の次数", "D 次数", "key"),
                          (axes[0, 2], kd, km, "H", "③ よくアクセスされる対象の初回歩数", "H 初回の歩数", "key"),
                          (axes[1, 0], ev, em, "H", "④ アクセスした時点の歩数", NAMES["H"], "event"),
                          (axes[1, 1], ev, em, "O", "⑤ アクセス直前の頻度【Tと定義上重複】", NAMES["O"], "event"),
                          (axes[1, 2], ev, em, "A", "⑥ アクセス直前の直近性（初回は欠測）", "間隔 A（R=1/(1+A)）", "event")]
                for ax, data, mask, factor, title, ylabel, unit in panels:
                    distribution(ax, data["T"][mask], data[factor][mask], title, ylabel,
                                 distribution_rows, kind, unit, factor)
                fig.suptitle(f"{graph} / α={alpha} / {kind} — 全アクセス回数別の分布", fontsize=18)
                fig.text(.02, .015, "箱＝25–75%、中央線＝中央値、ひげ＝10–90%。②③はキー各1票、④⑤⑥はアクセス各1票。nは有効な点の数。\n"
                         "全期間を使用（前半・後半に分割しない）。Oは同一キーで0〜T−1を取るため、⑤の関連は予測力の証拠ではない。横軸は回数帯、縦軸は対数系。", fontsize=10)
                fig.tight_layout(rect=(0, .08, 1, .95))
                fig.savefig(HERE / "01_total_distributions" / f"{tag}_{kind}.png", dpi=135)
                plt.close(fig)
                plot_conditionals(cond_rows, f"{graph} / α={alpha}", kind,
                                  HERE / "02_conditional_reuse" / f"{tag}_{kind}.png", aggregated)
            write_csv(HERE / "data" / f"{tag}_distributions.csv", distribution_rows)
            write_csv(HERE / "data" / f"{tag}_correlations.csv", corr_rows)
            write_csv(HERE / "data" / f"{tag}_conditional_by_start.csv", cond_rows)
            write_csv(HERE / "data" / f"{tag}_conditional_summary.csv", aggregated)
            manifest = dict(tag=tag, config=cfg, starts=meta["starts"], replay_checks=checks,
                            source_csv=str(csv_path.relative_to(ROOT)),
                            source_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                            graph_sha256=hashlib.sha256(edge_path.read_bytes()).hexdigest())
            (HERE / "data" / f"{tag}_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            print(f"Saved {tag}", flush=True)
            del all_events, all_keys, ev, kd, expected, seq, cond_rows
            gc.collect()
        del cluster, deg_map, edges
        gc.collect()


if __name__ == "__main__":
    main()
