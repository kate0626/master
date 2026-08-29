#!/usr/bin/env python3
# =============================================================================
# plot_startnode_dependence.py — 始点ごとの「提案 − LRU」と、その予測因子。
#   run_startnode_sweep.py --out-csv の結果を読む。
#   横軸: LRU/memo (LRU が上限のどれだけを取れているか = 始点近傍の局所性の強さ)
#   縦軸: 提案 − LRU [pt]
# =============================================================================
import argparse, csv, statistics as st
import matplotlib; matplotlib.use("Agg")

from pathlib import Path as _P
_FIGS = _P(__file__).resolve().parent
_RES = _FIGS.parent / "results"
import matplotlib.pyplot as plt

p = argparse.ArgumentParser()
p.add_argument("--csv", nargs="+",
               default=[str(_RES / "startsweep_VLDB.csv"),
                        str(_RES / "startsweep_Amazon.csv")])
p.add_argument("--out", default=str(_FIGS / "startnode_dependence.png"))
a = p.parse_args()

fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
marks = {"VLDB": ("o", "#4C72B0"), "Amazon": ("s", "#DD8452")}
allpts = []
for path in a.csv:
    rows = list(csv.DictReader(open(path)))
    g = rows[0]["graph"]
    mk, col = marks.get(g, ("^", "gray"))
    x = [float(r["lru"]) / float(r["memo"]) for r in rows]
    y = [float(r["d_lru"]) for r in rows]
    e = [float(r["d_lru_sd"]) for r in rows]
    starts = [r["start"] for r in rows]
    axes[0].errorbar(x, y, yerr=e, fmt=mk, color=col, capsize=3, label=g,
                     markersize=7, linestyle="none")
    for xi, yi, s in zip(x, y, starts):
        axes[0].annotate(f"s={s}", (xi, yi), textcoords="offset points",
                         xytext=(6, 3), fontsize=7.5, color=col)
    allpts += list(zip(x, y))
    # 右: 手法比較 (始点別)
    idx = range(len(rows))
    off = -0.2 if g == "VLDB" else 0.2
    axes[1].bar([i + off for i in idx], [float(r["d_arc"]) for r in rows],
                0.38, color=col, label=f"{g}", edgecolor="white")
    if g == "Amazon":
        axes[1].set_xticks(list(idx))
        axes[1].set_xticklabels([r["start"] for r in rows])

axes[0].axhline(0, color="black", lw=1)
axes[0].axvline(0.575, color="gray", ls="--", lw=1)
axes[0].annotate("locality threshold ≈ 0.58", (0.578, axes[0].get_ylim()[0] + 0.6),
                 fontsize=8, color="gray")
axes[0].set_xlabel("LRU / memo   (fraction of the achievable ceiling LRU already gets)")
axes[0].set_ylabel("proposed − LRU  [pt]")
axes[0].set_title("Gain over LRU depends on the start node")
axes[0].grid(alpha=0.3, lw=0.5); axes[0].legend(fontsize=8)

axes[1].axhline(0, color="black", lw=1)
axes[1].set_xlabel("start node")
axes[1].set_ylabel("proposed − ARC  [pt]")
axes[1].set_title("Against ARC the proposal is at parity")
axes[1].grid(axis="y", alpha=0.3, lw=0.5); axes[1].legend(fontsize=8)

fig.suptitle("Start-node dependence (α=0.1, 1,000 RWers, capacity 100, "
             "modulo partition, no deny)", fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.93))
fig.savefig(a.out, dpi=180)
print(f"[saved] {a.out}")

xs = [p[0] for p in allpts]; ys = [p[1] for p in allpts]
mx, my = st.mean(xs), st.mean(ys)
num = sum((x-mx)*(y-my) for x, y in allpts)
den = (sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys)) ** .5
print(f"相関係数 r(LRU/memo, 提案−LRU) = {num/den:+.3f}  (n={len(allpts)})")
no4 = [(x, y) for x, y in allpts if x < 0.9]
mx, my = st.mean([p[0] for p in no4]), st.mean([p[1] for p in no4])
num = sum((x-mx)*(y-my) for x, y in no4)
den = (sum((x-mx)**2 for x, _ in no4) * sum((y-my)**2 for _, y in no4)) ** .5
print(f"  天井付近(x>0.9)を除くと r = {num/den:+.3f}  (n={len(no4)})")
