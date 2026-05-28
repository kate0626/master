#!/usr/bin/env python3
"""
BFS スレッショルド（far値）ごとの実験結果を可視化する。
None・Memo を参照ラインとして重ねて表示。

- 横軸: BFS threshold (far2, far3, ...)
- 縦軸: walk time (図1) / cache hit rate (図2)
- Amazon・VLDB を上下サブプロットで並置
- walk_time < 1s のスタートノードは外れ値として除外
"""

import re
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

BFS_BASE = Path("base/auth-cache-bfs-degree/results/alpha0.01_walks100_capa100")
BASELINE = Path("base/auth-baseline-cache/results/alpha0.01_walks_1000_capa_100")
OUT_DIR = BFS_BASE

DATASETS = {
    "amazon": {
        "bfs_dir": BFS_BASE / "amazon0601" / "bfs",
        "log_basename": "amazon0601.log",
        "none_log": BFS_BASE / "amazon0601" / "base" / "none_100" / "amazon0601.log",
        "memo_log": BASELINE / "amazon0601" / "memo_100" / "amazon0601.log",
    },
    "vldb": {
        "bfs_dir": BFS_BASE / "vldb" / "bfs",
        "log_basename": "vldb.log",
        "none_log": BASELINE / "vldb" / "none_100" / "vldb.log",
        "memo_log": BASELINE / "vldb" / "memo_100" / "vldb.log",
    },
}

FAR_VALUES = [2, 3, 4, 5, 6]

COLORS = {
    "amazon": "#e6550d",
    "vldb":   "#3182bd",
}
REF_COLORS = {
    "none": "#888888",
    "memo": "#e7298a",
}


# ---------------------------------------------------------------------------
# パーサ
# ---------------------------------------------------------------------------

def parse_log(log_path: Path) -> list[dict]:
    """walk_time >= 1s のスタートノードだけ返す（外れ値除外）。"""
    results = []
    current_start = None
    walk_time = None
    hit_rate = None

    pat_start   = re.compile(r"\[START_NODE\]\s+(\d+)")
    pat_walk    = re.compile(r"Total walk time \(sum over all servers\):\s+([\d.]+)")
    pat_hitrate = re.compile(r"Auth cache hit:.*hit_rate:\s*([\d.]+)")

    with open(log_path) as f:
        for line in f:
            m = pat_start.search(line)
            if m:
                if current_start is not None and walk_time is not None:
                    results.append({"start": current_start,
                                    "walk_time": walk_time,
                                    "hit_rate": hit_rate})
                current_start = int(m.group(1))
                walk_time = hit_rate = None
                continue
            m = pat_walk.search(line)
            if m and walk_time is None:
                walk_time = float(m.group(1))
            m = pat_hitrate.search(line)
            if m and hit_rate is None:
                hit_rate = float(m.group(1))

    if current_start is not None and walk_time is not None:
        results.append({"start": current_start,
                        "walk_time": walk_time,
                        "hit_rate": hit_rate})

    return [r for r in results if r["walk_time"] >= 1.0]


def collect_bfs_data() -> dict:
    """BFS threshold実験データを収集。"""
    data = {}
    for ds_name, cfg in DATASETS.items():
        data[ds_name] = {}
        log_name = cfg.get("log_basename", "vldb.log")
        for far in FAR_VALUES:
            log = cfg["bfs_dir"] / f"bfs-lru_far{far}_depth2_100" / log_name
            if not log.exists():
                continue
            rows = parse_log(log)
            if rows:
                data[ds_name][far] = {
                    "walk_times": [r["walk_time"] for r in rows],
                    "hit_rates":  [r["hit_rate"]  for r in rows if r["hit_rate"] is not None],
                }
    return data


def collect_reference() -> dict:
    """None・Memo の参照データを収集。"""
    ref = {}
    for ds_name, cfg in DATASETS.items():
        ref[ds_name] = {}
        for label in ("none", "memo"):
            log = cfg[f"{label}_log"]
            if not log.exists():
                continue
            rows = parse_log(log)
            if rows:
                ref[ds_name][label] = {
                    "walk_times": [r["walk_time"] for r in rows],
                    "hit_rates":  [r["hit_rate"]  for r in rows if r["hit_rate"] is not None],
                }
    return ref


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------

def plot_one(ax, far_values, bfs_data, ref_data, metric_key, color, ylabel=None):
    """1つのサブプロットにBFS棒グラフ＋None/Memo参照線を描く。"""
    x = np.arange(len(far_values))
    bar_width = 0.55

    vals_list = [bfs_data.get(f, {}).get(metric_key, []) for f in far_values]
    means = [np.mean(v) if v else np.nan for v in vals_list]
    stds  = [np.std(v)  if v else np.nan for v in vals_list]

    ax.bar(x, means, width=bar_width, color=color, alpha=0.72,
           label="bfs-lru", edgecolor="white", linewidth=0.5)
    ax.errorbar(x, means, yerr=stds, fmt="none",
                ecolor="#333333", elinewidth=1.0, capsize=3)

    # 個別データ点
    rng = np.random.default_rng(42)
    for ox, vals in zip(x, vals_list):
        if vals:
            jitter = rng.uniform(-bar_width * 0.12, bar_width * 0.12, len(vals))
            ax.scatter(ox + jitter, vals, s=20, color=color,
                       edgecolors="#333333", linewidths=0.4, zorder=5, alpha=0.9)

    # None・Memo 参照ライン
    ref_styles = {
        "none": dict(color=REF_COLORS["none"], linestyle="--",  linewidth=1.6, label="None"),
        "memo": dict(color=REF_COLORS["memo"], linestyle="-.",  linewidth=1.6, label="Memo"),
    }
    for label, style in ref_styles.items():
        if label in ref_data:
            vals = ref_data[label].get(metric_key, [])
            if vals:
                m = np.mean(vals)
                ax.axhline(m, **style)
                # 値アノテーション（右端）
                ax.text(len(far_values) - 0.48, m,
                        f" {label}={m:.2f}", va="center", fontsize=8,
                        color=style["color"], fontweight="bold")

    # 軸設定
    all_vals = [v for vs in vals_list for v in vs]
    for label in ref_data:
        all_vals += ref_data[label].get(metric_key, [])
    if all_vals:
        lo, hi = min(all_vals), max(all_vals)
        margin = (hi - lo) * 0.15 or 1.0
        ax.set_ylim(lo - margin, hi + margin)

    ax.set_xticks(x)
    ax.set_xticklabels([f"far={f}" for f in far_values], fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4, color="#cccccc")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8, loc="upper right")


def main():
    bfs = collect_bfs_data()
    ref = collect_reference()

    ds_names = [ds for ds in ["amazon", "vldb"] if bfs.get(ds)]

    for metric_key, ylabel, title_suffix, out_name in [
        ("walk_times", "Total Walk Time (s)", "Walk Time", "bfs_threshold_time.png"),
        ("hit_rates",  "Cache Hit Rate",       "Hit Rate",  "bfs_threshold_hitrate.png"),
    ]:
        fig, axes = plt.subplots(len(ds_names), 1,
                                 figsize=(8, 4 * len(ds_names)),
                                 squeeze=False)

        for row, ds in enumerate(ds_names):
            ax = axes[row][0]
            far_vals = sorted(bfs[ds].keys())
            plot_one(ax, far_vals, bfs[ds], ref.get(ds, {}),
                     metric_key, COLORS[ds],
                     ylabel=ylabel)
            ax.set_title(f"{ds}  —  {title_suffix} vs BFS Threshold",
                         fontsize=11, fontweight="bold")

        fig.suptitle(
            f"BFS Threshold Sensitivity ({title_suffix})\n"
            "(bfs-lru, cap=100, walks=100, α=0.01)  |  dashed=None  dash-dot=Memo",
            fontsize=10, y=1.01,
        )
        fig.tight_layout()
        out = OUT_DIR / out_name
        fig.savefig(out, dpi=160, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved → {out}")


if __name__ == "__main__":
    main()
