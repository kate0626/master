#!/usr/bin/env python3
"""
memo / LRU(cap=100) のメモリ使用量が start_node の数で爆発することを
1 図 2 subplot (amazon / vldb) で可視化する。

縦軸は log scale。グラフ保持メモリと比較できるように、graph_mem の
水平点線も重ねる。
"""
from __future__ import annotations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "TakaoGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# 実測値 (前回までの memory_summary.json 集計より)
DATA = {
    "amazon0601": {
        "graph_mb":   2954.0,                # cluster 全体 (server0+server1)
        "V":          2_846_802,
        "memo_per_user_kb": 232.6,           # cluster 合算 / 1 user
        "lru100_per_user_kb": 24.1,
    },
    "vldb": {
        "graph_mb":   1085.2,
        "V":          1_366_946,
        "memo_per_user_kb": 706.3,
        "lru100_per_user_kb": 22.8,
    },
}

# シナリオ
SCENARIOS = [
    ("graph 保持",     None),
    ("1 始点",          1),
    ("100 始点",       100),
    ("|V|×10% 始点",   None),  # 後で計算
]

POL_COLORS = {
    "graph": "#9467bd",
    "memo":  "#1f77b4",
    "lru":   "#ff7f0e",
}

def compute(g: str) -> list[dict]:
    d = DATA[g]
    rows = []
    # graph 保持 (始点関係なく定数)
    rows.append({"scenario": "graph 保持",     "graph": d["graph_mb"], "memo": None, "lru": None})
    for label, N in [("1 始点", 1), ("100 始点", 100),
                      (f"|V|×10% 始点\n(={int(d['V']*0.1):,})", int(d["V"]*0.1))]:
        memo_mb = d["memo_per_user_kb"] * N / 1024
        lru_mb  = d["lru100_per_user_kb"] * N / 1024
        rows.append({"scenario": label, "graph": None,
                      "memo": memo_mb, "lru": lru_mb})
    return rows

def plot(out_path: Path):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for idx, (g, ax) in enumerate(zip(["amazon0601", "vldb"], axes)):
        rows = compute(g)
        labels = [r["scenario"] for r in rows]
        x = np.arange(len(labels))
        w = 0.27

        # 各 bar の値を整数値で
        memo_vals = [r["memo"] if r["memo"] is not None else 0 for r in rows]
        lru_vals  = [r["lru"]  if r["lru"]  is not None else 0 for r in rows]
        graph_vals = [r["graph"] if r["graph"] is not None else 0 for r in rows]

        # bar 描画
        bars_graph = ax.bar(x - w, graph_vals, w,
                             color=POL_COLORS["graph"], edgecolor="white",
                             label="graph 保持メモリ")
        bars_memo  = ax.bar(x,     memo_vals,  w,
                             color=POL_COLORS["memo"], edgecolor="white",
                             label="memo (∞)  全 user 合計")
        bars_lru   = ax.bar(x + w, lru_vals,   w,
                             color=POL_COLORS["lru"], edgecolor="white",
                             label="LRU cap=100  全 user 合計")

        # 数値ラベル (memo / LRU は graph 比率 % も併記)
        graph_mb = DATA[g]["graph_mb"]
        for bars, vals, show_pct in [(bars_graph, graph_vals, False),
                                       (bars_memo,  memo_vals,  True),
                                       (bars_lru,   lru_vals,   True)]:
            for b, v in zip(bars, vals):
                if v <= 0: continue
                # GB / MB / KB を自動切替
                if v >= 1024:
                    sz = f"{v/1024:.1f} GB"
                elif v >= 1:
                    sz = f"{v:.1f} MB"
                else:
                    sz = f"{v*1024:.0f} KB"
                if show_pct and graph_mb > 0:
                    pct = 100 * v / graph_mb
                    if pct >= 100:
                        pct_s = f"{pct:.0f}%"
                    elif pct >= 1:
                        pct_s = f"{pct:.1f}%"
                    else:
                        pct_s = f"{pct:.3f}%"
                    txt = f"{sz}\n({pct_s})"
                else:
                    txt = sz
                ax.text(b.get_x() + b.get_width()/2, v * 1.15, txt,
                        ha="center", va="bottom", fontsize=7.5, color="#222",
                        linespacing=1.2)

        # graph 保持の水平点線 (基準線)
        ax.axhline(DATA[g]["graph_mb"], color=POL_COLORS["graph"],
                   linestyle="--", linewidth=1.0, alpha=0.5)
        ax.text(len(labels)-0.4, DATA[g]["graph_mb"] * 1.05,
                f"graph 保持 = {DATA[g]['graph_mb']/1024:.2f} GB",
                fontsize=8, color=POL_COLORS["graph"],
                ha="right", va="bottom")

        ax.set_yscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("メモリ使用量 [MB, log scale]", fontsize=10)
        ax.set_title(f"{g}  (|V|={DATA[g]['V']:,})", fontsize=12, fontweight="bold")
        # y 範囲: 1 KB ~ 1 TB
        ax.set_ylim(0.01, 200_000)
        ax.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        if idx == 0:
            ax.legend(loc="upper left", fontsize=9, framealpha=0.92)

    fig.suptitle("auth_cache メモリ使用量の爆発  "
                 "(N 始点並列前提, 各 user 独立 cache)",
                 fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")

def main():
    out = Path("base/auth-baseline-cache/results/memory_explosion_amazon_vldb.png")
    plot(out)

    # コンソール用テキスト表示
    def fmt(mb):
        if mb >= 1024: return f"{mb/1024:.2f} GB"
        if mb >= 1:    return f"{mb:.1f} MB"
        return f"{mb*1024:.0f} KB"
    def pct(mb, graph_mb):
        if graph_mb <= 0: return ""
        p = 100 * mb / graph_mb
        if p >= 100: return f"({p:>6.0f}%)"
        if p >= 1:   return f"({p:>6.1f}%)"
        return f"({p:>6.3f}%)"

    print("\n=== 数値サマリ (括弧内: graph 保持 比) ===")
    for g in ["amazon0601", "vldb"]:
        gmb = DATA[g]["graph_mb"]
        print(f"\n--- {g} (graph 保持 = {gmb/1024:.2f} GB = 100%) ---")
        rows = compute(g)
        print(f"{'シナリオ':<25} {'memo':>22} {'LRU cap=100':>22}")
        for r in rows:
            sc = r["scenario"].replace("\n", " ")
            if r["graph"] is not None:
                memo_s = f"{fmt(r['graph'])} (100%)"
                lru_s  = "—"
            else:
                m = r["memo"]; l = r["lru"]
                memo_s = f"{fmt(m)} {pct(m, gmb)}"
                lru_s  = f"{fmt(l)} {pct(l, gmb)}"
            print(f"{sc:<25} {memo_s:>22} {lru_s:>22}")

if __name__ == "__main__":
    main()
