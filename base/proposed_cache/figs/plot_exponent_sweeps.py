#!/usr/bin/env python3
# =============================================================================
# plot_exponent_sweeps.py — 冪指数 θ / β / γ の 1D スイープ図（論文用）。
#
#   「θ・β・γ をどう決めたのか」に答えるための図。
#   run_param_sensitivity.py が出した CSV から θ/β/γ の3軸だけを抜き出し、
#   横軸=指数の値、縦軸=overall ヒット率 で並べる。
#     - LRU / ARC を水平線
#     - 動作点（論文の値）を灰色の縦線
#     - 各グラフの最良値を ★ で表示
#
#   ※ 追加の実験は不要（既存の sens_*.csv をそのまま使う）。
#     条件は sens_*.csv と同一：α=0.1、RW1000、cap100、始点0、独立seed5点。
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_exponent_sweeps.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"

AXES = [
    ("theta", "θ   (Freq exponent)",  "Freq"),
    ("beta",  "β   (Close exponent)", "Close"),
    ("gamma", "γ   (Deg exponent)",   "Deg"),
]
COL = {"VLDB": "#4C72B0", "Amazon": "#DD8452"}


def load(path):
    rows = list(csv.DictReader(open(path)))
    g = rows[0]["graph"]
    per = defaultdict(list)
    for r in rows:
        per[r["axis"]].append(r)
    meta = dict(lru=float(rows[0]["lru"]), arc=float(rows[0]["arc"]),
                memo=float(rows[0]["memo"]))
    return g, per, meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", nargs="+",
                   default=[str(_RES / "sens_VLDB.csv"),
                            str(_RES / "sens_Amazon.csv")])
    p.add_argument("--out", default=str(_FIGS / "exponent_sweeps.png"))
    p.add_argument("--combo", nargs="*", default=["VLDB=53.61", "Amazon=52.98"],
                   help="3軸すべてを最良値(θ1.5/β0.5/γ0.5)にしたときの実測値。"
                        "GRAPH=VALUE 形式。空文字を渡すと描かない。")
    a = p.parse_args()
    combo = {}
    for kv in (a.combo or []):
        if "=" in kv:
            k, v = kv.split("=", 1)
            combo[k] = float(v)

    data = [load(x) for x in a.csv]
    fig, axes = plt.subplots(1, len(AXES), figsize=(4.6 * len(AXES), 4.4),
                             sharey=True)

    summary = []
    for ax, (axis, xlabel, elem) in zip(axes, AXES):
        for g, per, meta in data:
            rows = sorted(per[axis], key=lambda r: float(r["value"]))
            if not rows:
                continue
            x = [float(r["value"]) for r in rows]
            y = [float(r["overall"]) for r in rows]
            e = [float(r["overall_sd"]) for r in rows]
            c = COL.get(g, "gray")
            ax.errorbar(x, y, yerr=e, marker="o", ms=5, lw=1.6, color=c,
                        capsize=3, label=g)
            ax.axhline(meta["lru"], color=c, ls="--", lw=1, alpha=.65)
            ax.axhline(meta["arc"], color=c, ls=":", lw=1.2, alpha=.65)
            # 最良値に ★
            bi = max(range(len(y)), key=lambda i: y[i])
            ax.plot(x[bi], y[bi], "*", ms=16, color=c, zorder=5,
                    markeredgecolor="white", markeredgewidth=0.7)
            # 動作点（is_center）に縦線
            cen = [r for r in rows if r["is_center"] == "1"]
            if cen:
                ax.axvline(float(cen[0]["value"]), color="gray", lw=1.1,
                           alpha=.55)
                cv = float(cen[0]["overall"])
                summary.append((elem, g, float(cen[0]["value"]), cv,
                                x[bi], y[bi], y[bi] - cv))
        ax.set_xlabel(xlabel, fontsize=11)
        ax.grid(alpha=.3, lw=.5)
        ax.set_axisbelow(True)

    # 3軸すべてを最良値にしたときの実測値（加法性の確認用）
    for ax in axes:
        for g, _, _ in data:
            if g in combo:
                ax.axhline(combo[g], color=COL.get(g, "gray"), ls="-.", lw=1.1,
                           alpha=.75)
    if combo:
        axes[1].annotate(
            "dash-dot = all three set to their best at once\n"
            + "   ".join(f"{g}: {v:.2f}" for g, v in combo.items()),
            xy=(0.5, 0.03), xycoords="axes fraction", ha="center", va="bottom",
            fontsize=7.5,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#AAAAAA", lw=0.6))

    axes[0].set_ylabel("overall cache hit rate  [%]")
    axes[0].legend(fontsize=9, loc="lower center")
    axes[-1].annotate("dashed = LRU\ndotted = ARC\n★ = best\ngray line = value used in the paper",
                      xy=(0.98, 0.04), xycoords="axes fraction", ha="right",
                      va="bottom", fontsize=8,
                      bbox=dict(boxstyle="round,pad=0.35", fc="white",
                                ec="#AAAAAA", lw=0.6))

    fig.suptitle("How the exponents were chosen — one-at-a-time sweeps: "
                 "each exponent is varied while the other two are held at 1\n"
                 "(α=0.1, 1,000 RWers, capacity 100, start 0, 5 independent seeds)",
                 fontsize=10.5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(a.out, dpi=190)
    print(f"[saved] {a.out}")

    print()
    hdr = (f"{'exp':<7}{'graph':<9}{'論文値':>8}{'その値':>9}"
           f"{'最良値':>8}{'最良':>9}{'差':>8}")
    print(hdr); print("-" * len(hdr))
    for elem, g, cx, cy, bx, by, d in summary:
        print(f"{elem:<7}{g:<9}{cx:>8.1f}{cy:>9.2f}{bx:>8.1f}{by:>9.2f}{d:>+8.2f}")
    print()
    print("差 = (最良のヒット率) − (論文の値でのヒット率)。0 に近いほど論文の値が妥当。")


if __name__ == "__main__":
    main()
