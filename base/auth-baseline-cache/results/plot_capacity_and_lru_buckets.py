#!/usr/bin/env python3
"""
plot_hitrate_by_access_freq.py で memo についてしか出せなかった「bucket 別
ヒット数」を、LRU についてもシミュレートして出す。あわせて

  1. memory 占有量 (from memory_summary.json + per-cap 推定)
  2. cap を変えたときの hit_rate の変化 (オフラインシミュ)

を 3 連の図 + テキストサマリで出力する。

入力:
  --results-dir   例: base/auth-baseline-cache/results/alpha0.01_walks_1000_capa_100

各 graph (amazon0601 / vldb) について
  results_dir/<graph>/lru_100/start=*_global_transition.json
  results_dir/<graph>/lru_100/start=*_memory_summary.json
を読む。

出力 (results_dir 配下に保存):
  lru_buckets_<graph>.png            ← memo と LRU を bucket 別に並べた棒
  capacity_sweep_<graph>.png         ← hit_rate vs cap の線図
  memory_summary_<graph>.txt         ← メモリ占有テキスト

シミュレーション仕様:
  - 各 start_node の transition.json から経験的 Markov chain を作る
  - 同じ (alpha, walks, seed) で walk を再生
  - 各ステップで auth lookup を LRU に流し、per-node hit/miss を記録
  - per-node hit を c[v] でバケット集計
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import OrderedDict, defaultdict
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
# bucket 定義 (plot_hitrate_by_access_freq.py と一致させる)
# ---------------------------------------------------------------------------
BUCKETS = [
    (1, 1, "=1"),
    (2, 2, "=2"),
    (3, 5, "3-5"),
    (6, 20, "6-20"),
    (21, 100, "21-100"),
    (101, None, "101+"),
]


def bucket_of(c: int) -> int:
    for i, (lo, hi, _) in enumerate(BUCKETS):
        if hi is None:
            if c >= lo:
                return i
        elif lo <= c <= hi:
            return i
    return -1


# ---------------------------------------------------------------------------
# LRU
# ---------------------------------------------------------------------------
class LRU:
    def __init__(self, cap: int):
        self.cap = cap
        self.od: OrderedDict[str, int] = OrderedDict()
        self.hits_per_node: dict[str, int] = defaultdict(int)
        self.miss_per_node: dict[str, int] = defaultdict(int)

    def access(self, key: str) -> bool:
        if key in self.od:
            self.od.move_to_end(key)
            self.hits_per_node[key] += 1
            return True
        self.miss_per_node[key] += 1
        self.od[key] = 1
        if self.cap > 0 and len(self.od) > self.cap:
            self.od.popitem(last=False)
        return False


# ---------------------------------------------------------------------------
# transition.json から経験的 Markov chain
# ---------------------------------------------------------------------------
def load_chain(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    start = str(d["controller"]["start_node"])
    walks = int(d["controller"]["walks"])
    alpha = float(d["controller"]["alpha"])
    seed = int(d["controller"]["seed"])

    transition: dict[str, list[tuple[str, int]]] = defaultdict(list)
    raw_t: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for key, c in d.get("transition", {}).items():
        m = re.match(r"^(.+?)->edge_(.+?)_(.+)$", key)
        if not m: continue
        src, a, b = m.group(1), m.group(2), m.group(3)
        dst = b if src == a else a
        raw_t[src][dst] += c
    for src, nbrs in raw_t.items():
        transition[src] = list(nbrs.items())

    attempts = d.get("authorization_attempts", {})
    avg_len = sum(v for k, v in d.get("access", {}).items()
                  if not k.startswith("edge_")) / max(1, walks)
    return {
        "start": start, "walks": walks, "alpha": alpha, "seed": seed,
        "transition": transition, "attempts": attempts,
        "avg_len": avg_len,
        "cache_hit": int(d.get("cache hit", 0)),
        "cache_miss": int(d.get("cache miss", 0)),
    }


def sample_next(row, rng):
    if not row: return None
    total = sum(c for _, c in row)
    if total == 0: return None
    r = rng.uniform(0, total)
    s = 0
    for v, c in row:
        s += c
        if r <= s:
            return v
    return row[-1][0]


def _edge_key(u: str, v: str) -> str:
    """実機の正規化規則: edge_{min}_{max} (u,v を int 比較)"""
    try:
        ui, vi = int(u), int(v)
    except ValueError:
        ui, vi = u, v
    a, b = (ui, vi) if ui <= vi else (vi, ui)
    return f"edge_{a}_{b}"


def simulate_lru(chain: dict, cap: int) -> dict:
    """1 start_node 1 LRU で simulate。
    実機の auth は「current node + 移動先 edge」両方を lookup する形式なので、
    sim もそれに合わせて 2 種類のキーを LRU に流す。
    返り値: per-key hit/miss, total."""
    lru = LRU(cap)
    rng = random.Random(chain["seed"])
    start = chain["start"]
    walks, alpha = chain["walks"], chain["alpha"]
    for _ in range(walks):
        node = start
        lru.access(node)                  # node auth
        while True:
            if rng.random() < alpha:
                break
            row = chain["transition"].get(node)
            if not row:
                break
            nxt = sample_next(row, rng)
            if nxt is None:
                break
            # edge auth (current_node, next_node) を lookup
            lru.access(_edge_key(node, nxt))
            node = nxt
            lru.access(node)              # next node auth
    h = sum(lru.hits_per_node.values())
    m = sum(lru.miss_per_node.values())
    return {"hits_per_node": dict(lru.hits_per_node),
            "miss_per_node": dict(lru.miss_per_node),
            "total_hits": h, "total_miss": m,
            "hit_rate": h / (h + m) if (h + m) > 0 else 0.0}


# ---------------------------------------------------------------------------
# bucket 集計 (実測 lookups + シミュ LRU hits)
# ---------------------------------------------------------------------------
def bucket_breakdown(chain: dict, sim: dict) -> dict:
    """各 bucket について:
       - lookups (実測, authorization_attempts ベース)
       - memo_hits (= Σ(c-1))
       - lru_hits_sim (シミュレータが返した per-node hit を bucket 集計)"""
    n_b = len(BUCKETS)
    lookups_b = [0] * n_b
    memo_hits_b = [0] * n_b
    lru_hits_b  = [0] * n_b
    for v, c in chain["attempts"].items():
        if c <= 0: continue
        b = bucket_of(c)
        if b < 0: continue
        lookups_b[b]  += c
        memo_hits_b[b] += max(c - 1, 0)
        lru_hits_b[b]  += sim["hits_per_node"].get(v, 0)
    return {"lookups_b": lookups_b,
            "memo_hits_b": memo_hits_b,
            "lru_hits_b":  lru_hits_b}


# ---------------------------------------------------------------------------
# memory_summary.json から auth_cache の bytes 占有量を取る
# ---------------------------------------------------------------------------
def read_memory(policy_dir: Path) -> dict:
    """各 server の {auth_cache_bytes, rss_kb, cache_entries, cache_capacity} を返す。"""
    rows: list[dict] = []
    for jf in sorted(policy_dir.glob("start=*_memory_summary.json")):
        try:
            arr = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for srv in arr:
            rows.append({
                "server": srv.get("server_id"),
                "rss_kb": srv.get("rss_kb"),
                "rss_kb_max": srv.get("rss_kb_max"),
                "cache_entries": srv.get("cache_entries"),
                "cache_capacity": srv.get("cache_capacity"),
                "authz_cache_bytes": srv.get("bytes_est", {}).get("authz_cache", 0),
                "neighbor_map_bytes": srv.get("bytes_est", {}).get("neighbor_map", 0),
                "owner_map_bytes":   srv.get("bytes_est", {}).get("owner_map", 0),
            })
    return rows


# ---------------------------------------------------------------------------
# 描画 1: bucket 別 memo vs LRU(sim)
# ---------------------------------------------------------------------------
def plot_lru_buckets(graph: str, chains: list, sims: list,
                      out_path: Path) -> None:
    # n_starts 個ぶんを合算
    n_b = len(BUCKETS)
    lookups_b = np.zeros(n_b)
    memo_b    = np.zeros(n_b)
    lru_b     = np.zeros(n_b)
    for ch, sm in zip(chains, sims):
        br = bucket_breakdown(ch, sm)
        lookups_b += np.array(br["lookups_b"])
        memo_b    += np.array(br["memo_hits_b"])
        lru_b     += np.array(br["lru_hits_b"])

    labels = [b[2] for b in BUCKETS]
    x = np.arange(n_b)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    # 左: bucket 別 hit 数 (lookups, memo, lru) を 3 本バーで
    w = 0.27
    ax = axes[0]
    ax.bar(x - w, lookups_b, w, color="#cccccc", edgecolor="white",
           label="lookups (total)")
    ax.bar(x,     memo_b,    w, color="#1f77b4", edgecolor="white",
           label="memo hits (= Σ(c-1))")
    ax.bar(x + w, lru_b,     w, color="#ff7f0e", edgecolor="white",
           label="LRU hits (simulated)")
    # 数値: lru/memo の比率
    for xi, lk, mh, lh in zip(x, lookups_b, memo_b, lru_b):
        if mh > 0:
            ratio = lh / mh
            ax.text(xi, max(lk, mh, lh) * 1.04,
                    f"{int(lh)}/{int(mh)}\n({ratio:.0%})",
                    ha="center", va="bottom", fontsize=7, color="#444")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_xlabel("ノード参照頻度 c", fontsize=10)
    ax.set_ylabel("件数 (n_starts 合算)", fontsize=10)
    ax.set_title(f"{graph} — bucket 別 lookups / memo hits / LRU hits (sim)",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="upper left", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4); ax.set_axisbelow(True)

    # 右: bucket 別 hit_rate (memo vs LRU)
    ax = axes[1]
    memo_hr = np.where(lookups_b > 0, memo_b / np.maximum(lookups_b, 1), 0)
    lru_hr  = np.where(lookups_b > 0, lru_b  / np.maximum(lookups_b, 1), 0)
    ax.bar(x - w/2, memo_hr, w, color="#1f77b4", edgecolor="white",
           label="memo")
    ax.bar(x + w/2, lru_hr,  w, color="#ff7f0e", edgecolor="white",
           label="LRU (sim)")
    for xi, m_, l_ in zip(x, memo_hr, lru_hr):
        ax.text(xi - w/2, m_ + 0.02, f"{m_:.2f}", ha="center", va="bottom",
                fontsize=8, color="#1f4e79")
        ax.text(xi + w/2, l_ + 0.02, f"{l_:.2f}", ha="center", va="bottom",
                fontsize=8, color="#b85c00")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_xlabel("ノード参照頻度 c", fontsize=10)
    ax.set_ylabel("bucket hit_rate", fontsize=10)
    ax.set_ylim(0, 1.05)
    ax.set_title(f"{graph} — bucket 別 hit_rate  (LRU はオフラインシミュ)",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="upper left", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4); ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# 描画 2: hit_rate vs cap (capacity sweep)
# ---------------------------------------------------------------------------
def plot_capacity_sweep(graph: str, chains: list, caps: list,
                         mem_rows: list, out_path: Path) -> dict:
    # cap ごとに全 start_node 集計
    results: dict[int, dict] = {}
    for cap in caps:
        sum_h, sum_m = 0, 0
        for ch in chains:
            sim = simulate_lru(ch, cap)
            sum_h += sim["total_hits"]; sum_m += sim["total_miss"]
        results[cap] = {
            "hits": sum_h, "misses": sum_m,
            "hit_rate": sum_h / (sum_h + sum_m) if (sum_h + sum_m) > 0 else 0
        }

    # memo (= cap=∞) の上限値
    memo_sum_h = 0; memo_sum_t = 0
    for ch in chains:
        # 実測 memo を出すには memo_100 から読むが、ここでは Σ(c-1) で近似
        memo_sum_h += sum(max(c - 1, 0) for c in ch["attempts"].values())
        memo_sum_t += sum(ch["attempts"].values())
    memo_hr_upper = memo_sum_h / memo_sum_t if memo_sum_t > 0 else 0

    # auth_cache の bytes/entry 推定 (cap=100 の実測から)
    if mem_rows:
        sample = mem_rows[0]
        bytes_per_entry = sample["authz_cache_bytes"] / max(1, sample["cache_entries"])
    else:
        bytes_per_entry = 100  # fallback

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.0))

    # 左: hit_rate vs cap
    ax = axes[0]
    xs = sorted(results.keys())
    ys = [results[c]["hit_rate"] for c in xs]
    ax.plot(xs, ys, marker="o", linewidth=1.8, color="#ff7f0e",
            label="LRU (sim)")
    ax.axhline(memo_hr_upper, color="#1f77b4", linestyle="--",
               linewidth=1.4, label=f"memo 上限 (Σ(c-1)/Σc) = {memo_hr_upper:.3f}")
    # cap=100 を強調
    if 100 in results:
        ax.axvline(100, color="#999", linestyle=":", linewidth=1, alpha=0.6)
        ax.text(100, 0.02, " 実機 cap=100", fontsize=8, color="#666")
    for xi, yi in zip(xs, ys):
        ax.text(xi, yi + 0.015, f"{yi:.3f}", ha="center", va="bottom",
                fontsize=8)
    ax.set_xscale("log")
    ax.set_xlabel("cache capacity (entries / server)", fontsize=10)
    ax.set_ylabel("hit_rate", fontsize=10)
    ax.set_ylim(0, max(1.0, memo_hr_upper + 0.05))
    ax.set_title(f"{graph} — capacity を増やすと hit_rate はどこまで上がるか",
                 fontsize=11, fontweight="bold")
    ax.yaxis.grid(True, linestyle="--", alpha=0.4); ax.set_axisbelow(True)
    ax.legend(loc="lower right", fontsize=9)

    # 右: 同じ cap でメモリ消費量を表示
    ax = axes[1]
    mems = [c * bytes_per_entry / 1024 for c in xs]   # KB
    ax.plot(xs, mems, marker="s", linewidth=1.8, color="#2ca02c")
    for xi, mi in zip(xs, mems):
        ax.text(xi, mi * 1.07, f"{mi:.1f} KB",
                ha="center", va="bottom", fontsize=8, color="#2ca02c")
    # RSS 全体との比較 (server 1 つぶん)
    if mem_rows:
        rss = mem_rows[0]["rss_kb"] / 1024  # MB
        ax.text(0.05, 0.95,
                f"参考: server RSS = {rss:.0f} MB\n"
                f"(graph + maps が大半。auth_cache は\n"
                f" 1 entry ≈ {bytes_per_entry:.0f} B)",
                transform=ax.transAxes, fontsize=9, verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#f5f5f5",
                          edgecolor="#cccccc"))
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("cache capacity (entries / server)", fontsize=10)
    ax.set_ylabel("auth_cache memory [KB]", fontsize=10)
    ax.set_title(f"{graph} — capacity に対するキャッシュ消費メモリ",
                 fontsize=11, fontweight="bold")
    ax.yaxis.grid(True, which="both", linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")
    return {"results": results, "memo_hr_upper": memo_hr_upper,
            "bytes_per_entry": bytes_per_entry}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--caps", type=str,
                    default="10,30,50,100,200,500,1000,2000,5000")
    args = ap.parse_args()

    caps = [int(x) for x in args.caps.split(",")]
    results_dir: Path = args.results_dir

    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir(): continue
        graph = graph_dir.name
        lru_dir = graph_dir / "lru_100"
        if not lru_dir.is_dir(): continue

        # 各 start_node の Markov chain
        chains = []
        for jf in sorted(lru_dir.glob("start=*_global_transition.json")):
            ch = load_chain(jf)
            if ch["avg_len"] > 1.001:
                chains.append(ch)
        if not chains:
            print(f"[skip] {graph}: no chains"); continue

        # cap=100 で LRU simulate (per-bucket 比較用)
        sims = [simulate_lru(ch, 100) for ch in chains]
        out1 = results_dir / f"lru_buckets_{graph}.png"
        plot_lru_buckets(graph, chains, sims, out1)

        # memory
        mem_rows = read_memory(lru_dir)
        if mem_rows:
            print(f"\n=== {graph} memory (cap=100, lru) ===")
            print(f"{'server':>6} {'rss[MB]':>9} {'cache_entries':>14} "
                  f"{'cache_bytes':>12} {'graph_maps[MB]':>16}")
            seen = set()
            for r in mem_rows:
                key = r["server"]
                if key in seen: continue
                seen.add(key)
                gmap = (r["neighbor_map_bytes"] + r["owner_map_bytes"]) / 1024 / 1024
                print(f"{r['server']:>6} {r['rss_kb']/1024:>9.0f} "
                      f"{r['cache_entries']:>14} {r['authz_cache_bytes']:>12} "
                      f"{gmap:>16.0f}")

        # capacity sweep
        out2 = results_dir / f"capacity_sweep_{graph}.png"
        sweep = plot_capacity_sweep(graph, chains, caps, mem_rows, out2)

        print(f"\n=== {graph} capacity sweep ===")
        print(f"{'cap':>6} {'hit_rate':>9} {'mem[KB]':>9}  {'vs memo上限':>11}")
        for c in caps:
            hr = sweep["results"][c]["hit_rate"]
            mem_kb = c * sweep["bytes_per_entry"] / 1024
            gap = sweep["memo_hr_upper"] - hr
            print(f"{c:>6} {hr:>9.3f} {mem_kb:>9.1f}  {gap:>+11.3f}")
        print(f"  memo 上限 = {sweep['memo_hr_upper']:.3f}")


if __name__ == "__main__":
    main()
