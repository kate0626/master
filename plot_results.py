#!/usr/bin/env python3
"""a.out (theta/beta/gamma 推移) と b.out (良域の全組) をパースして図を作る。"""
import re, os, tempfile
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp())
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------- a.out: パラメータ推移 ----------
def parse_a(path):
    blocks = {}
    cur = None
    for line in open(path, encoding="utf-8"):
        m = re.search(r"---\s*(\w+)\s*の推移", line)
        if m:
            cur = m.group(1); blocks[cur] = []; continue
        if cur:
            mm = re.match(r"\s*([0-9.]+)\s+\d+\s+([0-9.]+)\s+([0-9.]+)", line)
            if mm:
                blocks[cur].append((float(mm.group(1)), float(mm.group(3)), float(mm.group(2))))
            elif "=>" in line or line.strip()=="":
                cur = None
    return blocks

A = parse_a("a.out")
order = [p for p in ["theta","beta","gamma","delta"] if p in A]
fig, axes = plt.subplots(1, len(order), figsize=(4.2*len(order), 3.8))
if len(order)==1: axes=[axes]
for ax, p in zip(axes, order):
    rows = sorted(A[p])
    xs=[r[0] for r in rows]; hit=[r[1] for r in rows]
    ax.plot(xs, hit, "-o", color="#185FA5", lw=2, ms=7)
    bi = max(range(len(hit)), key=lambda i: hit[i])
    ax.plot(xs[bi], hit[bi], "o", color="#D85A30", ms=12, zorder=3,
            label=f"best {p}={xs[bi]:g}\nhit={hit[bi]:.3f}")
    for x,h in zip(xs,hit):
        ax.annotate(f"{h:.3f}", (x,h), textcoords="offset points", xytext=(0,8),
                    ha="center", fontsize=8, color="#333")
    ax.set_title(f"hit rate vs {p}  (others fixed)", fontsize=11)
    ax.set_xlabel(p); ax.set_ylabel("cache hit rate")
    ax.set_ylim(0, 0.27); ax.grid(alpha=.3); ax.legend(fontsize=9, loc="lower center")
fig.suptitle("VLDB (start=0): structural-prior parameter sweep", fontsize=12)
fig.tight_layout(rect=[0,0,1,0.96])
fig.savefig("fig_a_param_transition.png", dpi=140)
print("[OUT] fig_a_param_transition.png")

# ---------- b.out: 良域の全組 (hit vs time) ----------
def parse_b(path):
    rows=[]
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\s*(ppr_demand\S+)\s+\d+\s+([0-9.]+)\s+([0-9.]+)", line)
        if m:
            rows.append((m.group(1), float(m.group(2)), float(m.group(3))))
    return rows

B = parse_b("b.out")
fig2, ax = plt.subplots(figsize=(7.5, 5))
times=[r[2] for r in B]; hits=[r[1] for r in B]
lw = [("lw" in r[0]) for r in B]
# lw あり/なしで色分け
for flag,c,lab in [(False,"#1D9E75","lw=0 (no learning)"),(True,"#6D2E46","lw>0 (learning)")]:
    xs=[t for t,f in zip(times,lw) if f==flag]; ys=[h for h,f in zip(hits,lw) if f==flag]
    ax.scatter(xs, ys, c=c, s=70, label=lab, edgecolors="white", linewidths=.7, zorder=2)
# best (hit最大)
bi = max(range(len(B)), key=lambda i: B[i][1])
ax.scatter(times[bi], hits[bi], c="#D85A30", s=160, marker="*", zorder=3,
           label=f"best hit={hits[bi]:.3f}")
ax.annotate(B[bi][0].replace("ppr_demand_cap100_",""), (times[bi],hits[bi]),
            textcoords="offset points", xytext=(8,-12), fontsize=8, color="#D85A30")
ax.set_xlabel("execution time per start (s, RW=100 region)")
ax.set_ylabel("cache hit rate")
ax.set_title("VLDB (start=0): good-region configs (hit vs time)", fontsize=12)
ax.grid(alpha=.3); ax.legend(fontsize=9)
fig2.tight_layout(); fig2.savefig("fig_b_good_region.png", dpi=140)
print("[OUT] fig_b_good_region.png")

# ---------- 最適まとめ ----------
print("\n=== coordinate-descent best (a.out) ===")
for p in order:
    rows=sorted(A[p]); bi=max(range(len(rows)),key=lambda i:rows[i][1])
    print(f"  {p}: best={rows[bi][0]:g} (hit={rows[bi][1]:.3f})")
print(f"\n=== overall best hit (b.out) ===\n  {B[bi if False else max(range(len(B)),key=lambda i:B[i][1])][0]}")
