#!/usr/bin/env python3
"""
高頻度 auth lookup ノードを「不均一 RTT」で分析する (アルゴリズム適用前)。

analyze_high_freq_nodes.py (flat な c×E[RTT] 版) の発展版。
walk のアクセスパターン (どのノードを何回 lookup するか) は cache policy に依存せず
グラフ・alpha・seed だけで決まる。そこへ RTT モデルを掛けて「どのノードが通信時間
(RTT コスト) を最も生むか」= キャッシュ価値を可視化する。
→ アルゴリズム適用前の段階なので --policy none を推奨 (全 lookup がリモート)。

RTT モデルは base/rtt/ の 3 スクリプトと統一:
  - uni        : 均一 RTT          (plot_total_time.py)   close/mid/far の固定値
  - hetero     : 混合 RTT          (plot_mixed_rtt.py)    パターン A/B/C の E[RTT]
  - asymptotic : ホップ別漸近 RTT  (plot_hopwise_rtt.py)  E[RTT_k] が hop で収束

  uni/hetero は hop 距離に依存しない (全 lookup が同じ RTT)。
  asymptotic は hop k 依存: E[RTT_k] = Σ_c w_k[c]·RTT_c,
                            w_k = w_inf + (w_0 - w_inf)·rho^(k-1)
  → ノード v の hop 番号 k には start からの BFS 距離 dist(v) を使う
    (最短路 d で到達 ≒ hop d)。遠いノードほど 1 lookup あたり高コスト。

各ノード v の RTT コスト:
    rtt_cost(v) = c[v] × per_lookup_rtt_ms(model, scenario, dist(v))   [ms]
  c[v] = authorization_attempts[v] (= もしキャッシュしなければ発生するリモート呼び出し回数)

入力:
  - *_global_transition.json  ({graph}/{policy}_{cap}/start=*_global_transition.json)

出力 (各 graph について):
  high_freq_rtt_nodes_<graph>_<policy>_<model>_<scenario>.csv
  high_freq_rtt_summary_<graph>_<policy>_<model>_<scenario>.png

実行例 (アルゴリズム適用前・漸近モデル C1):
  python3 base/rtt/analyze_high_freq_nodes_hetero.py \
    --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
    --policy none --rtt-model asymptotic --scenario C1 --threshold 3

漸近 (hopwise)	plot_hopwise_rtt.py	E[RTT_k]=w_∞+(w_0-w_∞)ρ^(k-1)	hop k に依存


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

OUT_dir = Path("base/rtt/alpha0.01_walks_100_capa_100/hetero/rtt_detail")
OUT_dir.mkdir(exist_ok=True)

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
# RTT モデル定義 (base/rtt/ の 3 スクリプトと統一)
# ---------------------------------------------------------------------------
RTT_CLASSES = {"local": 10, "regional": 60, "global": 200}  # ms
CATEGORY_ORDER = ["local", "regional", "global"]

# --- uni: 均一 RTT (plot_total_time.py) ---
UNI_SCENARIOS = {"close": 10, "mid": 60, "far": 200}  # ms (固定)

# --- hetero: 混合 RTT (plot_mixed_rtt.py) ---
HETERO_WEIGHTS = {
    "A": {"local": 0.70, "regional": 0.20, "global": 0.10},  # E[RTT]=39ms
    "B": {"local": 0.30, "regional": 0.50, "global": 0.20},  # E[RTT]=73ms
    "C": {"local": 0.10, "regional": 0.30, "global": 0.60},  # E[RTT]=139ms
}

# --- asymptotic: ホップ別漸近 RTT (plot_hopwise_rtt.py) ---
#   w_inf = Phase2 の収束先, w0 = 初期分布, rho = 収束速度
ASYMP_SCENARIOS = {
    "A1": {
        "w0": {"local": 0.95, "regional": 0.04, "global": 0.01},
        "w_inf": HETERO_WEIGHTS["A"],
        "rho": 0.92,
    },
    "A2": {
        "w0": {"local": 0.10, "regional": 0.30, "global": 0.60},
        "w_inf": HETERO_WEIGHTS["A"],
        "rho": 0.80,
    },
    "B1": {
        "w0": {"local": 0.80, "regional": 0.15, "global": 0.05},
        "w_inf": HETERO_WEIGHTS["B"],
        "rho": 0.92,
    },
    "B2": {
        "w0": {"local": 0.05, "regional": 0.20, "global": 0.75},
        "w_inf": HETERO_WEIGHTS["B"],
        "rho": 0.80,
    },
    "C1": {
        "w0": {"local": 0.70, "regional": 0.20, "global": 0.10},
        "w_inf": HETERO_WEIGHTS["C"],
        "rho": 0.92,
    },
    "C2": {
        "w0": {"local": 0.02, "regional": 0.08, "global": 0.90},
        "w_inf": HETERO_WEIGHTS["C"],
        "rho": 0.80,
    },
}


def _ertt_from_weights(w: dict) -> float:
    return sum(w[c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)


def _asymp_ertt_at_hop(scn: dict, k: int) -> float:
    """E[RTT_k] [ms]  (k>=1)。w_k = w_inf + (w0 - w_inf)·rho^(k-1)"""
    k = max(1, int(k))
    rho, w0, w_inf = scn["rho"], scn["w0"], scn["w_inf"]
    decay = rho ** (k - 1)
    w = {c: w_inf[c] + (w0[c] - w_inf[c]) * decay for c in CATEGORY_ORDER}
    return sum(w[c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)


def make_rtt_fn(model: str, scenario: str):
    """(dist) -> 1 lookup あたり RTT [ms] を返す関数を作る。"""
    if model == "uni":
        rtt = float(UNI_SCENARIOS[scenario])
        return lambda dist: rtt
    if model == "hetero":
        ertt = _ertt_from_weights(HETERO_WEIGHTS[scenario])
        return lambda dist: ertt
    if model == "asymptotic":
        scn = ASYMP_SCENARIOS[scenario]
        # dist<1 (start 自身 / 未到達) は hop=1 とみなす
        return lambda dist: _asymp_ertt_at_hop(scn, dist if dist and dist >= 1 else 1)
    raise ValueError(f"unknown model: {model}")


def valid_scenarios(model: str) -> list[str]:
    return {
        "uni": list(UNI_SCENARIOS),
        "hetero": list(HETERO_WEIGHTS),
        "asymptotic": list(ASYMP_SCENARIOS),
    }[model]


# ---------------------------------------------------------------------------
# transition.json → 隣接リスト + 特徴量 + サーバ所属
# ---------------------------------------------------------------------------
def _node_access_on_server(server_stats: dict) -> dict[str, int]:
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

    # サーバ別 access → ノードの「リモート率」
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

    # 隣接 (無向化) と out_degree
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

    # BFS 距離 from start (= hop 番号 k の代理)
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
        "out_deg": out_deg_counts,
        "dist": dist,
        "remote_ratio": remote_ratio,
    }


# ---------------------------------------------------------------------------
# 高頻度ノード抽出 (+ 選択モデルの RTT コスト)
# ---------------------------------------------------------------------------
def extract_high_freq(run: dict, threshold: int, rtt_fn) -> list[dict]:
    """c >= threshold のノードを返す (rtt_cost_ms 列つき)。"""
    rows = []
    total_attempts = sum(run["attempts"].values())
    if total_attempts == 0:
        return rows
    for v, c in run["attempts"].items():
        if c < threshold:
            continue
        dist = run["dist"].get(v, -1)
        per_lookup = rtt_fn(dist)
        rows.append(
            {
                "start": run["start"],
                "node": v,
                "c": c,
                "access": run["node_access"].get(v, 0),
                "out_degree": run["out_deg"].get(v, 0),
                "dist_from_start": dist,
                "auth_fail_rate": float(run["fail_rate"].get(v, 0.0)),
                "remote_ratio": round(float(run["remote_ratio"].get(v, 0.0)), 4),
                "contribution_pct": 100 * c / total_attempts,
                "rtt_per_lookup_ms": round(per_lookup, 3),
                "rtt_cost_ms": round(c * per_lookup, 2),
            }
        )
    rows.sort(key=lambda r: -r["rtt_cost_ms"])  # RTT コスト降順
    return rows


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
def plot_summary(
    graph: str,
    policy: str,
    model: str,
    scenario: str,
    rows_all: list[dict],
    threshold: int,
    out_path: Path,
) -> None:
    if not rows_all:
        return
    out_degs = np.array([r["out_degree"] for r in rows_all], dtype=float)
    dists = np.array([r["dist_from_start"] for r in rows_all], dtype=float)
    rtt_cost = np.array([r["rtt_cost_ms"] for r in rows_all], dtype=float)

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    # (1) RTT コスト CCDF
    ax = axes[0][0]
    cost_sorted = np.sort(rtt_cost)[::-1]
    y_rank = np.arange(1, len(cost_sorted) + 1)
    ax.loglog(
        cost_sorted, y_rank, marker="o", markersize=3, linewidth=0.8, color="#d62728"
    )
    ax.set_xlabel("RTT コスト [ms]", fontsize=10)
    ax.set_ylabel("コスト >= x のノード数", fontsize=10)
    ax.set_title(
        f"(1) ノード別 RTT コスト分布 (CCDF)  [n={len(rows_all)}]",
        fontsize=11,
        fontweight="bold",
    )
    ax.grid(True, which="both", linestyle="--", alpha=0.4)

    # (2) RTT コスト vs out_degree  ← 次数狙いキャッシュが効くか
    ax = axes[0][1]
    ax.scatter(out_degs, rtt_cost, alpha=0.6, s=15, color="#1f77b4")
    ax.set_xlabel("out_degree (transition から推定)", fontsize=10)
    ax.set_ylabel("RTT コスト [ms]", fontsize=10)
    ax.set_yscale("log")
    if out_degs.max() > 0:
        ax.set_xscale("log")
    ax.set_title(
        "(2) RTT コスト vs out_degree (次数で狙えるか?)", fontsize=11, fontweight="bold"
    )
    ax.grid(True, linestyle="--", alpha=0.4)
    if len(out_degs) >= 2 and np.std(out_degs) > 0 and np.std(rtt_cost) > 0:
        corr = np.corrcoef(np.log1p(out_degs), np.log1p(rtt_cost))[0, 1]
        ax.text(
            0.05,
            0.95,
            f"Pearson(log) = {corr:.3f}",
            transform=ax.transAxes,
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

    # (3) RTT コスト vs dist_from_start (boxplot)  ← 漸近モデルの距離依存が見える
    ax = axes[1][0]
    by_dist: dict[int, list] = defaultdict(list)
    for d_, cost_ in zip(dists, rtt_cost):
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
                ymax * 0.92 if ymax > 0 else max(rtt_cost),
                f"n={len(by_dist[k])}",
                ha="center",
                fontsize=7,
                color="#555",
            )
    ax.set_xlabel("dist from start_node (BFS) = hop k", fontsize=10)
    ax.set_ylabel("RTT コスト [ms]", fontsize=10)
    ax.set_yscale("log")
    ax.set_title(
        "(3) RTT コスト vs BFS 距離 (漸近: 遠いほど高)", fontsize=10, fontweight="bold"
    )
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    # (4) 累積 RTT 時間カバー率 (RTT コスト降順で何ノードキャッシュすれば何 % 削れるか)
    ax = axes[1][1]
    cost_desc = np.sort(rtt_cost)[::-1] / 1000.0  # ms -> s
    cum = np.cumsum(cost_desc)
    total_s = cum[-1] if len(cum) else 0.0
    xs = np.arange(1, len(cum) + 1)
    ax.plot(xs, cum, color="#2ca02c", linewidth=1.6)
    ax.fill_between(xs, 0, cum, color="#2ca02c", alpha=0.15)
    if total_s > 0:
        frac = cum / total_s
        for tgt, col in [(0.5, "#ff7f0e"), (0.8, "#d62728"), (0.95, "#9467bd")]:
            n_need = int(np.searchsorted(frac, tgt) + 1)
            ax.axvline(
                n_need,
                color=col,
                linestyle="--",
                linewidth=1.0,
                alpha=0.7,
                label=f"top-{n_need} で {int(tgt*100)}%",
            )
    ax.set_xlabel("上位 N ノード (RTT コスト降順)", fontsize=10)
    ax.set_ylabel("累積削減 RTT 時間 [s]", fontsize=10)
    ax.set_title(
        f"(4) 上位 N キャッシュで削れる RTT 時間 (計 {total_s:.1f}s)",
        fontsize=11,
        fontweight="bold",
    )
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_xlim(left=1)

    fig.suptitle(
        f"{graph}  policy={policy}  RTT={model}/{scenario}  高頻度ノード RTT 分析 (c >= {threshold})",
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
        description="高頻度 auth lookup ノードを不均一 RTT (uni/hetero/asymptotic) で分析"
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="{graph}/{policy}_{cap}/start=*_global_transition.json を探すディレクトリ",
    )
    ap.add_argument(
        "--policy",
        type=str,
        default="none",
        help="どの policy ディレクトリを読むか (default: none = アルゴリズム適用前)",
    )
    ap.add_argument(
        "--threshold",
        type=int,
        default=3,
        help="この回数以上 lookup されたノードだけ抽出 (default: 3)",
    )
    ap.add_argument(
        "--rtt-model",
        type=str,
        default="asymptotic",
        choices=["uni", "hetero", "asymptotic"],
        help="RTT 積み上げモデル (default: asymptotic)",
    )
    ap.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="uni: close/mid/far  hetero: A/B/C  asymptotic: A1/A2/B1/B2/C1/C2 "
        "(default: uni=mid, hetero=C, asymptotic=C1)",
    )
    args = ap.parse_args()

    results_dir: Path = args.results_dir
    if not results_dir.is_dir():
        raise SystemExit(f"not a dir: {results_dir}")

    model = args.rtt_model
    scenario = args.scenario or {"uni": "mid", "hetero": "C", "asymptotic": "C1"}[model]
    if scenario not in valid_scenarios(model):
        raise SystemExit(
            f"model={model} の scenario は {valid_scenarios(model)} から選ぶ "
            f"(指定: {scenario})"
        )
    rtt_fn = make_rtt_fn(model, scenario)
    print(f"[RTT] model={model}  scenario={scenario}  policy={args.policy}")

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
            rows_all.extend(extract_high_freq(run, args.threshold, rtt_fn))

        if not rows_all:
            print(f"[skip] {graph}/{args.policy}: no high-freq nodes")
            continue

        tag = f"{graph}_{args.policy}_{model}_{scenario}"

        # CSV
        # csv_path = OUT_dir / f"high_freq_rtt_nodes_{tag}.csv"
        # keys = list(rows_all[0].keys())
        # with csv_path.open("w", newline="") as f:
        #     w = csv.DictWriter(f, fieldnames=keys)
        #     w.writeheader()
        #     w.writerows(rows_all)
        # print(f"[csv]   {csv_path}  ({len(rows_all)} rows)")

        # サマリ
        c_arr = np.array([r["c"] for r in rows_all], dtype=float)
        d_arr = np.array([r["dist_from_start"] for r in rows_all], dtype=float)
        deg_arr = np.array([r["out_degree"] for r in rows_all], dtype=float)
        rem_arr = np.array([r["remote_ratio"] for r in rows_all], dtype=float)
        cost_arr = np.array([r["rtt_cost_ms"] for r in rows_all], dtype=float)
        total_cost_s = cost_arr.sum() / 1000.0

        print(
            f"\n=== {graph}  policy={args.policy}  RTT={model}/{scenario}  c >= {args.threshold} ==="
        )
        print(f"  n_nodes (c>={args.threshold}) : {len(rows_all)}")
        print(f"  Σc (total lookups)            : {int(c_arr.sum())}")
        print(
            f"  c        : median={int(np.median(c_arr))}  p95={int(np.percentile(c_arr,95))}  max={int(c_arr.max())}"
        )
        if (d_arr >= 0).any():
            print(
                f"  dist(hop): median={int(np.median(d_arr[d_arr>=0]))}  max={int(d_arr.max())}"
            )
        print(
            f"  out_deg  : median={int(np.median(deg_arr))}  max={int(deg_arr.max())}"
        )
        print(f"  remote_ratio mean = {rem_arr.mean():.3f}")
        print(
            f"  RTT コスト総計 = {total_cost_s:.2f}s  "
            f"(= もしキャッシュしなければかかる通信時間)"
        )
        if np.std(deg_arr) > 0 and np.std(cost_arr) > 0:
            corr = np.corrcoef(np.log1p(deg_arr), np.log1p(cost_arr))[0, 1]
            print(
                f"  corr(log out_degree, log rtt_cost) = {corr:.3f}  "
                f"← 1 に近いほど『次数で高コストノードを狙える』"
            )
        # RTT コスト降順で top-N カバー
        cost_desc = np.sort(cost_arr)[::-1]
        cum = np.cumsum(cost_desc) / cost_desc.sum()
        for tgt in (0.5, 0.8, 0.95):
            n_need = int(np.searchsorted(cum, tgt) + 1)
            print(
                f"  top-{n_need:>4} ノードをキャッシュ → RTT 時間の {int(tgt*100)}% 削減"
            )

        out = OUT_dir / f"high_freq_rtt_summary_{tag}.png"
        plot_summary(graph, args.policy, model, scenario, rows_all, args.threshold, out)


if __name__ == "__main__":
    main()
