#!/usr/bin/env python3
"""
提案手法 vs 既存ポリシー の実時間比較プロット (実測ベース)

ベースライン: base/auth-baseline-cache/results/.../  (none/memo/lru/arc)
提案手法:    base/proposed_cache/results/.../   (bfs_prefetch/bfs_score)

集計内容 (per-start 平均):
  - walk_time     : 実測ウォーク時間 (サーバ側の walk_time_total を全サーバ合計)
  - auth_time     : 実測認可時間  (auth_time_total)
  - prefetch_time : 提案手法のみ。controller 視点の wall_clock_sec を使う
  - total         : walk_time + auth_time + prefetch_time

出力:
  out_dir/compare_total_time.png       積み上げ棒 (walk + auth + prefetch)
  out_dir/compare_walk_time.png        walk_time のみ
  out_dir/compare_auth_time.png        auth_time のみ
  out_dir/compare_hit_rate.png         キャッシュヒット率
  out_dir/compare_summary.csv          全数値表

実行:
  cd /Users/maiko/Documents/GitHub/master-progrem
  python3 base/proposed_cache/plot_proposed_vs_baseline.py \
      --baseline-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
      --proposed-dir base/proposed_cache/results/alpha0.01_walks_100_capa_100 \
      --out-dir base/proposed_cache/output_compare
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# 日本語フォント
_JP_FONTS = [
    "Hiragino Sans",
    "Hiragino Maru Gothic Pro",
    "AppleGothic",
    "Noto Sans CJK JP",
    "IPAGothic",
    "IPAPGothic",
    "TakaoGothic",
]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# ---------------------------------------------------------------------------
# ポリシー定義 (表示順 + 色)
# ---------------------------------------------------------------------------
POLICY_ORDER = ["none", "memo", "lru", "arc", "bfs_prefetch", "bfs_score"]
POLICY_LABELS = {
    "none": "なし",
    "memo": "メモ (無制限)",
    "lru": "LRU(100)",
    "arc": "ARC(100)",
    "bfs_prefetch": "提案1: BFS Prefetch",
    "bfs_score": "提案2: BFS Score",
}
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
    "bfs_prefetch": "#9467bd",
    "bfs_score": "#17becf",
}
GRAPH_LABELS = {
    "amazon0601": "Amazon0601",
    "vldb": "VLDB",
}


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------
def parse_one_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    wt = float(d.get("walk_time_total", 0.0))
    pm = (
        d.get("prefetch_metrics", {})
        if isinstance(d.get("prefetch_metrics", None), dict)
        else {}
    )
    # hop_count = transition dict の値合計
    tr = d.get("transition", {})
    if isinstance(tr, dict):
        hop_count = int(sum(v for v in tr.values() if isinstance(v, (int, float))))
    else:
        hop_count = int(tr or 0)
    return {
        "start_node": int(d.get("controller", {}).get("start_node", -1)),
        "walk_time_total": wt,
        "auth_time_total": float(d.get("auth_time_total", 0.0)),
        "auth_calls": int(d.get("auth_calls", 0)),
        "hop_count": hop_count,
        "cache_hit": int(d.get("cache hit", 0)),
        "cache_miss": int(d.get("cache miss", 0)),
        "cache_rate": float(d.get("cache rate", 0.0)),
        "prefetch_wall_clock": float(pm.get("wall_clock_sec", 0.0)),
        "prefetch_build_max": float(pm.get("server_build_max_sec", 0.0)),
        "prefetch_inserted": int(pm.get("total_inserted", 0)),
    }


def load_dir(root: Path, policy_filter: list[str]) -> dict:
    """root/{graph}/{policy_dir}/*_global_transition.json を読む。
    policy_dir は 'lru_100', 'bfs_prefetch_K4', 'bfs_score_N100_d0.7' 等のサフィックス可。
    policy_filter にマッチする prefix のみ採用。
    """
    data: dict = defaultdict(lambda: defaultdict(list))
    if not root.is_dir():
        return data
    for g in sorted(root.iterdir()):
        if not g.is_dir():
            continue
        graph = g.name
        for pdir in sorted(g.iterdir()):
            if not pdir.is_dir():
                continue
            name = pdir.name
            # policy 名: prefix で判定 (短いものから順)
            matched = None
            for pol in policy_filter:
                if name == pol or name.startswith(pol + "_"):
                    matched = pol
                    break
            if matched is None:
                continue
            for jf in sorted(pdir.glob("*_global_transition.json")):
                rec = parse_one_json(jf)
                if rec is None:
                    continue
                data[graph][matched].append(rec)
    return data


# ---------------------------------------------------------------------------
# 集約
# ---------------------------------------------------------------------------
def aggregate(records: list, exclude_short: bool = True) -> dict:
    if not records:
        return {
            "walk": 0.0,
            "auth": 0.0,
            "prefetch": 0.0,
            "total": 0.0,
            "hit_rate": 0.0,
            "auth_calls": 0.0,
            "prefetch_size": 0.0,
            "n": 0,
        }
    rs = records
    if exclude_short:
        # Length=1 (Traceback) は walk_time が 1s 未満
        rs = [r for r in records if r["walk_time_total"] >= 1.0]
    n = len(rs)
    if n == 0:
        return {
            "walk": 0.0,
            "auth": 0.0,
            "prefetch": 0.0,
            "total": 0.0,
            "hit_rate": 0.0,
            "auth_calls": 0.0,
            "prefetch_size": 0.0,
            "n": 0,
        }
    walk = sum(r["walk_time_total"] for r in rs) / n
    auth = sum(r["auth_time_total"] for r in rs) / n
    pf = sum(r["prefetch_wall_clock"] for r in rs) / n
    total_hits = sum(r["cache_hit"] for r in rs)
    total_lookups = sum(r["cache_hit"] + r["cache_miss"] for r in rs)
    hit_rate = (total_hits / total_lookups) if total_lookups > 0 else 0.0
    ac = sum(r["auth_calls"] for r in rs) / n
    hc = sum(r["hop_count"] for r in rs) / n
    ps = sum(r["prefetch_inserted"] for r in rs) / n
    return {
        "walk": walk,
        "auth": auth,
        "prefetch": pf,
        "total": walk + auth + pf,
        "hit_rate": hit_rate,
        "auth_calls": ac,
        "hop_count": hc,
        "prefetch_size": ps,
        "n": n,
    }


# ---------------------------------------------------------------------------
# 描画ヘルパ
# ---------------------------------------------------------------------------
def _xlabels_for(pols: list[str]) -> list[str]:
    return [POLICY_LABELS.get(p, p) for p in pols]


def plot_total_stack(graphs, agg_by_graph, out_path: Path):
    """walk + auth + prefetch の積み上げ棒"""
    fig, ax = plt.subplots(figsize=(max(10, len(graphs) * 5.5), 5.6))
    pols = POLICY_ORDER
    n_g = len(graphs)
    n_p = len(pols)
    bar_width = 0.8 / n_p
    x_base = np.arange(n_g)

    for i, pol in enumerate(pols):
        offset = (i - (n_p - 1) / 2) * bar_width
        x = x_base + offset
        walks = [agg_by_graph[g].get(pol, {}).get("walk", 0.0) for g in graphs]
        auths = [agg_by_graph[g].get(pol, {}).get("auth", 0.0) for g in graphs]
        pfs = [agg_by_graph[g].get(pol, {}).get("prefetch", 0.0) for g in graphs]
        totals = [w + a + p for w, a, p in zip(walks, auths, pfs)]

        c = POLICY_COLORS.get(pol, "#888")
        ax.bar(
            x,
            walks,
            bar_width,
            color=c,
            edgecolor="white",
            linewidth=0.4,
            label=POLICY_LABELS.get(pol, pol) if i < n_p else None,
        )
        ax.bar(
            x,
            auths,
            bar_width,
            bottom=walks,
            color=c,
            edgecolor="white",
            linewidth=0.4,
            hatch="///",
            alpha=0.85,
        )
        bottoms_for_pf = [w + a for w, a in zip(walks, auths)]
        ax.bar(
            x,
            pfs,
            bar_width,
            bottom=bottoms_for_pf,
            color=c,
            edgecolor="black",
            linewidth=0.6,
            hatch="xxx",
            alpha=0.5,
        )
        for xi, t in zip(x, totals):
            if t > 0:
                ax.text(xi, t * 1.01, f"{t:.1f}", ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x_base)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=11)
    ax.set_ylabel("実時間 [s] (per start 平均)", fontsize=11)
    ax.set_title(
        "提案手法 vs 既存ポリシー — 実時間比較 (per-start 平均)\n"
        "下: walk_time / 中(///): auth_time / 上(xxx): prefetch_time (提案のみ)",
        fontsize=12,
    )
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    # 凡例: policy + 内訳
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in pols
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time"),
        Patch(facecolor="lightgray", hatch="///", label="auth_time"),
        Patch(facecolor="lightgray", hatch="xxx", alpha=0.5, label="prefetch_time"),
    ]
    leg1 = ax.legend(
        handles=policy_handles, loc="upper left", fontsize=8, title="ポリシー"
    )
    ax.add_artist(leg1)
    ax.legend(handles=section_handles, loc="upper right", fontsize=8, title="内訳")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_walk_with_prefetch(graphs, agg_by_graph, out_path: Path):
    """walk_time + prefetch_time を積み上げた棒グラフ
    既存ポリシーは walk のみ。提案手法は walk + prefetch を見せる。
    """
    fig, ax = plt.subplots(figsize=(max(10, len(graphs) * 5.5), 5.6))
    pols = POLICY_ORDER
    n_g = len(graphs)
    n_p = len(pols)
    bar_width = 0.8 / n_p
    x_base = np.arange(n_g)

    for i, pol in enumerate(pols):
        offset = (i - (n_p - 1) / 2) * bar_width
        x = x_base + offset
        walks = [agg_by_graph[g].get(pol, {}).get("walk", 0.0) for g in graphs]
        pfs = [agg_by_graph[g].get(pol, {}).get("prefetch", 0.0) for g in graphs]
        totals = [w + p for w, p in zip(walks, pfs)]

        c = POLICY_COLORS.get(pol, "#888")
        ax.bar(
            x,
            walks,
            bar_width,
            color=c,
            edgecolor="white",
            linewidth=0.4,
            label=POLICY_LABELS.get(pol, pol),
        )
        # prefetch portion (only proposed has non-zero)
        ax.bar(
            x,
            pfs,
            bar_width,
            bottom=walks,
            color=c,
            edgecolor="black",
            linewidth=0.6,
            hatch="xxx",
            alpha=0.5,
        )
        for xi, w, t in zip(x, walks, totals):
            if t > 0:
                ax.text(xi, t * 1.01, f"{t:.1f}", ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x_base)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=11)
    ax.set_ylabel("時間 [s] (per start 平均)", fontsize=11)
    ax.set_title(
        "ウォーク時間 + プリフェッチ時間 (per-start 平均)\n"
        "下: walk_time / 上(xxx): prefetch_time (提案手法のみ)",
        fontsize=12,
    )
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in pols
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time"),
        Patch(facecolor="lightgray", hatch="xxx", alpha=0.5, label="prefetch_time"),
    ]
    leg1 = ax.legend(
        handles=policy_handles, loc="upper left", fontsize=8, title="ポリシー"
    )
    ax.add_artist(leg1)
    ax.legend(handles=section_handles, loc="upper right", fontsize=8, title="内訳")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# RTT 重み付けプロット
# ---------------------------------------------------------------------------
RTT_PATTERNS = [
    {"key": "close", "label": "近い (10ms)", "rtt_ms": 10},
    {"key": "mid", "label": "中位 (60ms)", "rtt_ms": 60},
    {"key": "far", "label": "遠い (200ms)", "rtt_ms": 200},
]


def plot_rtt_weighted(graphs, agg_by_graph, out_path: Path, sharey: bool = True):
    """3つの均一RTTを後から重み付けで適用し、総時間を比較。
    モデル (Phase 1 と統一):
        sim_rtt = (hop_count + auth_calls) × RTT
        total(RTT) = walk_time + auth_time + prefetch_time + sim_rtt
      ※ 提案手法では auth_calls が削減されるため、RTT が大きいほど提案の優位性が拡大する。
    """
    n_pat = len(RTT_PATTERNS)
    fig, axes = plt.subplots(
        1,
        n_pat,
        figsize=(max(16, len(graphs) * 4 * n_pat / 2), 5.8),
        sharey=sharey,
        squeeze=False,
    )
    pols = POLICY_ORDER
    n_g = len(graphs)
    n_p = len(pols)
    bar_width = 0.8 / n_p
    x_base = np.arange(n_g)

    # 共通スケール (sharey=True 時は全 RTT パターンを通じて y 上限を決める)
    all_max = 0.0
    if sharey:
        for pat in RTT_PATTERNS:
            rtt_s = pat["rtt_ms"] / 1000.0
            for g in graphs:
                for pol in pols:
                    v = agg_by_graph[g].get(pol, {})
                    if not v or v.get("n", 0) == 0:
                        continue
                    sim_rtt = (v.get("hop_count", 0) + v.get("auth_calls", 0)) * rtt_s
                    tot = v["walk"] + v["auth"] + v["prefetch"] + sim_rtt
                    all_max = max(all_max, tot)
        ylim_top = all_max * 1.12 if all_max > 0 else 1.0

    for col, pat in enumerate(RTT_PATTERNS):
        ax = axes[0][col]
        rtt_s = pat["rtt_ms"] / 1000.0
        for i, pol in enumerate(pols):
            offset = (i - (n_p - 1) / 2) * bar_width
            x = x_base + offset
            walks, auths, pfs, sim_rtts, totals = [], [], [], [], []
            for g in graphs:
                v = agg_by_graph[g].get(pol, {})
                if not v or v.get("n", 0) == 0:
                    walks.append(0)
                    auths.append(0)
                    pfs.append(0)
                    sim_rtts.append(0)
                    totals.append(0)
                    continue
                w = v["walk"]
                a = v["auth"]
                p = v["prefetch"]
                sim_rtt = (v.get("hop_count", 0) + v.get("auth_calls", 0)) * rtt_s
                walks.append(w)
                auths.append(a)
                pfs.append(p)
                sim_rtts.append(sim_rtt)
                totals.append(w + a + p + sim_rtt)

            c = POLICY_COLORS.get(pol, "#888")
            # 4層: walk → auth → prefetch → sim_rtt
            ax.bar(
                x,
                walks,
                bar_width,
                color=c,
                edgecolor="white",
                linewidth=0.4,
                label=POLICY_LABELS.get(pol, pol) if col == 0 else None,
            )
            bot = list(walks)
            ax.bar(
                x,
                auths,
                bar_width,
                bottom=bot,
                color=c,
                edgecolor="white",
                linewidth=0.4,
                hatch="///",
                alpha=0.85,
            )
            bot = [b + a for b, a in zip(bot, auths)]
            ax.bar(
                x,
                pfs,
                bar_width,
                bottom=bot,
                color=c,
                edgecolor="black",
                linewidth=0.6,
                hatch="xxx",
                alpha=0.5,
            )
            bot = [b + p for b, p in zip(bot, pfs)]
            ax.bar(
                x,
                sim_rtts,
                bar_width,
                bottom=bot,
                color=c,
                edgecolor="black",
                linewidth=0.6,
                hatch="...",
                alpha=0.45,
            )
            for xi, t in zip(x, totals):
                if t > 0:
                    ax.text(
                        xi,
                        t * 1.005,
                        f"{t:.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=6,
                        rotation=0,
                    )

        ax.set_xticks(x_base)
        ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=10)
        ax.set_title(f"{pat['label']}", fontsize=12)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        if sharey:
            ax.set_ylim(0, ylim_top)
        if col == 0:
            ax.set_ylabel("総時間 [s] (per-start 平均)", fontsize=11)

    # 凡例
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in pols
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time (実測)"),
        Patch(facecolor="lightgray", hatch="///", label="auth_time (実測)"),
        Patch(
            facecolor="lightgray",
            hatch="xxx",
            alpha=0.5,
            label="prefetch_time (実測, 提案のみ)",
        ),
        Patch(
            facecolor="lightgray",
            hatch="...",
            alpha=0.45,
            label="sim RTT = (hops+auth_calls)×RTT",
        ),
    ]
    leg1 = axes[0][0].legend(
        handles=policy_handles, loc="upper left", fontsize=7, title="ポリシー"
    )
    axes[0][0].add_artist(leg1)
    axes[0][-1].legend(
        handles=section_handles, loc="upper right", fontsize=7, title="内訳"
    )

    fig.suptitle(
        "提案手法 vs 既存ポリシー — 均一 RTT 適用後の総時間比較\n"
        "実測 (walk + auth + prefetch) + 模擬通信 (hops + auth_calls) × RTT",
        fontsize=13,
        y=1.03,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_metric(
    graphs,
    agg_by_graph,
    metric: str,
    ylabel: str,
    title: str,
    out_path: Path,
    value_fmt: str = "{:.2f}",
    pct: bool = False,
):
    """単一メトリクスの棒グラフ"""
    fig, ax = plt.subplots(figsize=(max(10, len(graphs) * 5.5), 5.0))
    pols = POLICY_ORDER
    n_g = len(graphs)
    n_p = len(pols)
    bar_width = 0.8 / n_p
    x_base = np.arange(n_g)

    for i, pol in enumerate(pols):
        offset = (i - (n_p - 1) / 2) * bar_width
        x = x_base + offset
        vals = [agg_by_graph[g].get(pol, {}).get(metric, 0.0) for g in graphs]
        c = POLICY_COLORS.get(pol, "#888")
        ax.bar(
            x,
            vals,
            bar_width,
            color=c,
            edgecolor="white",
            linewidth=0.4,
            label=POLICY_LABELS.get(pol, pol),
        )
        for xi, v in zip(x, vals):
            if v > 0 or (pct and v >= 0):
                show = v * 100 if pct else v
                ax.text(
                    xi,
                    v * 1.01 if not pct else v + 0.01,
                    value_fmt.format(show),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )

    ax.set_xticks(x_base)
    ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=12)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.legend(loc="best", fontsize=9, ncol=2)
    if pct:
        ax.set_ylim(0, 1.15)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def save_csv(graphs, agg_by_graph, out_path: Path):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "graph",
                "policy",
                "walk_time_s",
                "auth_time_s",
                "prefetch_time_s",
                "total_s",
                "hit_rate",
                "auth_calls_avg",
                "prefetch_size_avg",
                "n_valid",
                "diff_vs_lru_total_s",
            ]
        )
        for g in graphs:
            lru_total = agg_by_graph[g].get("lru", {}).get("total", 0.0)
            for pol in POLICY_ORDER:
                v = agg_by_graph[g].get(pol)
                if not v or v["n"] == 0:
                    continue
                diff = v["total"] - lru_total
                w.writerow(
                    [
                        g,
                        pol,
                        f"{v['walk']:.4f}",
                        f"{v['auth']:.4f}",
                        f"{v['prefetch']:.4f}",
                        f"{v['total']:.4f}",
                        f"{v['hit_rate']:.4f}",
                        f"{v['auth_calls']:.1f}",
                        f"{v['prefetch_size']:.0f}",
                        v["n"],
                        f"{diff:+.4f}",
                    ]
                )
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="提案手法 vs 既存ポリシー 実時間比較")
    ap.add_argument(
        "--baseline-dir",
        type=Path,
        required=True,
        help="auth-baseline-cache の結果ディレクトリ "
        "(例: base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100)",
    )
    ap.add_argument(
        "--proposed-dir",
        type=Path,
        required=True,
        help="proposed_cache の結果ディレクトリ "
        "(例: base/proposed_cache/results/alpha0.01_walks_100_capa_100)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("base/proposed_cache/output_compare"),
    )
    args = ap.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 読み込み
    baseline = load_dir(args.baseline_dir, ["none", "memo", "lru", "arc"])
    proposed = load_dir(args.proposed_dir, ["bfs_prefetch", "bfs_score"])

    # graph の集合
    all_graphs = sorted(set(baseline.keys()) | set(proposed.keys()))
    graph_order = ["amazon0601", "vldb"]
    graphs = [g for g in graph_order if g in all_graphs] + [
        g for g in all_graphs if g not in graph_order
    ]
    if not graphs:
        print(
            f"[ERROR] no graphs found in baseline={args.baseline_dir} or proposed={args.proposed_dir}"
        )
        return

    # 集約
    agg_by_graph: dict = {}
    for g in graphs:
        agg_by_graph[g] = {}
        for pol in POLICY_ORDER:
            src = baseline if pol in ("none", "memo", "lru", "arc") else proposed
            agg_by_graph[g][pol] = aggregate(src.get(g, {}).get(pol, []))

    # コンソール表示
    print("\n" + "=" * 110)
    print(f"{'提案手法 vs 既存ポリシー — 実時間比較 (per-start 平均)':^110}")
    print("=" * 110)
    for g in graphs:
        print(f"\n--- {GRAPH_LABELS.get(g, g)} ---")
        print(
            f"{'policy':<25}{'walk[s]':>9}{'auth[s]':>9}{'prefetch[s]':>13}"
            f"{'total[s]':>10}{'auth_calls':>12}{'hit_rate':>10}{'vs LRU':>10}{'n':>4}"
        )
        print("-" * 110)
        lru_total = agg_by_graph[g].get("lru", {}).get("total", 0.0)
        for pol in POLICY_ORDER:
            v = agg_by_graph[g].get(pol)
            if not v or v["n"] == 0:
                continue
            diff = v["total"] - lru_total
            marker = ""
            if pol in ("bfs_prefetch", "bfs_score") and diff < 0:
                marker = "  ★faster"
            print(
                f"{POLICY_LABELS.get(pol, pol):<25}"
                f"{v['walk']:>9.2f}"
                f"{v['auth']:>9.2f}"
                f"{v['prefetch']:>13.3f}"
                f"{v['total']:>10.2f}"
                f"{v['auth_calls']:>12.0f}"
                f"{v['hit_rate']*100:>9.1f}%"
                f"{diff:>+10.2f}"
                f"{v['n']:>4d}{marker}"
            )

    # 出力 — 1. 基本プロット
    save_csv(graphs, agg_by_graph, out_dir / "compare_summary.csv")
    plot_total_stack(graphs, agg_by_graph, out_dir / "compare_total_time.png")
    # walk_time に prefetch_time を含めた版
    plot_walk_with_prefetch(graphs, agg_by_graph, out_dir / "compare_walk_time.png")
    plot_metric(
        graphs,
        agg_by_graph,
        "auth",
        "auth_time [s]",
        "認可時間 (per-start 平均)",
        out_dir / "compare_auth_time.png",
    )
    plot_metric(
        graphs,
        agg_by_graph,
        "prefetch",
        "prefetch_time [s]",
        "プリフェッチ時間 (per-start 平均, 提案手法のみ)",
        out_dir / "compare_prefetch_time.png",
        value_fmt="{:.3f}",
    )
    plot_metric(
        graphs,
        agg_by_graph,
        "hit_rate",
        "キャッシュヒット率",
        "キャッシュヒット率 (per-start 平均)",
        out_dir / "compare_hit_rate.png",
        value_fmt="{:.1f}%",
        pct=True,
    )

    # 出力 — 2. RTT 重み付けプロット (3パターン横並び)
    plot_rtt_weighted(
        graphs, agg_by_graph, out_dir / "compare_rtt_weighted.png", sharey=True
    )
    plot_rtt_weighted(
        graphs, agg_by_graph, out_dir / "compare_rtt_weighted_indep_y.png", sharey=False
    )

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
