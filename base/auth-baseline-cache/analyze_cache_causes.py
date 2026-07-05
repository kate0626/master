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
    
    
python3 base/auth-baseline-cache/analyze_cache_causes.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
  --graphs amazon0601 vldb \
  --graph-dir dataset/Louvain/graph
  --policy none
  
  python3 base/auth-baseline-cache/analyze_cache_causes.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
  --graphs amazon0601 vldb \
  --graph-dir <amazon0601.gr / vldb.gr のあるディレクトリ> \
  --policy none \
  --focus-dist 2 4

注意:
  - 次数分析には .gr ファイルが必要です。
  - .gr が見つからない場合でも、transition から復元できる範囲で BFS距離分析は行います。
"""

from __future__ import annotations

import argparse
import csv
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


def bfs_distances_bipartite(
    gr_adj: Dict[str, List[str]], start_node: int, max_dist: Optional[int] = None
) -> Dict[str, int]:
    """元グラフ (.gr) 全体の二部展開上で start_node からの**真の**最短距離を返す。
    transition (歩いた辺のみ) ではなくグラフ全体を使うので、迂回や未訪問に左右されない。
    距離の単位は二部 hop: dist=2 が元グラフで 1 ホップ隣のノード、奇数はエッジ実体。
    max_dist を与えると、その距離までで打ち切る (高速化)。
    """
    start = str(start_node)
    if start not in gr_adj:
        return {start: 0}
    dist: Dict[str, int] = {start: 0}
    q: deque = deque([start])
    while q:
        node = q.popleft()
        du = dist[node]
        if max_dist is not None and du >= max_dist:
            continue
        for nb in gr_adj.get(node, ()):
            if nb not in dist:
                dist[nb] = du + 1
                q.append(nb)
    return dist


# ---------------------------------------------------------------------------
# 次数・BFS距離の分析（1つの result JSON に対して）
# ---------------------------------------------------------------------------
def analyze_one_result(
    result_path: Path,
    gr_adj: Optional[Dict[str, List[str]]],
    gr_degree: Optional[Dict[str, int]],
    dist_source: str = "transition",
    gr_dist_cache: Optional[Dict[int, Dict[str, int]]] = None,
) -> List[Dict]:
    """
    1つの global_transition.json を読んで、
    エンティティごとに access, degree, bfs_dist, エッジ両端次数特徴量を返す。

    dist_source:
      "transition" = 歩いた遷移上の距離 (.gr 不要、迂回/未訪問に依存)
      "graph"      = .gr 全体からの真の最短距離 (gr_adj 必須)
    gr_dist_cache: start_node ごとの真距離 dict をキャッシュして再計算を防ぐ。
    """
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

    if dist_source == "graph" and gr_adj is not None:
        sn = int(start_node)
        if gr_dist_cache is not None and sn in gr_dist_cache:
            bfs = gr_dist_cache[sn]
        else:
            bfs = bfs_distances_bipartite(gr_adj, sn)
            if gr_dist_cache is not None:
                gr_dist_cache[sn] = bfs
    else:
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
            "alpha": ctrl.get("alpha"),
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
    dist_source: str = "transition",
) -> Tuple[
    Dict[str, List[Dict]], Dict[str, Tuple[Dict[str, List[str]], Dict[str, int]]]
]:
    """グラフごとに次数・BFS距離・エッジ両端次数の分布をレポートし、詳細行を返す。"""
    all_detail: Dict[str, List[Dict]] = {}
    gr_cache: Dict[str, Tuple[Dict[str, List[str]], Dict[str, int]]] = {}

    for graph in graphs:
        gr_path = gr_paths.get(graph)
        gr_adj, gr_degree = None, None
        if gr_path and gr_path.exists():
            gr_adj, gr_degree = build_bipartite_from_gr(gr_path)
            gr_cache[graph] = (gr_adj, gr_degree)
            print(f"[graph] {graph}: .gr ロード済み ({len(gr_degree)} エンティティ)")
        else:
            print(f"[graph] {graph}: .gr なし → transition から BFS のみ復元")

        use_graph_dist = dist_source == "graph" and gr_adj is not None
        if dist_source == "graph" and gr_adj is None:
            print(
                f"[graph] {graph}: dist-source=graph 指定だが .gr が無いため "
                "transition 距離にフォールバック"
            )
        print(
            f"[graph] {graph}: 距離 = "
            f"{'真の最短距離(.gr 全体BFS)' if use_graph_dist else '観測経路(transition)BFS'}"
        )

        gr_dist_cache: Dict[int, Dict[str, int]] = {}
        detail_rows: List[Dict] = []
        for jf in sorted((results_base / graph).rglob("*_global_transition.json")):
            detail_rows.extend(
                analyze_one_result(
                    jf,
                    gr_adj,
                    gr_degree,
                    dist_source="graph" if use_graph_dist else "transition",
                    gr_dist_cache=gr_dist_cache,
                )
            )

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

    return all_detail, gr_cache


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

    # 図1.5: BFS距離 vs アクセス頻度（始点除外、1点=1エンティティ）
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

        if rows:
            xs = [int(r["bfs_dist"]) for r in rows]
            ys = [max(1, int(r["access"])) for r in rows]
            ax.scatter(xs, ys, s=12, alpha=0.24, color="#888", label="entities")

            ax2 = ax.twinx()
            node_dist_acc: Dict[int, List[int]] = defaultdict(list)
            edge_dist_acc: Dict[int, List[int]] = defaultdict(list)
            for r in rows:
                dist = int(r["bfs_dist"])
                acc = int(r["access"])
                if r.get("is_edge"):
                    edge_dist_acc[dist].append(acc)
                else:
                    node_dist_acc[dist].append(acc)

            if node_dist_acc:
                node_dists = sorted(node_dist_acc.keys())
                node_avg = [
                    sum(node_dist_acc[d]) / len(node_dist_acc[d]) for d in node_dists
                ]
                ax2.plot(
                    node_dists,
                    node_avg,
                    marker="o",
                    linewidth=1.6,
                    color="#1f77b4",
                    label="node avg",
                )
            if edge_dist_acc:
                edge_dists = sorted(edge_dist_acc.keys())
                edge_avg = [
                    sum(edge_dist_acc[d]) / len(edge_dist_acc[d]) for d in edge_dists
                ]
                ax2.plot(
                    edge_dists,
                    edge_avg,
                    marker="s",
                    linewidth=1.6,
                    color="#d62728",
                    label="edge avg",
                )
            if node_dist_acc or edge_dist_acc:
                ax2.set_ylabel("avg access count by entity type")
                ax2.grid(False)
                ax2.legend(fontsize=8, loc="upper right")

        ax.set_xlabel("BFS distance from start node (exclude 0)")
        ax.set_ylabel("access count (log)")
        ax.set_yscale("log")
        ax.set_title(f"{graph}\nAccess count per entity by BFS distance")
        ax.grid(True, alpha=0.3, axis="both")

    plt.tight_layout()
    out_path = out_dir / "bfs_dist_vs_access_scatter.png"
    plt.savefig(out_path, dpi=120)
    print(f"[plot] Saved access scatter by BFS distance: {out_path}")
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
# ヘルパ: Spearman 順位相関 (numpy のみで実装)
# ---------------------------------------------------------------------------
def _spearman(x: List[float], y: List[float]) -> Optional[float]:
    """Spearman の順位相関係数。n<3 や分散0なら None。"""
    if len(x) < 3:
        return None
    ax = np.argsort(np.argsort(np.asarray(x, dtype=float)))
    ay = np.argsort(np.argsort(np.asarray(y, dtype=float)))
    if ax.std() == 0 or ay.std() == 0:
        return None
    return float(np.corrcoef(ax, ay)[0, 1])


def _degree_bins(degrees: List[int], n_bins: int = 8) -> List[Tuple[float, float]]:
    """次数を log 等幅で n_bins に区切る (lo, hi] の列を返す。"""
    pos = [d for d in degrees if d and d > 0]
    if not pos:
        return []
    lo, hi = min(pos), max(pos)
    if lo == hi:
        return [(lo - 0.5, hi + 0.5)]
    edges = np.unique(
        np.round(np.logspace(np.log10(lo), np.log10(hi), n_bins + 1)).astype(int)
    )
    edges = sorted(set(edges.tolist()))
    bins = []
    prev = edges[0] - 1
    for e in edges:
        if e > prev:
            bins.append((prev, e))
            prev = e
    return bins


# ---------------------------------------------------------------------------
# プロット: 特定距離に絞った「次数分布」と「アクセス分布」
#   ある BFS 距離 d のノード集合について:
#     (a) 次数ヒストグラム  — どの次数のノードが何個あるか (.gr の degree が必要)
#     (b) アクセスヒストグラム — どのアクセス回数のノードが何個あるか (degree 不要)
#     (c) 次数ビン -> アクセス統計 の表
#   degree が None (=.gr 未指定) のときは (a)(c) を自動でスキップし (b) のみ描く。
# ---------------------------------------------------------------------------
def _log_count_hist(
    ax, values: List[int], xlabel: str, color: str, n_bins: int = 10
) -> None:
    """正の整数列を log 等幅ビンでヒストグラム化して bar 描画する。"""
    pos = [v for v in values if v and v > 0]
    if not pos:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_xlabel(xlabel)
        return
    lo, hi = min(pos), max(pos)
    if lo == hi:
        edges = np.array([lo - 0.5, hi + 0.5], dtype=float)
    else:
        edges = np.unique(np.logspace(np.log10(lo), np.log10(hi), n_bins + 1))
    counts, edges = np.histogram(pos, bins=edges)
    centers = np.sqrt(edges[:-1] * edges[1:])
    widths = edges[1:] - edges[:-1]
    ax.bar(centers, counts, width=widths, color=color, alpha=0.75, edgecolor="white")
    ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("node count")
    ax.grid(True, which="both", alpha=0.25)


def _quantile_bins(degrees: List[int], n_bins: int = 10) -> List[Tuple[float, float]]:
    """次数を等頻度(分位)で区切る (lo, hi] の列。各ビンのノード数がほぼ揃う。
    タイ(同じ次数が大量)で分位点が重複する場合はビンを併合する。"""
    pos = sorted(d for d in degrees if d and d > 0)
    if not pos:
        return []
    qs = np.quantile(pos, np.linspace(0, 1, n_bins + 1))
    edges = sorted({int(round(q)) for q in qs})
    if len(edges) < 2:
        return [(pos[0] - 1, pos[-1])]
    bins: List[Tuple[float, float]] = []
    prev = edges[0] - 1
    for e in edges[1:]:
        if e > prev:
            bins.append((prev, e))
            prev = e
    return bins


def plot_focus_distance(
    all_detail: Dict[str, List[Dict]],
    out_dir: Path,
    focus_dists: List[int],
    policy: str = "lru",
    n_bins: int = 10,
    xscale: str = "linear",
) -> None:
    if not HAS_PLOT or not focus_dists:
        return

    suffix = "" if policy == "lru" else f"_{policy}"
    summary_rows: List[Dict] = []  # CSV 用: (graph, dist, deg_bin) の集計

    for graph in all_detail.keys():
        base_rows = [
            r
            for r in all_detail[graph]
            if not r.get("is_edge")
            and r.get("bfs_dist") is not None
            and int(r.get("bfs_dist")) != 0
            and (r.get("policy") == policy)
        ]
        if not base_rows:  # policy 不一致なら全ポリシーへフォールバック
            base_rows = [
                r
                for r in all_detail[graph]
                if not r.get("is_edge")
                and r.get("bfs_dist") is not None
                and int(r.get("bfs_dist")) != 0
            ]
        if not base_rows:
            continue

        for d in focus_dists:
            grp = [r for r in base_rows if int(r["bfs_dist"]) == d]
            if not grp:
                print(f"[focus] {graph}: dist={d} のノードなし — スキップ")
                continue

            deg_acc = [
                (int(r["degree"]), int(r["access"]))
                for r in grp
                if r.get("degree") is not None
            ]
            accs_all = [int(r["access"]) for r in grp]
            has_deg = len(deg_acc) > 0

            rho = _spearman([dg for dg, _ in deg_acc], [a for _, a in deg_acc])
            rho_s = f", ρ(deg,acc)={rho:.2f}" if rho is not None else ""

            # ---- 各次数ごとに「ノード数」と「1ノードあたり平均アクセス」を集計 ----
            # 左軸: 各次数のノード存在数 (棒)
            # 右軸: その次数の 総アクセス / ノード数 = 1ノードあたり平均アクセス (線)
            #   → 高次数が少数でも、平均が高ければ右軸の線が上に出る (誤読しない)
            from collections import defaultdict as _dd

            by_deg: Dict[int, List[int]] = _dd(list)
            for dg, a in deg_acc:
                by_deg[dg].append(a)
            degs_sorted = sorted(by_deg)
            counts: List[int] = []
            means: List[float] = []  # = total / count (ユーザ指定の指標)
            meds: List[float] = []
            unstable: List[bool] = []

            if has_deg:
                print(
                    f"\n=== {graph}: dist={d} 次数ごと "
                    f"-> ノード数 & 1ノードあたり平均アクセス ==="
                )
                print(
                    f"  {'degree':>8} {'nodes':>7} {'acc_total':>10} "
                    f"{'acc_per_node':>13} {'acc_median':>11} {'acc_max':>8}"
                )
                print(f"  {'-'*62}")
                for dg in degs_sorted:
                    arr = np.asarray(by_deg[dg], dtype=float)
                    cnt = len(arr)
                    total = int(arr.sum())
                    per_node = total / cnt
                    med = float(np.median(arr))
                    counts.append(cnt)
                    means.append(per_node)
                    meds.append(med)
                    unstable.append(cnt < 5)
                    print(
                        f"  {dg:>8} {cnt:>7} {total:>10} "
                        f"{per_node:>13.1f} {med:>11.1f} {int(arr.max()):>8}"
                    )
                    summary_rows.append(
                        {
                            "graph": graph,
                            "bfs_dist": d,
                            "policy": policy,
                            "degree": dg,
                            "n_nodes": cnt,
                            "acc_total": total,
                            "acc_per_node": round(per_node, 3),
                            "acc_median": med,
                            "acc_min": int(arr.min()),
                            "acc_max": int(arr.max()),
                        }
                    )

            # ---- 図: 左軸=ノード数(棒) / 右軸=1ノードあたり平均アクセス(線) ----
            if has_deg and degs_sorted:
                fig, ax1 = plt.subplots(figsize=(8.4, 4.8))
                use_log = xscale == "log"
                if use_log:
                    widths = [dg * 0.22 for dg in degs_sorted]  # log軸で見かけ一定幅
                else:
                    span = max(degs_sorted) - min(degs_sorted)
                    w = max(1.0, span / max(len(degs_sorted), 1) * 0.8)
                    widths = [w] * len(degs_sorted)
                ax1.bar(
                    degs_sorted,
                    counts,
                    width=widths,
                    color="#9ecae1",
                    edgecolor="white",
                    alpha=0.85,
                    label="node count (left)",
                )
                if use_log:
                    ax1.set_xscale("log")
                ax1.set_xlabel(f"node degree ({'log' if use_log else 'linear'})")
                ax1.set_ylabel("node count", color="#3182bd")
                ax1.tick_params(axis="y", labelcolor="#3182bd")

                ax2 = ax1.twinx()
                ax2.plot(
                    degs_sorted,
                    means,
                    marker="o",
                    color="#d62728",
                    linewidth=1.7,
                    label="access per node = total/count (right)",
                )
                ax2.plot(
                    degs_sorted,
                    meds,
                    marker="s",
                    color="#ff7f0e",
                    linewidth=1.0,
                    linestyle="--",
                    alpha=0.7,
                    label="median access / node (right)",
                )
                # ノード数 < 5 の次数 (= 平均が不安定) を中抜きで明示
                # if any(unstable):
                #     ax2.scatter(
                #         [dg for dg, u in zip(degs_sorted, unstable) if u],
                #         [m for m, u in zip(means, unstable) if u],
                #         facecolors="white", edgecolors="#d62728", s=55, zorder=5,
                #         label="per-node (n<5, 不安定)",
                #     )
                ax2.set_yscale("log")
                ax2.set_ylabel("access per node (log)", color="#d62728")
                ax2.tick_params(axis="y", labelcolor="#d62728")
                # X軸を１００までにしたい
                ax1.set_xlim(1, 100)
                lines1, lab1 = ax1.get_legend_handles_labels()
                lines2, lab2 = ax2.get_legend_handles_labels()
                ax1.legend(lines1 + lines2, lab1 + lab2, fontsize=8, loc="upper left")
                ax1.grid(True, which="both", alpha=0.2)
                title = (
                    f"{graph} — BFS dist={d}: node count & access-per-node vs degree "
                    f"[policy={policy}]  (n={len(grp):,}{rho_s})"
                )
            else:
                # degree が無いとき: アクセス分布ヒストのみ
                fig, ax = plt.subplots(figsize=(6.2, 4.4))
                _log_count_hist(ax, accs_all, "access count (log)", "#d62728", n_bins)
                acc_sorted = sorted(accs_all)
                print(f"\n=== {graph}: dist={d} (degree なし) アクセス分布のみ ===")
                print(
                    f"  n={len(accs_all)} min={acc_sorted[0]} "
                    f"median={int(median(accs_all))} "
                    f"mean={sum(accs_all)/len(accs_all):.1f} max={acc_sorted[-1]}"
                )
                title = (
                    f"{graph} — BFS dist={d}: access distribution "
                    f"[policy={policy}]  (n={len(grp):,}; degree=NA, give .gr)"
                )

            fig.suptitle(title, fontsize=11, fontweight="bold")
            fig.tight_layout(rect=(0, 0, 1, 0.96))
            out_path = out_dir / f"focus_dist{d}_{graph}{suffix}.png"
            plt.savefig(out_path, dpi=130)
            plt.close()
            print(f"[plot] Saved focus dist={d}: {out_path}")

    # ---- サマリ CSV (次数ビン × 距離 の集計) ----
    if summary_rows:
        cols = [
            "graph",
            "bfs_dist",
            "policy",
            "degree",
            "n_nodes",
            "acc_total",
            "acc_per_node",
            "acc_median",
            "acc_min",
            "acc_max",
        ]
        csv_path = out_dir / f"focus_summary{suffix}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(summary_rows)
        print(f"[csv] Saved focus summary ({len(summary_rows)} rows) -> {csv_path}")
    elif focus_dists:
        print(
            "[csv] focus summary は degree が無いため未出力 "
            "(.gr を --graph-dir で渡すと次数ビン集計CSVが出ます)。"
        )


# ---------------------------------------------------------------------------
# プロット: 距離で条件づけた「次数 vs アクセス」(交絡=距離を除去)
#   主張「同じ距離なら次数が高いほどアクセスされる」の直接証明。
# ---------------------------------------------------------------------------
def plot_degree_vs_access_by_distance(
    all_detail: Dict[str, List[Dict]],
    out_dir: Path,
    max_dist: int = 5,
    min_nodes_per_bin: int = 5,
    policy: str = "lru",
) -> None:
    if not HAS_PLOT:
        return

    graphs_with_degree = [
        g
        for g in all_detail.keys()
        if any(
            r.get("degree") is not None and not r.get("is_edge") for r in all_detail[g]
        )
    ]
    if not graphs_with_degree:
        return

    for graph in graphs_with_degree:
        rows = [
            r
            for r in all_detail[graph]
            if not r.get("is_edge")
            and r.get("degree") is not None
            and r.get("bfs_dist") is not None
            and r.get("bfs_dist") != 0
            and r.get("policy") == policy
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
        if not rows:
            continue

        # 距離ビン: 1..max_dist は単独、それ以上はまとめる
        dist_groups: Dict[str, List[Dict]] = defaultdict(list)
        for r in rows:
            d = int(r["bfs_dist"])
            key = str(d) if d <= max_dist else f"{max_dist+1}+"
            dist_groups[key].append(r)

        def _dkey(k: str) -> int:
            return int(k[:-1]) if k.endswith("+") else int(k)

        ordered = sorted(dist_groups.keys(), key=_dkey)
        ordered = [k for k in ordered if len(dist_groups[k]) >= min_nodes_per_bin]
        if not ordered:
            continue

        ncol = len(ordered)
        fig, axes = plt.subplots(
            1, ncol, figsize=(4.0 * ncol, 4.2), squeeze=False, sharey=True
        )
        print(f"\n=== {graph}: 距離別 次数→アクセス Spearman 相関 ===")
        for ci, dk in enumerate(ordered):
            ax = axes[0][ci]
            grp = dist_groups[dk]
            degs = [int(r["degree"]) for r in grp]
            accs = [int(r["access"]) for r in grp]
            rho = _spearman(degs, accs)

            # 次数ビンで access の中央値/平均を集計してプロット
            bins = _degree_bins(degs)
            xs, med_acc, mean_acc = [], [], []
            for blo, bhi in bins:
                vals = [a for d, a in zip(degs, accs) if blo < d <= bhi]
                if len(vals) < max(2, min_nodes_per_bin // 2):
                    continue
                xs.append((blo + bhi) / 2 if blo > 0 else bhi)
                med_acc.append(float(np.median(vals)))
                mean_acc.append(float(np.mean(vals)))

            ax.scatter(degs, accs, s=10, alpha=0.25, color="#888", label="nodes")
            if xs:
                ax.plot(
                    xs,
                    mean_acc,
                    marker="o",
                    color="#d62728",
                    linewidth=1.8,
                    label="bin mean",
                )
                ax.plot(
                    xs,
                    med_acc,
                    marker="s",
                    color="#1f77b4",
                    linewidth=1.4,
                    linestyle="--",
                    label="bin median",
                )
            ax.set_xscale("log")
            ax.set_yscale("log")
            rho_s = f"ρ={rho:.2f}" if rho is not None else "ρ=NA"
            ax.set_title(f"dist={dk}  (n={len(grp):,}, {rho_s})", fontsize=10)
            ax.set_xlabel("node degree (log)")
            if ci == 0:
                ax.set_ylabel("access count (log)")
            ax.grid(True, which="both", alpha=0.25)
            if ci == ncol - 1:
                ax.legend(fontsize=8, loc="upper left")
            print(f"  dist={dk:>4}: n={len(grp):>6,}  " f"Spearman(deg,access)={rho_s}")

        fig.suptitle(
            f"{graph} — degree vs access, conditioned on BFS distance "
            f"(distance fixed = confounder removed) [policy={policy}]",
            fontsize=12,
            fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        suffix = "" if policy == "lru" else f"_{policy}"
        out_path = out_dir / f"degree_vs_access_by_dist_{graph}{suffix}.png"
        plt.savefig(out_path, dpi=130)
        print(f"[plot] Saved degree→access by distance: {out_path}")
        plt.close()


# ---------------------------------------------------------------------------
# プロット: ①生存バイアス補正版 「次数 vs アクセス（未アクセス=0 も含む）」
#   既存 plot_degree_vs_access_by_distance は access された entity しか持たない
#   transition 由来データを使うため「踏まれたノードだけ」を見ており、
#   高次数だが踏まれなかったノード(access=0)が欠落する生存バイアスがある。
#   本関数は元グラフ(.gr)の真の BFS 距離で「距離dに存在する全ノード」を列挙し、
#   未アクセスに access=0 を入れて相関・分布を引き直す。
#   距離は二部グラフ上のホップ (node->edge->node = 2)。ノードのみなので偶数距離。
#   右軸にアクセス率 P(access>0) を併記し「次数→踏まれる確率」を直接見せる。
# ---------------------------------------------------------------------------
def plot_degree_vs_access_by_distance_with_zeros(
    all_detail: Dict[str, List[Dict]],
    gr_cache: Dict[str, Tuple[Dict[str, List[str]], Dict[str, int]]],
    out_dir: Path,
    max_dist: int = 5,
    min_nodes_per_bin: int = 5,
    scatter_cap: int = 4000,
    policy: str = "lru",
) -> None:
    if not HAS_PLOT:
        return
    rng = np.random.default_rng(0)
    Y_FLOOR = 0.5  # access=0 を log 軸に置くための下駄

    for graph, (gr_adj, gr_degree) in gr_cache.items():
        rows = all_detail.get(graph)
        if not rows or gr_adj is None or gr_degree is None:
            continue

        # 始点ごとの access マップ (lru 優先、なければ全ポリシー)
        def _by_start(policy: Optional[str]) -> Dict[int, Dict[str, int]]:
            m: Dict[int, Dict[str, int]] = defaultdict(dict)
            for r in rows:
                if policy is not None and r.get("policy") != policy:
                    continue
                sn, ent = r.get("start_node"), r.get("entity")
                if sn is None or ent is None:
                    continue
                m[int(sn)][ent] = int(r.get("access", 0) or 0)
            return m

        access_by_start = _by_start(policy) or _by_start(None)
        if not access_by_start:
            continue

        # 各始点で元グラフを真 BFS。ノードの (deg, access, dist) を収集 (0 含む)
        deg_all: List[int] = []
        acc_all: List[int] = []
        dist_all: List[int] = []
        for start, acc_map in access_by_start.items():
            start_key = str(start)
            if start_key not in gr_adj:
                continue
            dist: Dict[str, int] = {start_key: 0}
            q: deque = deque([start_key])
            while q:
                node = q.popleft()
                nd = dist[node] + 1
                for nb in gr_adj[node]:
                    if nb not in dist:
                        dist[nb] = nd
                        q.append(nb)
            for ent, d in dist.items():
                if d == 0 or ent.startswith("edge_"):
                    continue
                deg = gr_degree.get(ent)
                if deg is None:
                    continue
                deg_all.append(int(deg))
                acc_all.append(int(acc_map.get(ent, 0)))
                dist_all.append(int(d))

        if not deg_all:
            continue

        groups: Dict[str, List[int]] = defaultdict(list)
        for i, d in enumerate(dist_all):
            key = str(d) if d <= max_dist else f"{max_dist+1}+"
            groups[key].append(i)

        def _dkey(k: str) -> int:
            return int(k[:-1]) if k.endswith("+") else int(k)

        ordered = sorted(groups.keys(), key=_dkey)
        ordered = [k for k in ordered if len(groups[k]) >= min_nodes_per_bin]
        if not ordered:
            continue

        ncol = len(ordered)
        fig, axes = plt.subplots(
            1, ncol, figsize=(4.0 * ncol, 4.2), squeeze=False, sharey=True
        )
        print(f"\n=== {graph}: [①補正/未アクセス0含む] 距離別 次数→アクセス 相関 ===")
        for ci, dk in enumerate(ordered):
            ax = axes[0][ci]
            idx = groups[dk]
            degs = [deg_all[i] for i in idx]
            accs = [acc_all[i] for i in idx]
            n_total = len(idx)
            acc_rate = (sum(1 for a in accs if a > 0) / n_total) if n_total else 0.0
            rho = _spearman(degs, accs)

            bins = _degree_bins(degs)
            xs, mean_acc, med_acc, rate = [], [], [], []
            for blo, bhi in bins:
                vals = [a for d, a in zip(degs, accs) if blo < d <= bhi]
                if len(vals) < max(2, min_nodes_per_bin // 2):
                    continue
                xs.append((blo + bhi) / 2 if blo > 0 else bhi)
                mean_acc.append(max(float(np.mean(vals)), Y_FLOOR))
                med_acc.append(max(float(np.median(vals)), Y_FLOOR))
                rate.append(sum(1 for v in vals if v > 0) / len(vals))

            if n_total > scatter_cap:
                sel = rng.choice(n_total, size=scatter_cap, replace=False)
            else:
                sel = np.arange(n_total)
            sx = [degs[i] for i in sel]
            sy = [max(accs[i], Y_FLOOR) for i in sel]
            ax.scatter(sx, sy, s=10, alpha=0.2, color="#888", label="nodes (incl 0)")
            if xs:
                ax.plot(
                    xs,
                    mean_acc,
                    marker="o",
                    color="#d62728",
                    linewidth=1.8,
                    label="bin mean",
                )
                ax.plot(
                    xs,
                    med_acc,
                    marker="s",
                    color="#1f77b4",
                    linewidth=1.4,
                    linestyle="--",
                    label="bin median",
                )
            ax.axhline(Y_FLOOR, color="#bbb", linewidth=0.8, linestyle=":")
            ax.set_xscale("log")
            ax.set_yscale("log")

            ax2 = ax.twinx()
            if xs:
                ax2.plot(
                    xs,
                    rate,
                    marker="^",
                    color="#2ca02c",
                    linewidth=1.4,
                    label="access rate",
                )
            ax2.set_ylim(0, 1.0)
            if ci == ncol - 1:
                ax2.set_ylabel("access rate  P(acc>0)")
            else:
                ax2.set_yticklabels([])

            rho_s = f"rho={rho:.2f}" if rho is not None else "rho=NA"
            ax.set_title(
                f"dist={dk}  (n={n_total:,}, hit%={acc_rate:.0%}, {rho_s})",
                fontsize=10,
            )
            ax.set_xlabel("node degree (log)")
            if ci == 0:
                ax.set_ylabel("access count (log, 0->0.5)")
            ax.grid(True, which="both", alpha=0.25)
            if ci == ncol - 1:
                h1, l1 = ax.get_legend_handles_labels()
                h2, l2 = ax2.get_legend_handles_labels()
                ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
            print(
                f"  dist={dk:>4}: n={n_total:>7,} 踏率={acc_rate:6.1%} "
                f"Spearman(deg,access)={rho_s}"
            )

        fig.suptitle(
            f"{graph} — degree vs access "
            "[bias-corrected: includes unaccessed nodes (access=0)] "
            "(true BFS distance, distance fixed)",
            fontsize=12,
            fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        suffix = "" if policy == "lru" else f"_{policy}"
        out_path = out_dir / f"degree_vs_access_by_dist_withzeros_{graph}{suffix}.png"
        plt.savefig(out_path, dpi=130)
        print(f"[plot] Saved degree→access (未アクセス0含む): {out_path}")
        plt.close()


# ---------------------------------------------------------------------------
# プロット: 退去(eviction)向け実測 「入った後の再利用」
#   母集団 = すでにアクセスされた(access>=1, 一度キャッシュに入った)ノードのみ。
#   未アクセス(0)は退去判断に無関係なので含めない(これは“正しい”母集団)。
#   実測する量 (予測モデルではなくログ集計):
#     (1) 再利用率 P(access>=2) を 次数別・距離別に。
#         = 新入りのうち「また使われた」割合。probation で残す価値の実体。
#     (2) access 回数ヒストグラム (log-log)。
#         べき乗 = 大多数は1回きり/少数が多数回 → 退去で freq が効く根拠。
# ---------------------------------------------------------------------------
def plot_reuse_among_cached(
    all_detail: Dict[str, List[Dict]],
    out_dir: Path,
    max_dist: int = 5,
    min_nodes_per_bin: int = 5,
) -> None:
    if not HAS_PLOT:
        return

    graphs_with_degree = [
        g
        for g in all_detail.keys()
        if any(
            r.get("degree") is not None and not r.get("is_edge") for r in all_detail[g]
        )
    ]

    def _dkey(k: str) -> int:
        return int(k[:-1]) if k.endswith("+") else int(k)

    for graph in graphs_with_degree:

        def _rows(policy: Optional[str]) -> List[Dict]:
            return [
                r
                for r in all_detail[graph]
                if not r.get("is_edge")
                and r.get("degree") is not None
                and r.get("bfs_dist") not in (None, 0)
                and int(r.get("access", 0) or 0) >= 1
                and (policy is None or r.get("policy") == policy)
            ]

        rows = _rows("lru") or _rows(None)
        if not rows:
            continue

        groups: Dict[str, List[Dict]] = defaultdict(list)
        for r in rows:
            d = int(r["bfs_dist"])
            key = str(d) if d <= max_dist else f"{max_dist+1}+"
            groups[key].append(r)
        ordered = [
            k
            for k in sorted(groups.keys(), key=_dkey)
            if len(groups[k]) >= min_nodes_per_bin
        ]
        if not ordered:
            continue

        # --- 実測テーブル(標準出力) ---
        print(f"\n=== {graph}: [退去向け実測] 入った後の再利用 (access>=1 のみ) ===")
        print(
            f"  {'dist':>5} {'n':>7} {'freq=1%':>8} {'freq>=2%':>9} "
            f"{'mean':>7} {'median':>7} {'rho(deg,reuse)':>15}"
        )
        for dk in ordered:
            grp = groups[dk]
            accs = [int(r["access"]) for r in grp]
            degs = [int(r["degree"]) for r in grp]
            n = len(accs)
            f1 = sum(1 for a in accs if a == 1) / n
            f2 = sum(1 for a in accs if a >= 2) / n
            reuse = [1 if a >= 2 else 0 for a in accs]
            rho = _spearman(degs, reuse)
            rho_s = f"{rho:.2f}" if rho is not None else "NA"
            print(
                f"  {dk:>5} {n:>7,} {f1:>7.1%} {f2:>8.1%} "
                f"{np.mean(accs):>7.1f} {np.median(accs):>7.1f} {rho_s:>15}"
            )

        # --- 図1: 再利用率 P(access>=2) vs 次数 (距離別パネル) ---
        ncol = len(ordered)
        fig, axes = plt.subplots(
            1, ncol, figsize=(4.0 * ncol, 4.2), squeeze=False, sharey=True
        )
        for ci, dk in enumerate(ordered):
            ax = axes[0][ci]
            grp = groups[dk]
            degs = [int(r["degree"]) for r in grp]
            accs = [int(r["access"]) for r in grp]
            overall = sum(1 for a in accs if a >= 2) / len(accs)

            bins = _degree_bins(degs)
            xs, rate, cnt = [], [], []
            for blo, bhi in bins:
                vals = [a for d, a in zip(degs, accs) if blo < d <= bhi]
                if len(vals) < max(2, min_nodes_per_bin // 2):
                    continue
                xs.append((blo + bhi) / 2 if blo > 0 else bhi)
                rate.append(sum(1 for v in vals if v >= 2) / len(vals))
                cnt.append(len(vals))

            if xs:
                ax.bar(
                    xs,
                    [c / max(cnt) for c in cnt],
                    width=[x * 0.5 for x in xs],
                    color="#ddd",
                    align="center",
                    label="cached count (norm)",
                )
                ax.plot(
                    xs,
                    rate,
                    marker="^",
                    color="#2ca02c",
                    linewidth=1.8,
                    label="reuse rate P(acc>=2)",
                )
            ax.axhline(overall, color="#d62728", linewidth=1.0, linestyle="--")
            ax.set_xscale("log")
            ax.set_ylim(0, 1.0)
            ax.set_title(
                f"dist={dk}  (n={len(grp):,}, reuse={overall:.0%})", fontsize=10
            )
            ax.set_xlabel("node degree (log)")
            if ci == 0:
                ax.set_ylabel("reuse rate  P(access>=2)")
            ax.grid(True, which="both", alpha=0.25)
            if ci == ncol - 1:
                ax.legend(fontsize=8, loc="upper right")
        fig.suptitle(
            f"{graph} — re-use among cached nodes (access>=1 only) "
            "[eviction-relevant population]",
            fontsize=12,
            fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        out_path = out_dir / f"reuse_rate_by_degree_dist_{graph}.png"
        plt.savefig(out_path, dpi=130)
        print(f"[plot] Saved reuse rate by degree/dist: {out_path}")
        plt.close()

        # --- 図2: access 回数ヒストグラム (log-log, 距離別) ---
        fig2, ax = plt.subplots(figsize=(6.4, 4.6))
        for dk in ordered:
            accs = np.asarray([int(r["access"]) for r in groups[dk]])
            maxa = int(accs.max())
            counts = np.bincount(accs, minlength=maxa + 1)[1:]  # freq=1..maxa
            xv = np.arange(1, maxa + 1)
            nz = counts > 0
            ax.plot(xv[nz], counts[nz], marker="o", markersize=3, label=f"dist={dk}")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("access count (freq)")
        ax.set_ylabel("number of cached nodes")
        ax.set_title(
            f"{graph} — access-count distribution among cached nodes\n"
            "(power-law => most freq=1, few reused many times)",
            fontsize=11,
        )
        ax.grid(True, which="both", alpha=0.25)
        ax.legend(fontsize=9)
        fig2.tight_layout()
        out_path2 = out_dir / f"access_freq_hist_{graph}.png"
        plt.savefig(out_path2, dpi=130)
        print(f"[plot] Saved access-freq histogram: {out_path2}")
        plt.close()


# ---------------------------------------------------------------------------
# プロット: 減衰を正規化した collapse 図
#   access / (1-alpha)^dist を次数に対してプロット。
#   異なる距離の点が1本の増加曲線に重なれば、
#     (a) 次数がアクセスを駆動  (b) prior=(1-alpha)^dist * deg の形が妥当
#   を同時に示せる。
# ---------------------------------------------------------------------------
def plot_degree_access_collapse(
    all_detail: Dict[str, List[Dict]],
    out_dir: Path,
    max_dist: int = 6,
    min_nodes_per_bin: int = 5,
) -> None:
    if not HAS_PLOT:
        return

    graphs_with_degree = [
        g
        for g in all_detail.keys()
        if any(
            r.get("degree") is not None and not r.get("is_edge") for r in all_detail[g]
        )
    ]
    if not graphs_with_degree:
        return

    for graph in graphs_with_degree:
        rows = [
            r
            for r in all_detail[graph]
            if not r.get("is_edge")
            and r.get("degree") is not None
            and r.get("bfs_dist") is not None
            and r.get("bfs_dist") != 0
            and r.get("alpha") is not None
        ]
        # policy 不問 (access は policy 不変)。alpha が無い行は除外。
        if not rows:
            continue

        fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), squeeze=True)
        cmap = plt.get_cmap("viridis")
        dists_present = sorted(
            {int(r["bfs_dist"]) for r in rows if int(r["bfs_dist"]) <= max_dist}
        )
        if not dists_present:
            plt.close()
            continue

        # 左: 生 (正規化なし) access vs degree, 距離で色分け
        # 右: 正規化 access/(1-a)^dist vs degree
        for ax_i, (ax, normalize) in enumerate([(axes[0], False), (axes[1], True)]):
            for d in dists_present:
                grp = [r for r in rows if int(r["bfs_dist"]) == d]
                if len(grp) < min_nodes_per_bin:
                    continue
                a = float(grp[0]["alpha"])
                degs = np.array([int(r["degree"]) for r in grp], dtype=float)
                accs = np.array([int(r["access"]) for r in grp], dtype=float)
                if normalize:
                    decay = (1.0 - a) ** d
                    accs = accs / max(decay, 1e-12)
                color = cmap(d / max(dists_present))
                ax.scatter(degs, accs, s=12, alpha=0.35, color=color, label=f"dist={d}")
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_xlabel("node degree (log)")
            if not normalize:
                ax.set_ylabel("access count (log)")
                ax.set_title(
                    "raw (no normalization)\n" "bands shift vertically per distance",
                    fontsize=11,
                )
            else:
                ax.set_ylabel("access / (1-alpha)^dist  (log)", fontsize=10)
                ax.set_title(
                    "decay-normalized\n" "collapse => access ~ degree", fontsize=11
                )
            ax.grid(True, which="both", alpha=0.25)
            ax.legend(fontsize=8, loc="upper left", ncol=2)

        fig.suptitle(
            f"{graph} — does access collapse onto a degree curve after "
            "dividing out decay?  (validates prior=(1-alpha)^dist x deg)",
            fontsize=12,
            fontweight="bold",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        out_path = out_dir / f"degree_access_collapse_{graph}.png"
        plt.savefig(out_path, dpi=130)
        print(f"[plot] Saved degree-access collapse: {out_path}")
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
def dump_detail_csv(
    all_detail: Dict[str, List[Dict]],
    out_path: Path,
    policy: Optional[str] = None,
    nodes_only: bool = False,
) -> None:
    """エンティティ単位の詳細行を 1 つの tidy CSV に書き出す。
    1 行 = (graph, start_node, entity)。これさえあれば距離/次数/アクセスの
    任意の集計 (pandas / Excel / R) が後から自由にできる。
    policy 指定時はその policy の行のみ。アクセス分布はキャッシュ非依存なので
    none 推奨。
    nodes_only=True でエッジ実体 (次数常に2) を除外しノードのみ出力。
    """
    columns = [
        "graph",
        "start_node",
        "policy",
        "capacity",
        "walks",
        "alpha",
        "entity",
        "is_edge",
        "access",
        "degree",
        "bfs_dist",
        "hit_rate",
        # --- エッジエンティティの両端ノード次数特徴量 ---
        "edge_u",
        "edge_v",
        "edge_deg_u",
        "edge_deg_v",
        "edge_min_degree",
        "edge_max_degree",
        "edge_avg_degree",
        "edge_degree_sum",
        "edge_degree_diff",
        "edge_degree_ratio",
        "edge_degree_type",
        "file",
    ]
    n_written = 0
    n_nodes = 0
    n_node_with_degree = 0  # ノードの実次数が入っている行 (エッジの degree=2 は除く)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for graph, rows in all_detail.items():
            for r in rows:
                if policy is not None and r.get("policy") != policy:
                    continue
                if nodes_only and r.get("is_edge"):
                    continue
                row = dict(r)
                row["graph"] = graph
                if not r.get("is_edge"):
                    n_nodes += 1
                    if r.get("degree") is not None:
                        n_node_with_degree += 1
                writer.writerow(row)
                n_written += 1
    print(
        f"\n[csv] Saved {n_written:,} rows -> {out_path}  (policy={policy})\n"
        f"[csv]   ノード次数あり {n_node_with_degree:,} / {n_nodes:,} ノード行"
    )
    if n_nodes and n_node_with_degree == 0:
        print(
            "[csv] 注意: ノードの degree 列が全て空です。--graph-dir に "
            "正しい <graph>.gr が無いか ID 不一致です (.gr 投入後に自動で埋まります)。"
        )


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
    parser.add_argument(
        "--policy",
        default="lru",
        help="次数→アクセスの図/CSVでアクセス値を採るポリシー。"
        "アクセス分布はキャッシュ非依存なので none を推奨（純粋なウォーク特性）。",
    )
    parser.add_argument(
        "--focus-dist",
        type=int,
        nargs="*",
        default=[],
        help="指定した BFS 距離に絞って、次数分布・アクセス分布・次数→アクセス表を出力する。"
        "例: --focus-dist 2 4",
    )
    parser.add_argument(
        "--csv-out",
        default=None,
        help="エンティティ単位の詳細を tidy CSV で書き出す先。"
        "省略時は <out-dir>/detail_<policy>.csv に自動保存。",
    )
    parser.add_argument(
        "--csv-nodes-only",
        action="store_true",
        help="detail CSV からエッジ実体(次数常に2)を除外しノードのみ出力する。",
    )
    parser.add_argument(
        "--focus-xscale",
        choices=["linear", "log"],
        default="linear",
        help="focus 図の横軸(次数)のスケール。default=linear。",
    )
    parser.add_argument(
        "--dist-source",
        choices=["graph", "transition"],
        default="graph",
        help="bfs_dist の定義。graph=.gr 全体からの真の最短距離(推奨)、"
        "transition=歩いた遷移上の距離。default=graph。",
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

    all_detail, gr_cache = report_degree_and_distance(
        results_base, args.graphs, gr_paths, dist_source=args.dist_source
    )

    # --- 詳細 tidy CSV を書き出す (後から自由に分析できる生データ) ---
    _node_tag = "_nodes" if args.csv_nodes_only else ""
    csv_path = (
        Path(args.csv_out)
        if args.csv_out
        else out_dir / f"detail_{args.policy}{_node_tag}.csv"
    )
    dump_detail_csv(
        all_detail, csv_path, policy=args.policy, nodes_only=args.csv_nodes_only
    )

    if HAS_PLOT:
        # if args.plot_saturation:
        #     plot_saturation(rows, args.graphs, out_dir)
        # plot_gini_vs_hitrate(rows, out_dir)
        plot_degree_distance(all_detail, out_dir)
        plot_degree_vs_access_by_distance(all_detail, out_dir, policy=args.policy)
        plot_degree_vs_access_by_distance_with_zeros(
            all_detail, gr_cache, out_dir, policy=args.policy
        )
        plot_focus_distance(
            all_detail,
            out_dir,
            args.focus_dist,
            policy=args.policy,
            xscale=args.focus_xscale,
        )
        plot_reuse_among_cached(all_detail, out_dir)
        plot_degree_access_collapse(all_detail, out_dir)
        # plot_edge_endpoint_degree(all_detail, out_dir)
        # plot_edge_degree_type(all_detail, out_dir)
    else:
        print("\n[info] pip install matplotlib numpy でグラフ出力が有効になります")


if __name__ == "__main__":
    main()
