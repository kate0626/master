#!/usr/bin/env python3
"""
図1: ヒット率の比較 (none / lru / ppr_demand / memo)
図2: RTT別の合計時間 (複数RTT値でライングラフ)

対象グラフ: amazon0601, vldb (regular/backtrack)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# ===== パス定義 =====
REPO = Path(__file__).parent.parent.parent.parent  # master-progrem/
BASE_DIR  = REPO / "base" / "auth-baseline-cache" / "results" / "alpha0.01_walks_100_capa_100"
PROP_DIR  = REPO / "base" / "proposed_cache"  / "results" / "alpha0.01_walks_100_capa_100"

# グラフ × ポリシー × ディレクトリ定義
POLICY_DEFS = {
    "amazon0601": [
        ("none",       BASE_DIR / "amazon0601" / "none_100"),
        ("LRU",        BASE_DIR / "amazon0601" / "lru_100"),
        ("PPR-demand\n(θ=2.0, δ=1.0)",  PROP_DIR  / "amazon0601" / "ppr_demand_cap100_t2.0_d1.0"),
        ("memo\n(上限)",     BASE_DIR / "amazon0601" / "memo_100"),
    ],
    "vldb": [
        ("none",       BASE_DIR / "vldb" / "none_100"),
        ("LRU",        BASE_DIR / "vldb" / "lru_100"),
        ("PPR-demand\n(θ=1.0, δ=0.5)",  PROP_DIR  / "vldb"       / "ppr_demand_cap100_t1.0_d0.5"),
        ("memo\n(上限)",     BASE_DIR / "vldb" / "memo_100"),
    ],
}

# RTT sweep 値 (ms)
RTT_VALUES_MS = [0, 0.5, 1.0, 2.0, 5.0, 10.0]

# 色
COLORS = {
    "none":          "#aaaaaa",
    "LRU":           "#ff7f0e",
    "PPR-demand\n(θ=2.0, δ=1.0)": "#1f77b4",
    "PPR-demand\n(θ=1.0, δ=0.5)": "#1f77b4",
    "memo\n(上限)":  "#2ca02c",
}
LINE_STYLES = {
    "none":          ("--",  "o",  "#aaaaaa"),
    "LRU":           ("-",   "s",  "#ff7f0e"),
    "PPR-demand\n(θ=2.0, δ=1.0)": ("-",   "^",  "#1f77b4"),
    "PPR-demand\n(θ=1.0, δ=0.5)": ("-",   "^",  "#1f77b4"),
    "memo\n(上限)":  (":",   "D",  "#2ca02c"),
}


# ===== データ収集 =====

def collect_stats(policy_dir: Path) -> dict | None:
    """JSON files から統計を集計"""
    if not policy_dir.exists():
        return None

    nh = nm = eh = em = wt = at = 0
    n = 0
    for jf in sorted(policy_dir.glob("start=*_global_transition.json")):
        data = json.loads(jf.read_text())
        for e in data.get("per_server_access_stats", []):
            sid = e.get("server_id")
            st  = e.get("stats", e)
            if sid == 1:   # node server
                nh += int(st.get("auth_cache_hit",  0))
                nm += int(st.get("auth_cache_miss", 0))
            else:           # edge server
                eh += int(st.get("auth_cache_hit",  0))
                em += int(st.get("auth_cache_miss", 0))
        wt += float(data.get("walk_time_total", 0))
        at += float(data.get("auth_time_total", 0))
        n  += 1

    if n == 0:
        return None

    total_hits   = nh + eh
    total_misses = nm + em
    total        = total_hits + total_misses
    return {
        "n":        n,
        "hit_rate": total_hits / total if total else 0.0,
        "node_miss": nm,
        "edge_miss": em,
        "walk_time": wt,
        "auth_time": at,
    }


def rtt_total_time(stats: dict, rtt_ms: float) -> float:
    """RTT (ms) を考慮した合計時間 (s)"""
    rtt_s = rtt_ms / 1000.0
    return stats["walk_time"] + (stats["node_miss"] + stats["edge_miss"]) * rtt_s


# ===== 図1: ヒット率バー =====

def plot_hitrate(axes, all_stats: dict[str, dict]) -> None:
    for ax, (graph, stats_list) in zip(axes, all_stats.items()):
        labels = [name for name, _ in stats_list]
        values = [s["hit_rate"] * 100 if s else 0 for _, s in stats_list]
        colors = [COLORS.get(lbl, "#888888") for lbl in labels]

        x = np.arange(len(labels))
        bars = ax.bar(x, values, color=colors, edgecolor="white",
                      linewidth=1.2, width=0.6)

        # バー上に値を表示
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.5,
                    f"{val:.1f}%",
                    ha="center", va="bottom", fontsize=9, fontweight="bold")

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("キャッシュヒット率 (%)", fontsize=10)
        ax.set_ylim(0, max(values) * 1.2 + 5)
        ax.set_title(f"{graph}  — ポリシー別ヒット率 (cap=100)",
                     fontsize=11, fontweight="bold")
        ax.yaxis.grid(True, linestyle="--", alpha=0.4)
        ax.set_axisbelow(True)

        # LRU ↔ PPR-demand の改善幅を強調
        lru_val = next((v for lbl, v in zip(labels, values) if lbl == "LRU"), None)
        ppr_val = next((v for lbl, v in zip(labels, values) if lbl.startswith("PPR")), None)
        if lru_val is not None and ppr_val is not None:
            diff = ppr_val - lru_val
            sign = "+" if diff >= 0 else ""
            ax.annotate(
                f"LRU比 {sign}{diff:.1f}pt",
                xy=(labels.index(next(l for l in labels if l.startswith("PPR"))),
                    ppr_val + 1),
                fontsize=8, color="#1f77b4", ha="center", va="bottom",
                fontweight="bold",
            )


# ===== 図2: RTT別時間 =====

def plot_rtt_time(axes, all_stats: dict[str, dict]) -> None:
    for ax, (graph, stats_list) in zip(axes, all_stats.items()):
        for pol_name, stats in stats_list:
            if stats is None or pol_name == "none":
                continue
            times = [rtt_total_time(stats, rtt) for rtt in RTT_VALUES_MS]
            ls, marker, color = LINE_STYLES.get(pol_name, ("-", "o", "#888"))
            display_name = pol_name.replace("\n", " ")
            ax.plot(RTT_VALUES_MS, times,
                    linestyle=ls, marker=marker, color=color,
                    linewidth=2, markersize=6, label=display_name)
            # 最右端に値を注記
            ax.annotate(f"{times[-1]:.0f}s",
                        xy=(RTT_VALUES_MS[-1], times[-1]),
                        xytext=(3, 0), textcoords="offset points",
                        fontsize=7.5, color=color, va="center")

        ax.set_xlabel("RTT (ms)", fontsize=10)
        ax.set_ylabel("合計時間 (s)\n= walk_time + miss × RTT", fontsize=9)
        ax.set_title(f"{graph}  — RTT別合計時間 (cap=100)",
                     fontsize=11, fontweight="bold")
        ax.legend(fontsize=8.5, framealpha=0.92, loc="upper left")
        ax.xaxis.grid(True, linestyle="--", alpha=0.3)
        ax.yaxis.grid(True, linestyle="--", alpha=0.3)
        ax.set_axisbelow(True)
        ax.set_xticks(RTT_VALUES_MS)
        ax.set_xticklabels([f"{r}" for r in RTT_VALUES_MS], fontsize=9)


# ===== main =====

def main():
    # --- データ収集 ---
    all_stats: dict[str, list[tuple[str, dict | None]]] = {}
    for graph, defs in POLICY_DEFS.items():
        stats_list = []
        for pol_name, d in defs:
            s = collect_stats(d)
            if s is None:
                print(f"[warn] {graph}/{pol_name}: data not found at {d}")
            stats_list.append((pol_name, s))
        all_stats[graph] = stats_list

    # --- 図1: ヒット率 ---
    fig1, axes1 = plt.subplots(1, 2, figsize=(12, 5))
    fig1.suptitle("ポリシー別キャッシュヒット率 (walks=100, cap=100)",
                  fontsize=13, fontweight="bold")
    plot_hitrate(axes1, all_stats)
    fig1.tight_layout(rect=[0, 0, 1, 0.95])
    out1 = Path(__file__).parent / "comparison_hitrate.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"[saved] {out1}")

    # --- 図2: RTT別時間 ---
    fig2, axes2 = plt.subplots(1, 2, figsize=(13, 5))
    fig2.suptitle("RTT別の合計実行時間 (walks=100, cap=100)  "
                  "— none は省略、memo は上限参考",
                  fontsize=13, fontweight="bold")
    plot_rtt_time(axes2, all_stats)
    fig2.tight_layout(rect=[0, 0, 1, 0.94])
    out2 = Path(__file__).parent / "comparison_rtt_time.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"[saved] {out2}")


if __name__ == "__main__":
    main()
