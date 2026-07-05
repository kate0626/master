#!/usr/bin/env python3
"""
アクセス頻度分析スクリプト。

複数の _global_transition.json を集約し、
- ノードごとのアクセス回数ランキング CSV
- 頻度分布 CSV（N回以上アクセスされるノード数）
- PNG 可視化
を出力する。

使い方:
  # ディレクトリ内の全 JSON を集約 (cache=none のみ)
  python3 analyze_access_frequency.py \
      --input results/access_locality/vldb \
      --out-dir results/access_locality/vldb \
      --filter-cache none

  # ファイルを直接指定
  python3 analyze_access_frequency.py \
      --input results/access_locality/vldb/start=0_*.json \
      --out-dir /tmp
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_auth_attempts(paths: list[Path]) -> dict[str, int]:
    merged: dict[str, int] = defaultdict(int)
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] skip {p}: {e}")
            continue
        for k, v in d.get("authorization_attempts", {}).items():
            merged[str(k)] += int(v)
    return dict(merged)


def write_ranking_csv(out_path: Path, counts: list[tuple[str, int]]) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "node_id", "access_count"])
        for rank, (nid, cnt) in enumerate(counts, 1):
            writer.writerow([rank, nid, cnt])
    print(f"[OUT] ranking CSV → {out_path}")


def write_distribution_csv(out_path: Path, counts: list[tuple[str, int]]) -> None:
    """
    各行: access_count N, N回ちょうどのノード数, N回以上のノード数, その割合
    """
    freq_counter = Counter(cnt for _, cnt in counts)
    total = len(counts)
    all_thresholds = sorted(freq_counter.keys())

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["access_count", "node_count_exact", "node_count_geq", "fraction_geq"])
        cum = total
        for ac in all_thresholds:
            exact = freq_counter[ac]
            writer.writerow([ac, exact, cum, round(cum / total, 6) if total else 0])
            cum -= exact
    print(f"[OUT] distribution CSV → {out_path}")


def plot(
    out_path: Path,
    counts: list[tuple[str, int]],
    graph_label: str,
    meta_label: str,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[WARN] matplotlib unavailable: {e}")
        return

    freq_counter = Counter(cnt for _, cnt in counts)
    total = len(counts)
    all_thresholds = sorted(freq_counter.keys())

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # 左: ランキング曲線（上位 500 ノード）
    ax = axes[0]
    top_n = min(500, len(counts))
    top_counts = [counts[i][1] for i in range(top_n)]
    ax.plot(range(1, top_n + 1), top_counts, color="#1565c0", lw=1.5)
    ax.set_xlabel("rank (by access count)")
    ax.set_ylabel("access count")
    ax.set_title(f"Access count ranking (top {top_n})\n{graph_label}  {meta_label}")
    ax.grid(True, alpha=0.3)
    if max(top_counts, default=1) > 10:
        ax.set_yscale("log")
        ax.set_ylabel("access count (log scale)")

    # 右: CCDF（N回以上にアクセスされたノードの割合）
    ax2 = axes[1]
    xs = all_thresholds
    cum = total
    ys = []
    for ac in xs:
        ys.append(cum / total if total else 0)
        cum -= freq_counter[ac]
    ax2.plot(xs, ys, color="#ef6c00", lw=1.5)
    ax2.set_xlabel("access count threshold N")
    ax2.set_ylabel("fraction of nodes accessed ≥ N times")
    ax2.set_title(f"CCDF of node access count\n{graph_label}  {meta_label}")
    ax2.set_ylim(0, 1.05)
    ax2.grid(True, alpha=0.3)
    if xs and xs[-1] > 50:
        ax2.set_xscale("log")
        ax2.set_xlabel("access count threshold N (log scale)")

    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    print(f"[OUT] PNG → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Access frequency distribution analyzer.")
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="JSON ファイルまたはディレクトリ（glob 可）。ディレクトリ指定時は *_global_transition.json を再帰検索。",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=".",
        help="出力先ディレクトリ（デフォルト: カレント）。",
    )
    parser.add_argument(
        "--out-prefix",
        type=str,
        default="freq",
        help="出力ファイル名のプレフィックス（デフォルト: freq）。",
    )
    parser.add_argument(
        "--filter-cache",
        type=str,
        default=None,
        help="ファイル名に _cache=<値>_ を含むものだけ使う（例: none, lru）。",
    )
    parser.add_argument(
        "--graph-label",
        type=str,
        default="",
        help="グラフのタイトルに使うラベル。",
    )
    args = parser.parse_args()

    # ファイル収集
    all_paths: list[Path] = []
    for pat in args.input:
        p = Path(pat)
        if p.is_dir():
            all_paths.extend(p.rglob("*_global_transition.json"))
        else:
            all_paths.extend(Path(g) for g in glob.glob(pat))

    if args.filter_cache:
        tag = f"_cache={args.filter_cache}_"
        all_paths = [p for p in all_paths if tag in p.name]

    all_paths = sorted(set(all_paths))
    print(f"[INFO] {len(all_paths)} JSON file(s) found.")
    if not all_paths:
        print("[ERROR] No files matched. Check --input and --filter-cache.")
        return

    merged = load_auth_attempts(all_paths)
    counts = sorted(merged.items(), key=lambda x: x[1], reverse=True)
    print(f"[INFO] Total nodes: {len(counts)}, total accesses: {sum(c for _, c in counts)}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix

    write_ranking_csv(out_dir / f"{prefix}_ranking.csv", counts)
    write_distribution_csv(out_dir / f"{prefix}_distribution.csv", counts)

    n_files = len(all_paths)
    meta = f"{n_files} JSON file(s)  filter={args.filter_cache or 'all'}"
    plot(out_dir / f"{prefix}_distribution.png", counts, args.graph_label or str(out_dir.name), meta)


if __name__ == "__main__":
    main()
