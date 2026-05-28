#!/usr/bin/env python3
"""
auth-baseline-cache の結果を読み込み、グラフごと・ポリシーごとの
認可時間とキャッシュヒット率を棒グラフで比較するスクリプト。

出力:
  results/cache_comparison_auth_time.png   認可時間
  results/cache_comparison_walk_time.png   ウォーク時間
  results/cache_comparison_hitrate.png     ヒット率
  results/cache_comparison_combined.png    時間 + ヒット率（横2面）
  
  
  python3 base/auth-baseline-cache/results/plot_cache_results.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100
  --out-dir base/rtt
  python3 base/auth-baseline-cache/results/plot_hitrate_by_access_freq.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# macOS では Hiragino Sans、Linux では Noto Sans CJK JP を優先して使う
_JP_FONTS = [
    "Hiragino Sans",
    "Hiragino Maru Gothic Pro",
    "AppleGothic",
    "Noto Sans CJK JP",
    "IPAGothic",
    "IPAPGothic",
    "TakaoGothic",
]
_available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _available:
        matplotlib.rcParams["font.family"] = _f
        break

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
RESULTS_DIR = Path(__file__).parent
OUT_DIR = RESULTS_DIR

POLICY_ORDER = ["none", "memo", "lru", "arc"]
POLICY_LABELS = {
    "none": "キャッシュなし",
    "memo": "メモ (無制限)",
    "lru": "LRU",
    "arc": "ARC",
}
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
}
GRAPH_LABELS = {
    "karate": "Karate",
    "fb-caltech-connected": "FB-Caltech",
    "amazon0601": "Amazon0601",
    "vldb": "VLDB",
}


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------
def load_results(results_dir: Path) -> dict:
    """
    results/{graph}/{policy}_{capacity}/*_global_transition.json を全部読み込む。

    戻り値:
        data[graph][policy] = list of {
            auth_time_total, walk_time_total,
            cache_hit, cache_miss, cache_rate,
            capacity, start_node
        }
    """
    data: dict = defaultdict(lambda: defaultdict(list))

    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name

        for policy_dir in sorted(graph_dir.iterdir()):
            if not policy_dir.is_dir():
                continue
            # ディレクトリ名は "policy_capacity" 形式
            parts = policy_dir.name.rsplit("_", 1)
            if len(parts) != 2:
                continue
            policy, cap_str = parts
            try:
                capacity = int(cap_str)
            except ValueError:
                continue

            for json_file in sorted(policy_dir.glob("*_global_transition.json")):
                try:
                    d = json.loads(json_file.read_text(encoding="utf-8"))
                except Exception:
                    continue

                ctrl = d.get("controller", {})
                start_node = ctrl.get("start_node", -1)

                # キーの揺れに対応
                cache_hit = d.get("cache hit", d.get("cache_hit", 0))
                cache_miss = d.get("cache miss", d.get("cache_miss", 0))
                cache_rate = d.get("cache rate", d.get("cache_rate", 0.0))
                walk_time = float(d.get("walk_time_total", 0.0))

                # Length=1 ＆ Traceback の start_node を除外する。
                # JSON 自体には avg_length が入っていないので、walk_time<1s で判定する
                # （Length=1 の walk_time は ~0.02s, Length=100+ では ~30s なので明確に分離可能）。
                if walk_time < 1.0:
                    continue

                data[graph][policy].append(
                    {
                        "auth_time_total": float(d.get("auth_time_total", 0.0)),
                        "walk_time_total": walk_time,
                        "cache_hit": int(cache_hit),
                        "cache_miss": int(cache_miss),
                        "cache_rate": float(cache_rate),
                        "capacity": capacity,
                        "start_node": start_node,
                    }
                )

    return data


def aggregate(records: list) -> dict:
    """
    同一 (graph, policy) のレコードリストを集約する。
    Length=1 / Traceback の start_node は load_results 側で既に除外されている前提。

    - 時間: capacity ごとに有効 start_node の平均 (= per-start 平均) を出し、
            capacity 間で平均する。
            「start_node 数で割ってから」平均することで、Length=1 除外で
            n_valid が graph/policy 間で変動しても公平比較できる。
    - ヒット率: 全レコードのヒット数 / (ヒット+ミス) で再計算。
    """
    if not records:
        return {"auth_time": 0.0, "walk_time": 0.0, "cache_rate": 0.0}

    by_cap: dict = defaultdict(list)
    for r in records:
        by_cap[r["capacity"]].append(r)

    cap_auth_per_start = []
    cap_walk_per_start = []
    total_hit = 0
    total_miss = 0

    for cap_records in by_cap.values():
        n = len(cap_records)
        if n == 0:
            continue
        cap_auth_per_start.append(sum(r["auth_time_total"] for r in cap_records) / n)
        cap_walk_per_start.append(sum(r["walk_time_total"] for r in cap_records) / n)
        total_hit += sum(r["cache_hit"] for r in cap_records)
        total_miss += sum(r["cache_miss"] for r in cap_records)

    denom = total_hit + total_miss
    return {
        "auth_time": float(np.mean(cap_auth_per_start)) if cap_auth_per_start else 0.0,
        "walk_time": float(np.mean(cap_walk_per_start)) if cap_walk_per_start else 0.0,
        "cache_rate": total_hit / denom if denom > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# 描画ヘルパー
# ---------------------------------------------------------------------------
def _add_bars(ax, x, graphs, policy_vals, bar_width, value_fmt):
    n_policies = len(POLICY_ORDER)
    for i, policy in enumerate(POLICY_ORDER):
        vals = policy_vals.get(policy, [0.0] * len(graphs))
        offset = (i - (n_policies - 1) / 2) * bar_width
        bars = ax.bar(
            x + offset,
            vals,
            width=bar_width,
            label=POLICY_LABELS.get(policy, policy),
            color=POLICY_COLORS.get(policy, None),
            edgecolor="white",
            linewidth=0.5,
        )
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() * 1.02,
                    value_fmt.format(val),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    rotation=45,
                )


def make_bar_chart(
    graphs: list,
    policy_vals: dict,
    ylabel: str,
    title: str,
    out_path: Path,
    value_fmt: str = "{:.3f}",
    pct_axis: bool = False,
) -> None:
    x = np.arange(len(graphs))
    bar_width = 0.18
    fig, ax = plt.subplots(figsize=(max(8, len(graphs) * 2.4), 5))

    _add_bars(ax, x, graphs, policy_vals, bar_width, value_fmt)

    ax.set_xticks(x)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=12)
    ax.legend(loc="upper right", fontsize=9)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    if pct_axis:
        ax.set_ylim(0, 1.15)
        ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[saved] {out_path}")


def make_combined_chart(
    graphs: list,
    auth_time_vals: dict,
    cache_rate_vals: dict,
    out_path: Path,
) -> None:
    x = np.arange(len(graphs))
    bar_width = 0.18
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(max(14, len(graphs) * 4.5), 5))

    _add_bars(ax1, x, graphs, auth_time_vals, bar_width, "{:.3f}")
    _add_bars(ax2, x, graphs, cache_rate_vals, bar_width, "{:.2f}")

    graph_tick_labels = [GRAPH_LABELS.get(g, g) for g in graphs]
    for ax, ylabel, title, pct in [
        (
            ax1,
            "認可時間 [s] (per start_node 平均)",
            "認可時間\n(per-start平均 / capacity平均, Length=1除外)",
            False,
        ),
        (ax2, "キャッシュヒット率", "キャッシュヒット率", True),
    ]:
        ax.set_xticks(x)
        ax.set_xticklabels(graph_tick_labels, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=11)
        ax.set_title(title, fontsize=12)
        ax.legend(loc="upper right", fontsize=9)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        if pct:
            ax.set_ylim(0, 1.15)
            ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1.0))

    fig.suptitle("キャッシュポリシー比較 (auth-baseline-cache)", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="auth-baseline-cache 結果のポリシー比較グラフを生成"
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        default=RESULTS_DIR,
        help="graph/policy_cap/*.json を検索するディレクトリ (default: スクリプトと同階層)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="画像の出力先 (default: --results-dir と同じ)",
    )
    args = ap.parse_args()

    results_dir = args.results_dir
    out_dir = args.out_dir if args.out_dir else results_dir

    # OUT_DIR をモジュールスコープで参照しているので上書きする
    global OUT_DIR
    OUT_DIR = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_results(results_dir)

    if not data:
        print("[ERROR] 結果ファイルが見つかりませんでした。")
        print(f"        検索先: {results_dir}")
        return

    # グラフ表示順（既知のものを先に、残りをアルファベット順で追加）
    graph_order = ["karate", "fb-caltech-connected", "amazon0601", "vldb"]
    graphs = [g for g in graph_order if g in data]
    for g in sorted(data.keys()):
        if g not in graphs:
            graphs.append(g)

    # 集約
    agg: dict = {}
    for graph in graphs:
        agg[graph] = {}
        for policy in POLICY_ORDER:
            agg[graph][policy] = aggregate(data[graph].get(policy, []))

    # policy → [graph0値, graph1値, ...] リスト
    auth_time_vals: dict = {
        p: [agg[g][p]["auth_time"] for g in graphs] for p in POLICY_ORDER
    }
    walk_time_vals: dict = {
        p: [agg[g][p]["walk_time"] for g in graphs] for p in POLICY_ORDER
    }
    cache_rate_vals: dict = {
        p: [agg[g][p]["cache_rate"] for g in graphs] for p in POLICY_ORDER
    }

    # サマリ表示
    print("\n===== 集約結果サマリ =====")
    print(
        f"{'グラフ':<22} {'ポリシー':<8} {'認可時間[s]':>12} {'ウォーク時間[s]':>14} {'ヒット率':>8}"
    )
    print("-" * 68)
    for graph in graphs:
        for policy in POLICY_ORDER:
            v = agg[graph][policy]
            if v["auth_time"] == 0.0 and v["walk_time"] == 0.0:
                continue
            print(
                f"{GRAPH_LABELS.get(graph, graph):<22} "
                f"{policy:<8} "
                f"{v['auth_time']:>12.4f} "
                f"{v['walk_time']:>14.4f} "
                f"{v['cache_rate']:>8.3f}"
            )

    # 個別グラフ出力
    make_bar_chart(
        graphs,
        auth_time_vals,
        ylabel="認可時間 [s] (per start_node 平均)",
        title="キャッシュポリシー別 認可時間比較\n(per-start平均 / capacity平均, Length=1除外)",
        out_path=OUT_DIR / "cache_comparison_auth_time.png",
    )
    make_bar_chart(
        graphs,
        walk_time_vals,
        ylabel="ウォーク時間 [s] (per start_node 平均)",
        title="キャッシュポリシー別 ウォーク時間比較\n(per-start平均 / capacity平均, Length=1除外)",
        out_path=OUT_DIR / "cache_comparison_walk_time.png",
    )
    make_bar_chart(
        graphs,
        cache_rate_vals,
        ylabel="キャッシュヒット率",
        title="キャッシュポリシー別 ヒット率比較",
        out_path=OUT_DIR / "cache_comparison_hitrate.png",
        value_fmt="{:.2f}",
        pct_axis=True,
    )

    # 時間 + ヒット率 結合グラフ
    make_combined_chart(
        graphs,
        auth_time_vals,
        cache_rate_vals,
        out_path=OUT_DIR / "cache_comparison_combined.png",
    )

    print(f"\n完了。出力先: {OUT_DIR}")


if __name__ == "__main__":
    main()
