#!/usr/bin/env python3
# =============================================================================
# plot_param_sensitivity.py — run_param_sensitivity.py の CSV から 1D プロファイル図。
#   各軸: 横=パラメータ値, 縦=overall ヒット率。LRU/ARC/memo を水平線、
#         「LRU を上回る帯」を塗り、動作点を縦線で示す。
# =============================================================================
import argparse, csv
from collections import defaultdict
import matplotlib; matplotlib.use("Agg")

from pathlib import Path as _P
_FIGS = _P(__file__).resolve().parent
_RES = _FIGS.parent / "results"
import matplotlib.pyplot as plt

LABEL = {"w": "w_node  (w_edge = 3·w_node)", "w_ratio": "w_edge / w_node",
         "theta": "θ  (Freq exponent)", "cap_node": "cap_node  (cap_edge = 100 − cap_node)",
         "cf": "C_F", "beta": "β  (Close exponent)", "gamma": "γ  (Deg exponent)"}
ORDER = ["cap_node", "w", "theta", "gamma", "w_ratio", "beta", "cf"]

p = argparse.ArgumentParser()
p.add_argument("--csv", nargs="+",
               default=[str(_RES / "sens_VLDB.csv"),
                        str(_RES / "sens_Amazon.csv")])
p.add_argument("--out", default=str(_FIGS / "param_sensitivity.png"))
a = p.parse_args()

data, meta = {}, {}
for path in a.csv:
    rows = list(csv.DictReader(open(path)))
    g = rows[0]["graph"]
    d = defaultdict(list)
    for r in rows:
        d[r["axis"]].append(r)
    data[g] = d
    meta[g] = dict(lru=float(rows[0]["lru"]), arc=float(rows[0]["arc"]),
                   memo=float(rows[0]["memo"]))

graphs = list(data)
cols = {"VLDB": "#4C72B0", "Amazon": "#DD8452"}
fig, axes = plt.subplots(2, 4, figsize=(17, 7.6))
axes = axes.ravel()

for ax, name in zip(axes, ORDER):
    for g in graphs:
        rows = sorted(data[g][name], key=lambda r: float(r["value"]))
        x = [float(r["value"]) for r in rows]
        y = [float(r["overall"]) for r in rows]
        e = [float(r["overall_sd"]) for r in rows]
        c = cols.get(g, "gray")
        ax.errorbar(x, y, yerr=e, marker="o", ms=4, color=c, capsize=2, label=g)
        ax.axhline(meta[g]["lru"], color=c, ls="--", lw=1, alpha=.7)
        ax.axhline(meta[g]["arc"], color=c, ls=":", lw=1.2, alpha=.7)
        cen = [r for r in rows if r["is_center"] == "1"]
        if cen:
            ax.axvline(float(cen[0]["value"]), color="gray", lw=1, alpha=.5)
        ax.fill_between(x, meta[g]["lru"], y,
                        where=[yy > meta[g]["lru"] for yy in y],
                        color=c, alpha=.10, interpolate=True)
    ax.set_xlabel(LABEL[name]); ax.set_ylabel("overall hit rate %")
    ax.grid(alpha=.3, lw=.5)
    if name == ORDER[0]:
        ax.legend(fontsize=7.5, loc="lower center")

axes[-1].axis("off")
axes[-1].text(0.02, 0.85, "dashed = LRU\ndotted  = ARC\ngray vertical = operating point\n"
              "shaded = region above LRU", fontsize=9, va="top")
fig.suptitle("Parameter sensitivity around the operating point "
             "(α=0.1, 1,000 RWers, capacity 100, start 0, 5 independent seeds)",
             fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(a.out, dpi=170)
print(f"[saved] {a.out}")
