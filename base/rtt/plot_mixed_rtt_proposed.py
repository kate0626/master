#!/usr/bin/env python3
"""
plot_mixed_rtt_proposed.py — 改良版 (LRU vs 提案手法比較追加)

plot_mixed_rtt.py をベースに以下を追加:
  - --proposed-dir: 提案手法 (ppr_demand) の結果ディレクトリ
  - ppr_demand をポリシーとして表示に追加
  - LRU vs 提案手法の直接差分グラフを新規追加

既存の出力 (mixed_rtt_*.png など) は変更なし。
新規出力:
  proposed_vs_lru_all.png         LRU との差分 (3パターン横並び)
  proposed_vs_lru_pattern_A.png   パターンA 単体
  proposed_vs_lru_pattern_B.png   パターンB 単体
  proposed_vs_lru_pattern_C.png   パターンC 単体
  proposed_mixed_rtt_all.png      提案手法込みの積み上げ棒 (全パターン)
  proposed_mixed_rtt_summary.csv  集計 CSV (提案手法を含む)

実行例:
  python3 base/rtt/plot_mixed_rtt_proposed.py \
      --results-dir  base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
      --proposed-dir base/proposed_cache/results/alpha0.01_walks_100_capa_100 \
      --out-dir      base/rtt/output_proposed
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# macOS / Linux で日本語フォントを優先選択
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
# RTT 単位 (ms)
# ---------------------------------------------------------------------------
RTT_CLASSES = {
    "local": 10,
    "regional": 60,
    "global": 200,
}

# ---------------------------------------------------------------------------
# 混合パターン
# ---------------------------------------------------------------------------
MIXED_PATTERNS = [
    {
        "key": "A",
        "label": "A: ローカル支配 (近70/中20/遠10)",
        "short": "Local-dominant",
        "weights": {"local": 0.7, "regional": 0.2, "global": 0.1},
        "fname_indiv": "mixed_rtt_pattern_A.png",
        "note": "国内向けサービス (国内EC, 国内SNS, ローカルニュース)",
    },
    {
        "key": "B",
        "label": "B: 域内バランス (近30/中50/遠20)",
        "short": "Regional-balanced",
        "weights": {"local": 0.3, "regional": 0.5, "global": 0.2},
        "fname_indiv": "mixed_rtt_pattern_B.png",
        "note": "アジア圏ビジネス / 多国籍リージョナル分散",
    },
    {
        "key": "C",
        "label": "C: グローバル分散 (近10/中30/遠60)",
        "short": "Global-distributed",
        "weights": {"local": 0.1, "regional": 0.3, "global": 0.6},
        "fname_indiv": "mixed_rtt_pattern_C.png",
        "note": "国際CDN / 国際メディア / 国際研究コラボ",
    },
]


def expected_rtt_ms(weights: dict) -> float:
    return sum(weights[k] * RTT_CLASSES[k] for k in weights)


def variance_rtt_ms2(weights: dict) -> float:
    mu = expected_rtt_ms(weights)
    return sum(weights[k] * (RTT_CLASSES[k] - mu) ** 2 for k in weights)


# ---------------------------------------------------------------------------
# ポリシー設定 — 提案手法 (ppr_demand) を追加
# ---------------------------------------------------------------------------
POLICY_ORDER = ["none", "memo", "lru", "ppr_demand", "arc"]
POLICY_LABELS = {
    "none": "キャッシュなし",
    "memo": "メモ (無制限)",
    "lru": "LRU",
    "arc": "ARC",
    "ppr_demand": "提案手法 (PPR-Demand)",
}
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
    "ppr_demand": "#d62728",  # 赤系で目立たせる
}
GRAPH_LABELS = {
    "karate": "Karate",
    "fb-caltech-connected": "FB-Caltech",
    "amazon0601": "Amazon0601",
    "vldb": "VLDB",
}


# ---------------------------------------------------------------------------
# データ読み込み — ベースライン (既存コードそのまま)
# ---------------------------------------------------------------------------
def load_results(results_dir: Path) -> dict:
    data: dict = defaultdict(lambda: defaultdict(list))

    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name

        for policy_dir in sorted(graph_dir.iterdir()):
            if not policy_dir.is_dir():
                continue
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
                walk_time = float(d.get("walk_time_total", 0.0))

                transition_obj = d.get("transition", {})
                if isinstance(transition_obj, dict):
                    hop_count = int(
                        sum(
                            v
                            for v in transition_obj.values()
                            if isinstance(v, (int, float))
                        )
                    )
                else:
                    hop_count = int(transition_obj or 0)

                auth_calls = int(d.get("auth_calls", 0))

                if walk_time < 1.0:
                    continue

                data[graph][policy].append(
                    {
                        "walk_time_total": walk_time,
                        "hop_count": hop_count,
                        "auth_calls": auth_calls,
                        "capacity": capacity,
                        "start_node": ctrl.get("start_node", -1),
                    }
                )

    return data


# ---------------------------------------------------------------------------
# データ読み込み — 提案手法 (ppr_demand) 専用ローダー
# ---------------------------------------------------------------------------
def load_proposed_results(proposed_dir: Path) -> dict:
    """
    proposed_dir 以下の構造を読む:
      {graph}/ppr_demand_cap{N}_t{t}_d{d}/*_global_transition.json

    ベースラインと同じレコード形式で data[graph]["ppr_demand"] に格納。
    """
    data: dict = defaultdict(lambda: defaultdict(list))

    for graph_dir in sorted(proposed_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name

        for policy_dir in sorted(graph_dir.iterdir()):
            if not policy_dir.is_dir():
                continue
            name = policy_dir.name

            # ppr_demand_cap{N}_t{t}_d{d} 形式だけ対象
            if not name.startswith("ppr_demand_cap"):
                continue
            try:
                # "ppr_demand_cap100_t1.0_d0.5" → cap=100
                cap_part = name.split("_t")[0]          # "ppr_demand_cap100"
                capacity = int(cap_part.replace("ppr_demand_cap", ""))
            except (ValueError, IndexError):
                continue

            for json_file in sorted(policy_dir.glob("*_global_transition.json")):
                try:
                    d = json.loads(json_file.read_text(encoding="utf-8"))
                except Exception:
                    continue

                ctrl = d.get("controller", {})
                walk_time = float(d.get("walk_time_total", 0.0))

                transition_obj = d.get("transition", {})
                if isinstance(transition_obj, dict):
                    hop_count = int(
                        sum(
                            v
                            for v in transition_obj.values()
                            if isinstance(v, (int, float))
                        )
                    )
                else:
                    hop_count = int(transition_obj or 0)

                auth_calls = int(d.get("auth_calls", 0))

                if walk_time < 1.0:
                    continue

                data[graph]["ppr_demand"].append(
                    {
                        "walk_time_total": walk_time,
                        "hop_count": hop_count,
                        "auth_calls": auth_calls,
                        "capacity": capacity,
                        "start_node": ctrl.get("start_node", -1),
                    }
                )

    return data


# ---------------------------------------------------------------------------
# 集約 (既存コードそのまま)
# ---------------------------------------------------------------------------
def aggregate(
    records: list, weights: dict, mc_samples: int = 0, rng_seed: int = 42
) -> dict:
    rtt_mean_sec = expected_rtt_ms(weights) / 1000.0
    rtt_std_per_hop_sec = (variance_rtt_ms2(weights) ** 0.5) / 1000.0

    if not records:
        return {
            "walk_per_start": 0.0,
            "sim_hop_per_start": 0.0,
            "total_per_start": 0.0,
            "walk_sum": 0.0,
            "sim_hop_sum": 0.0,
            "total_sum": 0.0,
            "hops_sum": 0,
            "auth_calls_sum": 0,
            "rt_count_sum": 0,
            "n_valid": 0,
            "mc_std_per_start": 0.0,
        }

    by_cap: dict = defaultdict(list)
    for r in records:
        by_cap[r["capacity"]].append(r)

    cap_walk_per_start = []
    cap_sim_per_start = []
    cap_mc_std = []
    walk_sum = 0.0
    sim_sum = 0.0
    hops_sum = 0
    auth_calls_sum = 0
    n_valid = 0

    rng = random.Random(rng_seed)
    classes = list(weights.keys())
    probs = [weights[c] for c in classes]
    rtts = [RTT_CLASSES[c] / 1000.0 for c in classes]

    for cap_records in by_cap.values():
        n = len(cap_records)
        if n == 0:
            continue
        walk_total = sum(r["walk_time_total"] for r in cap_records)
        hops_total = sum(r["hop_count"] for r in cap_records)
        auth_total = sum(r.get("auth_calls", 0) for r in cap_records)
        rt_total = hops_total + auth_total

        sim_total_mean = rt_total * rtt_mean_sec

        cap_walk_per_start.append(walk_total / n)
        cap_sim_per_start.append(sim_total_mean / n)

        if mc_samples > 0:
            per_start_totals = []
            for r in cap_records:
                rt_n = r["hop_count"] + r.get("auth_calls", 0)
                samples = []
                for _ in range(mc_samples):
                    counts = [0] * len(classes)
                    for _h in range(rt_n):
                        counts[
                            rng.choices(range(len(classes)), weights=probs, k=1)[0]
                        ] += 1
                    s = sum(c * r_ for c, r_ in zip(counts, rtts))
                    samples.append(s)
                per_start_totals.append(np.std(samples))
            cap_mc_std.append(
                float(np.mean(per_start_totals)) if per_start_totals else 0.0
            )

        walk_sum += walk_total
        sim_sum += sim_total_mean
        hops_sum += hops_total
        auth_calls_sum += auth_total
        n_valid += n

    walk_per_start = float(np.mean(cap_walk_per_start)) if cap_walk_per_start else 0.0
    sim_hop_per_start = float(np.mean(cap_sim_per_start)) if cap_sim_per_start else 0.0
    mc_std_per_start = float(np.mean(cap_mc_std)) if cap_mc_std else 0.0

    return {
        "walk_per_start": walk_per_start,
        "sim_hop_per_start": sim_hop_per_start,
        "total_per_start": walk_per_start + sim_hop_per_start,
        "walk_sum": walk_sum,
        "sim_hop_sum": sim_sum,
        "total_sum": walk_sum + sim_sum,
        "hops_sum": hops_sum,
        "auth_calls_sum": auth_calls_sum,
        "rt_count_sum": hops_sum + auth_calls_sum,
        "n_valid": n_valid,
        "mc_std_per_start": mc_std_per_start,
    }


# ---------------------------------------------------------------------------
# 描画ヘルパー (既存コードそのまま)
# ---------------------------------------------------------------------------
def _annotate(ax, bars, vals, fmt="{:.2f}", fontsize=8):
    for bar, val in zip(bars, vals):
        if abs(val) < 1e-9:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.01,
            fmt.format(val),
            ha="center",
            va="bottom",
            fontsize=fontsize,
        )


def _draw_stacked(ax, graphs, walk_vals: dict, sim_vals: dict, policies=None, bar_width=0.15):
    if policies is None:
        policies = POLICY_ORDER
    x = np.arange(len(graphs))
    n_policies = len(policies)
    for i, policy in enumerate(policies):
        w = walk_vals.get(policy, [0.0] * len(graphs))
        a = sim_vals.get(policy, [0.0] * len(graphs))
        offset = (i - (n_policies - 1) / 2) * bar_width
        ax.bar(
            x + offset,
            w,
            width=bar_width,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
        )
        bars_a = ax.bar(
            x + offset,
            a,
            width=bar_width,
            bottom=w,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
            hatch="///",
            alpha=0.85,
        )
        totals = [wi + ai for wi, ai in zip(w, a)]
        _annotate(ax, bars_a, totals, fmt="{:.1f}")
    return x


def _draw_diff(ax, graphs, diff_vals: dict, policies=None, bar_width=0.15):
    if policies is None:
        policies = POLICY_ORDER
    x = np.arange(len(graphs))
    n_policies = len(policies)
    for i, policy in enumerate(policies):
        vals = diff_vals.get(policy, [0.0] * len(graphs))
        offset = (i - (n_policies - 1) / 2) * bar_width
        bars = ax.bar(
            x + offset,
            vals,
            width=bar_width,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
            label=POLICY_LABELS.get(policy, policy),
        )
        for bar, val in zip(bars, vals):
            if abs(val) < 1e-6:
                continue
            va = "bottom" if val >= 0 else "top"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{val:+.1f}",
                ha="center",
                va=va,
                fontsize=8,
            )
    ax.axhline(0, color="black", linewidth=0.8)
    return x


def _add_stack_legend(ax, policies=None):
    from matplotlib.patches import Patch
    if policies is None:
        policies = POLICY_ORDER
    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in policies
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time"),
        Patch(facecolor="lightgray", hatch="///",
              label="sim_time = (auth_calls + hops) × E[RTT_mix]"),
    ]
    leg1 = ax.legend(
        handles=policy_handles, loc="upper left", fontsize=8, title="ポリシー"
    )
    ax.add_artist(leg1)
    ax.legend(handles=section_handles, loc="upper right", fontsize=8, title="内訳")


# ---------------------------------------------------------------------------
# 既存グラフ: 個別パターン積み上げ棒
# ---------------------------------------------------------------------------
def make_pattern_chart(graphs, walk_vals, sim_vals, pattern, out_path, policies=None):
    if policies is None:
        policies = POLICY_ORDER
    fig, ax = plt.subplots(figsize=(max(8, len(graphs) * 2.8), 5.5))
    x = _draw_stacked(ax, graphs, walk_vals, sim_vals, policies=policies)
    ax.set_xticks(x)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
    ax.set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    e_rtt = expected_rtt_ms(pattern["weights"])
    ax.set_title(
        f"混合RTTモデル — {pattern['label']}\n"
        f"E[RTT] = {e_rtt:.1f} ms  /  例: {pattern['note']}\n"
        f"総時間 = walk_time + (auth_calls + hop_count) × E[RTT]",
        fontsize=11,
    )
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    _add_stack_legend(ax, policies=policies)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# 既存グラフ: 3パターン横並び
# ---------------------------------------------------------------------------
def make_all_chart(graphs, per_pattern, out_path, policies=None):
    if policies is None:
        policies = POLICY_ORDER
    n = len(MIXED_PATTERNS)
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.8), 5.5), sharey=True
    )
    if n == 1:
        axes = [axes]

    all_max = 0.0
    for pat in MIXED_PATTERNS:
        v = per_pattern[pat["key"]]
        for policy in policies:
            for wi, ai in zip(v["walk"].get(policy, []), v["sim"].get(policy, [])):
                all_max = max(all_max, wi + ai)
    ylim_top = all_max * 1.15 if all_max > 0 else 1.0

    for ax, pat in zip(axes, MIXED_PATTERNS):
        v = per_pattern[pat["key"]]
        x = _draw_stacked(ax, graphs, v["walk"], v["sim"], policies=policies)
        ax.set_xticks(x)
        ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
        e_rtt = expected_rtt_ms(pat["weights"])
        ax.set_title(
            f"{pat['short']}\n(近{int(pat['weights']['local']*100)}/中{int(pat['weights']['regional']*100)}/遠{int(pat['weights']['global']*100)}, E[RTT]={e_rtt:.0f}ms)",
            fontsize=11,
        )
        ax.set_ylim(0, ylim_top)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

    axes[0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    _add_stack_legend(axes[-1], policies=policies)

    fig.suptitle(
        "Phase 2 不均一RTTモデル: 3混合パターン比較 (提案手法含む)\n"
        "総時間 = walk_time + (auth_calls + hop_count) × E[RTT_mix]",
        fontsize=13,
        y=1.03,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# 既存グラフ: memo 差分 横並び
# ---------------------------------------------------------------------------
def make_diff_all_chart(graphs, diff_per_pattern, out_path, policies=None):
    if policies is None:
        policies = POLICY_ORDER
    n = len(MIXED_PATTERNS)
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.8), 5.5), sharey=True
    )
    if n == 1:
        axes = [axes]

    all_max = 0.0
    all_min = 0.0
    for pat in MIXED_PATTERNS:
        v = diff_per_pattern[pat["key"]]
        for policy in policies:
            for val in v["diff"].get(policy, []):
                all_max = max(all_max, val)
                all_min = min(all_min, val)
    pad = max(abs(all_max), abs(all_min)) * 0.18 if (all_max or all_min) else 1.0
    ylim_top = all_max + pad
    ylim_bot = all_min - pad if all_min < 0 else -pad

    for ax, pat in zip(axes, MIXED_PATTERNS):
        v = diff_per_pattern[pat["key"]]
        memo_totals = v.get("memo_totals", [0.0] * len(graphs))
        x = _draw_diff(ax, graphs, v["diff"], policies=policies)
        xlabels = [
            f"{GRAPH_LABELS.get(g, g)}\n(memo={mt:.1f}s)"
            for g, mt in zip(graphs, memo_totals)
        ]
        ax.set_xticks(x)
        ax.set_xticklabels(xlabels, fontsize=9)
        e_rtt = expected_rtt_ms(pat["weights"])
        ax.set_title(
            f"{pat['short']}\n(E[RTT]={e_rtt:.0f}ms) / vs memo (同パターン)",
            fontsize=11,
        )
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.set_ylim(ylim_bot, ylim_top)

    axes[0].set_ylabel(
        "memo との差分 [s] (per start_node 平均, +=memoより遅い)", fontsize=11
    )
    axes[-1].legend(loc="best", fontsize=9, title="ポリシー")

    fig.suptitle(
        "Phase 2 不均一RTT — 同パターン内 memo 差分 (提案手法含む)",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# 新規グラフ: LRU vs 提案手法 直接比較
# ---------------------------------------------------------------------------
def _draw_lru_vs_proposed_ax(ax, graphs, lru_totals, prop_totals, title):
    """1つの subplot に LRU / 提案手法の棒グラフを描く共通ヘルパー。"""
    colors = {
        "lru": POLICY_COLORS["lru"],
        "ppr_demand": POLICY_COLORS["ppr_demand"],
    }
    bar_w = 0.35
    x = np.arange(len(graphs))

    ax.bar(x - bar_w / 2, lru_totals, width=bar_w,
           color=colors["lru"], label=POLICY_LABELS["lru"], alpha=0.9)
    ax.bar(x + bar_w / 2, prop_totals, width=bar_w,
           color=colors["ppr_demand"], label=POLICY_LABELS["ppr_demand"], alpha=0.9)

    # y 範囲を先に確定してからアノテーション位置を計算
    y_max = max(max(lru_totals, default=0), max(prop_totals, default=0))
    ylim_top = y_max * 1.30
    ax.set_ylim(0, ylim_top)

    for xi, (lt, pt) in enumerate(zip(lru_totals, prop_totals)):
        # バー頂上の数値
        ax.text(xi - bar_w / 2, lt + ylim_top * 0.01,
                f"{lt:.1f}", ha="center", va="bottom", fontsize=8)
        ax.text(xi + bar_w / 2, pt + ylim_top * 0.01,
                f"{pt:.1f}", ha="center", va="bottom", fontsize=8)
        # 差分ラベル (バーの高い方の上に配置)
        if lt > 0:
            diff = pt - lt
            sign = "▲" if diff > 0 else "▼"
            color_arrow = "red" if diff > 0 else "green"
            ax.text(xi, max(lt, pt) + ylim_top * 0.09,
                    f"{sign}{abs(diff):.1f}s\n({diff / lt * 100:+.1f}%)",
                    ha="center", va="bottom", fontsize=8,
                    color=color_arrow, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
    ax.set_title(title, fontsize=11)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(fontsize=9, loc="upper left")
    return x


def make_proposed_vs_lru_chart(graphs, agg_per_pattern, out_path_all, out_dir):
    """
    各グラフ × 各RTTパターン: LRU と提案手法の総処理時間を並べて表示。
    diff ラベル: ▼緑=提案手法が速い  ▲赤=提案手法が遅い
    """
    n_pat = len(MIXED_PATTERNS)
    # sharey=False: Amazon と VLDB でスケールが大きく異なるため各軸独立
    fig_all, axes_all = plt.subplots(
        1, n_pat, figsize=(max(12, len(graphs) * 4.2), 6.0), sharey=False
    )
    if n_pat == 1:
        axes_all = [axes_all]

    for ax, pat in zip(axes_all, MIXED_PATTERNS):
        agg = agg_per_pattern[pat["key"]]
        lru_totals = [agg[g].get("lru", {}).get("total_per_start", 0.0) for g in graphs]
        prop_totals = [agg[g].get("ppr_demand", {}).get("total_per_start", 0.0) for g in graphs]
        e_rtt = expected_rtt_ms(pat["weights"])
        _draw_lru_vs_proposed_ax(
            ax, graphs, lru_totals, prop_totals,
            title=f"{pat['short']}\n(E[RTT]={e_rtt:.0f}ms)",
        )

    axes_all[0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    fig_all.suptitle(
        "LRU vs 提案手法 (PPR-Demand) — 直接比較\n"
        "▼緑=提案手法が速い  ▲赤=提案手法が遅い",
        fontsize=13,
    )
    fig_all.tight_layout()
    fig_all.savefig(out_path_all, dpi=150, bbox_inches="tight")
    plt.close(fig_all)
    print(f"[saved] {out_path_all}")

    # 個別パターン
    for pat in MIXED_PATTERNS:
        agg = agg_per_pattern[pat["key"]]
        lru_totals = [agg[g].get("lru", {}).get("total_per_start", 0.0) for g in graphs]
        prop_totals = [agg[g].get("ppr_demand", {}).get("total_per_start", 0.0) for g in graphs]
        e_rtt = expected_rtt_ms(pat["weights"])

        fig, ax = plt.subplots(figsize=(max(7, len(graphs) * 2.6), 6.0))
        _draw_lru_vs_proposed_ax(
            ax, graphs, lru_totals, prop_totals,
            title=(
                f"LRU vs 提案手法 — {pat['label']}\n"
                f"E[RTT] = {e_rtt:.1f} ms  /  例: {pat['note']}"
            ),
        )
        ax.set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
        fig.tight_layout()
        out_indiv = out_dir / f"proposed_vs_lru_pattern_{pat['key']}.png"
        fig.savefig(out_indiv, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out_indiv}")


# ---------------------------------------------------------------------------
# CSV 出力 (提案手法を含む)
# ---------------------------------------------------------------------------
def save_summary_csv(graphs, per_pattern, out_path: Path, policies=None):
    if policies is None:
        policies = POLICY_ORDER
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "pattern_key", "pattern_label", "expected_rtt_ms",
            "graph", "policy",
            "walk_per_start_s", "sim_time_per_start_s", "total_per_start_s",
            "walk_sum_s", "sim_time_sum_s", "total_sum_s",
            "hops_sum", "auth_calls_sum", "rt_count_sum", "n_valid",
            "memo_diff_per_start_s", "memo_ratio",
            "lru_diff_per_start_s", "lru_ratio",
            "mc_std_per_start_s",
        ])
        for pat in MIXED_PATTERNS:
            agg = per_pattern[pat["key"]]["agg"]
            e_rtt = expected_rtt_ms(pat["weights"])
            for graph in graphs:
                base_memo = agg[graph].get("memo", {})
                base_memo_t = base_memo.get("total_per_start", 0.0)
                base_lru = agg[graph].get("lru", {})
                base_lru_t = base_lru.get("total_per_start", 0.0)
                for policy in policies:
                    v = agg[graph].get(policy, {})
                    if not v or v.get("n_valid", 0) == 0:
                        continue
                    diff_memo = v["total_per_start"] - base_memo_t
                    ratio_memo = v["total_per_start"] / base_memo_t if base_memo_t > 0 else 0
                    diff_lru = v["total_per_start"] - base_lru_t
                    ratio_lru = v["total_per_start"] / base_lru_t if base_lru_t > 0 else 0
                    w.writerow([
                        pat["key"], pat["label"], f"{e_rtt:.2f}",
                        graph, policy,
                        f"{v['walk_per_start']:.4f}",
                        f"{v['sim_hop_per_start']:.4f}",
                        f"{v['total_per_start']:.4f}",
                        f"{v['walk_sum']:.3f}",
                        f"{v['sim_hop_sum']:.3f}",
                        f"{v['total_sum']:.3f}",
                        v["hops_sum"],
                        v.get("auth_calls_sum", 0),
                        v.get("rt_count_sum", 0),
                        v["n_valid"],
                        f"{diff_memo:+.4f}", f"{ratio_memo:.4f}",
                        f"{diff_lru:+.4f}", f"{ratio_lru:.4f}",
                        f"{v.get('mc_std_per_start', 0.0):.4f}",
                    ])
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# キャパシティ別ディレクトリ探索
# ---------------------------------------------------------------------------
def find_cap_dir(parent: Path, cap: int) -> Path | None:
    """
    parent 以下から capacity=cap に対応するサブディレクトリを返す。
    命名規則が揺れていても `capa{cap}` / `capa_{cap}` の両方に対応。
    """
    for d in sorted(parent.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if f"capa_{cap}" in name or f"capa{cap}" in name:
            return d
    return None


def load_multi_capacity(
    results_parent: Path,
    proposed_parent: Path,
    capacities: list[int],
    mc_samples: int,
    rng_seed: int,
) -> dict:
    """
    複数のキャパシティ分のデータを集約して返す。

    Returns
    -------
    {
      cap: {
        graph: {
          "lru":        aggregate_result,
          "ppr_demand": aggregate_result,
        }
      }
    }
    (RTTパターンごとに呼ぶこと)
    """
    # ここでは生レコードだけ返す (aggregate は呼び出し側で行う)
    raw: dict[int, dict] = {}
    for cap in capacities:
        cap_data: dict = defaultdict(lambda: defaultdict(list))

        # ベースライン (LRU のみ期待)
        base_dir = find_cap_dir(results_parent, cap)
        if base_dir:
            d = load_results(base_dir)
            for graph, pdict in d.items():
                for policy, recs in pdict.items():
                    cap_data[graph][policy].extend(recs)
        else:
            print(f"[warn] cap={cap}: ベースラインディレクトリが見つかりません ({results_parent})")

        # 提案手法
        prop_dir = find_cap_dir(proposed_parent, cap)
        if prop_dir:
            d = load_proposed_results(prop_dir)
            for graph, pdict in d.items():
                for policy, recs in pdict.items():
                    cap_data[graph][policy].extend(recs)
        else:
            print(f"[warn] cap={cap}: 提案手法ディレクトリが見つかりません ({proposed_parent})")

        raw[cap] = cap_data

    return raw


# ---------------------------------------------------------------------------
# キャパシティ比較チャート
# ---------------------------------------------------------------------------
def make_capacity_comparison_chart(
    graphs: list,
    raw_by_cap: dict,
    capacities: list[int],
    out_dir: Path,
):
    """
    グラフ × RTTパターン ごとに、キャパシティ別 LRU vs 提案手法を表示。

    レイアウト: 行=グラフ, 列=RTTパターン
    x軸: キャパシティ (100, 200, 300 ...)
    """
    colors = {
        "lru": POLICY_COLORS["lru"],
        "ppr_demand": POLICY_COLORS["ppr_demand"],
    }
    n_graphs = len(graphs)
    n_pats = len(MIXED_PATTERNS)

    for pat in MIXED_PATTERNS:
        fig, axes = plt.subplots(
            1, n_graphs,
            figsize=(max(10, n_graphs * 4.5), 5.5),
            sharey=False,
        )
        if n_graphs == 1:
            axes = [axes]

        for ax, graph in zip(axes, graphs):
            lru_vals, prop_vals = [], []
            for cap in capacities:
                cap_data = raw_by_cap[cap]
                lru_agg = aggregate(
                    cap_data[graph].get("lru", []), pat["weights"]
                )
                prop_agg = aggregate(
                    cap_data[graph].get("ppr_demand", []), pat["weights"]
                )
                lru_vals.append(lru_agg["total_per_start"])
                prop_vals.append(prop_agg["total_per_start"])

            x = np.arange(len(capacities))
            bar_w = 0.35
            ax.bar(x - bar_w / 2, lru_vals, width=bar_w,
                   color=colors["lru"], label=POLICY_LABELS["lru"], alpha=0.9)
            ax.bar(x + bar_w / 2, prop_vals, width=bar_w,
                   color=colors["ppr_demand"], label=POLICY_LABELS["ppr_demand"], alpha=0.9)

            y_max = max(max(lru_vals, default=0), max(prop_vals, default=0))
            ylim_top = y_max * 1.32
            ax.set_ylim(0, ylim_top)

            for xi, (lt, pt) in enumerate(zip(lru_vals, prop_vals)):
                ax.text(xi - bar_w / 2, lt + ylim_top * 0.01,
                        f"{lt:.1f}", ha="center", va="bottom", fontsize=8)
                ax.text(xi + bar_w / 2, pt + ylim_top * 0.01,
                        f"{pt:.1f}", ha="center", va="bottom", fontsize=8)
                if lt > 0:
                    diff = pt - lt
                    sign = "▲" if diff > 0 else "▼"
                    c = "red" if diff > 0 else "green"
                    ax.text(xi, max(lt, pt) + ylim_top * 0.09,
                            f"{sign}{abs(diff):.1f}s\n({diff / lt * 100:+.1f}%)",
                            ha="center", va="bottom", fontsize=8,
                            color=c, fontweight="bold")

            ax.set_xticks(x)
            ax.set_xticklabels([f"cap={c}" for c in capacities], fontsize=10)
            ax.set_title(GRAPH_LABELS.get(graph, graph), fontsize=12)
            ax.grid(axis="y", linestyle="--", alpha=0.4)
            ax.legend(fontsize=9, loc="upper right")

        axes[0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
        e_rtt = expected_rtt_ms(pat["weights"])
        fig.suptitle(
            f"キャパシティ別 LRU vs 提案手法 — {pat['label']}\n"
            f"E[RTT]={e_rtt:.0f}ms  ▼緑=提案手法が速い  ▲赤=提案手法が遅い",
            fontsize=13,
        )
        fig.tight_layout()
        out_path = out_dir / f"capacity_vs_lru_pattern_{pat['key']}.png"
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out_path}")

    # 3パターン横並び (グラフごとに行を分ける)
    fig, axes = plt.subplots(
        n_graphs, n_pats,
        figsize=(max(14, n_pats * 5.0), n_graphs * 4.5),
        sharey=False,
    )
    if n_graphs == 1:
        axes = [axes]
    if n_pats == 1:
        axes = [[ax] for ax in axes]

    for ri, graph in enumerate(graphs):
        for ci, pat in enumerate(MIXED_PATTERNS):
            ax = axes[ri][ci]
            lru_vals, prop_vals = [], []
            for cap in capacities:
                cap_data = raw_by_cap[cap]
                lru_agg = aggregate(cap_data[graph].get("lru", []), pat["weights"])
                prop_agg = aggregate(cap_data[graph].get("ppr_demand", []), pat["weights"])
                lru_vals.append(lru_agg["total_per_start"])
                prop_vals.append(prop_agg["total_per_start"])

            x = np.arange(len(capacities))
            bar_w = 0.35
            ax.bar(x - bar_w / 2, lru_vals, width=bar_w,
                   color=colors["lru"], label=POLICY_LABELS["lru"], alpha=0.9)
            ax.bar(x + bar_w / 2, prop_vals, width=bar_w,
                   color=colors["ppr_demand"], label=POLICY_LABELS["ppr_demand"], alpha=0.9)

            y_max = max(max(lru_vals, default=0), max(prop_vals, default=0))
            ylim_top = y_max * 1.32
            ax.set_ylim(0, ylim_top)

            for xi, (lt, pt) in enumerate(zip(lru_vals, prop_vals)):
                ax.text(xi - bar_w / 2, lt + ylim_top * 0.01,
                        f"{lt:.1f}", ha="center", va="bottom", fontsize=7.5)
                ax.text(xi + bar_w / 2, pt + ylim_top * 0.01,
                        f"{pt:.1f}", ha="center", va="bottom", fontsize=7.5)
                if lt > 0:
                    diff = pt - lt
                    sign = "▲" if diff > 0 else "▼"
                    c = "red" if diff > 0 else "green"
                    ax.text(xi, max(lt, pt) + ylim_top * 0.09,
                            f"{sign}{abs(diff):.1f}s\n({diff/lt*100:+.1f}%)",
                            ha="center", va="bottom", fontsize=7,
                            color=c, fontweight="bold")

            ax.set_xticks(x)
            ax.set_xticklabels([f"cap={c}" for c in capacities], fontsize=9)
            e_rtt = expected_rtt_ms(pat["weights"])
            title = f"{GRAPH_LABELS.get(graph, graph)} / {pat['short']} (E[RTT]={e_rtt:.0f}ms)"
            ax.set_title(title, fontsize=10)
            ax.grid(axis="y", linestyle="--", alpha=0.4)
            if ri == 0 and ci == 0:
                ax.legend(fontsize=8)

        axes[ri][0].set_ylabel(
            f"{GRAPH_LABELS.get(graph, graph)}\n総処理時間 [s]", fontsize=10
        )

    fig.suptitle(
        "キャパシティ別 LRU vs 提案手法 — 全パターン\n"
        "▼緑=提案手法が速い  ▲赤=提案手法が遅い",
        fontsize=13,
    )
    fig.tight_layout()
    out_path = out_dir / "capacity_vs_lru_all.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def save_capacity_csv(graphs, raw_by_cap, capacities, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "pattern_key", "expected_rtt_ms", "graph", "capacity",
            "policy", "total_per_start_s", "walk_per_start_s", "sim_per_start_s",
            "auth_calls_sum", "n_valid",
            "lru_diff_s", "lru_ratio",
        ])
        for pat in MIXED_PATTERNS:
            e_rtt = expected_rtt_ms(pat["weights"])
            for graph in graphs:
                for cap in capacities:
                    cap_data = raw_by_cap[cap]
                    lru_agg = aggregate(cap_data[graph].get("lru", []), pat["weights"])
                    prop_agg = aggregate(cap_data[graph].get("ppr_demand", []), pat["weights"])
                    lru_t = lru_agg["total_per_start"]
                    for policy, agg_v in [("lru", lru_agg), ("ppr_demand", prop_agg)]:
                        if agg_v["n_valid"] == 0:
                            continue
                        diff = agg_v["total_per_start"] - lru_t
                        ratio = agg_v["total_per_start"] / lru_t if lru_t > 0 else 0
                        w.writerow([
                            pat["key"], f"{e_rtt:.2f}", graph, cap,
                            policy,
                            f"{agg_v['total_per_start']:.4f}",
                            f"{agg_v['walk_per_start']:.4f}",
                            f"{agg_v['sim_hop_per_start']:.4f}",
                            agg_v.get("auth_calls_sum", 0),
                            agg_v["n_valid"],
                            f"{diff:+.4f}", f"{ratio:.4f}",
                        ])
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Phase 2 改良版: LRU vs 提案手法 (ppr_demand) を不均一RTTで比較"
    )
    ap.add_argument("--results-dir", type=Path, default=None,
                    help="ベースライン結果ディレクトリ (単一キャパシティ時)")
    ap.add_argument("--proposed-dir", type=Path, default=None,
                    help="提案手法の結果ディレクトリ (単一キャパシティ時)")
    ap.add_argument("--results-parent", type=Path, default=None,
                    help="ベースライン結果の親ディレクトリ (複数キャパシティ時)")
    ap.add_argument("--proposed-parent", type=Path, default=None,
                    help="提案手法の結果の親ディレクトリ (複数キャパシティ時)")
    ap.add_argument("--capacities", type=int, nargs="+", default=[100, 200, 300],
                    help="比較するキャパシティ一覧 (default: 100 200 300)")
    ap.add_argument("--out-dir", type=Path, default=Path("base/rtt/output_proposed"),
                    help="出力先 (default: base/rtt/output_proposed)")
    ap.add_argument("--mc", type=int, default=0,
                    help="モンテカルロサンプル数 (0=決定論のみ)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    # 引数チェック
    single_mode = args.results_dir is not None
    multi_mode = args.results_parent is not None and args.proposed_parent is not None
    if not single_mode and not multi_mode:
        ap.error("--results-dir か (--results-parent + --proposed-parent) のどちらかを指定してください")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    graph_order = ["karate", "fb-caltech-connected", "amazon0601", "vldb"]

    print("=" * 78)
    print(f"{'Phase 2 改良版: LRU vs 提案手法 比較':^78}")
    print("=" * 78)
    for pat in MIXED_PATTERNS:
        w = pat["weights"]
        e = expected_rtt_ms(w)
        s = variance_rtt_ms2(w) ** 0.5
        print(f"  [{pat['key']}] {pat['label']}  E[RTT]={e:.1f}ms  std={s:.1f}ms")

    # ====================================================================
    # 単一キャパシティモード (従来の動作)
    # ====================================================================
    if single_mode:
        data = load_results(args.results_dir)
        if not data:
            print(f"[ERROR] ベースライン結果が見つかりません: {args.results_dir}")
            return

        has_proposed = False
        if args.proposed_dir and args.proposed_dir.exists():
            proposed_data = load_proposed_results(args.proposed_dir)
            for graph, policy_dict in proposed_data.items():
                for policy, records in policy_dict.items():
                    data[graph][policy].extend(records)
                    if records:
                        has_proposed = True
            print(f"[info] 提案手法データ読み込み完了: {args.proposed_dir}")
        else:
            print("[warn] --proposed-dir が未指定または存在しません。提案手法なしで実行。")

        active_policies = [p for p in POLICY_ORDER if any(data[g].get(p) for g in data)]
        graphs = [g for g in graph_order if g in data]
        for g in sorted(data.keys()):
            if g not in graphs:
                graphs.append(g)

        print(f"\nグラフ: {graphs}")
        print(f"ポリシー: {active_policies}")

        per_pattern: dict = {}
        diff_per_pattern: dict = {}

        for pat in MIXED_PATTERNS:
            agg: dict = {}
            for graph in graphs:
                agg[graph] = {}
                for policy in active_policies:
                    agg[graph][policy] = aggregate(
                        data[graph].get(policy, []),
                        pat["weights"],
                        mc_samples=args.mc,
                        rng_seed=args.seed,
                    )

            walk_vals = {p: [agg[g][p]["walk_per_start"] for g in graphs] for p in active_policies}
            sim_vals  = {p: [agg[g][p]["sim_hop_per_start"] for g in graphs] for p in active_policies}
            per_pattern[pat["key"]] = {"walk": walk_vals, "sim": sim_vals, "agg": agg}

            make_pattern_chart(
                graphs, walk_vals, sim_vals, pat,
                out_path=out_dir / f"proposed_{pat['fname_indiv']}",
                policies=active_policies,
            )

            print(f"\n===== {pat['label']} =====")
            print(f"{'グラフ':<14} {'ポリシー':<20} {'walk/s':>9} {'simTime/s':>10} {'total/s':>10} {'lru差/s':>9} {'n':>3}")
            print("-" * 80)
            for graph in graphs:
                base_lru = agg[graph].get("lru", {}).get("total_per_start", 0.0)
                for policy in active_policies:
                    v = agg[graph].get(policy, {})
                    if not v or v.get("n_valid", 0) == 0:
                        continue
                    print(
                        f"{GRAPH_LABELS.get(graph, graph):<14} "
                        f"{POLICY_LABELS.get(policy, policy):<20} "
                        f"{v['walk_per_start']:>9.3f} "
                        f"{v['sim_hop_per_start']:>10.3f} "
                        f"{v['total_per_start']:>10.3f} "
                        f"{v['total_per_start'] - base_lru:>+9.3f} "
                        f"{v['n_valid']:>3d}"
                    )

            diff_vals = {p: [] for p in active_policies}
            memo_totals = []
            for graph in graphs:
                base_t = agg[graph].get("memo", {}).get("total_per_start", 0.0)
                memo_totals.append(base_t)
                for policy in active_policies:
                    v = agg[graph].get(policy, {})
                    diff_vals[policy].append(
                        v["total_per_start"] - base_t if (v and v.get("n_valid", 0) > 0) else 0.0
                    )
            diff_per_pattern[pat["key"]] = {"diff": diff_vals, "memo_totals": memo_totals}

        make_all_chart(graphs, per_pattern, out_dir / "proposed_mixed_rtt_all.png", policies=active_policies)
        make_diff_all_chart(graphs, diff_per_pattern, out_dir / "proposed_mixed_rtt_diff_vs_memo_all.png", policies=active_policies)

        if has_proposed:
            agg_per_pattern = {pat["key"]: per_pattern[pat["key"]]["agg"] for pat in MIXED_PATTERNS}
            make_proposed_vs_lru_chart(graphs, agg_per_pattern, out_dir / "proposed_vs_lru_all.png", out_dir)
        else:
            print("[skip] 提案手法データがないため LRU vs Proposed グラフをスキップ")

        save_summary_csv(graphs, per_pattern, out_dir / "proposed_mixed_rtt_summary.csv", policies=active_policies)

    # ====================================================================
    # 複数キャパシティモード
    # ====================================================================
    if multi_mode:
        print(f"\n[multi-cap] キャパシティ {args.capacities} で比較")
        print(f"  ベースライン親: {args.results_parent}")
        print(f"  提案手法親    : {args.proposed_parent}")

        raw_by_cap = load_multi_capacity(
            args.results_parent,
            args.proposed_parent,
            args.capacities,
            mc_samples=args.mc,
            rng_seed=args.seed,
        )

        # グラフ一覧を全キャパシティのデータから収集
        all_graphs: set = set()
        for cap_data in raw_by_cap.values():
            all_graphs.update(cap_data.keys())
        graphs_mc = [g for g in graph_order if g in all_graphs]
        for g in sorted(all_graphs):
            if g not in graphs_mc:
                graphs_mc.append(g)
        print(f"  グラフ: {graphs_mc}")

        # キャパシティ比較グラフ
        make_capacity_comparison_chart(graphs_mc, raw_by_cap, args.capacities, out_dir)
        save_capacity_csv(graphs_mc, raw_by_cap, args.capacities, out_dir / "capacity_comparison_summary.csv")

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
