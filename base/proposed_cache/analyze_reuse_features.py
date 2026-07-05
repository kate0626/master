#!/usr/bin/env python3
"""
再利用特徴分析 (admission 設計のための挙動確認)。

cache=none の per-start `*_global_transition.json` を **start ごと** に読み、
キャッシュキー粒度 `(start, entity)` で「1回のみ(one-hit) / 複数回(multi-hit)」を
ラベリングして、以下を出力する:

  1. one-hit / multi-hit 割合 (per-start, pooled, node/edge 別)
       → 正しい粒度での 47/53 相当値。
  2. multi-hit の総アクセス回数分布 + 「上位 K エントリだけ入れたら reuse-hit の
     何 % を回収できるか」曲線 (Belady 的容量-ヒット率上限)。容量100がどこに来るか。
  3. 構造特徴 (deg, start からの BFS 距離) が one/multi をどれだけ分離するか (AUC)。
       弱ければ「次数・距離を捨てる」根拠。node エンティティのみで評価。
  4. start 横断一致: 複数 start に現れる entity の再利用ラベル一致度。
       高ければ entity 単位の再利用プライアを start 間で転移できる (Layer A 根拠)。

使い方:
  python3 analyze_reuse_features.py \
      --input results/access_locality/vldb \
      --edges dataset/Louvain/graph/vldb.gr \
      --out-dir results/reuse_features/vldb \
      --graph-label vldb
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict, deque, Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional

START_RE = re.compile(r"start=(\d+)")


# ----------------------------- グラフ -----------------------------
def load_adjacency(edge_path: Path) -> Dict[int, List[int]]:
    adj: Dict[int, List[int]] = defaultdict(list)
    with edge_path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s[0] == "#":
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            u, v = int(parts[0]), int(parts[1])
            adj[u].append(v)
            adj[v].append(u)
    return adj


def bfs_distances(adj: Dict[int, List[int]], start: int) -> Dict[int, int]:
    dist = {start: 0}
    dq = deque([start])
    while dq:
        u = dq.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                dq.append(v)
    return dist


# ----------------------------- 入出力 -----------------------------
def find_start_files(paths: List[str]) -> List[Path]:
    out: List[Path] = []
    for pat in paths:
        p = Path(pat)
        if p.is_dir():
            out.extend(p.rglob("*_global_transition.json"))
        else:
            out.append(p)
    # cache=none のみを採用 (全アクセス=1認可 で再利用回数が正しく取れる)
    out = [p for p in out if "cache=none" in p.name]
    return sorted(set(out))


def start_of(path: Path) -> int:
    m = START_RE.search(path.name)
    return int(m.group(1)) if m else -1


def is_edge(entity: str) -> bool:
    return str(entity).startswith("edge_")


def edge_endpoints(entity: str) -> Optional[Tuple[int, int]]:
    # "edge_u_v"
    try:
        _, u, v = entity.split("_")
        return int(u), int(v)
    except Exception:
        return None


# ----------------------------- 指標 -----------------------------
def auc_higher_is_multi(values_multi: List[float], values_one: List[float]) -> float:
    """値が大きいほど multi-hit と予測する分類器の AUC (Mann-Whitney)。
    0.5=無情報, 1.0=完全分離, <0.5 は逆相関。"""
    if not values_multi or not values_one:
        return float("nan")
    combined = [(v, 1) for v in values_multi] + [(v, 0) for v in values_one]
    combined.sort(key=lambda x: x[0])
    # rank 付け (同値は平均ランク)
    ranks = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 平均ランク
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    n_multi = len(values_multi)
    n_one = len(values_one)
    sum_rank_multi = sum(r for r, (_, lab) in zip(ranks, combined) if lab == 1)
    u = sum_rank_multi - n_multi * (n_multi + 1) / 2.0
    return u / (n_multi * n_one)


# ----------------------------- メイン -----------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="再利用特徴分析 (admission 設計用)")
    ap.add_argument("--input", nargs="+", required=True, help="per-start JSON かディレクトリ")
    ap.add_argument("--edges", required=True, help="元グラフ .gr (無向 'u v')")
    ap.add_argument("--out-dir", default=".", help="出力先")
    ap.add_argument("--graph-label", default="", help="図タイトル用ラベル")
    args = ap.parse_args()

    files = find_start_files(args.input)
    print(f"[INFO] {len(files)} cache=none per-start file(s)")
    if not files:
        print("[ERROR] cache=none ファイルが見つからない。--input を確認。")
        return

    print(f"[INFO] loading adjacency: {args.edges}")
    adj = load_adjacency(Path(args.edges))
    deg = {u: len(vs) for u, vs in adj.items()}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # (start, entity) -> count
    per_start: Dict[int, Dict[str, int]] = {}
    for p in files:
        s = start_of(p)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] skip {p}: {e}")
            continue
        aa = {str(k): int(v) for k, v in d.get("authorization_attempts", {}).items()}
        per_start[s] = aa

    # ---------- 1. one/multi 割合 ----------
    summary_rows = []
    pooled = {"node": Counter(), "edge": Counter()}  # kind -> {one,multi,access}
    for s, aa in sorted(per_start.items()):
        row = {"start": s}
        for kind in ("node", "edge"):
            items = [(k, c) for k, c in aa.items() if (is_edge(k) == (kind == "edge"))]
            n = len(items)
            one = sum(1 for _, c in items if c == 1)
            multi = n - one
            acc = sum(c for _, c in items)
            reuse_hits = sum(c - 1 for _, c in items)  # 初回ミス控除後の最大ヒット数
            row[f"{kind}_entries"] = n
            row[f"{kind}_one"] = one
            row[f"{kind}_multi"] = multi
            row[f"{kind}_multi_frac"] = round(multi / n, 4) if n else 0
            row[f"{kind}_accesses"] = acc
            row[f"{kind}_reuse_hits"] = reuse_hits
            row[f"{kind}_max_hitrate"] = round(reuse_hits / acc, 4) if acc else 0
            pooled[kind]["one"] += one
            pooled[kind]["multi"] += multi
            pooled[kind]["access"] += acc
            pooled[kind]["reuse"] += reuse_hits
        summary_rows.append(row)

    with (out_dir / "reuse_summary_by_start.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print(f"[OUT] {out_dir/'reuse_summary_by_start.csv'}")

    print("\n=== one-hit / multi-hit (pooled over starts) ===")
    for kind in ("node", "edge"):
        c = pooled[kind]
        tot = c["one"] + c["multi"]
        if tot:
            print(f"  {kind:5s}: one={c['one']} ({100*c['one']/tot:.1f}%)  "
                  f"multi={c['multi']} ({100*c['multi']/tot:.1f}%)  "
                  f"max_hitrate={c['reuse']/c['access']:.3f}")

    # ---------- 2. 容量-ヒット率上限曲線 (全 (start,entity) プール) ----------
    # 各 (start,entity) は cache すれば (c-1) 回の reuse-hit を生み 1 スロット占有。
    all_counts = [c for aa in per_start.values() for c in aa.values()]
    total_reuse = sum(c - 1 for c in all_counts)
    total_access = sum(all_counts)
    counts_desc = sorted(all_counts, reverse=True)
    cap_rows = []
    cum = 0
    # 代表的な K で回収率を出す (+ 全点 CSV)
    marks = sorted(set([10, 50, 100, 200, 500, 1000, 2000, 5000,
                        len(counts_desc)]))
    for K, c in enumerate(counts_desc, 1):
        cum += (c - 1)
        if K in marks:
            cap_rows.append({
                "capacity_K": K,
                "reuse_hits_captured": cum,
                "frac_of_max_reuse": round(cum / total_reuse, 4) if total_reuse else 0,
                "hitrate_if_oracle": round(cum / total_access, 4) if total_access else 0,
            })
    with (out_dir / "capacity_curve.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cap_rows[0].keys()))
        w.writeheader()
        w.writerows(cap_rows)
    print(f"[OUT] {out_dir/'capacity_curve.csv'}")
    print("\n=== oracle capacity curve (top-K entries by reuse) ===")
    for r in cap_rows:
        print(f"  K={r['capacity_K']:>6}: reuse回収={r['frac_of_max_reuse']*100:5.1f}%  "
              f"oracle hitrate={r['hitrate_if_oracle']*100:5.1f}%")

    # multi-hit の回数分布 (昇格閾値の判断材料)
    multi_counts = Counter(c for c in all_counts if c >= 2)
    with (out_dir / "multihit_count_dist.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["access_count", "num_entries"])
        for ac in sorted(multi_counts):
            w.writerow([ac, multi_counts[ac]])
    n_ge2 = sum(multi_counts.values())
    n_ge3 = sum(v for c, v in multi_counts.items() if c >= 3)
    if n_ge2:
        print(f"\n[昇格閾値] multi(>=2)中で >=3 にも届くのは {n_ge3}/{n_ge2} "
              f"({100*n_ge3/n_ge2:.1f}%)  -> 閾値2で取りこぼす『ちょうど2回』は "
              f"{100*(n_ge2-n_ge3)/n_ge2:.1f}%")

    # ---------- 3. 構造特徴の分離力 (node のみ) ----------
    feat_rows = []
    deg_multi, deg_one, dist_multi, dist_one = [], [], [], []
    for s, aa in per_start.items():
        if s not in deg and s not in adj:
            pass
        dist = bfs_distances(adj, s) if s in adj else {}
        for k, c in aa.items():
            if is_edge(k):
                continue
            nid = int(k)
            d_deg = deg.get(nid, 0)
            d_dist = dist.get(nid, -1)
            if c >= 2:
                deg_multi.append(d_deg)
                if d_dist >= 0:
                    dist_multi.append(d_dist)
            else:
                deg_one.append(d_deg)
                if d_dist >= 0:
                    dist_one.append(d_dist)

    auc_deg = auc_higher_is_multi(deg_multi, deg_one)
    # 距離は「近いほど multi」なので符号反転 (-dist が大きいほど multi)
    auc_dist = auc_higher_is_multi([-x for x in dist_multi], [-x for x in dist_one])

    def _med(xs):
        if not xs:
            return float("nan")
        xs2 = sorted(xs)
        return xs2[len(xs2) // 2]

    feat_rows.append({
        "feature": "degree",
        "auc_predict_multi": round(auc_deg, 4),
        "median_multi": _med(deg_multi),
        "median_one": _med(deg_one),
    })
    feat_rows.append({
        "feature": "bfs_distance(closer=multi)",
        "auc_predict_multi": round(auc_dist, 4),
        "median_multi": _med(dist_multi),
        "median_one": _med(dist_one),
    })
    with (out_dir / "structural_separation.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(feat_rows[0].keys()))
        w.writeheader()
        w.writerows(feat_rows)
    print(f"\n=== 構造特徴の分離力 (node, AUC; 0.5=無情報) ===")
    for r in feat_rows:
        print(f"  {r['feature']:30s} AUC={r['auc_predict_multi']}  "
              f"median multi={r['median_multi']} / one={r['median_one']}")

    # ---------- 4. start 横断一致 ----------
    # entity -> [各 start での is_multi]
    ent_labels: Dict[str, List[int]] = defaultdict(list)
    for s, aa in per_start.items():
        for k, c in aa.items():
            ent_labels[k].append(1 if c >= 2 else 0)
    multi_start_ents = {k: v for k, v in ent_labels.items() if len(v) >= 2}
    cross_rows = []
    consist = Counter()  # multi_fraction バケツ
    for k, labs in multi_start_ents.items():
        frac = sum(labs) / len(labs)
        # 0, (0,1), 1 に分類
        if frac == 0:
            consist["always_one"] += 1
        elif frac == 1:
            consist["always_multi"] += 1
        else:
            consist["mixed"] += 1
    tot_ms = max(1, len(multi_start_ents))
    print(f"\n=== start 横断一致 (>=2 start に出現する {len(multi_start_ents)} entity) ===")
    for k in ("always_one", "always_multi", "mixed"):
        print(f"  {k:12s}: {consist[k]} ({100*consist[k]/tot_ms:.1f}%)")
    with (out_dir / "cross_start_consistency.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bucket", "num_entities", "fraction"])
        for k in ("always_one", "always_multi", "mixed"):
            w.writerow([k, consist[k], round(consist[k] / tot_ms, 4)])

    # ---------- 図 ----------
    _plot(out_dir, args.graph_label or out_dir.name,
          counts_desc, total_reuse, total_access,
          multi_counts, deg_multi, deg_one, dist_multi, dist_one, consist, tot_ms)


def _plot(out_dir, label, counts_desc, total_reuse, total_access,
          multi_counts, deg_multi, deg_one, dist_multi, dist_one, consist, tot_ms):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[WARN] matplotlib unavailable: {e}")
        return

    fig, axes = plt.subplots(2, 2, figsize=(13, 10))

    # (a) 容量-reuse回収率曲線
    ax = axes[0][0]
    xs, ys = [], []
    cum = 0
    for K, c in enumerate(counts_desc, 1):
        cum += (c - 1)
        xs.append(K)
        ys.append(cum / total_reuse if total_reuse else 0)
    ax.plot(xs, ys, color="#1565c0", lw=1.5)
    ax.axvline(100, color="red", ls="--", lw=1, label="capacity=100")
    ax.set_xscale("log")
    ax.set_xlabel("capacity K (top-K entries by reuse, log)")
    ax.set_ylabel("fraction of max reuse-hits captured")
    ax.set_title("(a) Oracle capacity vs reuse capture")
    ax.set_ylim(0, 1.02)
    ax.grid(True, alpha=0.3)
    ax.legend()

    # (b) multi-hit 回数分布
    ax = axes[0][1]
    if multi_counts:
        ks = sorted(multi_counts)
        ax.bar(ks, [multi_counts[k] for k in ks], color="#ef6c00")
        ax.set_yscale("log")
        ax.set_xlabel("access count (multi-hit entries, >=2)")
        ax.set_ylabel("num entries (log)")
        ax.set_title("(b) Reuse-count distribution among multi-hit")
        ax.grid(True, alpha=0.3)

    # (c) 構造特徴: degree 分布 (one vs multi)
    ax = axes[1][0]
    if deg_multi and deg_one:
        import math
        mx = max(max(deg_multi), max(deg_one))
        bins = [b for b in range(0, min(mx, 60) + 2)]
        ax.hist(deg_one, bins=bins, alpha=0.5, density=True, label="one-hit", color="#888")
        ax.hist(deg_multi, bins=bins, alpha=0.5, density=True, label="multi-hit", color="#2e7d32")
        ax.set_xlabel("node degree")
        ax.set_ylabel("density")
        ax.set_title("(c) Degree: one-hit vs multi-hit")
        ax.legend()
        ax.grid(True, alpha=0.3)

    # (d) 構造特徴: BFS距離分布 (one vs multi)
    ax = axes[1][1]
    if dist_multi and dist_one:
        mx = max(max(dist_multi), max(dist_one))
        bins = [b - 0.5 for b in range(0, mx + 2)]
        ax.hist(dist_one, bins=bins, alpha=0.5, density=True, label="one-hit", color="#888")
        ax.hist(dist_multi, bins=bins, alpha=0.5, density=True, label="multi-hit", color="#2e7d32")
        ax.set_xlabel("BFS distance from start")
        ax.set_ylabel("density")
        ax.set_title("(d) Distance: one-hit vs multi-hit")
        ax.legend()
        ax.grid(True, alpha=0.3)

    fig.suptitle(f"Reuse features — {label}", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = out_dir / "reuse_features.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"[OUT] {out}")


if __name__ == "__main__":
    main()
