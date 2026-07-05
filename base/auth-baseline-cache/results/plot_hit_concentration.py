#!/usr/bin/env python3
"""
「特定の少数エントリが、キャッシュヒット(再利用)の大半を稼ぐ」ことを示す。
= 提案手法『良く使われるキャッシュは退去(OUT)させない』の動機づけの直接証明 (証明①: 偏り)。

指標:
  - ローレンツ曲線     : x=エントリ累積割合, y=ヒット累積割合。対角線から離れるほど集中。
  - Gini 係数          : 0=完全均等, 1=完全独占。
  - 上位 k% カバレッジ : 上位 1/5/10/20% のエントリが全ヒットの何% を占めるか。
  - 50%/80% 到達点     : 全ヒットの 50%/80% を稼ぐのに必要なエントリ数(割合)。

エントリ別「ヒット数」の定義 (2 系統):
  - memo  : authorization_attempts(c) から reuse = max(c-1, 0)。
            = そのエントリをキャッシュした場合に得られる再利用回数(政策非依存の上限)。
            全 JSON にあるので即出せる。【主軸】
  - LRU   : cache_hit_per_key (実機 LRU が実際に稼いだヒット)。
            新フィールドなので再計測後のみ。あれば重ねて描く。

出力 (results_dir 配下):
  hit_concentration_<graph>.png   ローレンツ曲線 (memo / LRU)
  hit_concentration_<graph>.csv   per-entity の memo_reuse / lru_hits (降順)
"""
from __future__ import annotations

import argparse
import csv
import json
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

TOPK = [0.01, 0.05, 0.10, 0.20]


def lorenz_and_gini(counts: list[int]):
    """正値の系列から (x, y, gini) を返す。x,y は (0,0) 始点のローレンツ点列。"""
    vals = np.array(sorted(c for c in counts if c > 0), dtype=float)  # 昇順
    n = len(vals)
    if n == 0 or vals.sum() == 0:
        return np.array([0, 1.0]), np.array([0, 1.0]), 0.0
    cum = np.cumsum(vals)
    y = np.concatenate([[0.0], cum / cum[-1]])
    x = np.concatenate([[0.0], np.arange(1, n + 1) / n])
    # Gini = 1 - Σ (x_i - x_{i-1})(y_i + y_{i-1})  (台形則)
    gini = 1.0 - np.sum((x[1:] - x[:-1]) * (y[1:] + y[:-1]))
    return x, y, float(gini)


def topk_coverage(counts: list[int]):
    """降順ソートで上位 k% カバレッジと 50/80% 到達エントリ割合を返す。"""
    vals = sorted((c for c in counts if c > 0), reverse=True)
    n = len(vals)
    tot = sum(vals)
    cov = {}
    for p in TOPK:
        k = max(1, int(round(n * p)))
        cov[p] = sum(vals[:k]) / tot if tot else 0.0
    # 50/80% 到達点
    reach = {0.5: None, 0.8: None}
    run = 0
    for i, v in enumerate(vals, 1):
        run += v
        for thr in (0.5, 0.8):
            if reach[thr] is None and run / tot >= thr:
                reach[thr] = i / n
    return cov, reach, n, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--lru-cap", type=int, default=100)
    ap.add_argument("--split-entity", action="store_true",
                    help="node と edge_ を分けて集計する")
    args = ap.parse_args()

    for graph_dir in sorted(args.results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name
        lru_dir = graph_dir / f"lru_{args.lru_cap}"
        if not lru_dir.is_dir():
            lru_dir = graph_dir / "lru_100"
        if not lru_dir.is_dir():
            continue

        memo_reuse: dict[str, int] = defaultdict(int)  # max(c-1,0) の合算
        lru_hits: dict[str, int] = defaultdict(int)     # cache_hit_per_key の合算
        have_lru = False
        any_data = False

        for jf in sorted(lru_dir.glob("start=*_global_transition.json")):
            try:
                o = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            aa = o.get("authorization_attempts", {})
            if not aa:
                continue
            any_data = True
            for v, c in aa.items():
                memo_reuse[v] += max(int(c) - 1, 0)
            hk = o.get("cache_hit_per_key", {})
            if hk:
                have_lru = True
                for v, h in hk.items():
                    lru_hits[v] += int(h)

        if not any_data:
            print(f"[skip] {graph}: no data")
            continue

        def _filter(d):
            if not args.split_entity:
                return {"all": d}
            nodes = {k: v for k, v in d.items() if not k.startswith("edge_")}
            edges = {k: v for k, v in d.items() if k.startswith("edge_")}
            return {"node": nodes, "edge": edges}

        # 描画
        fig, ax = plt.subplots(figsize=(7.5, 7.0))
        ax.plot([0, 1], [0, 1], "--", color="#999", linewidth=1.2,
                label="完全均等 (Gini=0)")

        print(f"\n=== {graph}  cap={args.lru_cap} ===")
        csv_rows: dict[str, dict] = {}

        for src_name, src, color in [
            ("memo再利用 (=Σ(c-1))", memo_reuse, "#1f77b4"),
            (f"LRU実測ヒット", lru_hits if have_lru else None, "#ff7f0e"),
        ]:
            if src is None:
                continue
            for grp, d in _filter(src).items():
                counts = list(d.values())
                if not counts or sum(counts) == 0:
                    continue
                x, y, gini = lorenz_and_gini(counts)
                cov, reach, n, tot = topk_coverage(counts)
                lab = f"{src_name}" + (f" [{grp}]" if grp != "all" else "")
                ax.plot(x, y, linewidth=2.0, color=color,
                        label=f"{lab}  (Gini={gini:.2f})")
                # 集計表示
                print(f"\n--- {lab} ---")
                print(f"  エントリ数={n:,}  総ヒット={tot:,}  Gini={gini:.3f}")
                for p in TOPK:
                    print(f"  上位{p*100:>4.0f}% ({max(1,int(round(n*p))):>5,}個) "
                          f"が全ヒットの {cov[p]*100:>5.1f}%")
                r50 = reach[0.5]; r80 = reach[0.8]
                if r50 is not None:
                    print(f"  全ヒットの50%を稼ぐのに必要なエントリ = 上位 {r50*100:.1f}%")
                if r80 is not None:
                    print(f"  全ヒットの80%を稼ぐのに必要なエントリ = 上位 {r80*100:.1f}%")
                for v, c in d.items():
                    csv_rows.setdefault(v, {})[src_name] = c

            # 色を1系統ずつずらすため何もしない (memo→青, LRU→橙固定)

        ax.set_xlabel("エントリ累積割合 (再利用が少ない順)", fontsize=11)
        ax.set_ylabel("ヒット(再利用)累積割合", fontsize=11)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        src_tag = "memo+LRU実測" if have_lru else "memo(LRU実測は再計測後)"
        ax.set_title(
            f"{graph} — キャッシュ再利用の集中度 (ローレンツ曲線)\n"
            f"曲線が下に膨らむほど『少数エントリが大半を独占』  [{src_tag}]",
            fontsize=12, fontweight="bold")
        ax.grid(True, linestyle="--", alpha=0.35)
        ax.legend(loc="upper left", fontsize=9, framealpha=0.92)

        out = args.results_dir / f"hit_concentration_{graph}.png"
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"\n[saved] {out}")

        # CSV
        csv_path = args.results_dir / f"hit_concentration_{graph}.csv"
        cols = ["memo再利用 (=Σ(c-1))"] + (["LRU実測ヒット"] if have_lru else [])
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["entity"] + cols)
            for v in sorted(csv_rows,
                            key=lambda k: -csv_rows[k].get(cols[0], 0)):
                w.writerow([v] + [csv_rows[v].get(c, 0) for c in cols])
        print(f"[csv]   {csv_path}")


if __name__ == "__main__":
    main()
