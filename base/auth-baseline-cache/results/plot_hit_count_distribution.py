#!/usr/bin/env python3
"""
「各ノードが cache 内で **何回 hit したか**」の分布を policy 別に可視化する。

ユーザの問題意識:
  - lookup 回数 c は policy 不変だが、
    実際にキャッシュで hit する回数は policy によって変わる
  - 例: 同じ c=10 のノードでも、memo なら 9 hit、LRU なら 0〜9 hit にばらつく
  - → policy 別に hit 回数の分布を見ることで「LRU はどんなノードを取りこぼしているか」
    が直接わかる

データ源:
  - `authorization_attempts[v]` (実測) → memo の per-node hits = c - 1 (理論最大)
  - LRU は per-node hits が JSON にないので **simulate_lru で推定**

出力:
  hit_count_dist_<graph>.png      bucket 別ノード数 (memo vs LRU 並列棒)
  hit_count_dist_<graph>.csv      per-node の c, memo_hits, lru_hits
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
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

# hit 回数 bucket
HIT_BUCKETS = [
    (0,   0,   "h=0\n(blind)"),
    (1,   1,   "h=1"),
    (2,   5,   "h=2-5"),
    (6,   20,  "h=6-20"),
    (21,  100, "h=21-100"),
    (101, None,"h=101+"),
]

def hit_bucket_of(h: int) -> int:
    for i, (lo, hi, _) in enumerate(HIT_BUCKETS):
        if hi is None:
            if h >= lo: return i
        elif lo <= h <= hi:
            return i
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--lru-cap", type=int, default=100)
    args = ap.parse_args()

    here = Path(__file__).parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from plot_capacity_and_lru_buckets import load_chain, simulate_lru

    for graph_dir in sorted(args.results_dir.iterdir()):
        if not graph_dir.is_dir(): continue
        graph = graph_dir.name
        lru_dir = graph_dir / "lru_100"
        if not lru_dir.is_dir(): continue

        # 全 start の attempts と sim の per-node hits を合算
        memo_hits_per_node: dict = defaultdict(int)
        lru_hits_per_node:  dict = defaultdict(int)
        c_per_node:         dict = defaultdict(int)

        for jf in sorted(lru_dir.glob("start=*_global_transition.json")):
            try:
                chain = load_chain(jf)
            except Exception:
                continue
            if chain["avg_len"] <= 1.001: continue
            sim = simulate_lru(chain, args.lru_cap)
            # attempts は実測の lookup 回数
            for v, c in chain["attempts"].items():
                c_per_node[v] += c
                # memo: 各 unique node は 1 miss + (c-1) hits
                memo_hits_per_node[v] += max(c - 1, 0)
            # LRU: sim から per-node hits
            for v, h in sim["hits_per_node"].items():
                lru_hits_per_node[v] += h

        if not c_per_node:
            print(f"[skip] {graph}: no data"); continue

        # bucket 集計
        n_b = len(HIT_BUCKETS)
        memo_count_b = [0]*n_b
        lru_count_b  = [0]*n_b
        for v in c_per_node:
            mh = memo_hits_per_node.get(v, 0)
            lh = lru_hits_per_node.get(v, 0)
            mb = hit_bucket_of(mh)
            lb = hit_bucket_of(lh)
            if mb >= 0: memo_count_b[mb] += 1
            if lb >= 0: lru_count_b[lb]  += 1

        total_nodes = len(c_per_node)
        print(f"\n=== {graph}  α=0.01 walks=100  cap={args.lru_cap} ===")
        print(f"全 unique ノード数 = {total_nodes:,}")
        print(f"{'hit bucket':>14} | {'memo n_nodes':>14} {'(%)':>7} | "
              f"{'LRU n_nodes':>14} {'(%)':>7}")
        print("-"*70)
        for i, (_,_,lab) in enumerate(HIT_BUCKETS):
            m = memo_count_b[i]; l = lru_count_b[i]
            print(f"{lab.replace(chr(10),' '):>14} | {m:>14,} "
                  f"{100*m/total_nodes:>6.1f}% | "
                  f"{l:>14,} {100*l/total_nodes:>6.1f}%")

        # CSV
        csv_path = args.results_dir / f"hit_count_dist_{graph}.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["node","c","memo_hits","lru_hits"])
            for v in sorted(c_per_node.keys(),
                             key=lambda x: -c_per_node[x]):
                w.writerow([v, c_per_node[v],
                            memo_hits_per_node.get(v,0),
                            lru_hits_per_node.get(v,0)])
        print(f"[csv]   {csv_path}")

        # 描画
        labels = [b[2] for b in HIT_BUCKETS]
        x = np.arange(len(labels))
        w = 0.4

        fig, ax = plt.subplots(figsize=(11, 5.5))
        bars_m = ax.bar(x - w/2, memo_count_b, w,
                         color="#1f77b4", edgecolor="white",
                         label="memo (∞)")
        bars_l = ax.bar(x + w/2, lru_count_b, w,
                         color="#ff7f0e", edgecolor="white",
                         label=f"LRU cap={args.lru_cap} (sim)")
        # ラベル
        max_count = max(max(memo_count_b), max(lru_count_b))
        for bars, vals in [(bars_m, memo_count_b), (bars_l, lru_count_b)]:
            for b, v in zip(bars, vals):
                if v <= 0: continue
                pct = 100 * v / total_nodes
                ax.text(b.get_x()+b.get_width()/2, v * 1.04,
                        f"{v:,}\n({pct:.1f}%)",
                        ha="center", va="bottom", fontsize=8, color="#222",
                        linespacing=1.15)
        ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
        ax.set_xlabel("ノードが cache で hit した回数 h", fontsize=10)
        ax.set_ylabel("ノード数 (log scale)", fontsize=10)
        ax.set_yscale("log")
        ax.set_ylim(0.5, max_count * 2.5)
        ax.set_title(
            f"{graph} — cache での hit 回数別ノード数 (memo vs LRU)  "
            f"全 unique = {total_nodes:,} 個",
            fontsize=12, fontweight="bold")
        ax.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.legend(loc="upper right", fontsize=10, framealpha=0.92)

        # 注釈
        note = (f"memo: 各 node 1 miss + (c-1) hits の理論最大 (= JSON authorization_attempts から厳密)\n"
                f"LRU : simulate_lru(cap={args.lru_cap}) による推定。sim walk が実機より短いため total が控えめになる")
        ax.text(0.01, 0.98, note, transform=ax.transAxes, fontsize=7.5,
                verticalalignment="top", color="#555",
                bbox=dict(boxstyle="round,pad=0.4",
                          facecolor="#f5f5f5", edgecolor="#cccccc"))

        out = args.results_dir / f"hit_count_dist_{graph}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out}")


if __name__ == "__main__":
    main()
