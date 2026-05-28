#!/usr/bin/env python3
"""
BFS prefetch のキャッシュ効果を「3 種類のヒット率」で内訳分析する。

  1) LRU baseline ヒット率
       lru ポリシーの実験ログ全体
  2) BFS 学習済みノードのヒット率
       bfs-lru ログ中の "[BFS-prefetched nodes]" 行
  3) BFS 未学習ノードのヒット率
       bfs-lru ログ中の "[non-prefetched nodes]" 行

判定:
  - (2) ≈ (1) → プリフェッチは効いているが BFS 外が足を引っ張っている
  - (2) <  (1) → プリフェッチしたエントリが LRU で追い出されている

集計方針:
  - Length=1 と Traceback の start_node ブロックは除外（avg_length > 1.001）
  - hit_rate = Σhit / Σ(hit + miss)  （start ごとの hit_rate を平均しない）
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# 日本語フォント（plot_cache_results.py と同じ優先順）
_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "TakaoGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

BFS_BASE = Path("base/auth-cache-bfs-degree/results/alpha0.01_walks100_capa100")
BASELINE = Path("base/auth-baseline-cache/results/alpha0.01_walks_1000_capa_100")
OUT_DIR  = BFS_BASE
OUT_FILE = OUT_DIR / "bfs_hitrate_breakdown.png"

# ---------------------------------------------------------------------------
# データセット定義
# ---------------------------------------------------------------------------
# graph名 -> {
#   "log_name": 各 run dir 内のログファイル名,
#   "bfs_dir":  bfs-lru sweep のルート（bfs-lru_far<F>_depth<D>_100 が並ぶ）,
#   "lru_log":  LRU baseline の生ログ
# }
DATASETS = {
    "amazon0601": {
        "log_name": "amazon0601.log",
        "bfs_dir":  BFS_BASE / "amazon0601" / "bfs",
        # bfs-degree 側の base/lru_100 を使う（auth-baseline 側にも同名 lru があるが
        # サーバ実装が違うのでフェアにするには bfs-degree 側を使う）
        "lru_log":  BFS_BASE / "amazon0601" / "base" / "lru_100" / "amazon0601.log",
    },
    "vldb": {
        "log_name": "vldb.log",
        "bfs_dir":  BFS_BASE / "vldb" / "bfs",
        "lru_log":  BASELINE / "vldb" / "lru_100" / "vldb.log",
    },
}

FAR_VALUES   = [2, 3, 4, 5, 6]
DEPTH_VALUES = [1, 2]

# ---------------------------------------------------------------------------
# パーサ
# ---------------------------------------------------------------------------
RE_START   = re.compile(r"\[START_NODE\]\s+(\d+)")
RE_AVG_LEN = re.compile(r"Avg length:\s+([\d.]+)")
RE_OVERALL = re.compile(
    r"Auth cache hit:\s*(\d+),\s*miss:\s*(\d+)"
)
RE_PRE = re.compile(
    r"Auth cache hit_rate \[BFS-prefetched nodes\]\s*:\s*\S+\s*"
    r"\(hit=(\d+),\s*miss=(\d+)\)"
)
RE_NON = re.compile(
    r"Auth cache hit_rate \[non-prefetched nodes\]\s*:\s*\S+\s*"
    r"\(hit=(\d+),\s*miss=(\d+)\)"
)


def parse_log(path: Path) -> list[dict]:
    """各 START_NODE ブロックから avg_len / overall / pre / non を取り出す。
    Length=1 ブロックは除外して返す。"""
    rows: list[dict] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur is None:
            return
        # Length=1 と "Avg length 行が来なかった (Traceback)" を除外
        if cur.get("avg_len") is None or cur["avg_len"] <= 1.001:
            cur = None
            return
        rows.append(cur)
        cur = None

    with open(path, errors="ignore") as f:
        for line in f:
            m = RE_START.search(line)
            if m:
                flush()
                cur = {
                    "sn": int(m.group(1)),
                    "avg_len": None,
                    "overall_hit": None, "overall_miss": None,
                    "pre_hit": None,     "pre_miss": None,
                    "non_hit": None,     "non_miss": None,
                }
                continue
            if cur is None:
                continue

            m = RE_AVG_LEN.search(line)
            if m:
                cur["avg_len"] = float(m.group(1))
                continue
            m = RE_OVERALL.search(line)
            if m and cur["overall_hit"] is None:
                cur["overall_hit"]  = int(m.group(1))
                cur["overall_miss"] = int(m.group(2))
                continue
            m = RE_PRE.search(line)
            if m and cur["pre_hit"] is None:
                cur["pre_hit"]  = int(m.group(1))
                cur["pre_miss"] = int(m.group(2))
                continue
            m = RE_NON.search(line)
            if m and cur["non_hit"] is None:
                cur["non_hit"]  = int(m.group(1))
                cur["non_miss"] = int(m.group(2))
                continue
    flush()
    return rows


def hit_rate(rows: list[dict], hit_key: str, miss_key: str) -> tuple[float, int, int]:
    """有効 start_node の hit/miss を合計してから rate を出す。
    None の場合（非該当）は除外。"""
    h = m = 0
    for r in rows:
        if r.get(hit_key) is None:
            continue
        h += r[hit_key]
        m += r[miss_key]
    if h + m == 0:
        return float("nan"), 0, 0
    return h / (h + m), h, m


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def collect_breakdown() -> dict:
    """
    戻り値:
      data[graph] = {
        "lru_overall": float,      # LRU baseline ヒット率
        "configs": [
          {
            "label":     "far2_d1",
            "n_valid":   int,
            "overall":   float,
            "pre":       float,
            "non":       float,
          }, ...
        ]
      }
    """
    data: dict = {}
    for graph, cfg in DATASETS.items():
        graph_data: dict = {"lru_overall": float("nan"), "configs": []}

        # LRU baseline
        if cfg["lru_log"].exists():
            lru_rows = parse_log(cfg["lru_log"])
            rate, _, _ = hit_rate(lru_rows, "overall_hit", "overall_miss")
            graph_data["lru_overall"] = rate
        else:
            print(f"[warn] LRU baseline log not found: {cfg['lru_log']}")

        # BFS sweep
        for far in FAR_VALUES:
            for depth in DEPTH_VALUES:
                log = cfg["bfs_dir"] / f"bfs-lru_far{far}_depth{depth}_100" / cfg["log_name"]
                if not log.exists():
                    continue
                rows = parse_log(log)
                if not rows:
                    continue
                ov, _, _   = hit_rate(rows, "overall_hit", "overall_miss")
                pre, _, _  = hit_rate(rows, "pre_hit",     "pre_miss")
                non, _, _  = hit_rate(rows, "non_hit",     "non_miss")
                graph_data["configs"].append({
                    "label":   f"far{far}_d{depth}",
                    "n_valid": len(rows),
                    "overall": ov,
                    "pre":     pre,
                    "non":     non,
                })

        data[graph] = graph_data
    return data


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
COLORS = {
    "lru":     "#ff7f0e",
    "overall": "#9467bd",
    "pre":     "#2ca02c",
    "non":     "#1f77b4",
}

LABEL_JP = {
    "lru":     "LRU (baseline)",
    "overall": "BFS-LRU overall",
    "pre":     "BFS-prefetched nodes",
    "non":     "non-prefetched nodes",
}


def plot_graph_panel(ax, graph: str, gdata: dict) -> None:
    configs = gdata["configs"]
    if not configs:
        ax.text(0.5, 0.5, f"no data for {graph}",
                ha="center", va="center", transform=ax.transAxes)
        return

    labels = [c["label"] for c in configs]
    x = np.arange(len(labels))
    bar_width = 0.26

    pre_vals = [c["pre"]     for c in configs]
    non_vals = [c["non"]     for c in configs]
    ov_vals  = [c["overall"] for c in configs]

    # 3 本の bar（pre / non / overall）
    ax.bar(x - bar_width, pre_vals, width=bar_width,
           color=COLORS["pre"], label=LABEL_JP["pre"],
           edgecolor="white", linewidth=0.4)
    ax.bar(x,             non_vals, width=bar_width,
           color=COLORS["non"], label=LABEL_JP["non"],
           edgecolor="white", linewidth=0.4)
    ax.bar(x + bar_width, ov_vals,  width=bar_width,
           color=COLORS["overall"], label=LABEL_JP["overall"],
           edgecolor="white", linewidth=0.4, alpha=0.7)

    # LRU baseline を水平線で
    if not np.isnan(gdata["lru_overall"]):
        ax.axhline(
            gdata["lru_overall"],
            color=COLORS["lru"], linestyle="--", linewidth=1.6,
            label=f"{LABEL_JP['lru']} ({gdata['lru_overall']:.3f})",
        )

    # 各 bar の上に値を表示
    for xi, v in zip(x - bar_width, pre_vals):
        if not np.isnan(v):
            ax.text(xi, v + 0.01, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["pre"])
    for xi, v in zip(x, non_vals):
        if not np.isnan(v):
            ax.text(xi, v + 0.01, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["non"])
    for xi, v in zip(x + bar_width, ov_vals):
        if not np.isnan(v):
            ax.text(xi, v + 0.01, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["overall"])

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8, rotation=0)
    ax.set_ylabel("hit rate", fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4, color="#cccccc")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(graph, fontsize=11, fontweight="bold")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.92)


def main() -> None:
    data = collect_breakdown()

    # ===== 数値サマリを表示 =====
    print("\n===== BFS hit rate breakdown =====")
    print(f"{'graph':<12} {'config':<10} {'n':>3}  {'pre':>6}  {'non':>6}  {'ov':>6}  | "
          f"{'lru_base':>8}")
    print("-" * 64)
    for g, gd in data.items():
        for c in gd["configs"]:
            print(f"{g:<12} {c['label']:<10} {c['n_valid']:>3}  "
                  f"{c['pre']:>6.3f}  {c['non']:>6.3f}  {c['overall']:>6.3f}  | "
                  f"{gd['lru_overall']:>8.3f}")
        print()

    # ===== 描画 =====
    graphs = list(data.keys())
    fig, axes = plt.subplots(len(graphs), 1,
                             figsize=(11, 4.0 * len(graphs)),
                             squeeze=False)
    for row, g in enumerate(graphs):
        plot_graph_panel(axes[row][0], g, data[g])

    fig.suptitle(
        "BFS prefetch hit-rate breakdown\n"
        "Σhit/Σ(hit+miss),  Length=1 除外,  cap=100, walks=100, α=0.01",
        fontsize=11, y=1.005,
    )
    fig.tight_layout()
    fig.savefig(OUT_FILE, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved → {OUT_FILE}")


if __name__ == "__main__":
    main()
