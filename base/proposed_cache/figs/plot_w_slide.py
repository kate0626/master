#!/usr/bin/env python3
# =============================================================================
# plot_w_slide.py — スライド用 2 パネル図。
#   左: 横軸 w        → 頂点がバラバラ（w では決まらない）
#   右: 横軸 幅の比    → 頂点が 2 付近に集まる（比で決まる）
#   縦軸はどちらも「提案 − LRU [pt]」。--normalize で各系列の最大値=1 に正規化。
#
#   python3 base/proposed_cache/figs/plot_w_slide.py
#   python3 base/proposed_cache/figs/plot_w_slide.py --normalize
# =============================================================================
import argparse, csv, glob
from collections import defaultdict
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"
COL = {"VLDB": "#4C72B0", "Amazon": "#DD8452"}
MK = {
    # 50: "s", 
      100: "o", 200: "^", 
    #   400: "D"
      }

p = argparse.ArgumentParser()
p.add_argument("--csv", nargs="*", default=None)
p.add_argument("--normalize", action="store_true")
p.add_argument("--out", default=None)
a = p.parse_args()

paths = a.csv or sorted(glob.glob(str(_RES / "wanalysis_*.csv")))
series = defaultdict(list)
for path in paths:
    for r in csv.DictReader(open(path)):
        series[(r["graph"], int(float(r["cap"])))].append(r)
for k in series:
    series[k].sort(key=lambda r: float(r["w_node"]))

out = a.out or str(_FIGS / ("w_slide_norm.png" if a.normalize else "w_slide.png"))
fig, (axL, axR) = plt.subplots(1, 2, figsize=(11.6, 4.5))

peaks = []
for (g, cap), rows in sorted(series.items()):
    c, mk = COL.get(g, "gray"), MK.get(cap, "o")
    w = [float(r["w_node"]) for r in rows]
    rt = [float(r["spread_ratio"]) for r in rows]
    y = [float(r["d_lru"]) for r in rows]
    if a.normalize:
        m = max(y) or 1.0
        y = [v / m for v in y]
    bi = max(range(len(y)), key=lambda i: y[i])
    peaks.append(rt[bi])
    for ax, x in ((axL, w), (axR, rt)):
        ax.plot(x, y, marker=mk, ms=4.5, lw=1.5, color=c, alpha=.9,
                label=f"{g}  C={cap}")
        ax.plot(x[bi], y[bi], "*", ms=15, color=c, zorder=5,
                markeredgecolor="white", markeredgewidth=0.7)

lo, hi = min(peaks), max(peaks)
# axR.axvspan(lo, hi, color="#FFD966", alpha=.35, zorder=0)
# axR.annotate(f"peaks at {lo:.1f}–{hi:.1f}\n(mean {sum(peaks)/len(peaks):.1f}×)",
#              ((lo + hi) / 2, 0.06), xycoords=("data", "axes fraction"),
#              ha="center", fontsize=10, fontweight="bold", color="#7A5F00")

ylab = ("proposed − LRU  (normalised to each curve's peak)" if a.normalize
        else "proposed − LRU  [pt]")
axL.set_xscale("symlog", linthresh=10)
axL.set_xlabel("w   (structural weight)")
axL.set_title("Against w:  the peaks scatter (25–400)", fontsize=11)
axR.set_xlabel("spread ratio  =  spread(w·Value) ÷ spread(Base)")
axR.set_title("Against the spread ratio:  the peaks line up", fontsize=11)
for ax in (axL, axR):
    ax.axhline(0, color="black", lw=1)
    ax.set_ylabel(ylab); ax.grid(alpha=.3, lw=.5); ax.set_axisbelow(True)
axL.legend(fontsize=7.5, ncol=2)

fig.suptitle("How strong should the structural score be relative to LRU?",
             fontsize=12)
fig.tight_layout(rect=(0, 0, 1, 0.93))
fig.savefig(out, dpi=190)
print(f"[saved] {out}   peaks: {lo:.2f}–{hi:.2f}  mean {sum(peaks)/len(peaks):.2f}")
