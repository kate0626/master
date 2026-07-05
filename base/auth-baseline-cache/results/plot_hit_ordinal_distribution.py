#!/usr/bin/env python3
"""
各キャッシュヒットが「そのノードにとって何回目のヒットか」(hit ordinal) を
policy 別に定量化する。

ユーザの問題意識:
  - hit_count_dist は per-node の **総ヒット数 h** の分布までしか出せない。
  - 知りたいのは「個々の hit が 1回目のヒットか 2回目か 3回目か…」という順序分布。
  - 例: hit 全体のうち何割が『初めて貯めた直後の2回目アクセス(=1回目ヒット)』で、
    何割が『何度も命中する常連ノードの k回目ヒット』なのか。

定義 (ユーザ確認済): 「k回目のヒット」= そのノードで成立した通算 k 番目のヒット
  (miss / 退去・再挿入は数に含めず、ヒット成立だけを通算カウント)。

policy:
  - memo (∞)  : 解析的。c回 lookup のノードは ordinal 1..(c-1) に1ずつ寄与。
                → count_memo[k] = #{node : attempts(c) >= k+1}
  - LRU cap   : simulate。ヒット時に per-node の hit_seq を +1 し、その値で集計。

出力 (results_dir 配下):
  hit_ordinal_dist_<graph>.png   bucket 別 hit 件数 (memo vs LRU 並列棒)
  hit_ordinal_dist_<graph>.csv   ordinal k ごとの memo_hits / lru_hits (exact)
"""
from __future__ import annotations

import argparse
import csv
import json
import random
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

# hit ordinal k の bucket (k=何回目のヒットか)
ORD_BUCKETS = [
    (1,   1,   "1回目"),
    (2,   2,   "2回目"),
    (3,   3,   "3回目"),
    (4,   5,   "4-5回目"),
    (6,   10,  "6-10回目"),
    (11,  50,  "11-50回目"),
    (51,  None, "51回目+"),
]


def ord_bucket_of(k: int) -> int:
    for i, (lo, hi, _) in enumerate(ORD_BUCKETS):
        if hi is None:
            if k >= lo:
                return i
        elif lo <= k <= hi:
            return i
    return -1


# ---------------------------------------------------------------------------
# LRU (hit ordinal を追跡する版)
# ---------------------------------------------------------------------------
class LRUOrdinal:
    """plot_capacity_and_lru_buckets.LRU と同仕様 + ヒット成立ごとの通算番号を集計。"""
    def __init__(self, cap: int):
        from collections import OrderedDict
        self.cap = cap
        self.od: "OrderedDict[str, int]" = OrderedDict()
        self.hit_seq: dict[str, int] = defaultdict(int)      # per-node 通算ヒット数
        self.hits_by_ordinal: dict[int, int] = defaultdict(int)  # k回目ヒットの件数
        self.total_hits = 0
        self.total_miss = 0

    def access(self, key: str) -> bool:
        if key in self.od:
            self.od.move_to_end(key)
            self.hit_seq[key] += 1
            self.hits_by_ordinal[self.hit_seq[key]] += 1
            self.total_hits += 1
            return True
        self.total_miss += 1
        self.od[key] = 1
        if self.cap > 0 and len(self.od) > self.cap:
            self.od.popitem(last=False)
        return False


def simulate_lru_ordinal(chain: dict, cap: int, sample_next, _edge_key) -> dict:
    """plot_capacity_and_lru_buckets.simulate_lru と同じ walk 再生。
    違いはヒット成立ごとの通算番号 (hit ordinal) を集計する点のみ。"""
    lru = LRUOrdinal(cap)
    rng = random.Random(chain["seed"])
    start = chain["start"]
    walks, alpha = chain["walks"], chain["alpha"]
    for _ in range(walks):
        node = start
        lru.access(node)
        while True:
            if rng.random() < alpha:
                break
            row = chain["transition"].get(node)
            if not row:
                break
            nxt = sample_next(row, rng)
            if nxt is None:
                break
            lru.access(_edge_key(node, nxt))
            node = nxt
            lru.access(node)
    return {"hits_by_ordinal": dict(lru.hits_by_ordinal),
            "total_hits": lru.total_hits, "total_miss": lru.total_miss}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--lru-cap", type=int, default=100)
    args = ap.parse_args()

    here = Path(__file__).parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from plot_capacity_and_lru_buckets import load_chain, sample_next, _edge_key

    for graph_dir in sorted(args.results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name
        lru_dir = graph_dir / f"lru_{args.lru_cap}"
        if not lru_dir.is_dir():
            lru_dir = graph_dir / "lru_100"
        if not lru_dir.is_dir():
            continue

        # memo: 全 start の attempts を合算 → ordinal 別寄与
        # lru : JSON に実測 cache_hit_ordinal_hist があればそれを使い、
        #        無ければ従来どおり simulate でフォールバック。
        memo_by_ord: dict[int, int] = defaultdict(int)
        lru_by_ord: dict[int, int] = defaultdict(int)

        any_data = False
        used_real = False        # 1 つでも実測 ordinal を使ったか
        used_sim = False
        for jf in sorted(lru_dir.glob("start=*_global_transition.json")):
            try:
                raw = json.loads(jf.read_text(encoding="utf-8"))
                chain = load_chain(jf)
            except Exception:
                continue
            if chain["avg_len"] <= 1.001:
                continue
            any_data = True
            # memo: c回 lookup のノードは k=1..(c-1) に +1
            for v, c in chain["attempts"].items():
                for k in range(1, max(c, 1)):
                    memo_by_ord[k] += 1
            # lru: 実測優先
            real_hist = raw.get("cache_hit_ordinal_hist")
            if real_hist:
                used_real = True
                for k, n in real_hist.items():
                    lru_by_ord[int(k)] += int(n)
            else:
                used_sim = True
                sim = simulate_lru_ordinal(chain, args.lru_cap,
                                           sample_next, _edge_key)
                for k, n in sim["hits_by_ordinal"].items():
                    lru_by_ord[k] += n

        if not any_data:
            print(f"[skip] {graph}: no data")
            continue

        if used_real and used_sim:
            lru_src = "実測+sim混在"
        elif used_real:
            lru_src = "実測"
        else:
            lru_src = "sim"

        # bucket 集計
        n_b = len(ORD_BUCKETS)
        memo_b = [0] * n_b
        lru_b = [0] * n_b
        for k, n in memo_by_ord.items():
            b = ord_bucket_of(k)
            if b >= 0:
                memo_b[b] += n
        for k, n in lru_by_ord.items():
            b = ord_bucket_of(k)
            if b >= 0:
                lru_b[b] += n

        memo_tot = sum(memo_b)
        lru_tot = sum(lru_b)
        print(f"\n=== {graph}  cap={args.lru_cap}  [LRU源: {lru_src}] ===")
        print(f"{'hit ordinal':>12} | {'memo hits':>12} {'(%)':>7} | "
              f"{'LRU hits':>12} {'(%)':>7}")
        print("-" * 64)
        for i, (_, _, lab) in enumerate(ORD_BUCKETS):
            m, l = memo_b[i], lru_b[i]
            mp = 100 * m / memo_tot if memo_tot else 0
            lp = 100 * l / lru_tot if lru_tot else 0
            print(f"{lab:>12} | {m:>12,} {mp:>6.1f}% | {l:>12,} {lp:>6.1f}%")
        print(f"{'合計':>12} | {memo_tot:>12,} {'100.0%':>7} | "
              f"{lru_tot:>12,} {'100.0%':>7}")

        # CSV (exact ordinal)
        csv_path = args.results_dir / f"hit_ordinal_dist_{graph}.csv"
        all_k = sorted(set(memo_by_ord) | set(lru_by_ord))
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hit_ordinal_k", "memo_hits", "lru_hits"])
            for k in all_k:
                w.writerow([k, memo_by_ord.get(k, 0), lru_by_ord.get(k, 0)])
        print(f"[csv]   {csv_path}")

        # 描画
        labels = [b[2] for b in ORD_BUCKETS]
        x = np.arange(n_b)
        w = 0.4
        fig, ax = plt.subplots(figsize=(11, 5.5))
        bars_m = ax.bar(x - w / 2, memo_b, w, color="#1f77b4",
                        edgecolor="white", label="memo (∞)")
        bars_l = ax.bar(x + w / 2, lru_b, w, color="#ff7f0e",
                        edgecolor="white",
                        label=f"LRU cap={args.lru_cap} ({lru_src})")
        max_count = max(max(memo_b), max(lru_b), 1)
        for bars, vals, tot in [(bars_m, memo_b, memo_tot),
                                (bars_l, lru_b, lru_tot)]:
            for b, v in zip(bars, vals):
                if v <= 0:
                    continue
                pct = 100 * v / tot if tot else 0
                ax.text(b.get_x() + b.get_width() / 2, v * 1.04,
                        f"{v:,}\n({pct:.1f}%)", ha="center", va="bottom",
                        fontsize=8, color="#222", linespacing=1.15)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_xlabel("そのノードにとって何回目のヒットか (hit ordinal k)",
                      fontsize=10)
        ax.set_ylabel("ヒット件数 (log scale)", fontsize=10)
        ax.set_yscale("log")
        ax.set_ylim(0.5, max_count * 2.5)
        ax.set_title(
            f"{graph} — ヒットの順序別件数 (memo vs LRU)  "
            f"memo計={memo_tot:,} / LRU計={lru_tot:,}",
            fontsize=12, fontweight="bold")
        ax.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.legend(loc="upper right", fontsize=10, framealpha=0.92)

        if used_real:
            lru_note = (f"LRU : 実機の cache_hit_ordinal_hist (実測)。"
                        "ckey=(start,entity) ごとの通算ヒット番号")
        else:
            lru_note = (f"LRU : simulate_lru_ordinal(cap={args.lru_cap}) による推定 "
                        "(実機 JSON に ordinal が無いためフォールバック)")
        note = ("memo: c回lookupのノードは k=1..(c-1) に寄与 (authorization_attempts から厳密)\n"
                + lru_note)
        ax.text(0.01, 0.98, note, transform=ax.transAxes, fontsize=7.5,
                verticalalignment="top", color="#555",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#f5f5f5",
                          edgecolor="#cccccc"))

        out = args.results_dir / f"hit_ordinal_dist_{graph}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out}")


if __name__ == "__main__":
    main()
