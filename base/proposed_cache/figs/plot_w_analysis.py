#!/usr/bin/env python3
# =============================================================================
# plot_w_analysis.py — 最適 w を「Base と Value の割合」で説明する図。
#
#   run_w_analysis.py の CSV を読み、3パネルで示す。
#     (a) w → overall ヒット率      … 最良 w はどこか（LRU/ARC/memo 参照線つき）
#     (b) w → Base 幅 と Score 幅   … 2項の尺度が釣り合う w はどこか
#     (c) w → 介入率                … スコアが recency の判断を覆した割合
#
#   横軸は w のグリッド位置（等間隔）。w=0 を含められるよう log 軸は使わない。
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_w_analysis.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"
COL = {"VLDB": "#4C72B0", "Amazon": "#DD8452"}


def load(path):
    rows = sorted(csv.DictReader(open(path)), key=lambda r: float(r["w_node"]))
    g = rows[0]["graph"]
    f = lambda k: [float(r[k]) for r in rows]
    d = dict(w=f("w_node"), overall=f("overall"), sd=f("overall_sd"),
             d_lru=f("d_lru"), interv=f("intervene"),
             base=f("base_spread"), score=f("bonus_spread"),
             ratio=f("spread_ratio"))
    meta = dict(lru=float(rows[0]["lru"]), arc=float(rows[0]["arc"]),
                memo=float(rows[0]["memo"]), cap=float(rows[0]["cap"]))
    return g, d, meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", nargs="+",
                   default=[str(_RES / "wanalysis_VLDB.csv"),
                            str(_RES / "wanalysis_Amazon.csv")])
    p.add_argument("--out", default=str(_FIGS / "w_analysis.png"))
    a = p.parse_args()

    data = [load(x) for x in a.csv]
    grid = data[0][1]["w"]
    xs = list(range(len(grid)))
    xt = [("0" if v == 0 else f"{v:g}") for v in grid]

    fig, axes = plt.subplots(1, 3, figsize=(16.2, 4.6))

    # ---------- (a) ヒット率 ----------
    ax = axes[0]
    for gi, (g, d, meta) in enumerate(data):
        c = COL.get(g, "gray")
        ax.errorbar(xs, d["overall"], yerr=d["sd"], marker="o", ms=5, lw=1.7,
                    color=c, capsize=3, label=g)
        ax.axhline(meta["lru"], color=c, ls="--", lw=1, alpha=.65)
        ax.axhline(meta["arc"], color=c, ls=":", lw=1.3, alpha=.65)
        bi = max(xs, key=lambda i: d["overall"][i])
        ax.plot(bi, d["overall"][bi], "*", ms=17, color=c, zorder=5,
                markeredgecolor="white", markeredgewidth=0.8)
        ax.annotate(f"best w={xt[bi]}", (bi, d["overall"][bi]),
                    xytext=(0, 14 + 13 * gi), textcoords="offset points",
                    ha="center", fontsize=8.5, color=c, fontweight="bold")
    if data[0][2]["cap"] in grid:
        ax.axvline(grid.index(data[0][2]["cap"]), color="gray", lw=1.1,
                   alpha=.55)
        ax.annotate("w = capacity", (grid.index(data[0][2]["cap"]), 0.02),
                    xycoords=("data", "axes fraction"), rotation=90,
                    fontsize=7.5, color="gray", ha="right", va="bottom")
    ax.set_ylabel("overall cache hit rate  [%]")
    ax.set_title("(a) Hit rate vs w\n(dashed = LRU, dotted = ARC)", fontsize=10)
    ax.legend(fontsize=9, loc="lower left")

    # ---------- (b) Base 幅 と Score 幅 ----------
    ax = axes[1]
    for g, d, meta in data:
        c = COL.get(g, "gray")
        ax.plot(xs, d["base"], marker="s", ms=4.5, lw=1.6, color=c,
                label=f"{g}  Base spread")
        ax.plot(xs, d["score"], marker="^", ms=5, lw=1.6, color=c, ls="--",
                label=f"{g}  Score spread (w·V/size)")
        bi = max(xs, key=lambda i: d["overall"][i])
        ax.plot(bi, d["score"][bi], "*", ms=15, color=c, zorder=5,
                markeredgecolor="white", markeredgewidth=0.7)
    ax.set_yscale("log")
    ax.set_title("(b) The two terms of Priority = Base + w·Value/size\n"
                 "(★ = the w that maximises hit rate)", fontsize=10)
    ax.set_ylabel("spread among resident entries  [clock ticks]")
    ax.legend(fontsize=7.5)

    # ---------- (c) 介入率 ----------
    ax = axes[2]
    for g, d, meta in data:
        c = COL.get(g, "gray")
        ax.plot(xs, d["interv"], marker="o", ms=5, lw=1.7, color=c, label=g)
        bi = max(xs, key=lambda i: d["overall"][i])
        ax.plot(bi, d["interv"][bi], "*", ms=17, color=c, zorder=5,
                markeredgecolor="white", markeredgewidth=0.8)
        ax.annotate(f"{d['interv'][bi]:.0f}%", (bi, d["interv"][bi]),
                    xytext=(6, -4), textcoords="offset points",
                    fontsize=9, color=c, fontweight="bold")
    ax.set_ylim(-3, 103)
    ax.set_ylabel("intervention rate  [%]")
    ax.set_title("(c) How often the score overrides LRU's choice\n"
                 "(★ = the w that maximises hit rate)", fontsize=10)
    ax.legend(fontsize=9, loc="lower right")

    for ax in axes:
        ax.set_xticks(xs); ax.set_xticklabels(xt, fontsize=8.5)
        ax.set_xlabel("w_node        (w_edge = 3·w_node)")
        ax.grid(alpha=.3, lw=.5); ax.set_axisbelow(True)

    fig.suptitle("What determines the optimal w "
                 "(α=0.1, 1,000 RWers, capacity 100, start 0, 5 independent seeds)",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(a.out, dpi=185)
    print(f"[saved] {a.out}")

    print()
    for g, d, meta in data:
        bi = max(xs, key=lambda i: d["overall"][i])
        ri = min(xs, key=lambda i: abs(d["ratio"][i] - 1.0))
        print(f"[{g}]  LRU {meta['lru']:.2f} / ARC {meta['arc']:.2f} / "
              f"memo {meta['memo']:.2f}   容量 {meta['cap']:.0f}")
        print(f"   最良 w = {xt[bi]}  → hit {d['overall'][bi]:.2f} "
              f"({d['d_lru'][bi]:+.2f} vs LRU)、介入率 {d['interv'][bi]:.1f}%、"
              f"幅の比 {d['ratio'][bi]:.2f}")
        print(f"   幅の比が 1 に最も近い w = {xt[ri]}  → hit {d['overall'][ri]:.2f} "
              f"({d['d_lru'][ri]:+.2f} vs LRU)、介入率 {d['interv'][ri]:.1f}%")
        print(f"   最良 w / 容量 = {d['w'][bi] / meta['cap']:.2f}")
        ok = [xt[i] for i in xs if d["d_lru"][i] > 0]
        print(f"   LRU を上回る w の範囲: {ok[0]} 〜 {ok[-1]}" if ok else "   LRU 超えなし")
        print()


if __name__ == "__main__":
    main()
