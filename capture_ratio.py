import os,tempfile
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp())
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 順番: [None, memo(上限), ARC, LRU, Proposed]
data = {
  "Amazon": [0, 46, 20, 21, 26.5],
  "VLDB":   [0, 47, 18, 22, 23.1],
}
labels = ["None","memo","ARC","LRU","Proposed"]
methods = ["ARC","LRU","Proposed"]  # 正規化して見せる対象

print("削減可能範囲 = None(0) → memo(上限)。capture% = 値 / memo × 100\n")
cap = {}
for g, vals in data.items():
    memo = vals[1]
    cap[g] = {}
    print(f"=== {g} (memo上限={memo}%) ===")
    for m in methods:
        v = vals[labels.index(m)]
        c = v/memo*100
        cap[g][m] = c
        print(f"  {m:<9} hit={v:>5}%  -> 削減可能範囲の {c:5.1f}% を達成")
    print()

# 図: capture% (削減可能範囲のうち達成%)
fig,ax=plt.subplots(figsize=(7,4.5))
import numpy as np
x=np.arange(len(methods)); w=0.36
colors={"Amazon":"#378ADD","VLDB":"#1D9E75"}
for i,(g,_) in enumerate(data.items()):
    ys=[cap[g][m] for m in methods]
    bars=ax.bar(x+(i-0.5)*w, ys, w, label=g, color=colors[g])
    for b,y in zip(bars,ys):
        ax.text(b.get_x()+b.get_width()/2, y+0.8, f"{y:.0f}%", ha="center", fontsize=9)
ax.set_xticks(x); ax.set_xticklabels(methods)
ax.set_ylabel("% of reducible range achieved\n(value / memo ceiling)")
ax.set_title("Share of achievable hit rate captured (None→memo = 100%)")
ax.set_ylim(0,70); ax.grid(alpha=.3,axis="y"); ax.legend()
fig.tight_layout(); fig.savefig("fig_capture_ratio.png",dpi=140)
print("[OUT] fig_capture_ratio.png")
