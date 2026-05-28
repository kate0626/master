#!/usr/bin/env python3
"""
walks 中に何度も auth lookup されたノード (高頻度ノード) の特徴を分析する。

`--with-lru-sim` を付けると LRU のオフラインシミュレーションを追加実行し、
per-node の hit/miss を CSV に出して「LRU はどんなノードを取りこぼしているか」
を直接見られるようにする。

入力:
  - *_global_transition.json
    - authorization_attempts: {v: c}   (lookup 回数 = 「ヒット候補回数」)
    - access: {v or edge: c}            (ノード/辺の訪問回数)
    - transition: {src->edge_X_Y: c}    (実遷移カウント)
    - authorization_failure_rate: {v: rate}
    - controller: {start_node, walks, ...}

出力:
  各 (graph, policy) について:
    high_freq_nodes_<graph>_<policy>.csv     全 c>=THRESHOLD ノードの特徴量
    high_freq_summary_<graph>_<policy>.png   特徴量の分布 (4 subplot)
  `--with-lru-sim` のとき追加で:
    lru_miss_pattern_<graph>.png             dist / out_degree / c ごとの LRU hit_rate

特徴量 (.gr ファイル無しでも transition から計算できるもの):
  - c             : authorization_attempts[v]            (= lookup 回数)
  - access_v      : access[v]                            (= 訪問回数)
  - max_access_v  : access[v] / start_node access[start] (start に対する相対頻度)
  - out_degree    : len(transition[v])                   (= 異なる遷移先の数 ≈ 次数の代理)
  - dist_from_start: start_node からの BFS 距離 (transition graph 上)
  - auth_fail_rate: authorization_failure_rate[v]
  - contribution_to_total: c / sum(c)                    (このノード単独で全 lookups の何 %)
  --with-lru-sim 時:
  - lru_hits      : LRU sim での hit 回数 (per-node)
  - lru_miss      : LRU sim での miss 回数 (per-node)
  - lru_hit_rate  : lru_hits / (lru_hits + lru_miss)
  - is_lru_blind  : lru_hits == 0 だが c >= threshold → LRU が完全に取りこぼしてる
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


_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "TakaoGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break


# ---------------------------------------------------------------------------
# transition.json → 隣接リスト + 特徴量
# ---------------------------------------------------------------------------
def load_one_start(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    start = str(d["controller"]["start_node"])
    walks = int(d["controller"]["walks"])

    # access (node only)
    node_access = {k: v for k, v in d.get("access", {}).items()
                   if not k.startswith("edge_")}
    # auth attempts
    attempts = d.get("authorization_attempts", {})
    fail_rate = d.get("authorization_failure_rate", {})

    # 隣接: 'src->edge_a_b' から無向グラフ風に
    adj: dict[str, set] = defaultdict(set)
    out_deg_counts: dict[str, int] = defaultdict(int)  # 「src からの異なる遷移先」
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
    }


# ---------------------------------------------------------------------------
# 高頻度ノード抽出
# ---------------------------------------------------------------------------
def extract_high_freq(run: dict, threshold: int,
                       lru_hits_per_node: dict | None = None,
                       lru_miss_per_node: dict | None = None) -> list[dict]:
    """c >= threshold のノードを返す。
    lru_*_per_node が与えられたら、per-node の hit/miss/hit_rate を追加する。"""
    rows = []
    total_attempts = sum(run["attempts"].values())
    if total_attempts == 0:
        return rows
    for v, c in run["attempts"].items():
        if c < threshold:
            continue
        access_v = run["node_access"].get(v, 0)
        out_deg  = run["out_deg"].get(v, 0)
        dist     = run["dist"].get(v, -1)
        fail     = run["fail_rate"].get(v, 0.0)
        row = {
            "start":             run["start"],
            "node":              v,
            "c":                 c,                       # lookup 回数
            "access":            access_v,                # walk 訪問回数
            "out_degree":        out_deg,                 # 異なる遷移先の数
            "dist_from_start":   dist,                    # BFS 距離
            "auth_fail_rate":    float(fail),
            "contribution_pct":  100 * c / total_attempts,
        }
        if lru_hits_per_node is not None:
            lh = lru_hits_per_node.get(v, 0)
            lm = lru_miss_per_node.get(v, 0)
            tot = lh + lm
            row["lru_hits"]     = lh
            row["lru_miss"]     = lm
            row["lru_lookups"]  = tot
            row["lru_hit_rate"] = (lh / tot) if tot > 0 else 0.0
            # LRU が完全に取りこぼしてるノード (sim でも 1 回もヒットしてない)
            row["is_lru_blind"] = 1 if (tot > 0 and lh == 0) else 0
        rows.append(row)
    rows.sort(key=lambda r: -r["c"])
    return rows


# ---------------------------------------------------------------------------
# LRU per-node sim — plot_capacity_and_lru_buckets.py の検証済みロジックを使う
# ---------------------------------------------------------------------------
def _simulate_lru_per_node(json_path: Path, cap: int) -> tuple[dict, dict]:
    """`load_chain` + `simulate_lru` を使って per-key hit/miss を返す。
    transition の真の遷移確率を使うので、adj 均等化より正確。"""
    import sys as _sys
    _here = Path(__file__).parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    from plot_capacity_and_lru_buckets import load_chain, simulate_lru
    chain = load_chain(json_path)
    sim = simulate_lru(chain, cap)
    return sim["hits_per_node"], sim["miss_per_node"]


# ---------------------------------------------------------------------------
# 描画: 高頻度ノード 4 連
# ---------------------------------------------------------------------------
def plot_high_freq_summary(graph: str, policy: str, rows_all: list[dict],
                            threshold: int, out_path: Path) -> None:
    if not rows_all:
        return
    cs       = np.array([r["c"]               for r in rows_all])
    accesses = np.array([r["access"]          for r in rows_all])
    out_degs = np.array([r["out_degree"]      for r in rows_all])
    dists    = np.array([r["dist_from_start"] for r in rows_all])
    fail_rs  = np.array([r["auth_fail_rate"]  for r in rows_all])

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))

    # (1) c distribution (CCDF, x=c, y=#nodes with attempts >= c)
    ax = axes[0][0]
    c_sorted = np.sort(cs)[::-1]
    y_rank   = np.arange(1, len(c_sorted) + 1)
    ax.loglog(c_sorted, y_rank, marker="o", markersize=3, linewidth=0.8)
    ax.set_xlabel("lookup count c", fontsize=10)
    ax.set_ylabel("number of nodes with attempts ≥ c", fontsize=10)
    ax.set_title(f"(1) 高頻度ノードの c 分布 (CCDF)  [n={len(rows_all)}]",
                 fontsize=11, fontweight="bold")
    ax.grid(True, which="both", linestyle="--", alpha=0.4)

    # (2) c vs out_degree
    ax = axes[0][1]
    ax.scatter(out_degs, cs, alpha=0.6, s=15, color="#1f77b4")
    ax.set_xlabel("out_degree (transition から推定)", fontsize=10)
    ax.set_ylabel("lookup count c", fontsize=10)
    ax.set_yscale("log")
    if out_degs.max() > 0:
        ax.set_xscale("log")
    ax.set_title("(2) c vs out_degree (= 次数の代理)",
                 fontsize=11, fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.4)
    if len(out_degs) >= 2 and np.std(out_degs) > 0 and np.std(cs) > 0:
        corr = np.corrcoef(np.log1p(out_degs), np.log1p(cs))[0, 1]
        ax.text(0.05, 0.95, f"Pearson(log) = {corr:.3f}",
                transform=ax.transAxes, fontsize=10,
                verticalalignment="top",
                bbox=dict(boxstyle="round", facecolor="white", alpha=0.8))

    # (3) c vs dist_from_start (boxplot per dist bucket)
    ax = axes[1][0]
    dist_max = int(dists[dists >= 0].max()) if (dists >= 0).any() else 0
    by_dist: dict[int, list] = defaultdict(list)
    for d_, c_ in zip(dists, cs):
        if d_ >= 0:
            by_dist[int(d_)].append(c_)
    keys = sorted(by_dist.keys())
    bx = [by_dist[k] for k in keys]
    bp = ax.boxplot(bx, positions=keys, widths=0.6, patch_artist=True,
                    showfliers=True)
    for patch in bp["boxes"]:
        patch.set_facecolor("#ffbf7f")
    # n_nodes per bucket annotation
    for k in keys:
        ax.text(k, ax.get_ylim()[1] * 0.92 if ax.get_ylim()[1] > 0 else max(cs),
                f"n={len(by_dist[k])}", ha="center", fontsize=8, color="#555")
    ax.set_xlabel("dist from start_node (BFS)", fontsize=10)
    ax.set_ylabel("lookup count c", fontsize=10)
    ax.set_yscale("log")
    ax.set_title("(3) c vs start からの BFS 距離  ← BFS prefetch の効きどころ確認",
                 fontsize=10, fontweight="bold")
    ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    # (4) cumulative contribution (上位 N 個で hit の何 % をカバーするか)
    ax = axes[1][1]
    cs_sorted = np.sort(cs)[::-1]
    cum = np.cumsum(cs_sorted) / cs_sorted.sum()
    xs  = np.arange(1, len(cum) + 1)
    ax.plot(xs, cum, color="#2ca02c", linewidth=1.6)
    ax.fill_between(xs, 0, cum, color="#2ca02c", alpha=0.15)
    # 50% / 80% / 95% カバーする最小 N を marker で
    for tgt, col in [(0.5, "#ff7f0e"), (0.8, "#d62728"), (0.95, "#9467bd")]:
        try:
            n_needed = int(np.searchsorted(cum, tgt) + 1)
            ax.axvline(n_needed, color=col, linestyle="--", linewidth=1.0,
                       alpha=0.7, label=f"top-{n_needed} で {int(tgt*100)}% カバー")
        except Exception:
            pass
    ax.set_xlabel("上位 N ノード (lookup 数で降順)", fontsize=10)
    ax.set_ylabel("累積 lookup カバー率", fontsize=10)
    ax.set_title("(4) 上位 N ノードで全 lookups の何 % をカバーするか",
                 fontsize=11, fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(loc="lower right", fontsize=8)
    ax.set_xlim(left=1)
    ax.set_ylim(0, 1.02)

    fig.suptitle(
        f"{graph}  policy={policy}  高頻度ノード分析 (c ≥ {threshold})",
        fontsize=12, fontweight="bold", y=1.005)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--threshold", type=int, default=3,
                    help="この回数以上 lookup されたノードだけ抽出 (default: 3)")
    ap.add_argument("--policy", type=str, default="lru",
                    help="どの policy の transition.json を読むか")
    ap.add_argument("--with-lru-sim", action="store_true",
                    help="LRU をオフラインシミュレートして per-node hit/miss を CSV に追加し、"
                         "「LRU が取りこぼしているノード」の特徴を可視化する")
    ap.add_argument("--lru-cap", type=int, default=100,
                    help="--with-lru-sim 時の cap (default: 100, 実機と一致)")
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
            lru_hits = lru_miss = None
            if args.with_lru_sim:
                lru_hits, lru_miss = _simulate_lru_per_node(jf, args.lru_cap)
            sub = extract_high_freq(run, args.threshold, lru_hits, lru_miss)
            rows_all.extend(sub)

        if not rows_all:
            print(f"[skip] {graph}/{args.policy}: no high-freq nodes")
            continue

        # CSV 出力
        suffix = f"_{args.policy}"
        if args.with_lru_sim:
            suffix += f"_lru_sim_cap{args.lru_cap}"
        csv_path = results_dir / f"high_freq_nodes_{graph}{suffix}.csv"
        keys = list(rows_all[0].keys())
        with csv_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows_all:
                w.writerow(r)
        print(f"[csv]   {csv_path}  ({len(rows_all)} rows)")

        # サマリ表示
        c_arr     = np.array([r["c"] for r in rows_all])
        d_arr     = np.array([r["dist_from_start"] for r in rows_all])
        deg_arr   = np.array([r["out_degree"] for r in rows_all])
        fail_arr  = np.array([r["auth_fail_rate"] for r in rows_all])
        total_c   = c_arr.sum()
        print(f"\n=== {graph}  policy={args.policy}  threshold c >= {args.threshold} ===")
        print(f"  n_nodes (c>={args.threshold}) : {len(rows_all)}")
        print(f"  Σc (total lookups in this set) : {int(total_c)}")
        print(f"  c       : min={c_arr.min()}  median={int(np.median(c_arr))}  "
              f"p95={int(np.percentile(c_arr,95))}  max={c_arr.max()}")
        print(f"  dist    : min={d_arr[d_arr>=0].min() if (d_arr>=0).any() else 'n/a'}  "
              f"median={int(np.median(d_arr[d_arr>=0])) if (d_arr>=0).any() else 'n/a'}  "
              f"max={d_arr.max()}")
        print(f"  out_deg : min={deg_arr.min()}  median={int(np.median(deg_arr))}  "
              f"max={deg_arr.max()}")
        print(f"  auth_fail_rate : mean={fail_arr.mean():.4f}  max={fail_arr.max():.4f}")
        # top-K で全 lookups の P% をカバーするのに必要な N
        cs_sorted = np.sort(c_arr)[::-1]
        cum = np.cumsum(cs_sorted) / cs_sorted.sum()
        for tgt in (0.5, 0.8, 0.95):
            n_need = int(np.searchsorted(cum, tgt) + 1)
            print(f"  top-{n_need:>4} ノードで このセット内の {int(tgt*100)}% lookups をカバー")

        # 図出力
        out = results_dir / f"high_freq_summary_{graph}{suffix}.png"
        plot_high_freq_summary(graph, args.policy, rows_all, args.threshold, out)

        # ===== LRU sim 付きの場合: 取りこぼし分析を追加表示 =====
        if args.with_lru_sim:
            blind_rows = [r for r in rows_all if r.get("is_lru_blind", 0) == 1]
            partial_rows = [r for r in rows_all
                             if r.get("lru_lookups", 0) > 0 and 0 < r.get("lru_hit_rate", 0) < 0.5]
            covered_rows = [r for r in rows_all if r.get("lru_hit_rate", 0) >= 0.5]
            print(f"\n  === LRU sim 結果 (cap={args.lru_cap}) ===")
            print(f"  is_lru_blind (sim で 1 度も hit せず) : {len(blind_rows)} / {len(rows_all)} "
                  f"({100*len(blind_rows)/len(rows_all):.1f}%)")
            print(f"  partial hit (0 < HR < 0.5)          : {len(partial_rows)} "
                  f"({100*len(partial_rows)/len(rows_all):.1f}%)")
            print(f"  well covered (HR >= 0.5)            : {len(covered_rows)} "
                  f"({100*len(covered_rows)/len(rows_all):.1f}%)")
            # blind ノードの特徴
            if blind_rows:
                bd_arr = np.array([r["dist_from_start"] for r in blind_rows])
                bdeg   = np.array([r["out_degree"] for r in blind_rows])
                bc     = np.array([r["c"] for r in blind_rows])
                print(f"\n  blind ノードの特徴:")
                print(f"    c       : median={int(np.median(bc))} max={int(bc.max())}")
                print(f"    dist    : median={int(np.median(bd_arr[bd_arr>=0])) if (bd_arr>=0).any() else 'n/a'} "
                      f"max={bd_arr.max()}")
                print(f"    out_deg : median={int(np.median(bdeg))} max={int(bdeg.max())}")
                print(f"    (これらが pin / prefetch すべき対象)")

            # LRU 取りこぼしパターンの図
            out2 = results_dir / f"lru_miss_pattern_{graph}_cap{args.lru_cap}.png"
            plot_lru_miss_pattern(graph, rows_all, args.lru_cap, out2)


# ---------------------------------------------------------------------------
# LRU 取りこぼしパターン専用プロット
# ---------------------------------------------------------------------------
def plot_lru_miss_pattern(graph: str, rows_all: list[dict],
                           cap: int, out_path: Path) -> None:
    """rows_all は extract_high_freq の出力 (lru_hits/lru_hit_rate 列を持つ)。
    LRU の per-node hit_rate を dist / out_degree / c ごとに集計し、
    「LRU は何を取りこぼしているか」を 3 panel で見せる。"""
    rows = [r for r in rows_all if r.get("lru_lookups", 0) > 0]
    if not rows:
        return
    cs       = np.array([r["c"] for r in rows])
    dists    = np.array([r["dist_from_start"] for r in rows])
    degs     = np.array([r["out_degree"] for r in rows])
    hrs      = np.array([r["lru_hit_rate"] for r in rows])
    # memo HR per node = (c-1)/c
    memo_hr  = (cs - 1) / np.maximum(cs, 1)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    # (1) dist vs LRU hit_rate
    ax = axes[0]
    by_dist: dict[int, list[float]] = defaultdict(list)
    by_dist_memo: dict[int, list[float]] = defaultdict(list)
    for d_, h_, m_ in zip(dists, hrs, memo_hr):
        if d_ >= 0:
            by_dist[int(d_)].append(h_)
            by_dist_memo[int(d_)].append(m_)
    keys = sorted(by_dist.keys())
    lru_means  = [np.mean(by_dist[k])      for k in keys]
    memo_means = [np.mean(by_dist_memo[k]) for k in keys]
    ns         = [len(by_dist[k]) for k in keys]
    ax.plot(keys, memo_means, marker="o", color="#1f77b4", linewidth=1.8,
            label="memo (= (c-1)/c)")
    ax.plot(keys, lru_means,  marker="s", color="#ff7f0e", linewidth=1.8,
            label=f"LRU sim (cap={cap})")
    for k, n in zip(keys, ns):
        ax.text(k, -0.05, f"n={n}", ha="center", fontsize=7, color="#666")
    ax.set_xlabel("dist from start_node (BFS)", fontsize=10)
    ax.set_ylabel("hit_rate (per-node 平均)", fontsize=10)
    ax.set_ylim(-0.1, 1.05)
    ax.set_title("(1) 距離別 LRU vs memo hit_rate", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(True, linestyle="--", alpha=0.4)

    # (2) out_degree vs LRU hit_rate
    ax = axes[1]
    deg_bins = [(0,0), (1,2), (3,5), (6,10), (11,30), (31, int(degs.max())+1)]
    labels = []; lru_avg = []; memo_avg = []; ns = []
    for lo, hi in deg_bins:
        if lo > degs.max(): continue
        mask = (degs >= lo) & (degs <= hi)
        if not mask.any(): continue
        labels.append(f"{lo}-{hi}" if lo != hi else str(lo))
        lru_avg.append(hrs[mask].mean())
        memo_avg.append(memo_hr[mask].mean())
        ns.append(int(mask.sum()))
    x = np.arange(len(labels)); w = 0.4
    ax.bar(x - w/2, memo_avg, w, color="#1f77b4", label="memo")
    ax.bar(x + w/2, lru_avg,  w, color="#ff7f0e", label=f"LRU sim (cap={cap})")
    for xi, n in zip(x, ns):
        ax.text(xi, -0.05, f"n={n}", ha="center", fontsize=7, color="#666")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_xlabel("out_degree bin", fontsize=10)
    ax.set_ylabel("hit_rate (per-node 平均)", fontsize=10)
    ax.set_ylim(-0.1, 1.05)
    ax.set_title("(2) out_degree 別 LRU vs memo hit_rate", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    # (3) c (lookup回数) bin vs LRU hit_rate
    ax = axes[2]
    c_bins = [(3,5), (6,20), (21,100), (101, int(cs.max())+1)]
    labels = []; lru_avg = []; memo_avg = []; ns = []
    for lo, hi in c_bins:
        if lo > cs.max(): continue
        mask = (cs >= lo) & (cs <= hi)
        if not mask.any(): continue
        labels.append(f"{lo}-{hi}" if hi - lo < 1000 else f"{lo}+")
        lru_avg.append(hrs[mask].mean())
        memo_avg.append(memo_hr[mask].mean())
        ns.append(int(mask.sum()))
    x = np.arange(len(labels)); w = 0.4
    ax.bar(x - w/2, memo_avg, w, color="#1f77b4", label="memo")
    ax.bar(x + w/2, lru_avg,  w, color="#ff7f0e", label=f"LRU sim (cap={cap})")
    for xi, n in zip(x, ns):
        ax.text(xi, -0.05, f"n={n}", ha="center", fontsize=7, color="#666")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_xlabel("c (lookup count) bin", fontsize=10)
    ax.set_ylabel("hit_rate (per-node 平均)", fontsize=10)
    ax.set_ylim(-0.1, 1.05)
    ax.set_title("(3) lookup 頻度別 LRU vs memo hit_rate", fontsize=11, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(True, axis="y", linestyle="--", alpha=0.4)

    fig.suptitle(f"{graph} — LRU の取りこぼしパターン (cap={cap})",
                 fontsize=12, fontweight="bold", y=1.01)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


if __name__ == "__main__":
    main()
