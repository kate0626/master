#!/usr/bin/env python3
"""複数スタートの access-by-distance CSV を 1 枚に重ねてプロットする。

controller が start_node ごとに出力する `*_access_by_distance.csv`
(列: hop, node_logical_distance, node_access, edge_access, total_access)
を集めて、始点からの距離を横軸に、各 start を 1 本の折れ線として重ね描きする。

「あくまで始点からの距離が分かればよい」ので、デフォルトは
  metric = node (ノードへのアクセス回数)
  x      = logical (= hop//2 = 元グラフ上の論理ホップ距離)
とする。--metric / --x で切替可能。

使い方:
  python3 base/proposed_cache/plot_access_by_distance.py \
    base/proposed_cache/results/.../<GRAPH>/none_0 \
    --out-prefix <出力先>/<GRAPH>_none \
    --title "amazon0601 (none)"

  # 複数ディレクトリ / glob もまとめて指定可
  python3 ... DIR1 DIR2 ... --metric node --x logical --normalize
"""
from __future__ import annotations

import argparse
import csv
import glob
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


def find_csvs(paths: List[str]) -> List[Path]:
    found: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            found.extend(sorted(pp.rglob("*_access_by_distance.csv")))
        else:
            # glob パターンまたは直接ファイル
            found.extend(Path(m) for m in sorted(glob.glob(p)))
    # 重複除去
    seen, uniq = set(), []
    for f in found:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(f)
    return uniq


def start_of(path: Path) -> str:
    m = re.search(r"start=(\d+)", path.name)
    return m.group(1) if m else path.stem


def load_curve(path: Path, metric: str, x_mode: str) -> Dict[int, int]:
    """1 CSV → {distance: access_count}。metric/x_mode に応じて集約。"""
    curve: Dict[int, int] = defaultdict(int)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            hop = int(row["hop"])
            node = int(row["node_access"])
            edge = int(row["edge_access"])
            total = int(row["total_access"])
            if metric == "node":
                y = node
                x = (hop // 2) if x_mode == "logical" else hop
            elif metric == "edge":
                y = edge
                x = (hop // 2) if x_mode == "logical" else hop
            else:  # total
                y = total
                x = (hop // 2) if x_mode == "logical" else hop
            if y:
                curve[x] += y
    return dict(curve)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="results dir / glob / CSV ファイル")
    ap.add_argument("--out-prefix", required=True, help="出力ファイルの接頭辞 (.png/.csv を付与)")
    ap.add_argument("--metric", choices=["node", "edge", "total"], default="node")
    ap.add_argument("--x", choices=["logical", "hop"], default="logical",
                    help="logical=元グラフ論理ホップ(hop//2), hop=bipartite hop")
    ap.add_argument("--normalize", action="store_true",
                    help="各 start のカーブをその総数で割り、形(局所性)を比較しやすくする")
    ap.add_argument("--title", default=None)
    ap.add_argument("--max-distance", type=int, default=None,
                    help="この距離までに x 軸を切る (任意)")
    args = ap.parse_args()

    csvs = find_csvs(args.paths)
    if not csvs:
        raise SystemExit(f"[plot] CSV が見つかりません: {args.paths}")

    # start ごとにカーブをまとめる (同一 start が複数あれば加算)
    curves: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for path in csvs:
        s = start_of(path)
        c = load_curve(path, args.metric, args.x)
        for x, y in c.items():
            curves[s][x] += y
    print(f"[plot] {len(csvs)} CSV / {len(curves)} starts: {sorted(curves)}")

    # 全 start の和 (aggregate) も作る
    agg: Dict[int, float] = defaultdict(float)
    for c in curves.values():
        for x, y in c.items():
            agg[x] += y

    max_x = max(agg) if agg else 0
    if args.max_distance is not None:
        max_x = min(max_x, args.max_distance)
    xs = list(range(0, max_x + 1))

    xlabel_unit = "logical hop (original-graph distance)" if args.x == "logical" else "bipartite hop"
    ylabel = f"{args.metric} access count"
    if args.normalize:
        ylabel = f"{args.metric} access fraction"

    # ---- combined CSV (wide: 行=distance, 列=各start + aggregate) ----
    start_keys = sorted(curves, key=lambda s: (len(s), s))
    out_csv = Path(f"{args.out_prefix}_access_by_distance_overlay.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["distance"] + [f"start{s}" for s in start_keys] + ["aggregate"])
        for x in xs:
            w.writerow(
                [x]
                + [curves[s].get(x, 0) for s in start_keys]
                + [int(agg.get(x, 0))]
            )
    print(f"[plot] saved CSV: {out_csv}")

    # ---- PNG (各 start を重ね描き + aggregate 太線) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"[plot] matplotlib 不可、PNG skip ({exc})")
        return

    fig, ax = plt.subplots(figsize=(9.5, 6))
    for s in start_keys:
        c = curves[s]
        ys = [c.get(x, 0) for x in xs]
        if args.normalize:
            tot = sum(ys) or 1
            ys = [y / tot for y in ys]
        ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.2,
                alpha=0.85, label=f"start {s}")

    # aggregate (全 start 合計) を太い破線で
    agg_ys = [agg.get(x, 0) for x in xs]
    if args.normalize:
        tot = sum(agg_ys) or 1
        agg_ys = [y / tot for y in agg_ys]
    ax.plot(xs, agg_ys, color="black", linewidth=2.4, linestyle="--",
            label="aggregate", zorder=5)

    ax.set_xlabel(f"distance from start ({xlabel_unit})")
    ax.set_ylabel(ylabel + ("" if args.normalize else " (log)"))
    if not args.normalize and any(y > 0 for y in agg_ys):
        ax.set_yscale("log")
    title = args.title or f"Access locality vs distance (metric={args.metric})"
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(ncol=2, fontsize=9)
    fig.tight_layout()
    out_png = Path(f"{args.out_prefix}_access_by_distance_overlay.png")
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"[plot] saved PNG: {out_png}")


if __name__ == "__main__":
    main()
