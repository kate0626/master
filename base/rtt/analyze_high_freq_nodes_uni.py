#!/usr/bin/env python3
"""
高頻度 auth lookup ノードを「RTT 重み付き」で分析する。

base/auth-baseline-cache/results/analyze_high_freq_nodes.py の RTT 版。
ベースの高頻度ノード分析に「リモート auth 呼び出し回数 × RTT 遅延」の軸を加え、
どのノードが通信時間 (RTT コスト) を最も生んでいるかを可視化する。
RTT モデルは base/rtt/plot_mixed_rtt.py と統一 (local/regional/global, 3 混合パターン)。

入力:
  - *_global_transition.json  ({graph}/{policy}_{cap}/start=*_global_transition.json)
    - authorization_attempts: {v: c}   (lookup 回数 = リモート auth 候補回数)
    - access: {v or edge: c}            (ノード/辺の訪問回数)
    - transition: {src->edge_X_Y: c}    (実遷移カウント)
    - authorization_failure_rate: {v: rate}
    - per_server_access_stats: [{server_id, endpoint, stats:{access:{...}}}, ...]
    - controller: {start_node, start_server, walks, ...}

RTT コストの考え方:
  ランダムウォーク中、キャッシュミスしたノードは 1 回ごとにリモート認可確認
  (= 1 hop の通信) が必要になり RTT 遅延が乗る。各ノード v の「もしキャッシュ
  しなければ発生する通信時間」を
        rtt_cost_<pattern>(v) = c[v] * E[RTT_<pattern>]   [ms]
  で見積もる。E[RTT] はパターンごとの期待 RTT (近/中/遠の重み付き平均)。
  → 「このノードをキャッシュすれば何秒の通信を削れるか」= キャッシュ価値の指標。
  これにより「次数 (degree) を狙うキャッシュが効くか」を RTT コストの観点で検証できる。

出力 (各 graph, policy について):
  high_freq_rtt_nodes_<graph>_<policy>.csv     全 c>=THRESHOLD ノードの特徴量 + RTT コスト
  high_freq_rtt_summary_<graph>_<policy>.png   4 subplot (RTT 重み付き)
  
cd /Users/maiko/Documents/GitHub/master-progrem

python3 base/rtt/analyze_high_freq_nodes.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
  --policy none \
  --threshold 2

  --threshold 3　この回数以上アクセスされたノードのみに関して分析

"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict, deque
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT_dir = Path("base/rtt/high_freq_nodes")
OUT_dir.mkdir(parents=True, exist_ok=True)

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
# RTT モデル (base/rtt/plot_mixed_rtt.py と統一)
# ---------------------------------------------------------------------------
RTT_CLASSES = {
    "local": 10,  # 近い (国内DC)
    "regional": 60,  # 中位 (アジア圏)
    "global": 200,  # 遠い (大陸間)
}

MIXED_PATTERNS = [
    {
        "key": "A",
        "short": "Local-dominant",
        "weights": {"local": 0.7, "regional": 0.2, "global": 0.1},
    },
    {
        "key": "B",
        "short": "Regional-balanced",
        "weights": {"local": 0.3, "regional": 0.5, "global": 0.2},
    },
    {
        "key": "C",
        "short": "Global-distributed",
        "weights": {"local": 0.1, "regional": 0.3, "global": 0.6},
    },
]


def expected_rtt_ms(weights: dict) -> float:
    """E[RTT_mix] in ms"""
    return sum(weights[k] * RTT_CLASSES[k] for k in weights)


PATTERN_ERTT = {p["key"]: expected_rtt_ms(p["weights"]) for p in MIXED_PATTERNS}


# ---------------------------------------------------------------------------
# transition.json → 隣接リスト + 特徴量 + サーバ所属
# ---------------------------------------------------------------------------
def _node_access_on_server(server_stats: dict) -> dict[str, int]:
    """1 サーバの access から node 部分 (edge_ を除く) だけ取り出す。"""
    acc = server_stats.get("access", {})
    return {k: v for k, v in acc.items() if not k.startswith("edge_")}


def load_one_start(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    start = str(d["controller"]["start_node"])
    walks = int(d["controller"]["walks"])
    start_server = int(d["controller"].get("start_server", 0))

    node_access = {
        k: v for k, v in d.get("access", {}).items() if not k.startswith("edge_")
    }
    attempts = d.get("authorization_attempts", {})
    fail_rate = d.get("authorization_failure_rate", {})

    # サーバ別 access → ノードの「リモート率」を出す
    # remote_ratio[v] = (start_server 以外でアクセスされた回数) / (総アクセス回数)
    per_server = d.get("per_server_access_stats", [])
    start_server_access: dict[str, int] = {}
    for s in per_server:
        if int(s.get("server_id", -1)) == start_server:
            start_server_access = _node_access_on_server(s.get("stats", {}))
            break
    remote_ratio: dict[str, float] = {}
    for v, total in node_access.items():
        if total <= 0:
            continue
        local = start_server_access.get(v, 0)
        remote_ratio[v] = max(0.0, (total - local) / total)

    # 隣接: 'src->edge_a_b' から無向グラフ風に
    adj: dict[str, set] = defaultdict(set)
    out_deg_counts: dict[str, int] = defaultdict(int)
    for key in d.get("transition", {}):
        m = re.match(r"^(.+?)->edge_(.+?)_(.+)$", key)
        if not m:
            continue
        src, a, b = m.group(1), m.group(2), m.group(3)
        dst = b if src == a else a
        adj[src].add(dst)
        adj[dst].add(src)
    for v, neighbors in adj.items():
        out_deg_counts[v] = len(neighbors)

    # BFS 距離 from start
    dist: dict[str, int] = {start: 0}
    q = deque([start])
    while q:
        u = q.popleft()
        for w in adj.get(u, ()):
            if w not in dist:
                dist[w] = dist[u] + 1
                q.append(w)

    avg_len = (sum(node_access.values()) / walks) if walks > 0 else 0.0

    return {
        "start": start,
        "walks": walks,
        "avg_len": avg_len,
        "node_access": node_access,
        "attempts": attempts,
        "fail_rate": fail_rate,
        "adj": adj,
        "out_deg": out_deg_counts,
        "dist": dist,
        "remote_ratio": remote_ratio,
    }


# ---------------------------------------------------------------------------
# 高頻度ノード抽出 (+ RTT コスト)
# ---------------------------------------------------------------------------
def extract_high_freq(run: dict, threshold: int) -> list[dict]:
    """c >= threshold のノードを返す (RTT コスト列つき)。"""
    rows = []
    total_attempts = sum(run["attempts"].values())
    if total_attempts == 0:
        return rows
    for v, c in run["attempts"].items():
        if c < threshold:
            continue
        access_v = run["node_access"].get(v, 0)
        out_deg = run["out_deg"].get(v, 0)
        dist = run["dist"].get(v, -1)
        fail = run["fail_rate"].get(v, 0.0)
        rremote = run["remote_ratio"].get(v, 0.0)
        row = {
            "start": run["start"],
            "node": v,
            "c": c,  # lookup 回数
            "access": access_v,  # walk 訪問回数
            "out_degree": out_deg,  # 異なる遷移先の数
            "dist_from_start": dist,  # BFS 距離
            "auth_fail_rate": float(fail),
            "remote_ratio": round(float(rremote), 4),  # start サーバ外アクセス率
            "contribution_pct": 100 * c / total_attempts,
        }
        # RTT コスト [ms] = c * E[RTT_pattern]
        for key, ertt in PATTERN_ERTT.items():
            row[f"rtt_cost_{key}_ms"] = round(c * ertt, 2)
        rows.append(row)
    rows.sort(key=lambda r: -r["c"])
    return rows


# ---------------------------------------------------------------------------
# 描画: RTT 重み付き 高頻度ノード 4 連
# ---------------------------------------------------------------------------
def plot_high_freq_rtt_summary(
    graph: str, policy: str, rows_all: list[dict], threshold: int, out_path: Path
) -> None:
    if not rows_all:
        return
    cs = np.array([r["c"] for r in rows_all], dtype=float)
    out_degs = np.array([r["out_degree"] for r in rows_all], dtype=float)
    dists = np.array([r["dist_from_start"] for r in rows_all], dtype=float)
    # 代表として pattern C (global-distributed) の RTT コストを使う
    rtt_c = np.array([r["rtt_cost_C_ms"] for r in rows_all], dtype=float)

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    # (1) RTT コスト分布 (CCDF, x=rtt_cost_C, y=#nodes with cost >= x)
    ax = axes[0][0]
    cost_sorted = np.sort(rtt_c)[::-1]
    y_rank = np.arange(1, len(cost_sorted) + 1)
    ax.loglog(
        cost_sorted, y_rank, marker="o", markersize=3, linewidth=0.8, color="#d62728"
    )
    ax.set_xlabel("RTT コスト [ms]  (pattern C: global)", fontsize=10)
    ax.set_ylabel("コスト >= x のノード数", fontsize=10)
    ax.set_title(
        f"(1) ノード別 RTT コスト分布 (CCDF)  [n={len(rows_all)}]",
        fontsize=11,
        fontweight="bold",
    )
    ax.grid(True, which="both", linestyle="--", alpha=0.4)

    # (2) RTT コスト vs out_degree  ← 次数狙いキャッシュが効くか検証
    ax = axes[0][1]
    ax.scatter(out_degs, rtt_c, alpha=0.6, s=15, color="#1f77b4")
    ax.set_xlabel("out_degree (transition から推定)", fontsize=10)
    ax.set_ylabel("RTT コスト [ms] (pattern C)", fontsize=10)
    ax.set_yscale("log")
    if out_degs.max() > 0:
        ax.set_xscale("log")
    ax.set_title(
        "(2) RTT コスト vs out_degree (次数で狙えるか?)", fontsize=11, fontweight="bold"
    )
    ax.grid(True, linestyle="--", alpha=0.4)
    if len(out_degs) >= 2 and np.std(out_degs) > 0 and np.std(rtt_c) > 0:
        corr = np.corrcoef(np.log1p(out_degs), np.log1p(rtt_c))[0, 1]
        ax.text(
            0.05,
            0.95,
            f"Pearson(log) = {corr:.3f}",
            transform=ax.transAxes,
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

    # (3) RTT コスト vs dist_from_start (boxplot)  ← BFS prefetch の効きどころ
    ax = axes[1][0]
    by_dist: dict[int, list] = defaultdict(list)
    for d_, cost_ in zip(dists, rtt_c):
        if d_ >= 0:
            by_dist[int(d_)].append(cost_)
    keys = sorted(by_dist.keys())
    if keys:
        bx = [by_dist[k] for k in keys]
        bp = ax.boxplot(
            bx, positions=keys, widths=0.6, patch_artist=True, showfliers=True
        )
        for patch in bp["boxes"]:
            patch.set_facecolor("#ffbf7f")
        ymax = ax.get_ylim()[1]
        for k in keys:
            ax.text(
                k,
                ymax * 0.92 if ymax > 0 else max(rtt_c),
                f"n={len(by_dist[k])}",
                ha="center",
                fontsize=8,
                color="#555",
            )
    ax.set_xlabel("dist from start_node (BFS)", fontsize=10)
    ax.set_ylabel("RTT コスト [ms] (pattern C)", fontsize=10)
    ax.set_yscale("log")
    ax.set_title(
        "(3) RTT コスト vs start からの BFS 距離", fontsize=10, fontweight="bold"
    )
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    # (4) 累積 RTT 時間カバー率 (3 パターン重ね、秒単位)
    ax = axes[1][1]
    colors = {"A": "#2ca02c", "B": "#ff7f0e", "C": "#d62728"}
    cs_sorted = np.sort(cs)[::-1]
    xs = np.arange(1, len(cs_sorted) + 1)
    for p in MIXED_PATTERNS:
        key = p["key"]
        ertt = PATTERN_ERTT[key]
        cost_sorted = cs_sorted * ertt / 1000.0  # ms -> s
        cum = np.cumsum(cost_sorted)
        total_s = cum[-1] if len(cum) else 0.0
        ax.plot(
            xs,
            cum,
            color=colors[key],
            linewidth=1.6,
            label=f"{key} ({p['short']}, E[RTT]={ertt:.0f}ms, 計{total_s:.1f}s)",
        )
    ax.set_xlabel("上位 N ノード (lookup 数で降順)", fontsize=10)
    ax.set_ylabel("累積 RTT 通信時間 [s]", fontsize=10)
    ax.set_title(
        "(4) 上位 N ノードをキャッシュした時に削れる RTT 時間",
        fontsize=11,
        fontweight="bold",
    )
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_xlim(left=1)

    fig.suptitle(
        f"{graph}  policy={policy}  高頻度ノード RTT 分析 (c >= {threshold})",
        fontsize=12,
        fontweight="bold",
        y=1.005,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        description="高頻度 auth lookup ノードを RTT 重み付きで分析する"
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="{graph}/{policy}_{cap}/start=*_global_transition.json を探すディレクトリ",
    )
    ap.add_argument(
        "--threshold",
        type=int,
        default=3,
        help="この回数以上 lookup されたノードだけ抽出 (default: 3)",
    )
    ap.add_argument(
        "--policy",
        type=str,
        default="lru",
        help="どの policy の transition.json を読むか (default: lru)",
    )
    args = ap.parse_args()

    results_dir: Path = args.results_dir
    if not results_dir.is_dir():
        raise SystemExit(f"not a dir: {results_dir}")

    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name
        pol_dir = graph_dir / f"{args.policy}_100"
        if not pol_dir.is_dir():
            cands = list(graph_dir.glob(f"{args.policy}_*"))
            if not cands:
                continue
            pol_dir = cands[0]

        rows_all: list[dict] = []
        for jf in sorted(pol_dir.glob("start=*_global_transition.json")):
            try:
                run = load_one_start(jf)
            except Exception as e:
                print(f"[warn] failed to parse {jf}: {e}")
                continue
            if run["avg_len"] <= 1.001:
                continue
            rows_all.extend(extract_high_freq(run, args.threshold))

        if not rows_all:
            print(f"[skip] {graph}/{args.policy}: no high-freq nodes")
            continue

        # CSV 出力
        csv_path = OUT_dir / f"high_freq_rtt_nodes_{graph}_{args.policy}.csv"
        keys = list(rows_all[0].keys())
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows_all:
                w.writerow(r)
        print(f"[csv]   {csv_path}  ({len(rows_all)} rows)")

        # サマリ表示
        c_arr = np.array([r["c"] for r in rows_all], dtype=float)
        d_arr = np.array([r["dist_from_start"] for r in rows_all], dtype=float)
        deg_arr = np.array([r["out_degree"] for r in rows_all], dtype=float)
        rem_arr = np.array([r["remote_ratio"] for r in rows_all], dtype=float)
        total_c = c_arr.sum()

        print(
            f"\n=== {graph}  policy={args.policy}  threshold c >= {args.threshold} ==="
        )
        print(f"  n_nodes (c>={args.threshold}) : {len(rows_all)}")
        print(f"  Σc (total lookups in this set) : {int(total_c)}")
        print(
            f"  c        : min={int(c_arr.min())}  median={int(np.median(c_arr))}  "
            f"p95={int(np.percentile(c_arr,95))}  max={int(c_arr.max())}"
        )
        if (d_arr >= 0).any():
            print(
                f"  dist     : min={int(d_arr[d_arr>=0].min())}  "
                f"median={int(np.median(d_arr[d_arr>=0]))}  max={int(d_arr.max())}"
            )
        print(
            f"  out_deg  : min={int(deg_arr.min())}  median={int(np.median(deg_arr))}  "
            f"max={int(deg_arr.max())}"
        )
        print(
            f"  remote_ratio : mean={rem_arr.mean():.3f}  "
            f"(start サーバ外でアクセスされた割合 = リモート性)"
        )

        # degree と RTT コストの相関 (= 次数狙いが効くかの定量指標)
        if np.std(deg_arr) > 0 and np.std(c_arr) > 0:
            corr = np.corrcoef(np.log1p(deg_arr), np.log1p(c_arr))[0, 1]
            print(
                f"  corr(log out_degree, log c) = {corr:.3f}  "
                f"← 1 に近いほど『次数でキャッシュ対象を狙える』"
            )

        # パターン別 総 RTT 通信時間 + 上位 K カバー
        cs_sorted = np.sort(c_arr)[::-1]
        cum_frac = np.cumsum(cs_sorted) / cs_sorted.sum()
        print(f"  --- RTT 通信時間 (Σc × E[RTT], このセット内) ---")
        for p in MIXED_PATTERNS:
            ertt = PATTERN_ERTT[p["key"]]
            total_s = total_c * ertt / 1000.0
            print(
                f"    pattern {p['key']} ({p['short']:>18}, E[RTT]={ertt:6.1f}ms): "
                f"総 {total_s:8.2f}s"
            )
        for tgt in (0.5, 0.8, 0.95):
            n_need = int(np.searchsorted(cum_frac, tgt) + 1)
            print(
                f"  top-{n_need:>4} ノードをキャッシュ → このセット内 RTT 時間の "
                f"{int(tgt*100)}% を削減"
            )

        # 図出力
        out = OUT_dir / f"high_freq_rtt_summary_{graph}_{args.policy}.png"
        plot_high_freq_rtt_summary(graph, args.policy, rows_all, args.threshold, out)


if __name__ == "__main__":
    main()
