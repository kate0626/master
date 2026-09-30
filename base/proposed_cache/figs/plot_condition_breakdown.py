#!/usr/bin/env python3
# =============================================================================
# plot_condition_breakdown.py — 「実験条件のどれが結論を左右したか」の切り分け図。
#
#   run_loo_ablation.py を条件を変えて回した --out-raw の CSV を読み、
#     (a) 条件ごとの 提案 / LRU / ARC / memo のヒット率
#     (b) 条件ごとの 提案 − LRU（対応差 ± sd）
#     (c) 各要因を1つだけ変えたときに 提案 − LRU がどれだけ動くか
#   を1枚にまとめる。
#
#   前提となる7条件（既定）:
#     A start0 / モジュロ / deny なし   ← 論文 表1 の条件
#     D start0 / モジュロ / deny 30%
#     C start0 / 実分割   / deny なし
#     B start0 / 実分割   / deny 30%
#     E start1 / モジュロ / deny なし
#     F start1 / モジュロ / deny 30%
#     G start1 / 実分割   / deny 30%
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_condition_breakdown.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"

# (ラベル, 始点, 分割, deny, ファイル)
CONDS = [
    ("A", "0", "modulo", "no",   "loo_VLDB_a0.1_raw.csv"),
    ("D", "0", "modulo", "30%",  "loo_VLDB_denyOnly_raw.csv"),
    ("C", "0", "real",   "no",   "loo_VLDB_partOnly_raw.csv"),
    ("B", "0", "real",   "30%",  "loo_VLDB_deny_raw.csv"),
    ("E", "1", "modulo", "no",   "loo_E.csv"),
    ("F", "1", "modulo", "30%",  "loo_F.csv"),
    ("G", "1", "real",   "30%",  "loo_G.csv"),
]
ROWS = {"lru": "LRU (共有)", "arc": "ARC (共有)",
        "memo": "memo (上限)", "prop": "提案 (全要素)"}


def load(path):
    per = defaultdict(list)
    for r in csv.DictReader(open(path)):
        per[r["name"]].append(float(r["overall_hit"]))
    return {k: per[nm] for k, nm in ROWS.items()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--res-dir", default=str(_RES))
    p.add_argument("--out", default=str(_FIGS / "condition_breakdown.png"))
    a = p.parse_args()

    data, labels = [], []
    for lab, s0, part, deny, fn in CONDS:
        fp = Path(a.res_dir) / fn
        if not fp.exists():
            print(f"  [skip] {fn} が無い"); continue
        d = load(fp)
        d["dlru"] = [x - y for x, y in zip(d["prop"], d["lru"])]
        d["darc"] = [x - y for x, y in zip(d["prop"], d["arc"])]
        data.append(d)
        labels.append((lab, s0, part, deny))

    if not data:
        raise SystemExit("読める CSV がありません。RUNBOOK §5 のコマンドで生成してください。")

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.8),
                             gridspec_kw=dict(width_ratios=[1.5, 1.15, 0.95]))
    xs = list(range(len(data)))
    xt = [f"{lab}\nstart {s0}\n{part}\ndeny {deny}" for lab, s0, part, deny in labels]

    # ---------- (a) 絶対値 ----------
    ax = axes[0]
    series = [("lru", "LRU", "#8C8C8C"), ("arc", "ARC", "#55A868"),
              ("prop", "proposed", "#C44E52")]
    w = 0.26
    for k, (key, nm, col) in enumerate(series):
        ax.bar([x + (k - 1) * w for x in xs], [st.mean(d[key]) for d in data], w,
               yerr=[st.stdev(d[key]) for d in data], capsize=2.5,
               color=col, label=nm, edgecolor="white", linewidth=0.5)
    ax.plot(xs, [st.mean(d["memo"]) for d in data], "k_", ms=22, mew=1.4,
            label="memo (ceiling)")
    ax.set_xticks(xs); ax.set_xticklabels(xt, fontsize=7.5)
    ax.set_ylabel("overall hit rate %")
    ax.set_title("(a) Hit rate under each condition", fontsize=10)
    ax.legend(fontsize=8); ax.grid(axis="y", alpha=.3, lw=.5)

    # ---------- (b) 提案 − LRU ----------
    ax = axes[1]
    vals = [st.mean(d["dlru"]) for d in data]
    errs = [st.stdev(d["dlru"]) for d in data]
    cols = ["#4C72B0" if lab[1] == "0" else "#DD8452" for lab in labels]
    hat = ["" if lab[2] == "modulo" else "//" for lab in labels]
    for x, v_, e_, c, h in zip(xs, vals, errs, cols, hat):
        ax.bar(x, v_, 0.6, yerr=e_, capsize=3, color=c, hatch=h,
               edgecolor="white", linewidth=0.8)
        ax.annotate(f"{v_:+.2f}", (x, v_), ha="center",
                    va="bottom" if v_ >= 0 else "top",
                    xytext=(0, 4 if v_ >= 0 else -12),
                    textcoords="offset points", fontsize=8.5)
    ax.axhline(0, color="black", lw=1)
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = max(1.0, (hi - lo) * 0.18)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xticks(xs); ax.set_xticklabels(xt, fontsize=7.5)
    ax.set_ylabel("proposed − LRU  [pt]")
    ax.set_title("(b) The advantage survives only in condition A\n"
                 "(blue = start 0, orange = start 1, hatched = real partition)",
                 fontsize=10)
    ax.grid(axis="y", alpha=.3, lw=.5)

    # ---------- (c) 要因を1つだけ変えたときの移動量 ----------
    ax = axes[2]
    idx = {lab[0]: i for i, lab in enumerate(labels)}
    factors = []
    if "A" in idx and "D" in idx:
        factors.append(("deny\n(A→D)", st.mean(data[idx["D"]]["dlru"])
                        - st.mean(data[idx["A"]]["dlru"])))
    if "A" in idx and "C" in idx:
        factors.append(("partition\n(A→C)", st.mean(data[idx["C"]]["dlru"])
                        - st.mean(data[idx["A"]]["dlru"])))
    if "A" in idx and "E" in idx:
        factors.append(("start node\n(A→E)", st.mean(data[idx["E"]]["dlru"])
                        - st.mean(data[idx["A"]]["dlru"])))
    fx = list(range(len(factors)))
    ax.bar(fx, [f[1] for f in factors], 0.55,
           color=["#8C8C8C", "#4C72B0", "#DD8452"][:len(factors)],
           edgecolor="white", linewidth=0.8)
    for x, (_, v_) in zip(fx, factors):
        ax.annotate(f"{v_:+.2f}", (x, v_), ha="center",
                    va="bottom" if v_ >= 0 else "top",
                    xytext=(0, 4 if v_ >= 0 else -12),
                    textcoords="offset points", fontsize=9, fontweight="bold")
    ax.axhline(0, color="black", lw=1)
    fv = [f[1] for f in factors]
    lo, hi = min(fv + [0]), max(fv + [0])
    pad = max(0.8, (hi - lo) * 0.22)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xticks(fx); ax.set_xticklabels([f[0] for f in factors], fontsize=9)
    ax.set_ylabel("shift of (proposed − LRU)  [pt]")
    ax.set_title("(c) Which condition moved the result\n"
                 "(one factor changed at a time from A)", fontsize=10)
    ax.grid(axis="y", alpha=.3, lw=.5)

    fig.suptitle("Which experimental condition drives the conclusion "
                 "(VLDB, α=0.1, 1,000 RWers, capacity 100)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(a.out, dpi=175)
    print(f"[saved] {a.out}")

    print()
    print(f"{'cond':<5}{'start':<7}{'partition':<11}{'deny':<7}"
          f"{'LRU':>8}{'ARC':>8}{'proposed':>10}{'−LRU':>9}{'−ARC':>8}")
    for (lab, s0, part, deny), d in zip(labels, data):
        print(f"{lab:<5}{s0:<7}{part:<11}{deny:<7}"
              f"{st.mean(d['lru']):>8.2f}{st.mean(d['arc']):>8.2f}"
              f"{st.mean(d['prop']):>10.2f}{st.mean(d['dlru']):>+9.2f}"
              f"{st.mean(d['darc']):>+8.2f}")
    print()
    for nm, v_ in factors:
        print(f"  {nm.splitlines()[0]:<12} を変えると 提案−LRU が {v_:+.2f} pt 動く")


if __name__ == "__main__":
    main()
