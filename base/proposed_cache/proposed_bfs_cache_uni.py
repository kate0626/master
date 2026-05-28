#!/usr/bin/env python3
"""
提案手法: BFS 距離ベースのキャッシュ戦略 (均一 RTT モデル)

ベースデータ:
  base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100/{graph}/none_100/
  の transition.json (キャッシュなしランの記録) を使う。
  ここから access / attempts / 隣接情報 / start_node が得られる。

提案ポリシー:
  - 提案1: BFS K-hop prefetch
      start_node から BFS 距離 ≤ K のノードを「事前キャッシュ」する。
      ウォーク中の auth lookup について、対象ノードが BFS_K_ball に含まれていれば HIT。
      → ウォーク開始前に固定セットを cache に展開する静的プリフェッチ。
  - 提案2: BFS × frequency 加重スコア型キャッシュ (容量 N)
      各ノード v に対し
          score(v) = attempts(v) × decay^BFS_dist(v)
      を計算し、上位 N 個を cache。残りは miss。
      → 「BFS で近く、かつ高頻度」のノードを優先保持。

均一 RTT モデル:
  total_time(per start) = walk_time + (hop_count + auth_calls_eff) × RTT
    hop_count       : transition 合計 (経路 hop 数)
    auth_calls_eff  : 提案ポリシー下での cache miss 数 (HIT は通信不要)
    walk_time       : none policy で実測された値を流用 (ウォーク経路自体は同じ)

比較対象:
  既存の none / memo / lru / arc ポリシー (実測 auth_calls から逆算)

出力:
  out_dir/proposed_summary.csv          全数値表
  out_dir/proposed_total_time.png       距離別パターンごとの総処理時間バー
  out_dir/proposed_hitrate_breakdown.png ヒット率内訳
  out_dir/proposed_diff_vs_lru.png      LRU 比較の差分グラフ

実行:
  cd /Users/maiko/Documents/GitHub/master-progrem
  python3 base/proposed_cache/proposed_bfs_cache_uni.py \
      --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
      --out-dir base/proposed_cache/output_uni
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict, deque
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# 日本語フォント
_JP_FONTS = [
    "Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
    "Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "TakaoGothic",
]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# ---------------------------------------------------------------------------
# RTT パターン (Phase 1 と統一)
# ---------------------------------------------------------------------------
RTT_PATTERNS = [
    {"key": "close", "label": "近い (10ms)",  "rtt_ms": 10},
    {"key": "mid",   "label": "中位 (60ms)",  "rtt_ms": 60},
    {"key": "far",   "label": "遠い (200ms)", "rtt_ms": 200},
]

# 提案1: BFS K-hop prefetch の K 値群
BFS_K_CANDIDATES = [3, 5, 7, 10, 15]

# 提案2: BFS × frequency スコア型キャッシュの容量
RTT_AWARE_CAPACITIES = [100, 200, 500]
# 距離減衰率 (BFS 距離が 1 増えるごとに score を γ 倍する)
BFS_DECAY = 0.7

# ---------------------------------------------------------------------------
# 既存ポリシーと色
# ---------------------------------------------------------------------------
EXISTING_POLICIES = ["none", "memo", "lru", "arc"]
POLICY_LABELS = {
    "none": "なし",
    "memo": "memo",
    "lru": "LRU(100)",
    "arc": "ARC(100)",
}
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
}
PROPOSED_COLORS_BFS = ["#9467bd", "#c5b0d5", "#8c564b", "#c49c94", "#e377c2"]
PROPOSED_COLORS_RTT = ["#17becf", "#9edae5", "#bcbd22"]

GRAPH_LABELS = {
    "amazon0601": "Amazon0601",
    "vldb": "VLDB",
}


# ---------------------------------------------------------------------------
# 1 件の transition.json を解析: adj / BFS / attempts / hop / walk_time
# ---------------------------------------------------------------------------
def parse_run(path: Path) -> dict | None:
    d = json.loads(path.read_text(encoding="utf-8"))
    ctrl = d.get("controller", {})
    start = str(ctrl.get("start_node", "?"))
    walks = int(ctrl.get("walks", 0))

    walk_time = float(d.get("walk_time_total", 0.0))
    if walk_time < 1.0:
        return None  # Length=1 Traceback を除外

    # hop_count
    tr = d.get("transition", {})
    if isinstance(tr, dict):
        hop_count = int(sum(v for v in tr.values() if isinstance(v, (int, float))))
    else:
        hop_count = int(tr or 0)

    # attempts: ノード v に対する auth 確認回数 (cache 無効時の miss 数に相当)
    attempts: dict[str, int] = {}
    for v, c in d.get("authorization_attempts", {}).items():
        attempts[str(v)] = int(c)

    # 隣接リスト (transition の "src->edge_a_b" から無向グラフを構築)
    adj: dict[str, set[str]] = defaultdict(set)
    edge_pattern = re.compile(r"^(.+?)->edge_(.+?)_(.+)$")
    for key in tr:
        m = edge_pattern.match(key)
        if not m:
            continue
        src, a, b = m.group(1), m.group(2), m.group(3)
        dst = b if src == a else a
        adj[src].add(dst)
        adj[dst].add(src)

    # BFS 距離 (start から)
    dist: dict[str, int] = {start: 0}
    q = deque([start])
    while q:
        u = q.popleft()
        for w in adj.get(u, ()):
            if w not in dist:
                dist[w] = dist[u] + 1
                q.append(w)

    return {
        "start": start,
        "walks": walks,
        "walk_time": walk_time,
        "hop_count": hop_count,
        "attempts": attempts,
        "adj": adj,
        "dist": dist,
        "total_attempts": sum(attempts.values()),
    }


# ---------------------------------------------------------------------------
# 提案1: BFS K-hop prefetch のシミュレーション
# ---------------------------------------------------------------------------
def simulate_bfs_prefetch(run: dict, K: int) -> dict:
    """BFS 距離 ≤ K のノードを事前 cache に入れた場合の hit/miss を計算。"""
    cache_set = {v for v, d in run["dist"].items() if d <= K}
    hits = sum(c for v, c in run["attempts"].items() if v in cache_set)
    misses = run["total_attempts"] - hits
    return {
        "cache_size": len(cache_set),
        "hits": hits,
        "misses": misses,
        "hit_rate": hits / run["total_attempts"] if run["total_attempts"] > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# 提案2: BFS × frequency スコアの上位 N をキャッシュ
# ---------------------------------------------------------------------------
def simulate_bfs_rtt_aware(run: dict, capacity: int, decay: float = BFS_DECAY) -> dict:
    """
    各ノード v に対し score(v) = attempts(v) × decay^BFS_dist(v)
    のスコアで上位 capacity 個を選び、それらは hit と扱う。
    BFS 距離不明のノードは decay^∞ ≒ 0 として優先度最下位。
    """
    scored = []
    for v, c in run["attempts"].items():
        d = run["dist"].get(v, None)
        if d is None:
            score = 0.0  # 到達不能ノードはスコア 0
        else:
            score = c * (decay**d)
        scored.append((score, v, c))
    scored.sort(key=lambda x: -x[0])
    cache_set = {v for _, v, _ in scored[:capacity]}
    hits = sum(c for _, v, c in scored[:capacity])
    misses = run["total_attempts"] - hits
    return {
        "cache_size": len(cache_set),
        "hits": hits,
        "misses": misses,
        "hit_rate": hits / run["total_attempts"] if run["total_attempts"] > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# 既存ポリシー (memo/lru/arc) の auth_calls を読む — RTT-only 比較用
# ---------------------------------------------------------------------------
def read_existing_policy_stats(graph_dir: Path, policy: str) -> dict:
    pol_dir = graph_dir / f"{policy}_100"
    if not pol_dir.is_dir():
        return {}
    rows = []
    for jf in sorted(pol_dir.glob("start=*_global_transition.json")):
        try:
            d = json.loads(jf.read_text())
        except Exception:
            continue
        wt = float(d.get("walk_time_total", 0.0))
        if wt < 1.0:
            continue
        tr = d.get("transition", {})
        if isinstance(tr, dict):
            hops = int(sum(v for v in tr.values() if isinstance(v, (int, float))))
        else:
            hops = int(tr or 0)
        auth = int(d.get("auth_calls", 0))
        hit = int(d.get("cache hit", 0))
        miss = int(d.get("cache miss", 0))
        rows.append({
            "start": str(d.get("controller", {}).get("start_node", "?")),
            "walk_time": wt,
            "hop_count": hops,
            "auth_calls": auth,
            "cache_hit": hit,
            "cache_miss": miss,
        })
    return {"rows": rows}


# ---------------------------------------------------------------------------
# 集約: 1 graph 分の全 start ノードについて、各ポリシーの (walk_time, hop, auth_calls) を集計
# ---------------------------------------------------------------------------
def aggregate_graph(graph_dir: Path) -> dict:
    none_dir = graph_dir / "none_100"
    if not none_dir.is_dir():
        return {}

    # 提案手法用: none の transition.json を全部読む (start ごとの run)
    proposed_per_start: list[dict] = []
    for jf in sorted(none_dir.glob("start=*_global_transition.json")):
        r = parse_run(jf)
        if r is None:
            continue
        # 各提案ポリシーで hits/misses を計算
        sim_bfs = {f"prefetch_K={K}": simulate_bfs_prefetch(r, K) for K in BFS_K_CANDIDATES}
        sim_rtt = {
            f"rtt_aware_N={cap}": simulate_bfs_rtt_aware(r, cap) for cap in RTT_AWARE_CAPACITIES
        }
        proposed_per_start.append({
            "start": r["start"],
            "walk_time": r["walk_time"],
            "hop_count": r["hop_count"],
            "total_attempts": r["total_attempts"],
            "sim_bfs": sim_bfs,
            "sim_rtt": sim_rtt,
        })

    # 既存ポリシー
    existing = {}
    for pol in EXISTING_POLICIES:
        existing[pol] = read_existing_policy_stats(graph_dir, pol)

    return {
        "proposed_per_start": proposed_per_start,
        "existing": existing,
    }


# ---------------------------------------------------------------------------
# 総時間計算 (per-start 平均) — モデル: total = walk_time + (hops + auth) × RTT
# ---------------------------------------------------------------------------
def compute_totals(graph_data: dict, rtt_ms: float) -> dict:
    """
    返り値:
      {
        "policies": [policy_name, ...],
        "walk_per_start": {policy: float},
        "sim_per_start": {policy: float},       # (hops + auth) × RTT [s]
        "total_per_start": {policy: float},
        "hit_rate": {policy: float},
        "auth_calls_avg": {policy: float},      # per-start 平均
        "hop_count_avg": {policy: float},
      }
    """
    rtt_sec = rtt_ms / 1000.0
    out_walk = {}
    out_sim = {}
    out_total = {}
    out_hitrate = {}
    out_auth = {}
    out_hops = {}
    names: list[str] = []

    # 既存ポリシー
    for pol in EXISTING_POLICIES:
        rows = graph_data["existing"].get(pol, {}).get("rows", [])
        if not rows:
            continue
        n = len(rows)
        wt = sum(r["walk_time"] for r in rows) / n
        hops = sum(r["hop_count"] for r in rows) / n
        auth = sum(r["auth_calls"] for r in rows) / n
        total_lookups = sum(r["cache_hit"] + r["cache_miss"] for r in rows)
        total_hits = sum(r["cache_hit"] for r in rows)
        hit_rate = total_hits / total_lookups if total_lookups > 0 else 0.0
        sim = (hops + auth) * rtt_sec
        out_walk[pol] = wt
        out_sim[pol] = sim
        out_total[pol] = wt + sim
        out_hitrate[pol] = hit_rate
        out_auth[pol] = auth
        out_hops[pol] = hops
        names.append(pol)

    # 提案手法: walk_time は none の値を流用 (ウォーク経路は同じ)
    pp = graph_data["proposed_per_start"]
    if pp:
        n = len(pp)
        wt_baseline = sum(r["walk_time"] for r in pp) / n
        hops_avg = sum(r["hop_count"] for r in pp) / n

        # 提案1: BFS prefetch
        for K in BFS_K_CANDIDATES:
            key = f"BFS_K={K}"
            misses = [r["sim_bfs"][f"prefetch_K={K}"]["misses"] for r in pp]
            hits = [r["sim_bfs"][f"prefetch_K={K}"]["hits"] for r in pp]
            totals = [r["total_attempts"] for r in pp]
            avg_miss = sum(misses) / n
            avg_hits = sum(hits) / n
            avg_total = sum(totals) / n
            hit_rate = (sum(hits) / sum(totals)) if sum(totals) > 0 else 0.0
            sim = (hops_avg + avg_miss) * rtt_sec
            out_walk[key] = wt_baseline
            out_sim[key] = sim
            out_total[key] = wt_baseline + sim
            out_hitrate[key] = hit_rate
            out_auth[key] = avg_miss
            out_hops[key] = hops_avg
            names.append(key)

        # 提案2: BFS-RTT-aware
        for cap in RTT_AWARE_CAPACITIES:
            key = f"BFS-Score(top{cap})"
            misses = [r["sim_rtt"][f"rtt_aware_N={cap}"]["misses"] for r in pp]
            hits = [r["sim_rtt"][f"rtt_aware_N={cap}"]["hits"] for r in pp]
            totals = [r["total_attempts"] for r in pp]
            avg_miss = sum(misses) / n
            avg_hits = sum(hits) / n
            avg_total = sum(totals) / n
            hit_rate = (sum(hits) / sum(totals)) if sum(totals) > 0 else 0.0
            sim = (hops_avg + avg_miss) * rtt_sec
            out_walk[key] = wt_baseline
            out_sim[key] = sim
            out_total[key] = wt_baseline + sim
            out_hitrate[key] = hit_rate
            out_auth[key] = avg_miss
            out_hops[key] = hops_avg
            names.append(key)

    return {
        "policies": names,
        "walk_per_start": out_walk,
        "sim_per_start": out_sim,
        "total_per_start": out_total,
        "hit_rate": out_hitrate,
        "auth_calls_avg": out_auth,
        "hop_count_avg": out_hops,
    }


# ---------------------------------------------------------------------------
# 描画: 距離別総時間
# ---------------------------------------------------------------------------
def _color_for(pol: str) -> str:
    if pol in POLICY_COLORS:
        return POLICY_COLORS[pol]
    if pol.startswith("BFS_K="):
        idx = BFS_K_CANDIDATES.index(int(pol.split("=")[1]))
        return PROPOSED_COLORS_BFS[idx % len(PROPOSED_COLORS_BFS)]
    if pol.startswith("BFS-Score(top"):
        m = re.search(r"top(\d+)", pol)
        if m:
            cap = int(m.group(1))
            idx = RTT_AWARE_CAPACITIES.index(cap)
            return PROPOSED_COLORS_RTT[idx % len(PROPOSED_COLORS_RTT)]
    return "#cccccc"


def _label_for(pol: str) -> str:
    return POLICY_LABELS.get(pol, pol)


def plot_total_time(graphs: list[str], per_graph_totals: dict, out_path: Path):
    """3 つの RTT パターン (close/mid/far) × graphs で総時間棒グラフ"""
    n_patterns = len(RTT_PATTERNS)
    fig, axes = plt.subplots(
        n_patterns, len(graphs),
        figsize=(max(10, len(graphs) * 6.5), n_patterns * 4.2),
        squeeze=False,
    )

    for col, graph in enumerate(graphs):
        for row, pat in enumerate(RTT_PATTERNS):
            ax = axes[row][col]
            totals = per_graph_totals[graph][pat["key"]]
            names = totals["policies"]
            values = [totals["total_per_start"][p] for p in names]
            walks = [totals["walk_per_start"][p] for p in names]
            sims = [totals["sim_per_start"][p] for p in names]
            x = np.arange(len(names))
            colors = [_color_for(p) for p in names]
            ax.bar(x, walks, color=colors, edgecolor="white", linewidth=0.5)
            ax.bar(x, sims, bottom=walks, color=colors, edgecolor="white",
                   linewidth=0.5, hatch="///", alpha=0.85)
            for xi, v in zip(x, values):
                ax.text(xi, v * 1.01, f"{v:.1f}", ha="center", va="bottom", fontsize=7)
            ax.set_xticks(x)
            ax.set_xticklabels(
                [_label_for(p) for p in names], rotation=45, ha="right", fontsize=8,
            )
            if col == 0:
                ax.set_ylabel(f"total [s]  (per-start)", fontsize=10)
            if row == 0:
                ax.set_title(f"{GRAPH_LABELS.get(graph, graph)}", fontsize=11)
            ax.set_title(
                f"{GRAPH_LABELS.get(graph, graph)}  {pat['label']}",
                fontsize=10,
            )
            ax.grid(axis="y", linestyle="--", alpha=0.4)

    fig.suptitle(
        "提案手法 vs 既存ポリシー — 均一 RTT モデルでの per-start 総時間\n"
        "下: walk_time / 上(ハッチ): (hops + auth_calls) × RTT",
        fontsize=13, y=1.005,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_hitrate_breakdown(graphs: list[str], per_graph_totals: dict, out_path: Path):
    """ヒット率の比較 (RTT 非依存なので 1 つだけ表示)"""
    n_g = len(graphs)
    fig, axes = plt.subplots(1, n_g, figsize=(max(10, n_g * 6.5), 5.0), squeeze=False)
    for col, graph in enumerate(graphs):
        ax = axes[0][col]
        # 任意のパターンを使う (hit_rate は pattern 非依存)
        totals = per_graph_totals[graph][RTT_PATTERNS[0]["key"]]
        names = totals["policies"]
        rates = [totals["hit_rate"][p] for p in names]
        x = np.arange(len(names))
        colors = [_color_for(p) for p in names]
        bars = ax.bar(x, rates, color=colors, edgecolor="white", linewidth=0.5)
        for xi, v in zip(x, rates):
            ax.text(xi, v + 0.01, f"{v*100:.1f}%", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(
            [_label_for(p) for p in names], rotation=45, ha="right", fontsize=9
        )
        ax.set_ylim(0, 1.1)
        ax.set_ylabel("ヒット率")
        ax.set_title(f"{GRAPH_LABELS.get(graph, graph)}", fontsize=11)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

    fig.suptitle("ヒット率内訳 (提案 vs 既存)", fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_diff_vs_lru(graphs: list[str], per_graph_totals: dict, out_path: Path):
    """LRU(100) を基準とした各ポリシーの 総時間 差分 (3 RTT パターン × 各 graph)"""
    n_pat = len(RTT_PATTERNS)
    fig, axes = plt.subplots(
        n_pat, len(graphs), figsize=(max(10, len(graphs) * 6.5), n_pat * 4.0),
        squeeze=False,
    )
    for col, graph in enumerate(graphs):
        for row, pat in enumerate(RTT_PATTERNS):
            ax = axes[row][col]
            totals = per_graph_totals[graph][pat["key"]]
            names = totals["policies"]
            base = totals["total_per_start"].get("lru", 0.0)
            diffs = [totals["total_per_start"][p] - base for p in names]
            x = np.arange(len(names))
            colors = [_color_for(p) for p in names]
            ax.bar(x, diffs, color=colors, edgecolor="white", linewidth=0.5)
            ax.axhline(0, color="black", linewidth=0.8)
            for xi, v in zip(x, diffs):
                if abs(v) < 1e-6:
                    continue
                va = "bottom" if v >= 0 else "top"
                ax.text(xi, v, f"{v:+.1f}", ha="center", va=va, fontsize=7)
            ax.set_xticks(x)
            ax.set_xticklabels(
                [_label_for(p) for p in names], rotation=45, ha="right", fontsize=8,
            )
            if col == 0:
                ax.set_ylabel("LRU(100) との差分 [s]\n+ = LRU より遅い / − = 速い", fontsize=9)
            ax.set_title(
                f"{GRAPH_LABELS.get(graph, graph)}  {pat['label']}  "
                f"(LRU={base:.1f}s)",
                fontsize=10,
            )
            ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.suptitle("LRU(100) を基準とした per-start 総時間の差分", fontsize=13, y=1.005)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def save_csv(graphs, per_graph_totals, out_path: Path):
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "graph", "rtt_pattern", "rtt_ms", "policy",
            "walk_per_start_s", "sim_per_start_s", "total_per_start_s",
            "hop_count_avg", "auth_calls_avg", "hit_rate",
            "diff_vs_lru_s",
        ])
        for graph in graphs:
            for pat in RTT_PATTERNS:
                t = per_graph_totals[graph][pat["key"]]
                base_lru = t["total_per_start"].get("lru", 0.0)
                for pol in t["policies"]:
                    w.writerow([
                        graph, pat["key"], pat["rtt_ms"], pol,
                        f"{t['walk_per_start'][pol]:.4f}",
                        f"{t['sim_per_start'][pol]:.4f}",
                        f"{t['total_per_start'][pol]:.4f}",
                        f"{t['hop_count_avg'][pol]:.2f}",
                        f"{t['auth_calls_avg'][pol]:.2f}",
                        f"{t['hit_rate'][pol]:.4f}",
                        f"{(t['total_per_start'][pol] - base_lru):+.4f}",
                    ])
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="提案手法 (BFS prefetch + BFS×freq score) を均一RTTで評価"
    )
    ap.add_argument(
        "--results-dir", type=Path, required=True,
        help="auth-baseline-cache の results ディレクトリ (alpha0.01_walks_100_capa_100)",
    )
    ap.add_argument(
        "--out-dir", type=Path,
        default=Path("base/proposed_cache/output_uni"),
    )
    args = ap.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # graphs 検出
    graphs = []
    for g in ("amazon0601", "vldb"):
        if (args.results_dir / g).is_dir():
            graphs.append(g)
    if not graphs:
        print(f"[ERROR] 対象 graph が無い: {args.results_dir}")
        return

    # 集約
    per_graph_data: dict = {}
    per_graph_totals: dict = {}
    for graph in graphs:
        gdir = args.results_dir / graph
        gd = aggregate_graph(gdir)
        per_graph_data[graph] = gd
        per_graph_totals[graph] = {
            pat["key"]: compute_totals(gd, pat["rtt_ms"]) for pat in RTT_PATTERNS
        }

    # --------- コンソール出力 ---------
    print("\n" + "=" * 105)
    print(f"{'提案手法 vs 既存ポリシー (均一 RTT モデル, per-start 平均)':^105}")
    print("=" * 105)
    print(f"  モデル: total = walk_time + (hops + auth_calls_eff) × RTT")
    print(f"  提案1 BFS_K=K: BFS 距離 ≤ K の球をプリフェッチ (K={BFS_K_CANDIDATES})")
    print(f"  提案2 BFS-Score(topN): score = attempts × {BFS_DECAY}^BFS_dist, 上位 N をキャッシュ (N={RTT_AWARE_CAPACITIES})")
    print()

    for graph in graphs:
        for pat in RTT_PATTERNS:
            t = per_graph_totals[graph][pat["key"]]
            print(
                f"--- {GRAPH_LABELS.get(graph, graph)}  {pat['label']} "
                f"(RTT={pat['rtt_ms']}ms) ---"
            )
            print(
                f"{'policy':<24}{'walk/s':>8}{'sim/s':>10}{'total/s':>10}"
                f"{'auth_calls':>12}{'hit_rate':>10}{'vs LRU':>10}"
            )
            print("-" * 95)
            base_lru = t["total_per_start"].get("lru", 0.0)
            for pol in t["policies"]:
                diff = t["total_per_start"][pol] - base_lru
                marker = ""
                if pol.startswith(("BFS_K=", "BFS-Score(")) and diff < 0:
                    marker = "  ★faster"
                print(
                    f"{pol:<24}"
                    f"{t['walk_per_start'][pol]:>8.2f}"
                    f"{t['sim_per_start'][pol]:>10.2f}"
                    f"{t['total_per_start'][pol]:>10.2f}"
                    f"{t['auth_calls_avg'][pol]:>12.0f}"
                    f"{t['hit_rate'][pol]*100:>9.1f}%"
                    f"{diff:>+10.2f}{marker}"
                )
            print()

    # --------- 出力 ---------
    save_csv(graphs, per_graph_totals, out_dir / "proposed_summary.csv")
    plot_total_time(graphs, per_graph_totals, out_dir / "proposed_total_time.png")
    plot_hitrate_breakdown(
        graphs, per_graph_totals, out_dir / "proposed_hitrate_breakdown.png"
    )
    plot_diff_vs_lru(graphs, per_graph_totals, out_dir / "proposed_diff_vs_lru.png")

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
