#!/usr/bin/env python3
"""
auth-baseline-cache の結果を読み込み、各ポリシーの
"距離別 RTT を加味した総処理時間" を計算・棒グラフ化するスクリプト。

モデル (均一RTT・ベースライン):
  リモート通信回数 = auth_calls + hop_count
      auth_calls : リモート認可確認回数 (= cache miss 数, JSON `auth_calls`)
      hop_count  : ランダムウォーク中の hop 数 (= sum of `transition`)

  シミュレートされた通信時間 = (auth_calls + hop_count) × RTT_pattern
  総処理時間                  = walk_time_total + シミュレート通信時間

  ※ 各 hop で「データ取得用のリモート通信 (1 RTT)」が発生し、それに加えて
     auth キャッシュにミスした hop では「認可確認用のリモート通信 (+1 RTT)」が
     さらに必要になる。すなわち cache 効果は「auth 側の RTT 削減」に現れる。

距離パターン (均一RTT):
  - 近い (close)  : 10 ms   …国内DC間 / イントラリージョン
  - 中位 (mid)    : 60 ms   …アジア圏内 (日本↔HK 53ms, 日本↔SG 73ms の中央値)
  - 遠い (far)    : 200 ms  …大陸間 (日本↔米東 163ms, 日本↔欧州 235ms の中央値)

出力 (既存の cache_comparison_walk_time.png のスタイルに合わせ、
      walk_time の上に hop×RTT を積み上げた版):
  <results-dir>/cache_comparison_walk_time_rtt_close.png  均一・近い (10ms)
  <results-dir>/cache_comparison_walk_time_rtt_mid.png    均一・中位 (60ms)
  <results-dir>/cache_comparison_walk_time_rtt_far.png    均一・遠い (200ms)
  <results-dir>/cache_comparison_walk_time_rtt_all.png    3パターン横並び (1×3)

差分グラフ (memo を基準: diff_total = total - memo_total):
  <results-dir>/cache_diff_vs_memo_close.png              均一・近い (10ms)
  <results-dir>/cache_diff_vs_memo_mid.png                均一・中位 (60ms)
  <results-dir>/cache_diff_vs_memo_far.png                均一・遠い (200ms)
  <results-dir>/cache_diff_vs_memo_all.png                差分3パターン横並び (1×3)

実行例:
  python3 base/auth-baseline-cache/results/plot_total_time.py \
      --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100
      
"""

from __future__ import annotations

import argparse
import json
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
# 設定
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

# 距離パターン (均一 RTT モデル)
RTT_PATTERNS = [
    {
        "key": "close",
        "label": "近い (10ms)",
        "rtt_ms": 10,
        "fname": "cache_comparison_walk_time_rtt_close.png",
    },
    {
        "key": "mid",
        "label": "中位 (60ms)",
        "rtt_ms": 60,
        "fname": "cache_comparison_walk_time_rtt_mid.png",
    },
    {
        "key": "far",
        "label": "遠い (200ms)",
        "rtt_ms": 200,
        "fname": "cache_comparison_walk_time_rtt_far.png",
    },
]


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------
def load_results(results_dir: Path) -> dict:
    """
    results/{graph}/{policy}_{capacity}/*_global_transition.json を全部読み込む。

    戻り値:
        data[graph][policy] = list of {
            walk_time_total, hop_count, auth_calls, capacity, start_node
        }

      hop_count  = JSON の `transition` (dict) の値合計
                    (= ランダムウォーク中に実際にエッジを越えた回数)
      auth_calls = JSON の `auth_calls`
                    (= cache miss 数 = リモート認可確認のリモート通信回数)
    """
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

                # hop_count = transition dict の値合計 (= ウォークが通ったエッジ数)
                # transition のキーには "edge_X_Y" と node id が混在するが、
                # 値の合計で hop 数を表す設計になっている。
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

                # Length=1 (Traceback) を除外: walk_time が極端に短い run は除く
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


def aggregate(records: list, rtt_ms: float) -> dict:
    """
    同一 (graph, policy) の records を集約し、与えられた RTT パターンで
    シミュレート総時間を返す。

    モデル: rt_count = auth_calls + hop_count
            sim_time = rt_count × RTT
            total    = walk_time + sim_time

    返り値:
      walk_per_start:           per-start 平均の walk_time [s]
      sim_hop_per_start:        per-start 平均の (auth_calls+hops) × RTT [s]
      total_per_start:          walk_per_start + sim_hop_per_start [s]
      walk_sum:                 全有効 start の walk_time 累積 [s]
      sim_hop_sum:              全有効 start の sim_time 累積 [s]
      total_sum:                walk_sum + sim_hop_sum [s]
      hops_sum:                 全有効 start の hop_count 累積
      auth_calls_sum:           全有効 start の auth_calls 累積
      rt_count_sum:             auth_calls_sum + hops_sum (リモート通信総数)
      n_valid:                  有効 start 数
    """
    rtt_sec = rtt_ms / 1000.0

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
        }

    by_cap: dict = defaultdict(list)
    for r in records:
        by_cap[r["capacity"]].append(r)

    cap_walk_per_start = []
    cap_sim_hop_per_start = []
    walk_sum = 0.0
    sim_hop_sum = 0.0
    hops_sum = 0
    auth_calls_sum = 0
    n_valid = 0

    for cap_records in by_cap.values():
        n = len(cap_records)
        if n == 0:
            continue
        walk_total = sum(r["walk_time_total"] for r in cap_records)
        hops_total = sum(r["hop_count"] for r in cap_records)
        auth_total = sum(r.get("auth_calls", 0) for r in cap_records)
        rt_total = hops_total + auth_total
        sim_hop_total = rt_total * rtt_sec

        cap_walk_per_start.append(walk_total / n)
        cap_sim_hop_per_start.append(sim_hop_total / n)
        walk_sum += walk_total
        sim_hop_sum += sim_hop_total
        hops_sum += hops_total
        auth_calls_sum += auth_total
        n_valid += n

    walk_per_start = float(np.mean(cap_walk_per_start)) if cap_walk_per_start else 0.0
    sim_hop_per_start = (
        float(np.mean(cap_sim_hop_per_start)) if cap_sim_hop_per_start else 0.0
    )

    return {
        "walk_per_start": walk_per_start,
        "sim_hop_per_start": sim_hop_per_start,
        "total_per_start": walk_per_start + sim_hop_per_start,
        "walk_sum": walk_sum,
        "sim_hop_sum": sim_hop_sum,
        "total_sum": walk_sum + sim_hop_sum,
        "hops_sum": hops_sum,
        "auth_calls_sum": auth_calls_sum,
        "rt_count_sum": hops_sum + auth_calls_sum,
        "n_valid": n_valid,
    }


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
def _annotate(ax, bars, vals, fmt="{:.2f}", fontsize=8):
    for bar, val in zip(bars, vals):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() * 1.01,
                fmt.format(val),
                ha="center",
                va="bottom",
                fontsize=fontsize,
            )


def _draw_stacked(ax, graphs, walk_vals: dict, sim_vals: dict, bar_width=0.18):
    """各 policy について walk と sim_hop を積み上げて描画。"""
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
        _annotate(ax, bars_a, totals, fmt="{:.2f}")
    return x


def _add_legends(ax):
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in POLICY_ORDER
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time"),
        Patch(
            facecolor="lightgray",
            hatch="///",
            label="sim_time = (auth_calls + hops) × RTT",
        ),
    ]
    leg1 = ax.legend(
        handles=policy_handles, loc="upper left", fontsize=8, title="ポリシー"
    )
    ax.add_artist(leg1)
    ax.legend(handles=section_handles, loc="upper right", fontsize=8, title="内訳")


def make_pattern_chart(
    graphs: list,
    walk_vals: dict,
    sim_vals: dict,
    rtt_ms: float,
    pattern_label: str,
    out_path: Path,
) -> None:
    """1パターン用の積み上げ棒グラフを保存。"""
    fig, ax = plt.subplots(figsize=(max(8, len(graphs) * 2.4), 5.5))

    x = _draw_stacked(ax, graphs, walk_vals, sim_vals)

    ax.set_xticks(x)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
    ax.set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    ax.set_title(
        f"キャッシュポリシー別 ウォーク時間 + 通信時間  ({pattern_label})\n"
        f"均一RTT = {rtt_ms:g} ms  /  総時間 = walk_time + (auth_calls + hop_count) × RTT\n"
        f"(per-start平均 / capacity平均, Length=1除外)",
        fontsize=11,
    )
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    _add_legends(ax)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[saved] {out_path}")


def make_all_patterns_chart(
    graphs: list,
    per_pattern: dict,
    out_path: Path,
) -> None:
    """
    3パターン (close / mid / far) を 1 枚に横並びで描画。
    per_pattern[pattern_key] = {"walk": walk_vals, "sim": sim_vals, "rtt_ms": rtt}
    """
    n = len(RTT_PATTERNS)
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.2), 5.5), sharey=False
    )
    if n == 1:
        axes = [axes]

    # Y 軸を揃えるため、最大値を計算
    all_max = 0.0
    for pat in RTT_PATTERNS:
        v = per_pattern[pat["key"]]
        for policy in POLICY_ORDER:
            w = v["walk"].get(policy, [])
            a = v["sim"].get(policy, [])
            for wi, ai in zip(w, a):
                all_max = max(all_max, wi + ai)
    ylim_top = all_max * 1.15 if all_max > 0 else 1.0

    for ax, pat in zip(axes, RTT_PATTERNS):
        v = per_pattern[pat["key"]]
        x = _draw_stacked(ax, graphs, v["walk"], v["sim"])
        ax.set_xticks(x)
        ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
        ax.set_title(f"{pat['label']}", fontsize=12)
        ax.set_ylim(0, ylim_top)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

    axes[0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    _add_legends(axes[-1])

    fig.suptitle(
        "均一RTTモデル: 距離別パターン比較  "
        "(総時間 = walk_time + (auth_calls + hop_count) × RTT)",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# 差分グラフ (memo 基準)
# ---------------------------------------------------------------------------
def _draw_diff(ax, graphs, diff_vals: dict, bar_width=0.18):
    """policy ごとに memo 基準の差分を棒で並べる。"""
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
            # 0 でなければラベル付与
            if abs(val) < 1e-6:
                continue
            ha = "center"
            offset_y = 1.5 if val >= 0 else -1.5
            va = "bottom" if val >= 0 else "top"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset_y * (0.005 * max(1.0, abs(val))),
                f"{val:+.2f}",
                ha=ha,
                va=va,
                fontsize=8,
            )
    ax.axhline(0, color="black", linewidth=0.8)
    return x


def make_diff_chart(
    graphs: list,
    diff_vals: dict,
    rtt_ms: float,
    pattern_label: str,
    memo_totals: list,
    out_path: Path,
) -> None:
    """1パターン用の差分棒グラフ。memo_totals は各 graph の memo 総時間。"""
    fig, ax = plt.subplots(figsize=(max(8, len(graphs) * 2.4), 5.5))
    x = _draw_diff(ax, graphs, diff_vals)

    # 各グラフの x ラベルに memo 基準値を付記
    xlabels = [
        f"{GRAPH_LABELS.get(g, g)}\n(memo={mt:.2f}s)"
        for g, mt in zip(graphs, memo_totals)
    ]
    ax.set_xticks(x)
    ax.set_xticklabels(xlabels, fontsize=10)
    ax.set_ylabel(
        "memo との差分 [s] (per start_node 平均, +=memoより遅い)", fontsize=11
    )
    ax.set_title(
        f"同パターン memo 基準の総処理時間差分  ({pattern_label})\n"
        f"均一RTT = {rtt_ms:g} ms  /  diff = total - memo_total  (同じ {rtt_ms:g}ms 内で比較)",
        fontsize=11,
    )
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(loc="best", fontsize=9, title="ポリシー")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"[saved] {out_path}")


def make_all_diff_chart(
    graphs: list,
    diff_per_pattern: dict,
    out_path: Path,
) -> None:
    """3パターンの差分を横並びで描画。y軸は全パターンで共有(共通スケール)。"""
    n = len(RTT_PATTERNS)
    # sharey=True で y 軸を共有
    fig, axes = plt.subplots(
        1, n, figsize=(max(14, len(graphs) * 4.2), 5.5), sharey=True
    )
    if n == 1:
        axes = [axes]

    # 全パターン・全ポリシーから最大/最小を取って共通の y 範囲を計算
    all_max = 0.0
    all_min = 0.0
    for pat in RTT_PATTERNS:
        v = diff_per_pattern[pat["key"]]
        for policy in POLICY_ORDER:
            for val in v["diff_per_start"].get(policy, []):
                all_max = max(all_max, val)
                all_min = min(all_min, val)
    pad = max(abs(all_max), abs(all_min)) * 0.18 if (all_max or all_min) else 1.0
    ylim_top = all_max + pad
    ylim_bot = all_min - pad if all_min < 0 else -pad

    for ax, pat in zip(axes, RTT_PATTERNS):
        v = diff_per_pattern[pat["key"]]
        memo_totals = v.get("memo_totals", [0.0] * len(graphs))
        x = _draw_diff(ax, graphs, v["diff_per_start"])
        xlabels = [
            f"{GRAPH_LABELS.get(g, g)}\n(memo={mt:.2f}s)"
            for g, mt in zip(graphs, memo_totals)
        ]
        ax.set_xticks(x)
        ax.set_xticklabels(xlabels, fontsize=9)
        ax.set_title(f"{pat['label']}  /  vs memo@{pat['rtt_ms']:g}ms", fontsize=12)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.set_ylim(ylim_bot, ylim_top)

    axes[0].set_ylabel(
        "memo との差分 [s] (per start_node 平均, +=memoより遅い)", fontsize=11
    )
    axes[-1].legend(loc="best", fontsize=9, title="ポリシー")

    fig.suptitle(
        "同パターン内 memo 基準の総処理時間差分: 距離別比較 (y軸共通スケール)  "
        "(diff = total(RTT) − memo_total(同RTT))",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="均一RTTモデル: 距離別 RTT を加算した総処理時間を棒グラフ化"
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
        default=None,
        help="画像の出力先 (default: --results-dir と同じ)",
    )
    args = ap.parse_args()

    results_dir = args.results_dir
    out_dir = args.out_dir if args.out_dir else results_dir
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

    # サマリ表示用に集約結果を保持
    per_pattern: dict = {}

    for pat in RTT_PATTERNS:
        rtt_ms = pat["rtt_ms"]
        agg: dict = {}
        for graph in graphs:
            agg[graph] = {}
            for policy in POLICY_ORDER:
                agg[graph][policy] = aggregate(data[graph].get(policy, []), rtt_ms)

        walk_vals: dict = {
            p: [agg[g][p]["walk_per_start"] for g in graphs] for p in POLICY_ORDER
        }
        sim_vals: dict = {
            p: [agg[g][p]["sim_hop_per_start"] for g in graphs] for p in POLICY_ORDER
        }

        per_pattern[pat["key"]] = {
            "walk": walk_vals,
            "sim": sim_vals,
            "rtt_ms": rtt_ms,
            "agg": agg,
        }

        # 個別出力
        make_pattern_chart(
            graphs,
            walk_vals,
            sim_vals,
            rtt_ms,
            pat["label"],
            out_path=out_dir / pat["fname"],
        )

        # コンソールサマリ
        print(f"\n===== 集約 (均一RTT = {rtt_ms} ms : {pat['label']}) =====")
        print(f"  sim_time = (auth_calls + hop_count) × RTT")
        print(
            f"{'グラフ':<14} {'ポリシー':<8} {'walk/s':>10} "
            f"{'simRT/s':>10} {'total/s':>10} "
            f"{'hops':>9} {'auth_calls':>11} {'rt_total':>10} {'n':>3}"
        )
        print("-" * 100)
        for graph in graphs:
            for policy in POLICY_ORDER:
                v = agg[graph][policy]
                if v["n_valid"] == 0:
                    continue
                print(
                    f"{GRAPH_LABELS.get(graph, graph):<14} "
                    f"{policy:<8} "
                    f"{v['walk_per_start']:>10.3f} "
                    f"{v['sim_hop_per_start']:>10.3f} "
                    f"{v['total_per_start']:>10.3f} "
                    f"{v['hops_sum']:>9d} "
                    f"{v['auth_calls_sum']:>11d} "
                    f"{v['rt_count_sum']:>10d} "
                    f"{v['n_valid']:>3d}"
                )

    # 3パターン横並び比較
    make_all_patterns_chart(
        graphs,
        per_pattern,
        out_path=out_dir / "cache_comparison_walk_time_rtt_all.png",
    )

    # ---------------------------------------------------------------------
    # memo を基準とした差分 (diff = total_policy - total_memo)
    # ---------------------------------------------------------------------
    baseline_policy = "memo"
    diff_per_pattern: dict = {}

    print(f"\n{'='*110}")
    print(
        f"{'同パターン内 memo を基準とした差分秒 (diff = total(RTT) - memo_total(同じRTT))':^110}"
    )
    print(f"{'='*110}")

    for pat in RTT_PATTERNS:
        rtt_ms = pat["rtt_ms"]
        agg = per_pattern[pat["key"]]["agg"]

        diff_vals: dict = {p: [] for p in POLICY_ORDER}
        diff_sum_vals: dict = {p: [] for p in POLICY_ORDER}
        memo_totals: list = []  # 各 graph の memo 総時間 (同じ RTT)
        memo_total_sums: list = []
        for graph in graphs:
            base = agg[graph].get(baseline_policy, {})
            base_total = base.get("total_per_start", 0.0)
            base_total_sum = base.get("total_sum", 0.0)
            memo_totals.append(base_total)
            memo_total_sums.append(base_total_sum)
            for policy in POLICY_ORDER:
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    diff_vals[policy].append(0.0)
                    diff_sum_vals[policy].append(0.0)
                else:
                    diff_vals[policy].append(v["total_per_start"] - base_total)
                    diff_sum_vals[policy].append(v["total_sum"] - base_total_sum)

        diff_per_pattern[pat["key"]] = {
            "diff_per_start": diff_vals,
            "diff_sum": diff_sum_vals,
            "memo_totals": memo_totals,
            "memo_total_sums": memo_total_sums,
            "rtt_ms": rtt_ms,
            "label": pat["label"],
        }

        # コンソール表示
        print(
            f"\n--- 均一RTT = {rtt_ms} ms : {pat['label']}   (基準: memo@{rtt_ms}ms) ---"
        )
        print(
            f"{'グラフ':<14} {'比較対象':<18} {'total/s [s]':>12} "
            f"{'memo差/s [s]':>13} {'memo比':>8} {'total_sum [s]':>14} {'memo差_sum [s]':>15}"
        )
        print("-" * 100)
        for graph in graphs:
            base = agg[graph].get(baseline_policy, {})
            base_t = base.get("total_per_start", 0.0)
            base_ts = base.get("total_sum", 0.0)
            # memo (基準) を先頭に
            print(
                f"{GRAPH_LABELS.get(graph, graph):<14} "
                f"{'memo (基準)':<18} "
                f"{base_t:>12.3f} "
                f"{0.0:>+13.3f} "
                f"{1.0:>7.2f}x "
                f"{base_ts:>14.3f} "
                f"{0.0:>+15.3f}"
            )
            for policy in POLICY_ORDER:
                if policy == baseline_policy:
                    continue
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    continue
                tot = v["total_per_start"]
                tot_s = v["total_sum"]
                diff = tot - base_t
                diff_s = tot_s - base_ts
                ratio = tot / base_t if base_t > 0 else 0.0
                compare_label = f"{policy:<6} vs memo"
                print(
                    f"{GRAPH_LABELS.get(graph, graph):<14} "
                    f"{compare_label:<18} "
                    f"{tot:>12.3f} "
                    f"{diff:>+13.3f} "
                    f"{ratio:>7.2f}x "
                    f"{tot_s:>14.3f} "
                    f"{diff_s:>+15.3f}"
                )

        # 個別の差分グラフ (同パターン memo 基準)
        make_diff_chart(
            graphs,
            diff_vals,
            rtt_ms,
            pat["label"],
            memo_totals,
            out_path=out_dir / f"cache_diff_vs_memo_{pat['key']}.png",
        )

    # 差分3パターン横並び
    make_all_diff_chart(
        graphs,
        diff_per_pattern,
        out_path=out_dir / "cache_diff_vs_memo_all.png",
    )

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
