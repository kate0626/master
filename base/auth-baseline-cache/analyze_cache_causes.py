#!/usr/bin/env python3
"""
キャッシュヒット率低迷の原因分析スクリプト（全体版）

主な分析:
  原因1: アクセス局所性の偏り（ジニ係数）
  原因2: キー空間の多様性（ノード vs エッジエンティティ）
  原因3: 容量スイープ（capacityを複数変えた場合の飽和曲線）
  追加1: BFS距離ごとのアクセス頻度分布（始点ノードを除外、箱ひげ図）
  追加2: ノード次数ごとのアクセス頻度分布（5点以上ある次数のみ、箱ひげ図）
  追加3: BFS距離とノード次数の関係
  追加4: エッジエンティティ edge_u_v の両端ノード次数分析
  追加5: エッジ両端ノード次数とエッジアクセス頻度の関係（箱ひげ図）

使い方例:
  python3 base/auth-baseline-cache/analyze_cache_causes.py \
    --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100\
    --graphs karate amazon0601 vldb \
    --graph-dir dataset/Louvain/graph

注意:
  - 次数分析には .gr ファイルが必要です。
  - .gr が見つからない場合でも、transition から復元できる範囲で BFS距離分析は行います。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple
import matplotlib.pyplot as plt

try:
    import matplotlib.pyplot as plt
    import numpy as np  # noqa: F401  # 必須ではないが、環境確認も兼ねて読み込む

    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False
    print("[warn] matplotlib/numpy not found. テキスト出力のみ。", file=sys.stderr)


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------
def gini(counts: List[float]) -> float:
    """アクセス回数の偏りを表すジニ係数を計算する。0に近いほど均等、1に近いほど偏りが大きい。"""
    if not counts or sum(counts) == 0:
        return 0.0
    arr = sorted(counts)
    n = len(arr)
    total = sum(arr)
    cum = 0.0
    for i, v in enumerate(arr):
        cum += (2 * (i + 1) - n - 1) * v
    return cum / (n * total)


def safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def select_policy_rows(rows: List[Dict], policy: str = "lru") -> List[Dict]:
    """代表ポリシーを優先して選ぶ。なければ全行を返す。"""
    selected = [r for r in rows if r.get("policy") == policy]
    return selected if selected else rows


# ---------------------------------------------------------------------------
# JSONから集計
# ---------------------------------------------------------------------------
def load_results(result_dir: Path) -> List[Dict]:
    rows = []
    for jf in sorted(result_dir.rglob("*_global_transition.json")):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        ctrl = d.get("controller", {})
        access = d.get("access", {})
        hit = d.get("cache hit", 0) or 0
        miss = d.get("cache miss", 0) or 0
        total = hit + miss
        hit_rate = hit / total if total > 0 else None

        node_counts = [v for k, v in access.items() if not k.startswith("edge_")]
        edge_counts = [v for k, v in access.items() if k.startswith("edge_")]
        all_counts = list(access.values())

        rows.append(
            {
                "file": str(jf),
                "graph": jf.parts[-3] if len(jf.parts) >= 3 else "?",
                "policy": ctrl.get("cache_policy", "?"),
                "capacity": ctrl.get("cache_capacity"),
                "start_node": ctrl.get("start_node"),
                "walks": ctrl.get("walks"),
                "hit_rate": hit_rate,
                "cache_hit": hit,
                "cache_miss": miss,
                "unique_entities": len(access),
                "unique_nodes": len(node_counts),
                "unique_edges": len(edge_counts),
                "gini_all": gini(all_counts),
                "gini_nodes": gini(node_counts),
                "gini_edges": gini(edge_counts),
                "edge_fraction": len(edge_counts) / max(1, len(access)),
            }
        )
    return rows


def load_results_for_graph(results_base: Path, graph: str) -> List[Dict]:
    rows = load_results(results_base / graph)
    for r in rows:
        r["graph"] = graph
    return rows


# ---------------------------------------------------------------------------
# 原因1: ジニ係数レポート
# ---------------------------------------------------------------------------
def report_gini(rows: List[Dict]) -> None:
    print("\n" + "=" * 90)
    print("原因1: アクセス局所性（ジニ係数）")
    print("=" * 90)
    print(
        f"{'graph':<15} {'policy':<8} {'cap':>6} {'walks':>7} "
        f"{'hit_rate':>9} {'gini_all':>9} {'gini_node':>10} {'gini_edge':>10} {'uniq_ent':>9}"
    )
    print("-" * 90)

    for r in rows:
        if r.get("hit_rate") is None:
            continue
        cap = r.get("capacity") or 0
        walks = r.get("walks") or 0
        print(
            f"{r['graph']:<15} {r['policy']:<8} {cap:>6} {walks:>7} "
            f"{r['hit_rate']:>9.3f} {r['gini_all']:>9.3f} "
            f"{r['gini_nodes']:>10.3f} {r['gini_edges']:>10.3f} "
            f"{r['unique_entities']:>9}"
        )


# ---------------------------------------------------------------------------
# 原因2: キー空間の比較（ノード vs エッジ）
# ---------------------------------------------------------------------------
def report_key_diversity(rows: List[Dict]) -> None:
    print("\n" + "=" * 90)
    print("原因2: キー空間の多様性（エッジエンティティの比率）")
    print("=" * 90)
    print(
        f"{'graph':<15} {'policy':<8} {'cap':>6} {'walks':>7} "
        f"{'hit_rate':>9} {'uniq_node':>10} {'uniq_edge':>10} {'edge_frac':>10}"
    )
    print("-" * 90)

    for r in rows:
        if r.get("hit_rate") is None:
            continue
        cap = r.get("capacity") or 0
        walks = r.get("walks") or 0
        print(
            f"{r['graph']:<15} {r['policy']:<8} {cap:>6} {walks:>7} "
            f"{r['hit_rate']:>9.3f} {r['unique_nodes']:>10} "
            f"{r['unique_edges']:>10} {r['edge_fraction']:>10.3f}"
        )


# ---------------------------------------------------------------------------
# 原因3: 容量スイープ（飽和曲線）
# ---------------------------------------------------------------------------
def report_capacity_sweep(rows: List[Dict], graphs: List[str]) -> None:
    print("\n" + "=" * 90)
    print("原因3: 容量スイープ（飽和曲線）")
    print("=" * 90)

    grouped: Dict[str, Dict[str, List[Tuple[int, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for r in rows:
        if r.get("hit_rate") is None or r.get("capacity") is None:
            continue
        grouped[r["graph"]][r["policy"]].append((r["capacity"], r["hit_rate"]))

    for graph in graphs:
        if graph not in grouped:
            continue
        print(f"\n  [{graph}]")
        print(f"  {'policy':<8} ", end="")
        caps = sorted({cap for pol in grouped[graph].values() for cap, _ in pol})
        for c in caps:
            print(f"  cap={c:>5}", end="")
        print()

        for policy in sorted(grouped[graph]):
            cap_to_rates: Dict[int, List[float]] = defaultdict(list)
            for cap, rate in grouped[graph][policy]:
                cap_to_rates[cap].append(rate)
            print(f"  {policy:<8} ", end="")
            for c in caps:
                rates = cap_to_rates.get(c, [])
                if rates:
                    print(f"  {sum(rates)/len(rates):>8.3f}", end="")
                else:
                    print(f"  {'---':>8}", end="")
            print()


# ---------------------------------------------------------------------------
# グラフ構築（.gr ファイルから）
# ---------------------------------------------------------------------------
def build_bipartite_from_gr(
    gr_path: Path,
) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """
    .gr ファイルから二部グラフを構築し、各エンティティの次数を返す。

    戻り値:
      adj:    node <-> edge_entity の二部グラフ隣接リスト
      degree: 二部グラフ上の次数

    注意:
      - 元グラフのノードの次数は、この二部グラフ上でも元グラフ次数と一致する。
      - edge_u_v の次数は基本的に2になる。
    """
    adj: Dict[str, List[str]] = defaultdict(list)
    degree: Dict[str, int] = defaultdict(int)

    with gr_path.open(encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue

            u, v = parts[0], parts[1]
            iu, iv = safe_int(u), safe_int(v)
            if iu is not None and iv is not None:
                a, b = (u, v) if iu <= iv else (v, u)
            else:
                a, b = sorted([u, v])

            eid = f"edge_{a}_{b}"

            # 二部グラフの辺: node ↔ edge_entity
            adj[u].append(eid)
            adj[v].append(eid)
            adj[eid].append(u)
            adj[eid].append(v)

    for node, neighbors in adj.items():
        degree[node] = len(neighbors)

    return dict(adj), dict(degree)


def parse_edge_entity(entity: str) -> Optional[Tuple[str, str]]:
    """
    edge_u_v から両端ノード u, v を取り出す。

    例:
      edge_1_2 -> ("1", "2")
    """
    if not entity.startswith("edge_"):
        return None

    rest = entity[len("edge_") :]
    parts = rest.split("_")
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def get_empty_edge_features() -> Dict[str, Optional[float]]:
    return {
        "edge_u": None,
        "edge_v": None,
        "edge_deg_u": None,
        "edge_deg_v": None,
        "edge_min_degree": None,
        "edge_max_degree": None,
        "edge_avg_degree": None,
        "edge_degree_sum": None,
        "edge_degree_diff": None,
        "edge_degree_ratio": None,
        "edge_degree_type": None,
    }


def classify_edge_by_endpoint_degree(
    edge_min_degree: Optional[float],
    edge_max_degree: Optional[float],
    hub_threshold: int = 10,
) -> Optional[str]:
    """
    エッジを両端ノードの次数で分類する。

    low-low: 両端とも hub_threshold 未満
    hub-low: 片側だけ hub_threshold 以上
    hub-hub: 両端とも hub_threshold 以上
    """
    if edge_min_degree is None or edge_max_degree is None:
        return None

    mn = int(edge_min_degree)
    mx = int(edge_max_degree)

    if mx < hub_threshold:
        return "low-low"
    if mn >= hub_threshold:
        return "hub-hub"
    return "hub-low"


def get_edge_endpoint_degree_features(
    entity: str,
    gr_degree: Optional[Dict[str, int]],
    hub_threshold: int = 10,
) -> Dict[str, Optional[float]]:
    """
    edge_u_v の両端ノード u, v の次数から、エッジ用の特徴量を作る。

    edgeエンティティ自体の二部グラフ上の次数は基本的に2なので、
    ここでは edge_u_v の両端ノード次数を分析対象にする。
    """
    features = get_empty_edge_features()

    if gr_degree is None:
        return features

    parsed = parse_edge_entity(entity)
    if parsed is None:
        return features

    u, v = parsed
    du = gr_degree.get(u)
    dv = gr_degree.get(v)

    if du is None or dv is None:
        return features

    mn = min(du, dv)
    mx = max(du, dv)

    features.update(
        {
            "edge_u": u,
            "edge_v": v,
            "edge_deg_u": du,
            "edge_deg_v": dv,
            "edge_min_degree": mn,
            "edge_max_degree": mx,
            "edge_avg_degree": (du + dv) / 2,
            "edge_degree_sum": du + dv,
            "edge_degree_diff": abs(du - dv),
            "edge_degree_ratio": mx / max(1, mn),
            "edge_degree_type": classify_edge_by_endpoint_degree(
                mn, mx, hub_threshold=hub_threshold
            ),
        }
    )
    return features


# ---------------------------------------------------------------------------
# BFS距離（transition フィールドから復元、.gr 不要）
# ---------------------------------------------------------------------------
def bfs_from_transition(transition: Dict[str, int], start_node: int) -> Dict[str, int]:
    """
    transition フィールド ("A->B": count) から無向グラフを再構成し、
    start_node からの BFS 距離を返す。

    注意:
      これは元グラフ全体ではなく、実際に観測された transition 上での距離。
    """
    adj: Dict[str, set] = defaultdict(set)
    for key in transition:
        if "->" not in key:
            continue
        a, b = key.split("->", 1)
        adj[a].add(b)
        adj[b].add(a)

    start = str(start_node)
    dist: Dict[str, int] = {start: 0}
    q: deque = deque([start])
    while q:
        node = q.popleft()
        for nb in adj[node]:
            if nb not in dist:
                dist[nb] = dist[node] + 1
                q.append(nb)
    return dist


# ---------------------------------------------------------------------------
# 次数・BFS距離の分析（1つの result JSON に対して）
# ---------------------------------------------------------------------------
def analyze_one_result(
    result_path: Path,
    gr_adj: Optional[Dict[str, List[str]]],
    gr_degree: Optional[Dict[str, int]],
) -> List[Dict]:
    """
    1つの global_transition.json を読んで、
    エンティティごとに access, degree, bfs_dist, エッジ両端次数特徴量を返す。
    """
    _ = gr_adj  # 現状は次数辞書のみ使用。将来の拡張用に引数は残す。

    try:
        d = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    ctrl = d.get("controller", {})
    start_node = ctrl.get("start_node")
    if start_node is None:
        return []

    access = d.get("access", {})
    transition = d.get("transition", {})
    hit = d.get("cache hit", 0) or 0
    miss = d.get("cache miss", 0) or 0
    hit_rate = hit / (hit + miss) if (hit + miss) > 0 else None

    bfs = bfs_from_transition(transition, start_node)

    rows = []
    for entity, cnt in access.items():
        is_edge = entity.startswith("edge_")

        degree = None
        if gr_degree is not None:
            degree = gr_degree.get(entity)
        elif is_edge:
            degree = (
                2  # .gr がない場合の最低限の扱い。二部グラフ上でエッジは基本的に2。
            )

        bfs_dist = bfs.get(entity)

        edge_features = (
            get_edge_endpoint_degree_features(entity, gr_degree)
            if is_edge
            else get_empty_edge_features()
        )

        row = {
            "file": str(result_path),
            "start_node": start_node,
            "policy": ctrl.get("cache_policy", "?"),
            "capacity": ctrl.get("cache_capacity"),
            "walks": ctrl.get("walks"),
            "hit_rate": hit_rate,
            "entity": entity,
            "is_edge": is_edge,
            "access": cnt,
            "degree": degree,
            "bfs_dist": bfs_dist,
        }
        row.update(edge_features)
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# テキストレポート: エッジ両端次数分析
# ---------------------------------------------------------------------------
def report_edge_endpoint_degrees(lru_rows: List[Dict]) -> None:
    """エッジエンティティについて、両端ノードの次数からアクセス頻度を分析する。"""
    edge_rows = [
        r for r in lru_rows if r.get("is_edge") and r.get("edge_max_degree") is not None
    ]

    if not edge_rows:
        print(
            "\n  エッジ両端次数分析: データなし（.gr 未指定、またはedge_u_v形式でない可能性）"
        )
        return

    print("\n  エッジ両端ノード次数分析（edge_u_v の u, v の次数）:")
    print(
        f"  {'feature':>18} {'bin':>10} {'edges':>8} "
        f"{'total_access':>13} {'avg_access':>11} {'median':>9}"
    )
    print(f"  {'-'*78}")

    features = [
        ("edge_min_degree", "min_endpoint_deg"),
        ("edge_max_degree", "max_endpoint_deg"),
        ("edge_avg_degree", "avg_endpoint_deg"),
        ("edge_degree_diff", "degree_diff"),
        ("edge_degree_sum", "degree_sum"),
    ]

    for feature, label in features:
        val_acc: Dict[int, List[int]] = defaultdict(list)
        for r in edge_rows:
            v = r.get(feature)
            if v is None:
                continue
            val_acc[int(v)].append(int(r["access"]))

        for v in sorted(val_acc):
            accs = val_acc[v]
            print(
                f"  {label:>18} {v:>10} {len(accs):>8} "
                f"{sum(accs):>13} {sum(accs)/len(accs):>11.1f} {median(accs):>9.1f}"
            )


def report_edge_degree_type(lru_rows: List[Dict]) -> None:
    """エッジを low-low / hub-low / hub-hub に分けてアクセス頻度を見る。"""
    edge_rows = [
        r
        for r in lru_rows
        if r.get("is_edge") and r.get("edge_degree_type") is not None
    ]

    if not edge_rows:
        return

    type_acc: Dict[str, List[int]] = defaultdict(list)
    for r in edge_rows:
        type_acc[str(r["edge_degree_type"])].append(int(r["access"]))

    print("\n  エッジタイプ別アクセス頻度（hub_threshold=10）:")
    print(
        f"  {'type':>10} {'edges':>8} {'total_access':>13} "
        f"{'avg_access':>11} {'median':>9}"
    )
    print(f"  {'-'*60}")

    for t in ["low-low", "hub-low", "hub-hub"]:
        if t not in type_acc:
            continue
        accs = type_acc[t]
        print(
            f"  {t:>10} {len(accs):>8} {sum(accs):>13} "
            f"{sum(accs)/len(accs):>11.1f} {median(accs):>9.1f}"
        )


def report_bfs_vs_degree(lru_rows: List[Dict]) -> None:
    """BFS距離とノード次数の関係を見る。ノードのみ、始点bfs_dist=0は除外。"""
    node_rows = [
        r
        for r in lru_rows
        if not r.get("is_edge")
        and r.get("degree") is not None
        and r.get("bfs_dist") is not None
        and r.get("bfs_dist") != 0
    ]

    if not node_rows:
        return

    dist_deg: Dict[int, List[int]] = defaultdict(list)
    for r in node_rows:
        dist_deg[int(r["bfs_dist"])].append(int(r["degree"]))

    print("\n  BFS距離とノード次数の関係（ノードのみ、始点除外）:")
    print(
        f"  {'bfs_dist':>9} {'nodes':>8} {'avg_degree':>12} "
        f"{'median_degree':>14} {'max_degree':>11}"
    )
    print(f"  {'-'*65}")

    for dist in sorted(dist_deg):
        vals = dist_deg[dist]
        print(
            f"  {dist:>9} {len(vals):>8} {sum(vals)/len(vals):>12.2f} "
            f"{median(vals):>14.2f} {max(vals):>11}"
        )


# ---------------------------------------------------------------------------
# 次数・BFS距離のテキストレポート
# ---------------------------------------------------------------------------
def report_degree_and_distance(
    results_base: Path,
    graphs: List[str],
    gr_paths: Dict[str, Optional[Path]],
) -> Dict[str, List[Dict]]:
    """グラフごとに次数・BFS距離・エッジ両端次数の分布をレポートし、詳細行を返す。"""
    all_detail: Dict[str, List[Dict]] = {}

    for graph in graphs:
        gr_path = gr_paths.get(graph)
        gr_adj, gr_degree = None, None
        if gr_path and gr_path.exists():
            gr_adj, gr_degree = build_bipartite_from_gr(gr_path)
            print(f"[graph] {graph}: .gr ロード済み ({len(gr_degree)} エンティティ)")
        else:
            print(f"[graph] {graph}: .gr なし → transition から BFS のみ復元")

        detail_rows: List[Dict] = []
        for jf in sorted((results_base / graph).rglob("*_global_transition.json")):
            detail_rows.extend(analyze_one_result(jf, gr_adj, gr_degree))

        if not detail_rows:
            continue

        all_detail[graph] = detail_rows

        print(f"\n{'='*90}")
        print(f"次数・BFS距離・エッジ両端次数の分布: [{graph}]")
        print(f"{'='*90}")

        lru_rows = select_policy_rows(detail_rows, policy="lru")

        # --- BFS距離分布レポート（始点除外） ---
        from_start: Dict[int, Dict[int, List[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for r in lru_rows:
            if r.get("bfs_dist") is None:
                continue
            if r.get("bfs_dist") == 0:
                continue
            from_start[int(r["start_node"])][int(r["bfs_dist"])].append(
                int(r["access"])
            )

        print("\n  BFS距離ごとのアクセス頻度（始点ノード bfs_dist=0 は除外）:")
        print(
            f"  {'bfs_dist':>9} {'entities':>9} {'total_access':>13} "
            f"{'avg_access':>11} {'median':>9} {'note'}"
        )
        print(f"  {'-'*70}")

        all_dists: Dict[int, List[int]] = defaultdict(list)
        for sn_data in from_start.values():
            for dist, accs in sn_data.items():
                all_dists[dist].extend(accs)

        for dist in sorted(all_dists):
            accs = all_dists[dist]
            total = sum(accs)
            avg = total / len(accs)
            note = "(edges)" if dist % 2 == 1 else "(nodes)"
            print(
                f"  {dist:>9} {len(accs):>9} {total:>13} "
                f"{avg:>11.1f} {median(accs):>9.1f} {note}"
            )

        # --- ノード次数分布レポート ---
        if gr_degree is not None:
            print("\n  Node degree distribution (from .gr):")
            print(
                f"  {'degree':>12} {'nodes':>9} {'total_access':>13} "
                f"{'avg_access':>11} {'median':>9}"
            )
            print(f"  {'-'*65}")
            node_rows = [
                r
                for r in lru_rows
                if not r.get("is_edge") and r.get("degree") is not None
            ]
            deg_acc: Dict[int, List[int]] = defaultdict(list)
            for r in node_rows:
                dv = r.get("degree")
                if dv is not None:
                    deg_acc[int(dv)].append(int(r["access"]))

            for dv in sorted(deg_acc):
                accs = deg_acc[dv]
                print(
                    f"  {dv:>12} {len(accs):>9} {sum(accs):>13} "
                    f"{sum(accs)/len(accs):>11.1f} {median(accs):>9.1f}"
                )

            # 追加: BFS距離とノード次数の関係
            report_bfs_vs_degree(lru_rows)

            # 追加: エッジ両端ノード次数の分析
            report_edge_endpoint_degrees(lru_rows)
            report_edge_degree_type(lru_rows)

    return all_detail


# ---------------------------------------------------------------------------
# プロット: 原因3 飽和曲線
# ---------------------------------------------------------------------------
def plot_saturation(rows: List[Dict], graphs: List[str], out_dir: Path) -> None:
    if not HAS_PLOT:
        return

    fig, axes = plt.subplots(
        1, len(graphs), figsize=(5 * len(graphs), 4), squeeze=False
    )
    target_policies = ["lru", "arc"]

    for gi, graph in enumerate(graphs):
        ax = axes[0][gi]
        grouped: Dict[str, Dict[int, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for r in rows:
            if (
                r.get("graph") != graph
                or r.get("hit_rate") is None
                or r.get("capacity") is None
            ):
                continue
            grouped[str(r["policy"])][int(r["capacity"])].append(float(r["hit_rate"]))

        for policy in target_policies:
            if policy not in grouped:
                continue
            caps = sorted(grouped[policy])
            means = [sum(grouped[policy][c]) / len(grouped[policy][c]) for c in caps]
            ax.plot(caps, means, marker="o", label=policy)

        ax.set_title(graph)
        ax.set_xlabel("cache capacity")
        ax.set_ylabel("avg hit rate")
        ax.set_ylim(0, 1)
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = out_dir / "cause3_saturation_curve.png"
    plt.savefig(out_path, dpi=120)
    print(f"\n[plot] Saved saturation curve: {out_path}")
    plt.close()


# ---------------------------------------------------------------------------
# プロット: 原因1 ジニ係数 vs ヒット率
# ---------------------------------------------------------------------------
def plot_gini_vs_hitrate(rows: List[Dict], out_dir: Path) -> None:
    if not HAS_PLOT:
        return

    valid = [
        r
        for r in rows
        if r.get("hit_rate") is not None and r.get("gini_all") is not None
    ]
    if not valid:
        return

    fig, ax = plt.subplots(figsize=(6, 4))
    graphs = sorted({r["graph"] for r in valid})
    colors = plt.cm.tab10.colors  # type: ignore

    for gi, g in enumerate(graphs):
        pts = [r for r in valid if r["graph"] == g]
        ax.scatter(
            [p["gini_all"] for p in pts],
            [p["hit_rate"] for p in pts],
            label=g,
            color=colors[gi % len(colors)],
            alpha=0.7,
            s=30,
        )

    ax.set_xlabel("Gini coefficient (access distribution)")
    ax.set_ylabel("cache hit rate")
    ax.set_title("Cause 1: Gini coefficient vs hit rate")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = out_dir / "cause1_gini_vs_hitrate.png"
    plt.savefig(out_path, dpi=120)
    print(f"[plot] Saved Gini vs hit rate scatter: {out_path}")
    plt.close()


# ---------------------------------------------------------------------------
# プロット: BFS距離箱ひげ / ノード次数箱ひげ / BFS距離と次数
# ---------------------------------------------------------------------------
def plot_degree_distance(all_detail: Dict[str, List[Dict]], out_dir: Path) -> None:
    if not HAS_PLOT:
        return

    graphs = list(all_detail.keys())
    if not graphs:
        return

    # 図1: BFS距離 vs アクセス頻度（始点除外、箱ひげ）
    fig, axes = plt.subplots(
        1, len(graphs), figsize=(5 * len(graphs), 4), squeeze=False
    )

    for gi, graph in enumerate(graphs):
        ax = axes[0][gi]
        rows = [
            r
            for r in all_detail[graph]
            if r.get("bfs_dist") is not None
            and r.get("bfs_dist") != 0
            and r.get("policy") == "lru"
        ]
        if not rows:
            rows = [
                r
                for r in all_detail[graph]
                if r.get("bfs_dist") is not None and r.get("bfs_dist") != 0
            ]

        dist_acc: Dict[int, List[int]] = defaultdict(list)
        for r in rows:
            dist_acc[int(r["bfs_dist"])].append(int(r["access"]))

        dists = sorted(dist_acc.keys())
        data = [dist_acc[d] for d in dists]

        if data:
            ax.boxplot(
                data, positions=dists, widths=0.6, patch_artist=False, showfliers=True
            )
            means = [sum(dist_acc[d]) / len(dist_acc[d]) for d in dists]
            ax.plot(dists, means, marker="o", linestyle="--", label="mean")
            ax.legend()

        ax.set_xlabel("BFS distance from start node (exclude 0)")
        ax.set_ylabel("access count")
        ax.set_title(f"{graph}\nAccess frequency by BFS distance")
        ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out_path = out_dir / "bfs_dist_vs_access_boxplot.png"
    plt.savefig(out_path, dpi=120)
    print(f"\n[plot] Saved access frequency by BFS distance: {out_path}")
    plt.close()

    # 図2: ノード次数 vs アクセス頻度（5点以上ある次数のみ、箱ひげ）
    graphs_with_degree = [
        g
        for g in graphs
        if any(
            r.get("degree") is not None and not r.get("is_edge") for r in all_detail[g]
        )
    ]
    if not graphs_with_degree:
        return

    fig, axes = plt.subplots(
        1,
        len(graphs_with_degree),
        figsize=(6 * len(graphs_with_degree), 4),
        squeeze=False,
    )

    for gi, graph in enumerate(graphs_with_degree):
        ax = axes[0][gi]
        rows = [
            r
            for r in all_detail[graph]
            if not r.get("is_edge")
            and r.get("degree") is not None
            and r.get("policy") == "lru"
        ]
        if not rows:
            rows = [
                r
                for r in all_detail[graph]
                if not r.get("is_edge") and r.get("degree") is not None
            ]

        deg_acc: Dict[int, List[int]] = defaultdict(list)
        for r in rows:
            deg_acc[int(r["degree"])].append(int(r["access"]))

        valid_degrees = sorted(deg_acc.keys())
        data = [deg_acc[d] for d in valid_degrees]

        if data:
            positions = list(range(len(valid_degrees)))
            ax.boxplot(
                data,
                positions=positions,
                widths=0.6,
                patch_artist=False,
                showfliers=True,
            )
            means = [sum(deg_acc[d]) / len(deg_acc[d]) for d in valid_degrees]
            ax.plot(positions, means, marker="o", linestyle="--", label="mean")
            step = max(1, len(valid_degrees) // 12)
            ax.set_xticks(positions[::step])
            ax.set_xticklabels(valid_degrees[::step], rotation=45)
            ax.legend()
        else:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")

        ax.set_xlabel("node degree")
        ax.set_ylabel("access count")
        ax.set_title(f"{graph}\nAccess frequency by node degree")
        ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out_path = out_dir / "node_degree_vs_access_boxplot.png"
    plt.savefig(out_path, dpi=120)
    print(f"[plot] Saved access frequency by node degree: {out_path}")
    plt.close()

    # 図3: BFS距離ごとのノード次数分布（半径と次数の関係）
    fig, axes = plt.subplots(
        1,
        len(graphs_with_degree),
        figsize=(6 * len(graphs_with_degree), 4),
        squeeze=False,
    )

    for gi, graph in enumerate(graphs_with_degree):
        ax = axes[0][gi]
        rows = [
            r
            for r in all_detail[graph]
            if not r.get("is_edge")
            and r.get("degree") is not None
            and r.get("bfs_dist") is not None
            and r.get("bfs_dist") != 0
            and r.get("policy") == "lru"
        ]
        if not rows:
            rows = [
                r
                for r in all_detail[graph]
                if not r.get("is_edge")
                and r.get("degree") is not None
                and r.get("bfs_dist") is not None
                and r.get("bfs_dist") != 0
            ]

        dist_deg: Dict[int, List[int]] = defaultdict(list)
        for r in rows:
            dist_deg[int(r["bfs_dist"])].append(int(r["degree"]))

        dists = sorted(dist_deg.keys())
        data = [dist_deg[d] for d in dists]

        if data:
            ax.boxplot(
                data, positions=dists, widths=0.6, patch_artist=False, showfliers=True
            )
            means = [sum(dist_deg[d]) / len(dist_deg[d]) for d in dists]
            ax.plot(dists, means, marker="o", linestyle="--", label="mean degree")
            ax.legend()

        ax.set_xlabel("BFS distance from start node (nodes only, exclude 0)")
        ax.set_ylabel("node degree")
        ax.set_title(f"{graph}\nNode degree by BFS distance")
        ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out_path = out_dir / "bfs_dist_vs_node_degree_boxplot.png"
    plt.savefig(out_path, dpi=120)
    print(f"[plot] Saved node degree by BFS distance: {out_path}")
    plt.close()


# ---------------------------------------------------------------------------
# プロット: エッジ両端ノード次数とエッジアクセス頻度
# ---------------------------------------------------------------------------
def plot_edge_endpoint_degree(all_detail: Dict[str, List[Dict]], out_dir: Path) -> None:
    if not HAS_PLOT:
        return

    graphs = [
        g
        for g in all_detail.keys()
        if any(
            r.get("is_edge") and r.get("edge_max_degree") is not None
            for r in all_detail[g]
        )
    ]
    if not graphs:
        return

    features = [
        ("edge_max_degree", "max endpoint degree"),
        ("edge_avg_degree", "avg endpoint degree"),
        # ("edge_degree_diff", "endpoint degree diff"),
        # ("edge_degree_sum", "endpoint degree sum"),
    ]

    for feature, xlabel in features:
        fig, axes = plt.subplots(
            1, len(graphs), figsize=(6 * len(graphs), 4), squeeze=False
        )

        for gi, graph in enumerate(graphs):
            ax = axes[0][gi]
            rows = [
                r
                for r in all_detail[graph]
                if r.get("is_edge")
                and r.get(feature) is not None
                and r.get("policy") == "lru"
            ]
            if not rows:
                rows = [
                    r
                    for r in all_detail[graph]
                    if r.get("is_edge") and r.get(feature) is not None
                ]

            feat_acc: Dict[int, List[int]] = defaultdict(list)
            for r in rows:
                feat_acc[int(r[feature])].append(int(r["access"]))

            xs = sorted(feat_acc.keys())
            data = [feat_acc[x] for x in xs]

            if data:
                positions = list(range(len(xs)))
                ax.boxplot(
                    data,
                    positions=positions,
                    widths=0.6,
                    patch_artist=False,
                    showfliers=True,
                )
                means = [sum(feat_acc[x]) / len(feat_acc[x]) for x in xs]
                ax.plot(positions, means, marker="o", linestyle="--", label="mean")
                step = max(1, len(xs) // 12)
                ax.set_xticks(positions[::step])
                ax.set_xticklabels(xs[::step], rotation=45)
                ax.legend()
            else:
                ax.text(0.5, 0.5, "No data", ha="center", va="center")

            ax.set_xlabel(xlabel)
            ax.set_ylabel("edge entity access count")
            ax.set_title(f"{graph}\n{xlabel} vs edge access")
            ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        out_path = out_dir / f"edge_{feature}_vs_access_boxplot.png"
        plt.savefig(out_path, dpi=120)
        print(f"[plot] Saved edge endpoint degree plot: {out_path}")
        plt.close()


def plot_edge_degree_type(all_detail: Dict[str, List[Dict]], out_dir: Path) -> None:
    """low-low / hub-low / hub-hub ごとのエッジアクセス頻度を箱ひげで見る。"""
    if not HAS_PLOT:
        return
    graphs = [
        g
        for g in all_detail.keys()
        if any(
            r.get("is_edge") and r.get("edge_degree_type") is not None
            for r in all_detail[g]
        )
    ]

    if not graphs:
        return

    fig, axes = plt.subplots(
        1, len(graphs), figsize=(5 * len(graphs), 4), squeeze=False
    )
    order = ["low-low", "hub-low", "hub-hub"]

    for gi, graph in enumerate(graphs):
        ax = axes[0][gi]
        rows = [
            r
            for r in all_detail[graph]
            if r.get("is_edge")
            and r.get("edge_degree_type") is not None
            and r.get("policy") == "lru"
        ]
        if not rows:
            rows = [
                r
                for r in all_detail[graph]
                if r.get("is_edge") and r.get("edge_degree_type") is not None
            ]

        type_acc: Dict[str, List[int]] = defaultdict(list)
        for r in rows:
            type_acc[str(r["edge_degree_type"])].append(int(r["access"]))

        labels = [t for t in order if t in type_acc]
        data = [type_acc[t] for t in labels]

        if data:
            positions = list(range(len(labels)))
            ax.boxplot(
                data,
                positions=positions,
                widths=0.6,
                patch_artist=False,
                showfliers=True,
            )
            means = [sum(type_acc[t]) / len(type_acc[t]) for t in labels]
            ax.plot(positions, means, marker="o", linestyle="--", label="mean")
            ax.set_xticks(positions)
            ax.set_xticklabels(labels, rotation=20)
            ax.legend()
        ax.set_xlabel("edge type by endpoint degree")
        ax.set_ylabel("edge entity access count")
        ax.set_title(f"{graph}\nAccess frequency by edge type")
        ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    out_path = out_dir / "edge_degree_type_vs_access_boxplot.png"
    plt.savefig(out_path, dpi=120)
    print(f"[plot] Saved access frequency by edge type: {out_path}")
    plt.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="キャッシュヒット率低迷の原因分析")
    parser.add_argument(
        "--results-dir",
        default="base/auth-baseline-cache/results",
        help="結果ルートディレクトリ",
    )
    parser.add_argument(
        "--graphs",
        nargs="+",
        default=["karate", "amazon0601", "vldb"],
        help="対象グラフ名",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="プロット出力先（デフォルト: --results-dir と同じ）",
    )
    parser.add_argument(
        "--graph-dir",
        default="dataset/Louvain/graph",
        help=".gr ファイルのディレクトリ。グラフ名.gr を自動検索する。",
    )
    parser.add_argument(
        "--plot-saturation",
        action="store_true",
        help="capacityを複数変えた実験がある場合、飽和曲線も出力する。",
    )
    args = parser.parse_args()

    results_base = Path(args.results_dir)
    out_dir = Path(args.out_dir) if args.out_dir else results_base
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    for graph in args.graphs:
        gdir = results_base / graph
        if not gdir.exists():
            print(f"[skip] {gdir} が存在しません", file=sys.stderr)
            continue
        g_rows = load_results_for_graph(results_base, graph)
        print(f"[load] {graph}: {len(g_rows)} 件のJSONを読み込みました")
        rows.extend(g_rows)

    if not rows:
        print("結果JSONが見つかりません。--results-dir を確認してください。")
        return

    report_gini(rows)
    report_key_diversity(rows)
    report_capacity_sweep(rows, args.graphs)

    # .gr ファイルの検索（グラフ名.gr を探す）
    graph_dir = Path(args.graph_dir)
    gr_paths: Dict[str, Optional[Path]] = {}
    for graph in args.graphs:
        candidate = graph_dir / f"{graph}.gr"
        gr_paths[graph] = candidate if candidate.exists() else None
        if gr_paths[graph] is None:
            print(
                f"[info] {graph}.gr が {graph_dir} に見つかりません（transition のみで BFS を復元）"
            )

    all_detail = report_degree_and_distance(results_base, args.graphs, gr_paths)

    if HAS_PLOT:
        if args.plot_saturation:
            plot_saturation(rows, args.graphs, out_dir)
        plot_gini_vs_hitrate(rows, out_dir)
        plot_degree_distance(all_detail, out_dir)
        plot_edge_endpoint_degree(all_detail, out_dir)
        plot_edge_degree_type(all_detail, out_dir)
    else:
        print("\n[info] pip install matplotlib numpy でグラフ出力が有効になります")


if __name__ == "__main__":
    main()
