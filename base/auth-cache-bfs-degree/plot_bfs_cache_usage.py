#!/usr/bin/env python3
"""
BFS prefetch がキャッシュをどう使っているかを「量の比率」で可視化する。

各 (graph, far, depth) 構成について、Length=1 を除外した上で:

  A) prefetched_nodes / total_capacity
       BFS が prefetch しようとしたノード数 ÷ 2サーバ合計 capacity (=200)
       1 を超えると prefetch だけで cap を溢れている = ほぼ全部 evict される

  B) end_cache_entries / total_capacity
       walk 終了時点でキャッシュに残っているエントリ数 / cap
       1 に近いほど LRU が cap いっぱい使い切っている

  C) prefetched_lookups / total_lookups
       walk 中に prefetched ノードに対して行った参照 / 全参照
       小さいほど「prefetch した範囲が実際の RW では再訪されていない」

  D) prefetched_nodes / cache_entries_end
       prefetch ノード数 ÷ 最終キャッシュ残量
       1 を大きく上回るほど「prefetch されたノードはほぼ追い出されている」
"""
from __future__ import annotations

import json
import re
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

BFS_BASE = Path("base/auth-cache-bfs-degree/results/alpha0.01_walks100_capa100")
OUT_FILE = BFS_BASE / "bfs_cache_usage_ratio.png"

DATASETS = {
    "amazon0601": {
        "log_name": "amazon0601.log",
        "bfs_dir":  BFS_BASE / "amazon0601" / "bfs",
    },
    "vldb": {
        "log_name": "vldb.log",
        "bfs_dir":  BFS_BASE / "vldb"      / "bfs",
    },
}
FAR_VALUES   = [2, 3, 4, 5, 6]
DEPTH_VALUES = [1, 2]

# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------
RE_START   = re.compile(r"\[START_NODE\]\s+(\d+)")
RE_AVG_LEN = re.compile(r"Avg length:\s+([\d.]+)")
RE_PREF    = re.compile(r"\[BFS_PREFETCH\].*nodes_cached=(\d+)")
RE_LOOKUPS = re.compile(r"Total auth cache lookups:\s*(\d+)")
RE_PRE_HM  = re.compile(
    r"Auth cache hit_rate \[BFS-prefetched nodes\]\s*:\s*\S+\s*"
    r"\(hit=(\d+),\s*miss=(\d+)\)"
)


def parse_log(path: Path) -> list[dict]:
    """各 START_NODE ブロックから
       avg_len / prefetch_nodes / total_lookups / pre_lookups を取得。
       Length=1 / Traceback は除外。"""
    rows: list[dict] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur is None:
            return
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
                    "prefetch_nodes": None,
                    "total_lookups": None,
                    "pre_lookups": None,
                }
                continue
            if cur is None:
                continue
            m = RE_AVG_LEN.search(line)
            if m:
                cur["avg_len"] = float(m.group(1)); continue
            m = RE_PREF.search(line)
            if m and cur["prefetch_nodes"] is None:
                cur["prefetch_nodes"] = int(m.group(1)); continue
            m = RE_LOOKUPS.search(line)
            if m and cur["total_lookups"] is None:
                cur["total_lookups"] = int(m.group(1)); continue
            m = RE_PRE_HM.search(line)
            if m and cur["pre_lookups"] is None:
                cur["pre_lookups"] = int(m.group(1)) + int(m.group(2)); continue
    flush()
    return rows


def read_memory_for_run(run_dir: Path) -> dict:
    """全 start_node の memory_summary.json から
       (end_cache_entries_sum, cache_capacity_sum) を返す。"""
    ends: list[int] = []
    caps: list[int] = []
    for jf in sorted(run_dir.glob("start=*_memory_summary.json")):
        try:
            arr = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for srv in arr:
            ends.append(int(srv.get("cache_entries", 0)))
            caps.append(int(srv.get("cache_capacity", 0)))
    # サーバごとに分かれているので「per-start_node 合計」に直す
    n_srv = max(1, len(arr) if arr else 1)
    # ends は (start_node x server) 並びになっている
    per_start_end = []
    per_start_cap = []
    for i in range(0, len(ends), n_srv):
        per_start_end.append(sum(ends[i:i + n_srv]))
        per_start_cap.append(sum(caps[i:i + n_srv]))
    return {"end": per_start_end, "cap": per_start_cap}


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------
def collect_usage() -> dict:
    data: dict = {}
    for graph, cfg in DATASETS.items():
        rows_by_config: list[dict] = []
        for far in FAR_VALUES:
            for depth in DEPTH_VALUES:
                run_dir = cfg["bfs_dir"] / f"bfs-lru_far{far}_depth{depth}_100"
                log = run_dir / cfg["log_name"]
                if not log.exists():
                    continue
                rows = parse_log(log)
                if not rows:
                    continue

                # memory_summary を start_node 順に対応付ける
                mem = read_memory_for_run(run_dir)
                # rows[i] と mem["end"][i] / mem["cap"][i] が対応するとは限らない
                # (Length=1 で除外したぶんずれる) ので、各 row の "sn" 順で
                # memory side を sn 順に並べ直す
                jf_sn = []
                for jf in sorted(run_dir.glob("start=*_memory_summary.json")):
                    m = re.match(r"start=(\d+)_", jf.name)
                    if m: jf_sn.append(int(m.group(1)))
                sn2end = {sn: mem["end"][i] for i, sn in enumerate(jf_sn)
                          if i < len(mem["end"])}
                sn2cap = {sn: mem["cap"][i] for i, sn in enumerate(jf_sn)
                          if i < len(mem["cap"])}

                def avg(seq):
                    seq = [x for x in seq if x is not None]
                    return float(np.mean(seq)) if seq else float("nan")

                pref_nodes   = [r["prefetch_nodes"] for r in rows]
                total_lookup = [r["total_lookups"]  for r in rows]
                pre_lookup   = [r["pre_lookups"]    for r in rows]
                end_entries  = [sn2end.get(r["sn"]) for r in rows]
                cap_sum      = [sn2cap.get(r["sn"]) for r in rows]

                avg_pref  = avg(pref_nodes)
                avg_end   = avg(end_entries)
                avg_cap   = avg(cap_sum) or 200.0
                avg_tlook = avg(total_lookup)
                avg_plook = avg(pre_lookup)

                rows_by_config.append({
                    "label":           f"far{far}_d{depth}",
                    "n_valid":         len(rows),
                    "pref_nodes":      avg_pref,
                    "end_entries":     avg_end,
                    "total_capacity":  avg_cap,
                    "pref_per_cap":    avg_pref / avg_cap   if avg_cap else float("nan"),
                    "end_per_cap":     avg_end  / avg_cap   if avg_cap else float("nan"),
                    "pref_per_end":    avg_pref / avg_end   if avg_end else float("nan"),
                    "pref_lookup_frac": (avg_plook / avg_tlook) if avg_tlook else float("nan"),
                })
        data[graph] = rows_by_config
    return data


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
COLORS = {
    "pref_per_cap":     "#2ca02c",  # 緑 (BFS が押し込もうとした量)
    "end_per_cap":      "#1f77b4",  # 青 (最終キャッシュ占有)
    "pref_lookup_frac": "#9467bd",  # 紫 (prefetch 対象への参照割合)
}
LABELS = {
    "pref_per_cap":     "prefetched / capacity",
    "end_per_cap":      "end cache_entries / capacity",
    "pref_lookup_frac": "prefetched_lookups / total_lookups",
}


def plot_panel(ax, graph: str, rows: list[dict]) -> None:
    if not rows:
        ax.text(0.5, 0.5, f"no data for {graph}",
                ha="center", va="center", transform=ax.transAxes)
        return
    labels = [r["label"] for r in rows]
    x      = np.arange(len(labels))
    w      = 0.26

    pref = [r["pref_per_cap"]     for r in rows]
    endf = [r["end_per_cap"]      for r in rows]
    pfra = [r["pref_lookup_frac"] for r in rows]

    ax.bar(x - w, pref, width=w, color=COLORS["pref_per_cap"],
           label=LABELS["pref_per_cap"], edgecolor="white", linewidth=0.4)
    ax.bar(x,     endf, width=w, color=COLORS["end_per_cap"],
           label=LABELS["end_per_cap"],  edgecolor="white", linewidth=0.4)
    ax.bar(x + w, pfra, width=w, color=COLORS["pref_lookup_frac"],
           label=LABELS["pref_lookup_frac"], edgecolor="white", linewidth=0.4)

    # capacity = 1.0 を強調する赤い水平線
    ax.axhline(1.0, color="#d62728", linestyle="--", linewidth=1.1,
               alpha=0.7, label="capacity (=1.0)")

    for xi, v in zip(x - w, pref):
        if not np.isnan(v):
            ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["pref_per_cap"])
    for xi, v in zip(x, endf):
        if not np.isnan(v):
            ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["end_per_cap"])
    for xi, v in zip(x + w, pfra):
        if not np.isnan(v):
            ax.text(xi, v + 0.02, f"{v:.2f}", ha="center", va="bottom",
                    fontsize=7, color=COLORS["pref_lookup_frac"])

    top = max([1.05] + [v for v in pref + endf + pfra if not np.isnan(v)])
    ax.set_ylim(0, top * 1.18)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("ratio", fontsize=10)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4, color="#cccccc")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title(graph, fontsize=11, fontweight="bold")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.92)


def main() -> None:
    data = collect_usage()

    # 表示
    print()
    print(f"{'graph':<12} {'config':<10} {'n':>3}  "
          f"{'pref':>6} {'end':>6} {'cap':>6}   "
          f"{'pref/cap':>8} {'end/cap':>7} {'pref/end':>8} {'preflook_frac':>13}")
    print("-" * 90)
    for g, rows in data.items():
        for r in rows:
            print(f"{g:<12} {r['label']:<10} {r['n_valid']:>3}  "
                  f"{r['pref_nodes']:>6.1f} {r['end_entries']:>6.1f} {r['total_capacity']:>6.0f}   "
                  f"{r['pref_per_cap']:>8.3f} {r['end_per_cap']:>7.3f} "
                  f"{r['pref_per_end']:>8.3f} {r['pref_lookup_frac']:>13.4f}")
        print()

    graphs = list(data.keys())
    fig, axes = plt.subplots(len(graphs), 1,
                             figsize=(12, 4.2 * len(graphs)),
                             squeeze=False)
    for row, g in enumerate(graphs):
        plot_panel(axes[row][0], g, data[g])

    fig.suptitle(
        "BFS prefetch cache usage ratio\n"
        "cap = 2 servers × 100 = 200 [entries].  Length=1 除外.",
        fontsize=11, y=1.005,
    )
    fig.tight_layout()
    fig.savefig(OUT_FILE, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved → {OUT_FILE}")


if __name__ == "__main__":
    main()
