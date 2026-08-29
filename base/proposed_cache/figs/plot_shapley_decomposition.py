#!/usr/bin/env python3
# =============================================================================
# plot_shapley_decomposition.py
#   run_shapley_decomposition.py の CSV から「寄与の分解」図を作る。
#
#   (a) 積み上げ棒: LRU からの上積みを4要素に分解。合計が総改善に厳密に一致する。
#   (b) 要素別の棒: Shapley 値（足し合わせ可）± sd を VLDB / Amazon 並べて表示。
#       参考に LOO（限界寄与）を薄い枠線の棒で重ねる。
# =============================================================================
from __future__ import annotations

import argparse
import csv

import matplotlib

from pathlib import Path as _P
_FIGS = _P(__file__).resolve().parent
_RES = _FIGS.parent / "results"
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ELEMS = ["Pool", "Freq", "Close", "Deg"]
LABEL = {"Pool": "Pool split", "Freq": "Freq", "Close": "Close", "Deg": "Deg"}
ECOL = {"Pool": "#4C72B0", "Freq": "#DD8452", "Close": "#55A868", "Deg": "#C44E52"}


def load(path):
    rows = list(csv.DictReader(open(path)))
    g = rows[0]["graph"]
    d = {r["element"]: r for r in rows}
    meta = dict(lru=float(rows[0]["lru"]), v_empty=float(rows[0]["v_empty"]),
                v_full=float(rows[0]["v_full"]), arc=float(rows[0]["arc"]),
                memo=float(rows[0]["memo"]),
                gain_lru=float(rows[0]["gain_vs_lru"]),
                gain_empty=float(rows[0]["gain_vs_empty"]))
    return g, d, meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", nargs="+",
                   default=[str(_RES / "shapley_VLDB.csv"),
                            str(_RES / "shapley_Amazon.csv")])
    p.add_argument("--out", default=str(_FIGS / "shapley_decomposition.png"))
    a = p.parse_args()

    data = [load(x) for x in a.csv]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8),
                             gridspec_kw=dict(width_ratios=[1, 1.45]))

    # ---------- (a) 積み上げ棒: 総改善の分解 ----------
    ax = axes[0]
    xs = range(len(data))
    for xi, (g, d, meta) in zip(xs, data):
        bottom = 0.0
        for e in ELEMS:
            h = float(d[e]["shapley"])
            ax.bar(xi, h, 0.5, bottom=bottom, color=ECOL[e],
                   edgecolor="white", linewidth=0.8,
                   label=LABEL[e] if xi == 0 else None)
            if abs(h) > 0.25:
                ax.annotate(f"{h:+.2f}", (xi, bottom + h / 2), ha="center",
                            va="center", fontsize=8.5, color="white",
                            fontweight="bold")
            else:
                ax.annotate(f"{h:+.2f}", (xi + 0.28, bottom + h / 2),
                            ha="left", va="center", fontsize=8)
            bottom += h
        ax.annotate(f"total {meta['gain_empty']:+.2f} pt", (xi, bottom),
                    ha="center", va="bottom", fontsize=9.5, fontweight="bold",
                    xytext=(0, 4), textcoords="offset points")
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{g}\n(LRU {meta['lru']:.1f}% → "
                        f"{meta['v_full']:.1f}%)" for g, _, meta in data],
                       fontsize=9)
    ax.set_ylabel("contribution to the gain over LRU  [pt]")
    ax.set_title("(a) The gain decomposes exactly\n(Shapley values, they sum to the total)",
                 fontsize=10)
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(0.02, 0.42),
              framealpha=0.95)
    ax.set_ylim(0, max(m["gain_empty"] for _, _, m in data) * 1.22)
    ax.grid(axis="y", alpha=0.3, lw=0.5)

    # ---------- (b) 要素別: Shapley と LOO を並べる ----------
    ax = axes[1]
    width = 0.34
    for k, (g, d, meta) in enumerate(data):
        off = (k - 0.5) * width
        sh = [float(d[e]["shapley"]) for e in ELEMS]
        shsd = [float(d[e]["shapley_sd"]) for e in ELEMS]
        lo = [float(d[e]["loo"]) for e in ELEMS]
        col = "#4C72B0" if k == 0 else "#DD8452"
        ax.bar([i + off for i in range(len(ELEMS))], sh, width, yerr=shsd,
               capsize=3, color=col, edgecolor="white", linewidth=0.6,
               label=f"{g}  Shapley")
        ax.bar([i + off for i in range(len(ELEMS))], lo, width, fill=False,
               edgecolor=col, linewidth=1.4, linestyle="--",
               label=f"{g}  leave-one-out")
        for i, (s_, l_) in enumerate(zip(sh, lo)):
            ax.annotate(f"{s_:+.1f}", (i + off, s_), ha="center",
                        va="bottom" if s_ >= 0 else "top",
                        xytext=(0, 3 if s_ >= 0 else -11),
                        textcoords="offset points", fontsize=7.5)
    ax.axhline(0, color="black", lw=1)
    ax.set_xticks(range(len(ELEMS)))
    ax.set_xticklabels([LABEL[e] for e in ELEMS])
    ax.set_ylabel("contribution  [pt]")
    ax.set_title("(b) Per-element contribution\n"
                 "dashed = leave-one-out (marginal; does NOT sum to the total)",
                 fontsize=10)
    ax.legend(fontsize=7.5, ncol=2)
    ax.grid(axis="y", alpha=0.3, lw=0.5)

    fig.suptitle("Where the improvement over LRU comes from "
                 "(α=0.1, 1,000 RWers, capacity 100, start 0)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(a.out, dpi=180)
    print(f"[saved] {a.out}")

    for g, d, meta in data:
        ssum = sum(float(d[e]["shapley"]) for e in ELEMS)
        lsum = sum(float(d[e]["loo"]) for e in ELEMS)
        print(f"{g}: Σ Shapley = {ssum:+.2f}  (総改善 {meta['gain_empty']:+.2f})   "
              f"Σ LOO = {lsum:+.2f}  → 交互作用 {lsum - meta['gain_empty']:+.2f}")


if __name__ == "__main__":
    main()
