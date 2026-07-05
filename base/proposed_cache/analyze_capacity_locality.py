#!/usr/bin/env python3
"""
容量制限付き局所性分析スクリプト。

「K 本の walk を使って容量 C のキャッシュを作ったとき、
 K=100（全 walk）で作る最適キャッシュにどれだけ近いか」を定量評価し、
少ない walk 数でも局所性が収束することを示す。

■ 出力メトリクス（per K, C）
  - jaccard      : top-C@K と top-C@100 のノード集合の Jaccard 係数
  - coverage_all : top-C@K が全 100 walk のアクセスをどれだけカバーするか (0-1)
  - hit_rate_held: top-C@K キャッシュを使って残り (K+1)〜100 walk を捌いたときのヒット率

■ 入力
  per_walk_access.json（auth-many-server または split_controller_proposed 形式）

■ 使い方
  # D2/1-approach の既存データで動かす
  python3 base/proposed_cache/analyze_capacity_locality.py \\
      --input runs/auth/D2/1-approach/0.3/vldb \\
      --out-dir base/proposed_cache/results/capacity_locality/vldb \\
      --graph-label vldb

  # 複数グラフをまとめて
  python3 base/proposed_cache/analyze_capacity_locality.py \\
      --input runs/auth/D2/1-approach/0.3/vldb \\
               runs/auth/D2/1-approach/0.3/amazon0601 \\
               runs/auth/D2/1-approach/0.3/karate \\
      --out-dir base/proposed_cache/results/capacity_locality \\
      --per-graph
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple


# ──────────────────────────────────────────────
# IO
# ──────────────────────────────────────────────

def load_per_walk(path: Path) -> Tuple[List[dict], dict]:
    """per_walk_access.json を読み込む。(per_walk_list, controller_meta) を返す。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(d, dict) and "per_walk_access" in d:
        return d["per_walk_access"], d.get("controller", {})
    if isinstance(d, list):
        return d, {}
    raise ValueError(f"Unexpected JSON format: {path}")


def extract_start_node(path: Path) -> Optional[str]:
    m = re.search(r"start=(\d+)", path.name)
    return m.group(1) if m else None


# ──────────────────────────────────────────────
# 集計
# ──────────────────────────────────────────────

def build_counter(per_walk: List[dict], k_start: int, k_end: int) -> Counter:
    """walk_index が [k_start, k_end] の範囲のアクセスを集計する（1-indexed）。"""
    c: Counter = Counter()
    for w in per_walk:
        idx = int(w.get("walk_index", 0))
        if k_start <= idx <= k_end:
            for ent, cnt in w.get("access", {}).items():
                c[str(ent)] += int(cnt)
    return c


def top_c_nodes(counter: Counter, capacity: int) -> Set[str]:
    return {ent for ent, _ in counter.most_common(capacity)}


# ──────────────────────────────────────────────
# メトリクス
# ──────────────────────────────────────────────

def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a or b) else 0.0


def coverage(cache: Set[str], counter_ref: Counter) -> float:
    total = sum(counter_ref.values())
    if total == 0:
        return 0.0
    return sum(counter_ref.get(n, 0) for n in cache) / total


def hit_rate_held_out(cache: Set[str], per_walk: List[dict], k_held_start: int, k_held_end: int) -> float:
    """
    walk_index in [k_held_start, k_held_end] のアクセスを「テスト」として
    cache でのヒット率を計算する。
    """
    counter_held = build_counter(per_walk, k_held_start, k_held_end)
    if not counter_held:
        return float("nan")
    hit = sum(counter_held.get(n, 0) for n in cache)
    total = sum(counter_held.values())
    return hit / total


# ──────────────────────────────────────────────
# メインの分析ループ
# ──────────────────────────────────────────────

def analyze(
    per_walk: List[dict],
    k_list: Sequence[int],
    c_list: Sequence[int],
    start_node: Optional[str] = None,
) -> List[dict]:
    """
    各 (K, C) の組み合わせでメトリクスを計算し、rowsリストを返す。

    K_FINAL = max(k_list) を「最終（近似）真値」とする。
    """
    k_final = max(k_list)

    # K_FINAL のカウンタをキャッシュ（繰り返し使う）
    counter_final = build_counter(per_walk, 1, k_final)
    # 全 walk（1〜K_FINAL）のアクセス総計
    counter_all = counter_final  # ここでは同じ

    rows = []
    for K in k_list:
        counter_k = build_counter(per_walk, 1, K)

        for C in c_list:
            cache_k = top_c_nodes(counter_k, C)
            cache_final = top_c_nodes(counter_final, C)

            jac = jaccard(cache_k, cache_final)
            cov_all = coverage(cache_k, counter_all)

            # ヒット率：K+1〜K_FINAL を held-out テストセットに
            if K < k_final:
                hr_held = hit_rate_held_out(cache_k, per_walk, K + 1, k_final)
            else:
                hr_held = float("nan")  # K=K_FINAL はテストセットなし

            rows.append(
                {
                    "start_node": start_node or "?",
                    "K": K,
                    "C": C,
                    "jaccard": round(jac, 4),
                    "coverage_all": round(cov_all, 4),
                    "hit_rate_held": round(hr_held, 4) if hr_held == hr_held else "nan",
                    "cache_size_at_k": len(cache_k),
                    "cache_size_optimal": len(cache_final),
                }
            )

    return rows


# ──────────────────────────────────────────────
# CSV / PNG 出力
# ──────────────────────────────────────────────

def save_csv(out_path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OUT] CSV → {out_path}")


def plot_metrics(
    out_path: Path,
    rows: List[dict],
    graph_label: str,
    k_final: int,
    c_list: Sequence[int],
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[WARN] matplotlib unavailable: {e}")
        return

    # rows を (C, K) でインデックス
    data: Dict[int, Dict[int, dict]] = defaultdict(dict)
    for r in rows:
        data[int(r["C"])][int(r["K"])] = r

    k_list = sorted({int(r["K"]) for r in rows})
    colors = plt.cm.tab10.colors  # type: ignore

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    metrics = [
        ("jaccard", "Jaccard (top-C@K vs top-C@final)", "Jaccard"),
        ("coverage_all", "Coverage on all walks", "Coverage"),
        ("hit_rate_held", "Hit rate on held-out walks", "Hit rate"),
    ]

    for ax, (key, title, ylabel) in zip(axes, metrics):
        for i, C in enumerate(c_list):
            ys = []
            xs = []
            for K in k_list:
                val = data[C].get(K, {}).get(key, float("nan"))
                if val == "nan":
                    val = float("nan")
                ys.append(float(val))
                xs.append(K)
            ax.plot(xs, ys, marker="o", label=f"C={C}", color=colors[i % len(colors)])

        ax.axvline(x=k_final, color="gray", linestyle="--", linewidth=0.8, label=f"K_final={k_final}")
        ax.set_xlabel("K (walks used to build cache)")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{title}\n{graph_label}")
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    print(f"[OUT] PNG → {out_path}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def collect_files(input_args: List[str]) -> List[Path]:
    paths: List[Path] = []
    for arg in input_args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(p.rglob("*_per_walk_access.json"))
        else:
            paths.extend(Path(g) for g in glob.glob(arg))
    return sorted(set(paths))


def run_for_files(
    files: List[Path],
    k_list: List[int],
    c_list: List[int],
    out_dir: Path,
    graph_label: str,
    prefix: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows: List[dict] = []

    for f in files:
        per_walk, meta = load_per_walk(f)
        start_node = extract_start_node(f) or meta.get("start_node", "?")
        print(f"[INFO] {f.name}  start={start_node}  walks={len(per_walk)}")
        rows = analyze(per_walk, k_list, c_list, start_node=str(start_node))
        all_rows.extend(rows)

    if not all_rows:
        print("[WARN] No data to output.")
        return

    save_csv(out_dir / f"{prefix}_capacity_locality.csv", all_rows)

    # 複数 start_node がある場合は平均を取って plot
    from collections import defaultdict as _dd
    agg: Dict[Tuple[int, int], List[dict]] = _dd(list)
    for r in all_rows:
        agg[(int(r["K"]), int(r["C"]))].append(r)

    # start_node ごとに別グラフ or 全体平均
    start_nodes = sorted({r["start_node"] for r in all_rows})
    if len(start_nodes) == 1:
        plot_rows = all_rows
    else:
        # K, C ごとに平均
        agg2: Dict[Tuple[int, int], Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for r in all_rows:
            key = (int(r["K"]), int(r["C"]))
            for metric in ("jaccard", "coverage_all", "hit_rate_held"):
                v = r[metric]
                if v != "nan":
                    agg2[key][metric].append(float(v))
        plot_rows = []
        for (K, C), metrics in sorted(agg2.items()):
            plot_rows.append(
                {
                    "start_node": "avg",
                    "K": K,
                    "C": C,
                    "jaccard": round(sum(metrics.get("jaccard", [0])) / max(len(metrics.get("jaccard", [1])), 1), 4),
                    "coverage_all": round(sum(metrics.get("coverage_all", [0])) / max(len(metrics.get("coverage_all", [1])), 1), 4),
                    "hit_rate_held": round(sum(metrics.get("hit_rate_held", [0])) / max(len(metrics.get("hit_rate_held", [1])), 1), 4),
                    "cache_size_at_k": 0,
                    "cache_size_optimal": 0,
                }
            )

    k_final = max(k_list)
    plot_metrics(
        out_dir / f"{prefix}_capacity_locality.png",
        plot_rows,
        graph_label=graph_label,
        k_final=k_final,
        c_list=c_list,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capacity-constrained locality analysis for random walk caching."
    )
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="per_walk_access.json ファイル、またはそれを含むディレクトリ。",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=".",
        help="出力先ディレクトリ。",
    )
    parser.add_argument(
        "--out-prefix",
        type=str,
        default="result",
        help="出力ファイル名プレフィックス。",
    )
    parser.add_argument(
        "--graph-label",
        type=str,
        default="",
        help="グラフタイトル用ラベル。",
    )
    parser.add_argument(
        "--k-list",
        nargs="+",
        type=int,
        default=list(range(10, 101, 10)),
        help="評価する K 値リスト（デフォルト: 10,20,...,100）。",
    )
    parser.add_argument(
        "--c-list",
        nargs="+",
        type=int,
        default=[30, 50, 100, 200],
        help="評価するキャッシュ容量 C のリスト（デフォルト: 30 50 100 200）。",
    )
    parser.add_argument(
        "--per-graph",
        action="store_true",
        help="--input で複数ディレクトリを渡したとき、ディレクトリごとに別グラフとして出力。",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)

    if args.per_graph:
        for inp in args.input:
            p = Path(inp)
            files = list(p.rglob("*_per_walk_access.json")) if p.is_dir() else [p]
            label = args.graph_label or p.name
            run_for_files(
                files,
                args.k_list,
                args.c_list,
                out_dir / p.name,
                graph_label=label,
                prefix=args.out_prefix,
            )
    else:
        files = collect_files(args.input)
        print(f"[INFO] {len(files)} per_walk_access.json file(s) found.")
        run_for_files(
            files,
            args.k_list,
            args.c_list,
            out_dir,
            graph_label=args.graph_label or out_dir.name,
            prefix=args.out_prefix,
        )


if __name__ == "__main__":
    main()
