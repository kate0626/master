#!/usr/bin/env python3
"""
Phase 2: 不均一 RTT モデル (混合パターン)

各リモート通信 (hop + auth 確認) が「ローカル / 中距離 / 長距離」のいずれかに
従って RTT を支払う混合モデル。各通信を確率分布から独立同分布 (IID) で抽出。

リモート通信回数 = auth_calls + hop_count
    auth_calls : リモート認可確認回数 (= cache miss 数, JSON `auth_calls`)
    hop_count  : ランダムウォーク中の hop 数 (= sum of `transition`)

sim_time = (auth_calls + hop_count) × E[RTT_mix]
total    = walk_time + sim_time

----------------------------------------------------------------------
文献根拠 (各 RTT 単位値の出典)
----------------------------------------------------------------------
  ローカル (近い)   10 ms : Azure Japan East ↔ Japan West (12 ms 実測, 公式)
  中距離  (中位)   60 ms : Azure Japan East ↔ Hong Kong (53 ms), ↔ Singapore (73 ms) の中央値
  長距離  (遠い)  200 ms : Azure Japan East ↔ 米東 (163 ms), ↔ 欧州 (235 ms) の中央値
                          NSF論文 "intercontinental long-haul ≥ 57 ms" にも整合 (par.nsf.gov/10535037)
  → 1000km あたり ~10-12 ms (CloudCast, arXiv:2201.06989) という光速則とも整合。

----------------------------------------------------------------------
混合パターン (文献根拠)
----------------------------------------------------------------------
  A. ローカル支配  (local-dominant)
     比率: 近70% / 中20% / 遠10%
     例:   日本国内向け EC, 国内 SNS, ローカルニュース
     根拠: Internet Society / OECD "Local content & Internet development" 2012 にて
           先進国では top-level domain 国別ドメイン下のコンテンツが優位。
           CDN 上位 PoP のヒット率は 80%+ が "healthy" (Fastly, Cloudflare)。
     E[RTT] = 0.7×10 + 0.2×60 + 0.1×200 = 39 ms

  B. 域内バランス  (regional-balanced)
     比率: 近30% / 中50% / 遠20%
     例:   アジア圏ビジネス, 多国籍企業のリージョナル分散
     根拠: TeleGeography "International bandwidth" 報告では、
           アジア域内の bandwidth が域間を上回るが、海外依存も無視できない比率。
     E[RTT] = 0.3×10 + 0.5×60 + 0.2×200 = 73 ms

  C. グローバル分散 (global-distributed)
     比率: 近10% / 中30% / 遠60%
     例:   国際 CDN エッジケース, 国際メディア, 国際的研究コラボレーション
     根拠: 海底ケーブル研究 (arXiv:2110.05772) によれば多くの国で
           Web リソースの 60%以上 が submarine cable (= 国際路) に依存。
     E[RTT] = 0.1×10 + 0.3×60 + 0.6×200 = 139 ms

----------------------------------------------------------------------
モデル
----------------------------------------------------------------------
  決定論モード (default):
    sim_time = (auth_calls + hop_count) × E[RTT_mix]
    total    = walk_time + sim_time

  モンテカルロモード (--mc N):
    各リモート通信 (auth_calls + hop_count 回) ごとに category を確率で抽選
    → N 回試行して平均と CI を計算

----------------------------------------------------------------------
出力 (--out-dir, default は base/rtt/output)
----------------------------------------------------------------------
  mixed_rtt_pattern_A.png         パターンA 単体 (積み上げ棒)
  mixed_rtt_pattern_B.png         パターンB 単体
  mixed_rtt_pattern_C.png         パターンC 単体
  mixed_rtt_all.png               3パターン横並び (共通スケール)
  mixed_rtt_diff_vs_memo_all.png  3パターン memo 差分 (共通スケール)
  mixed_rtt_summary.csv           各値の表形式

実行例:
  python3 base/rtt/plot_mixed_rtt.py \
      --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
      --out-dir     base/rtt/output
      
python3 base/rtt/plot_mixed_rtt.py \
    --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
    --out-dir base/rtt/output \
    --mc 50

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
# RTT 単位 (ms) — 既存の Phase 1 と統一
# ---------------------------------------------------------------------------
RTT_CLASSES = {
    "local": 10,  # 近い (国内DC)
    "regional": 60,  # 中位 (アジア圏)
    "global": 200,  # 遠い (大陸間)
}

# ---------------------------------------------------------------------------
# 混合パターン (Phase 2)
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
    """E[RTT_mix] in ms"""
    return sum(weights[k] * RTT_CLASSES[k] for k in weights)


def variance_rtt_ms2(weights: dict) -> float:
    """Var[RTT_mix] in ms^2 (per hop)"""
    mu = expected_rtt_ms(weights)
    return sum(weights[k] * (RTT_CLASSES[k] - mu) ** 2 for k in weights)


# ---------------------------------------------------------------------------
# 設定 (Phase 1 と統一)
# ---------------------------------------------------------------------------
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
                start_node = ctrl.get("start_node", -1)
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

                # auth_calls = リモート認可確認回数 (= cache miss 数)
                auth_calls = int(d.get("auth_calls", 0))

                # Length=1 (Traceback) を除外
                if walk_time < 1.0:
                    continue

                data[graph][policy].append(
                    {
                        "walk_time_total": walk_time,
                        "hop_count": hop_count,
                        "auth_calls": auth_calls,
                        "capacity": capacity,
                        "start_node": start_node,
                    }
                )

    return data


def aggregate(
    records: list, weights: dict, mc_samples: int = 0, rng_seed: int = 42
) -> dict:
    """
    モデル: rt_count = auth_calls + hop_count  (リモート通信総数)
            sim_time = rt_count × E[RTT_mix]   (決定論)

    決定論モード:
      sim_hop_per_start = (auth_calls + hop_count) × E[RTT_mix] (per-start avg)

    モンテカルロモード (mc_samples > 0):
      rt_count 回の独立 RTT 抽選を mc_samples 回行い、合計の平均と標準偏差。
    """
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
        rt_total = hops_total + auth_total  # リモート通信総数

        # 決定論: sim_time = (auth_calls + hops) × E[RTT_mix]
        sim_total_mean = rt_total * rtt_mean_sec

        cap_walk_per_start.append(walk_total / n)
        cap_sim_per_start.append(sim_total_mean / n)

        # MC で per-start のばらつきを計算 (rt_count = auth + hops 回の抽選)
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
# 描画ヘルパー
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


def _draw_stacked(ax, graphs, walk_vals: dict, sim_vals: dict, bar_width=0.18):
    x = np.arange(len(graphs))
    n_policies = len(POLICY_ORDER)
    for i, policy in enumerate(POLICY_ORDER):
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


def _draw_diff(ax, graphs, diff_vals: dict, bar_width=0.18):
    x = np.arange(len(graphs))
    n_policies = len(POLICY_ORDER)
    for i, policy in enumerate(POLICY_ORDER):
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


def _add_stack_legend(ax):
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in POLICY_ORDER
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
# 描画: 個別 + 横並び
# ---------------------------------------------------------------------------
def make_pattern_chart(graphs, walk_vals, sim_vals, pattern, out_path):
    fig, ax = plt.subplots(figsize=(max(8, len(graphs) * 2.4), 5.5))
    x = _draw_stacked(ax, graphs, walk_vals, sim_vals)
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
    _add_stack_legend(ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[saved] {out_path}")


def make_all_chart(graphs, per_pattern, out_path):
    n = len(MIXED_PATTERNS)
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.2), 5.5), sharey=True
    )
    if n == 1:
        axes = [axes]

    # 共通スケール
    all_max = 0.0
    for pat in MIXED_PATTERNS:
        v = per_pattern[pat["key"]]
        for policy in POLICY_ORDER:
            for wi, ai in zip(v["walk"].get(policy, []), v["sim"].get(policy, [])):
                all_max = max(all_max, wi + ai)
    ylim_top = all_max * 1.15 if all_max > 0 else 1.0

    for ax, pat in zip(axes, MIXED_PATTERNS):
        v = per_pattern[pat["key"]]
        x = _draw_stacked(ax, graphs, v["walk"], v["sim"])
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
    _add_stack_legend(axes[-1])

    fig.suptitle(
        "Phase 2 不均一RTTモデル: 3混合パターン比較 (y軸共通スケール)\n"
        "総時間 = walk_time + (auth_calls + hop_count) × E[RTT_mix]",
        fontsize=13,
        y=1.03,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def make_diff_all_chart(graphs, diff_per_pattern, out_path):
    n = len(MIXED_PATTERNS)
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.2), 5.5), sharey=True
    )
    if n == 1:
        axes = [axes]

    all_max = 0.0
    all_min = 0.0
    for pat in MIXED_PATTERNS:
        v = diff_per_pattern[pat["key"]]
        for policy in POLICY_ORDER:
            for val in v["diff"].get(policy, []):
                all_max = max(all_max, val)
                all_min = min(all_min, val)
    pad = max(abs(all_max), abs(all_min)) * 0.18 if (all_max or all_min) else 1.0
    ylim_top = all_max + pad
    ylim_bot = all_min - pad if all_min < 0 else -pad

    for ax, pat in zip(axes, MIXED_PATTERNS):
        v = diff_per_pattern[pat["key"]]
        memo_totals = v.get("memo_totals", [0.0] * len(graphs))
        x = _draw_diff(ax, graphs, v["diff"])
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
        "Phase 2 不均一RTT — 同パターン内 memo 差分 (y軸共通スケール)",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# CSV 出力
# ---------------------------------------------------------------------------
def save_summary_csv(graphs, per_pattern, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "pattern_key",
                "pattern_label",
                "expected_rtt_ms",
                "graph",
                "policy",
                "walk_per_start_s",
                "sim_time_per_start_s",
                "total_per_start_s",
                "walk_sum_s",
                "sim_time_sum_s",
                "total_sum_s",
                "hops_sum",
                "auth_calls_sum",
                "rt_count_sum",
                "n_valid",
                "memo_diff_per_start_s",
                "memo_ratio",
                "mc_std_per_start_s",
            ]
        )
        for pat in MIXED_PATTERNS:
            agg = per_pattern[pat["key"]]["agg"]
            e_rtt = expected_rtt_ms(pat["weights"])
            for graph in graphs:
                base = agg[graph].get("memo", {})
                base_total = base.get("total_per_start", 0.0)
                for policy in POLICY_ORDER:
                    v = agg[graph].get(policy, {})
                    if not v or v.get("n_valid", 0) == 0:
                        continue
                    diff = v["total_per_start"] - base_total
                    ratio = v["total_per_start"] / base_total if base_total > 0 else 0
                    w.writerow(
                        [
                            pat["key"],
                            pat["label"],
                            f"{e_rtt:.2f}",
                            graph,
                            policy,
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
                            f"{diff:+.4f}",
                            f"{ratio:.4f}",
                            f"{v.get('mc_std_per_start', 0.0):.4f}",
                        ]
                    )
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Phase 2: 不均一 RTT (3混合パターン) で総処理時間を分析・可視化"
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="graph/policy_cap/*.json を検索するディレクトリ",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("base/rtt/output"),
        help="画像/CSV の出力先 (default: base/rtt/output)",
    )
    ap.add_argument(
        "--mc",
        type=int,
        default=0,
        help="モンテカルロサンプル数 (0=決定論のみ, 例: 100)",
    )
    ap.add_argument("--seed", type=int, default=42, help="MC seed")
    args = ap.parse_args()

    results_dir = args.results_dir
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    data = load_results(results_dir)
    if not data:
        print(f"[ERROR] 結果ファイルが見つかりませんでした: {results_dir}")
        return

    graph_order = ["karate", "fb-caltech-connected", "amazon0601", "vldb"]
    graphs = [g for g in graph_order if g in data]
    for g in sorted(data.keys()):
        if g not in graphs:
            graphs.append(g)

    # ====================================================================
    # 混合パターン定義の文献根拠を表示
    # ====================================================================
    print("=" * 78)
    print(f"{'Phase 2: 不均一 RTT モデル (3 混合パターン)':^78}")
    print("=" * 78)
    print(
        f"RTT 単位 [ms]: local={RTT_CLASSES['local']}, regional={RTT_CLASSES['regional']}, global={RTT_CLASSES['global']}"
    )
    print()
    for pat in MIXED_PATTERNS:
        w = pat["weights"]
        e = expected_rtt_ms(w)
        s = variance_rtt_ms2(w) ** 0.5
        print(f"  [{pat['key']}] {pat['label']}")
        print(f"      例: {pat['note']}")
        print(
            f"      weights = local:{w['local']:.2f} / regional:{w['regional']:.2f} / global:{w['global']:.2f}"
        )
        print(f"      E[RTT]  = {e:.2f} ms,  std[RTT/hop] = {s:.2f} ms")
        print()

    # ====================================================================
    # 集約 (各混合パターンごと)
    # ====================================================================
    per_pattern: dict = {}
    diff_per_pattern: dict = {}

    for pat in MIXED_PATTERNS:
        agg: dict = {}
        for graph in graphs:
            agg[graph] = {}
            for policy in POLICY_ORDER:
                agg[graph][policy] = aggregate(
                    data[graph].get(policy, []),
                    pat["weights"],
                    mc_samples=args.mc,
                    rng_seed=args.seed,
                )

        walk_vals = {
            p: [agg[g][p]["walk_per_start"] for g in graphs] for p in POLICY_ORDER
        }
        sim_vals = {
            p: [agg[g][p]["sim_hop_per_start"] for g in graphs] for p in POLICY_ORDER
        }

        per_pattern[pat["key"]] = {
            "walk": walk_vals,
            "sim": sim_vals,
            "agg": agg,
        }

        # 個別グラフ
        make_pattern_chart(
            graphs,
            walk_vals,
            sim_vals,
            pat,
            out_path=out_dir / pat["fname_indiv"],
        )

        # console summary
        print(f"\n===== {pat['label']} =====")
        print(f"  sim_time = (auth_calls + hop_count) × E[RTT_mix]")
        print(
            f"{'グラフ':<14} {'ポリシー':<8} {'walk/s':>9} {'simTime/s':>10} {'total/s':>10} "
            f"{'memo差/s':>10} {'memo比':>7} {'hops':>8} {'auth_calls':>11} {'rt_total':>10} {'n':>3}"
            + ("  mc_std" if args.mc > 0 else "")
        )
        print("-" * 130)
        for graph in graphs:
            base = agg[graph].get("memo", {})
            base_t = base.get("total_per_start", 0.0)
            for policy in POLICY_ORDER:
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    continue
                diff = v["total_per_start"] - base_t
                ratio = v["total_per_start"] / base_t if base_t > 0 else 0
                line = (
                    f"{GRAPH_LABELS.get(graph, graph):<14} "
                    f"{policy:<8} "
                    f"{v['walk_per_start']:>9.3f} "
                    f"{v['sim_hop_per_start']:>10.3f} "
                    f"{v['total_per_start']:>10.3f} "
                    f"{diff:>+10.3f} "
                    f"{ratio:>6.2f}x "
                    f"{v['hops_sum']:>8d} "
                    f"{v.get('auth_calls_sum', 0):>11d} "
                    f"{v.get('rt_count_sum', 0):>10d} "
                    f"{v['n_valid']:>3d}"
                )
                if args.mc > 0:
                    line += f"  {v.get('mc_std_per_start', 0):.3f}"
                print(line)

        # 差分計算
        diff_vals = {p: [] for p in POLICY_ORDER}
        memo_totals = []
        for graph in graphs:
            base = agg[graph].get("memo", {})
            base_t = base.get("total_per_start", 0.0)
            memo_totals.append(base_t)
            for policy in POLICY_ORDER:
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    diff_vals[policy].append(0.0)
                else:
                    diff_vals[policy].append(v["total_per_start"] - base_t)
        diff_per_pattern[pat["key"]] = {
            "diff": diff_vals,
            "memo_totals": memo_totals,
        }

    # ====================================================================
    # 横並びグラフ
    # ====================================================================
    make_all_chart(graphs, per_pattern, out_dir / "mixed_rtt_all.png")
    make_diff_all_chart(
        graphs, diff_per_pattern, out_dir / "mixed_rtt_diff_vs_memo_all.png"
    )

    # CSV
    save_summary_csv(graphs, per_pattern, out_dir / "mixed_rtt_summary.csv")

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
