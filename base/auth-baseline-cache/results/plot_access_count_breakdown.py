#!/usr/bin/env python3
"""
アクセス回数 c の分布を可視化する。

- 1回のみアクセスされたノードの割合
- 2回以上アクセスされたノードの内訳 (c=2, 3, 4, 5, 6-10, 11-50, 51+)
"""
from __future__ import annotations

import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic", "IPAPGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# (label, lo, hi) — hi=None は上限なし
MULTI_BUCKETS = [
    ("c=2",    2,   2),
    ("c=3",    3,   3),
    ("c=4",    4,   4),
    ("c=5",    5,   5),
    ("c=6-10", 6,   10),
    ("c=11-50",11,  50),
    ("c=51+",  51,  None),
]

COLOR_SINGLE = "#d62728"   # 赤系: 1回のみ
COLOR_MULTI  = "#1f77b4"   # 青系: 複数回
MULTI_COLORS = [
    "#aec7e8", "#6baed6", "#4393c3", "#2166ac",
    "#08519c", "#023858", "#011628",
]


def load_c_counts(csv_path: Path) -> list[int]:
    """CSVから各ノードのアクセス回数cを返す"""
    counts = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            counts.append(int(row["c"]))
    return counts


def bucket_counts(counts: list[int]) -> tuple[int, list[int]]:
    """c=1 の個数と MULTI_BUCKETS ごとの個数を返す"""
    single = sum(1 for c in counts if c == 1)
    multi = []
    for _, lo, hi in MULTI_BUCKETS:
        if hi is None:
            multi.append(sum(1 for c in counts if c >= lo))
        else:
            multi.append(sum(1 for c in counts if lo <= c <= hi))
    return single, multi


def plot_graph(ax_pie, ax_bar, counts: list[int], graph_name: str):
    total = len(counts)
    single, multi_vals = bucket_counts(counts)
    multi_total = sum(multi_vals)

    # ---- 左: ドーナツ図 (c=1 vs c≥2) ----
    sizes = [single, multi_total]
    colors_pie = [COLOR_SINGLE, COLOR_MULTI]
    wedges, texts, autotexts = ax_pie.pie(
        sizes,
        colors=colors_pie,
        autopct=lambda p: f"{p:.1f}%",
        startangle=90,
        pctdistance=0.75,
        wedgeprops=dict(width=0.55, edgecolor="white", linewidth=2),
        textprops=dict(fontsize=11),
    )
    autotexts[0].set_fontsize(13)
    autotexts[0].set_fontweight("bold")
    autotexts[1].set_fontsize(13)
    autotexts[1].set_fontweight("bold")

    ax_pie.text(0, 0, f"N={total:,}", ha="center", va="center",
                fontsize=10, color="#333")

    legend_patches = [
        mpatches.Patch(color=COLOR_SINGLE, label=f"c=1（1回のみ）  {single:,}ノード"),
        mpatches.Patch(color=COLOR_MULTI,  label=f"c>=2（複数回）  {multi_total:,}ノード"),
    ]
    ax_pie.legend(handles=legend_patches, loc="lower center",
                  bbox_to_anchor=(0.5, -0.18), fontsize=9, framealpha=0.9)
    ax_pie.set_title(f"{graph_name}\nアクセス回数の概要", fontsize=11, fontweight="bold", pad=8)

    # ---- 右: 棒グラフ (c=1 + c>=2 内訳) ----
    all_labels = ["c=1\n(1回のみ)"] + [b[0] for b in MULTI_BUCKETS]
    all_vals   = [single] + multi_vals
    all_colors = [COLOR_SINGLE] + MULTI_COLORS
    x = np.arange(len(all_labels))

    bars = ax_bar.bar(x, all_vals, color=all_colors, edgecolor="white", linewidth=0.8)

    for bar, val in zip(bars, all_vals):
        if val == 0:
            continue
        pct_of_total = 100 * val / total
        ax_bar.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.05,
            f"{val:,}\n({pct_of_total:.1f}%)",
            ha="center", va="bottom", fontsize=8.5, color="#222", linespacing=1.2,
        )

    ax_bar.set_xticks(x)
    ax_bar.set_xticklabels(all_labels, fontsize=9)
    ax_bar.set_xlabel("アクセス回数 c", fontsize=10)
    ax_bar.set_ylabel("ノード数", fontsize=10)
    ax_bar.set_yscale("log")
    ax_bar.set_ylim(0.5, max(all_vals) * 4)
    ax_bar.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
    ax_bar.set_axisbelow(True)

    note = f"全{total:,}ノード中の内訳（括弧内は全ノードに対する割合）"
    ax_bar.set_title(f"{graph_name}\nアクセス回数別ノード数", fontsize=11, fontweight="bold", pad=8)
    ax_bar.text(0.98, 0.97, note, transform=ax_bar.transAxes,
                ha="right", va="top", fontsize=8, color="#555",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="#f5f5f5", edgecolor="#ccc"))


def main():
    results_dir = Path(__file__).parent / "alpha0.01_walks_100_capa_100"

    datasets = [
        ("amazon0601", results_dir / "hit_count_dist_amazon0601.csv"),
        ("vldb",       results_dir / "hit_count_dist_vldb.csv"),
    ]

    # 2グラフ × 2パネル (pie + bar) = 2行4列
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("アクセス回数分布：1回のみ vs 複数回の内訳\n(α=0.01, walks=100)",
                 fontsize=14, fontweight="bold", y=0.98)

    for row, (graph_name, csv_path) in enumerate(datasets):
        if not csv_path.exists():
            print(f"[skip] {csv_path} not found")
            continue
        counts = load_c_counts(csv_path)
        plot_graph(axes[row, 0], axes[row, 1], counts, graph_name)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = Path(__file__).parent / "access_count_breakdown.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
