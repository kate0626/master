#!/usr/bin/env python3
"""距離別アクセスを「その距離にあるノード数 N(d)」で正規化してプロットする。

生のアクセス回数 A(d) は
  A(d) = (構造: 距離dにあるノード数 N(d)) × (局所性: 1ノードあたり平均アクセス回数)
の積なので、グラフ構造 (BFSシェル幅) が違うと局所性が同じでも形が変わってしまう。
そこで **アクセス密度 A(d)/N(d) = 距離dのノード1個あたり平均アクセス回数** を見る。
これで構造(シェル幅)を割り出して、純粋な「始点からの距離に対する局所性」を比較できる。

距離 d は **元グラフ上の BFS 最短ホップ** (controller の logical hop と同じ単位)。
各ノードの総アクセス回数 (controller 出力 JSON の "access") を、その始点からの
BFS 距離で分類して集計する。N(d) は元グラフ全体で距離 d にあるノード数 (踏まれて
いないノードも含むシェル幅)。

入力:
  - --edges  元グラフ .gr (無向エッジリスト "u v")
  - results dir / glob: controller が start ごとに出す *_global_transition.json
    (ファイル名の start=<N> から始点を判定し、その JSON の "access" を使う)

使い方:
  python3 base/proposed_cache/access_density_by_distance.py \
    --edges dataset/Louvain/graph/vldb.gr \
    base/proposed_cache/results/access_locality/vldb \
    --out-prefix base/proposed_cache/results/access_locality/vldb/vldb_none_density \
    --title "VLDB — access density (per node) vs distance"
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Tuple


def load_adjacency(edge_path: Path) -> Dict[int, List[int]]:
    adj: Dict[int, List[int]] = defaultdict(list)
    with edge_path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) != 2:
                continue
            u, v = int(parts[0]), int(parts[1])
            adj[u].append(v)
            adj[v].append(u)  # 元グラフは無向として扱う
    return adj


def bfs_distances(adj: Dict[int, List[int]], start: int) -> Dict[int, int]:
    """start から各ノードへの最短ホップ (元グラフ)。到達不能ノードは含めない。"""
    dist: Dict[int, int] = {start: 0}
    dq = deque([start])
    while dq:
        u = dq.popleft()
        du = dist[u]
        for w in adj.get(u, ()):
            if w not in dist:
                dist[w] = du + 1
                dq.append(w)
    return dist


def find_access_jsons(paths: List[str]) -> List[Path]:
    found: List[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            found.extend(sorted(pp.rglob("*_global_transition.json")))
        else:
            found.extend(Path(m) for m in sorted(glob.glob(p)))
    seen, uniq = set(), []
    for f in found:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(f)
    return uniq


def start_of(path: Path) -> int:
    m = re.search(r"start=(\d+)", path.name)
    if not m:
        raise SystemExit(f"[density] ファイル名から start=<N> を判定できません: {path}")
    return int(m.group(1))


def node_access_counts(access: Dict[str, int]) -> Dict[int, int]:
    """access dict から node のみ抽出 (edge_* は除外)。キーは int ノードID。"""
    out: Dict[int, int] = {}
    for k, v in access.items():
        ks = str(k)
        if ks.startswith("edge_"):
            continue
        if ks.startswith("node:"):
            ks = ks[len("node:"):]
        try:
            out[int(ks)] = out.get(int(ks), 0) + int(v)
        except ValueError:
            continue
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="results dir / glob / *_global_transition.json")
    ap.add_argument("--edges", required=True, help="元グラフ .gr (無向エッジリスト)")
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--max-distance", type=int, default=None)
    args = ap.parse_args()

    jsons = find_access_jsons(args.paths)
    if not jsons:
        raise SystemExit(f"[density] global_transition.json が見つかりません: {args.paths}")

    print(f"[density] loading graph: {args.edges}")
    adj = load_adjacency(Path(args.edges))
    print(f"[density] |V|={len(adj)} (with neighbors)")

    # start -> {distance: (N(d) shell size, accessed_nodes, A(d) access sum)}
    per_start: Dict[int, Dict[int, Dict[str, int]]] = {}
    for jp in jsons:
        s = start_of(jp)
        data = json.loads(jp.read_text(encoding="utf-8"))
        acc = node_access_counts(data.get("access", {}))
        dist = bfs_distances(adj, s)
        # シェル幅 N(d): 元グラフで距離 d にあるノード数
        shell: Dict[int, int] = defaultdict(int)
        for v, d in dist.items():
            shell[d] += 1
        # A(d): 距離 d のノードへの総アクセス、accessed: 実際に踏まれた distinct ノード数
        a_sum: Dict[int, int] = defaultdict(int)
        a_nodes: Dict[int, int] = defaultdict(int)
        unreached = 0
        for v, c in acc.items():
            d = dist.get(v)
            if d is None:
                unreached += 1
                continue
            a_sum[d] += c
            a_nodes[d] += 1
        rec: Dict[int, Dict[str, int]] = {}
        for d in shell:
            rec[d] = {
                "shell": shell[d],
                "accessed": a_nodes.get(d, 0),
                "access_sum": a_sum.get(d, 0),
            }
        per_start[s] = rec
        tot_acc = sum(a_sum.values())
        print(f"[density] start={s}: reachable={len(dist)} maxdist={max(dist.values())} "
              f"access_nodes={len(acc)} access_total={tot_acc} unreached_accessed={unreached}")

    starts = sorted(per_start)
    max_d = max((max(rec) for rec in per_start.values() if rec), default=0)
    if args.max_distance is not None:
        max_d = min(max_d, args.max_distance)
    ds = list(range(0, max_d + 1))

    # ---- CSV (long 形式: 1 行 = (start, distance, shell, accessed, access_sum, density)) ----
    out_csv = Path(f"{args.out_prefix}_access_density.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start", "distance", "n_nodes_shell", "n_nodes_accessed",
                    "access_sum", "access_per_node"])
        for s in starts:
            rec = per_start[s]
            for d in ds:
                r = rec.get(d)
                if not r:
                    continue
                shell = r["shell"]
                dens = (r["access_sum"] / shell) if shell else 0.0
                w.writerow([s, d, shell, r["accessed"], r["access_sum"], f"{dens:.6g}"])
    print(f"[density] saved CSV: {out_csv}")

    # ---- PNG: density A(d)/N(d) vs distance (per start + aggregate) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"[density] matplotlib 不可、PNG skip ({exc})")
        return

    fig, (ax_d, ax_n) = plt.subplots(2, 1, figsize=(9.5, 8), sharex=True,
                                     gridspec_kw={"height_ratios": [2, 1]})

    agg_a: Dict[int, int] = defaultdict(int)
    agg_n: Dict[int, int] = defaultdict(int)
    for s in starts:
        rec = per_start[s]
        ys, xs = [], []
        for d in ds:
            r = rec.get(d)
            if not r or r["shell"] == 0:
                continue
            xs.append(d)
            ys.append(r["access_sum"] / r["shell"])
            agg_a[d] += r["access_sum"]
            agg_n[d] += r["shell"]
        ax_d.plot(xs, ys, marker="o", markersize=3, linewidth=1.2, alpha=0.85,
                  label=f"start {s}")

    agg_x = [d for d in ds if agg_n.get(d, 0) > 0]
    agg_y = [agg_a[d] / agg_n[d] for d in agg_x]
    ax_d.plot(agg_x, agg_y, color="black", linewidth=2.4, linestyle="--",
              label="aggregate", zorder=5)
    if any(y > 0 for y in agg_y):
        ax_d.set_yscale("log")
    ax_d.set_ylabel("access per node  A(d)/N(d)  (log)")
    ax_d.set_title(args.title or "Access density (per node at distance d) vs distance")
    ax_d.grid(True, which="both", alpha=0.3)
    ax_d.legend(ncol=2, fontsize=9)

    # 下段: 参考として各 start のシェル幅 N(d) (構造そのもの)
    for s in starts:
        rec = per_start[s]
        xs = [d for d in ds if d in rec]
        ys = [rec[d]["shell"] for d in xs]
        ax_n.plot(xs, ys, marker=".", markersize=2, linewidth=1.0, alpha=0.8,
                  label=f"start {s}")
    ax_n.set_yscale("log")
    ax_n.set_xlabel("distance from start (original-graph BFS hop)")
    ax_n.set_ylabel("N(d): nodes at\ndistance d (log)")
    ax_n.grid(True, which="both", alpha=0.3)

    fig.tight_layout()
    out_png = Path(f"{args.out_prefix}_access_density.png")
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"[density] saved PNG: {out_png}")


if __name__ == "__main__":
    main()
