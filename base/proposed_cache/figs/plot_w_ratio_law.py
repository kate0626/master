#!/usr/bin/env python3
# =============================================================================
# plot_w_ratio_law.py — 「w は LRU の何倍にすべきか」を示す証明図。
#
#   主張: 最適な配合は w という設定値ではなく
#         「構造スコアの振れ幅 ÷ recency の振れ幅」（= 幅の比）で決まる。
#
#   3パネルで示す:
#     (a) 横軸 w            … 容量ごとに山の頂点がずれる（w は普遍量でない）
#     (b) 横軸 幅の比        … すべての系列で頂点が同じ位置に揃う（比が普遍量）
#     (c) 横軸 容量, 縦軸 最良 w … w ∝ 容量（傾き1の参照線つき）
#
#   入力: run_w_analysis.py の CSV を複数（容量ごと・グラフごと）
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_w_ratio_law.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
import glob
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"

COL = {"VLDB": "#4C72B0", "Amazon": "#DD8452"}
MK = {50: "s", 100: "o", 200: "^", 400: "D", 800: "v"}


def load_all(paths):
    """(graph, cap) -> 昇順の行リスト"""
    series = defaultdict(list)
    for p in paths:
        for r in csv.DictReader(open(p)):
            series[(r["graph"], int(float(r["cap"])))].append(r)
    for k in series:
        series[k].sort(key=lambda r: float(r["w_node"]))
    return series


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", nargs="*", default=None,
                   help="既定は results/wanalysis_*.csv をすべて読む")
    p.add_argument("--out", default=str(_FIGS / "w_ratio_law.png"))
    a = p.parse_args()

    paths = a.csv or sorted(glob.glob(str(_RES / "wanalysis_*.csv")))
    if not paths:
        raise SystemExit("wanalysis_*.csv が見つかりません")
    series = load_all(paths)
    print(f"[読み込み] {len(paths)} ファイル / {len(series)} 系列")

    fig, axes = plt.subplots(1, 3, figsize=(16.4, 4.7))

    best = {}
    for (g, cap), rows in sorted(series.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        c = COL.get(g, "gray")
        mk = MK.get(cap, "o")
        w = [float(r["w_node"]) for r in rows]
        dl = [float(r["d_lru"]) for r in rows]
        rt = [float(r["spread_ratio"]) for r in rows]
        lab = f"{g}  C={cap}"

        axes[0].plot(w, dl, marker=mk, ms=4.5, lw=1.5, color=c, alpha=.9,
                     label=lab)
        axes[1].plot(rt, dl, marker=mk, ms=4.5, lw=1.5, color=c, alpha=.9,
                     label=lab)

        bi = max(range(len(dl)), key=lambda i: dl[i])
        best[(g, cap)] = (w[bi], rt[bi], dl[bi])
        axes[0].plot(w[bi], dl[bi], "*", ms=15, color=c, zorder=5,
                     markeredgecolor="white", markeredgewidth=0.7)
        axes[1].plot(rt[bi], dl[bi], "*", ms=15, color=c, zorder=5,
                     markeredgecolor="white", markeredgewidth=0.7)

    # ---------- (a) 横軸 w ----------
    ax = axes[0]
    ax.set_xscale("symlog", linthresh=10)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("w_node        (w_edge = 3·w_node)")
    ax.set_ylabel("proposed − LRU  [pt]")
    ax.set_title("(a) Against w itself\nthe peaks do NOT line up", fontsize=10)
    ax.legend(fontsize=7, ncol=2)

    # ---------- (b) 横軸 幅の比 ----------
    ax = axes[1]
    rts = [v[1] for v in best.values()]
    lo, hi = min(rts), max(rts)
    ax.axvspan(lo, hi, color="#FFD966", alpha=.35, zorder=0)
    ax.axhline(0, color="black", lw=1)
    ax.set_xlabel("spread ratio   =   spread(w·Value/size)  ÷  spread(Base)")
    ax.set_ylabel("proposed − LRU  [pt]")
    ax.set_title(f"(b) Against the spread ratio\nall peaks fall in {lo:.1f}–{hi:.1f}",
                 fontsize=10)
    ax.annotate(f"optimum ≈ {sum(rts)/len(rts):.1f}×",
                ((lo + hi) / 2, ax.get_ylim()[0]), xycoords=("data", "axes fraction"),
                xytext=((lo + hi) / 2, 0.06), textcoords=("data", "axes fraction"),
                ha="center", fontsize=9, fontweight="bold", color="#8A6D00")
    ax.legend(fontsize=7, ncol=2)

    # ---------- (c) 最良 w vs 容量 ----------
    ax = axes[2]
    for g in sorted({k[0] for k in best}):
        caps = sorted(c for (gg, c) in best if gg == g)
        ws = [best[(g, c)][0] for c in caps]
        ax.plot(caps, ws, marker="o", ms=6, lw=1.7, color=COL.get(g, "gray"),
                label=g)
        for c_, w_ in zip(caps, ws):
            ax.annotate(f"{w_/c_:.2f}C", (c_, w_), xytext=(5, -9),
                        textcoords="offset points", fontsize=7.5,
                        color=COL.get(g, "gray"))
    allc = sorted({k[1] for k in best})
    ax.plot(allc, allc, ls="--", lw=1.2, color="gray", label="w = C (reference)")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("cache capacity  C")
    ax.set_ylabel("best w_node")
    ax.set_title("(c) The best w scales with capacity\n(w ≈ C)", fontsize=10)
    ax.legend(fontsize=8)

    for ax in axes:
        ax.grid(alpha=.3, lw=.5); ax.set_axisbelow(True)

    fig.suptitle("How large should the structural score be relative to LRU? "
                 "(α=0.1, 1,000 RWers, start 0, 5 independent seeds)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(a.out, dpi=185)
    print(f"[saved] {a.out}")

    print()
    hdr = f"{'graph':<9}{'容量':>6}{'最良 w':>9}{'w/C':>7}{'幅の比':>8}{'vs LRU':>9}"
    print(hdr); print("-" * len(hdr))
    for (g, cap), (w, rt, dl) in sorted(best.items()):
        print(f"{g:<9}{cap:>6}{w:>9.1f}{w/cap:>7.2f}{rt:>8.2f}{dl:>+9.2f}")
    print("-" * len(hdr))
    print(f"幅の比の範囲: {min(rts):.2f} 〜 {max(rts):.2f}   "
          f"平均 {sum(rts)/len(rts):.2f}")
    wc = [w / cap for (g, cap), (w, rt, dl) in best.items()]
    print(f"w/C の範囲   : {min(wc):.2f} 〜 {max(wc):.2f}   "
          f"平均 {sum(wc)/len(wc):.2f}")


if __name__ == "__main__":
    main()
