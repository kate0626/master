#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_auth_from_edges.py with NG ratio option

Usage:
  python3 base/auth-many-server/create_json_table.py \
      ./dataset/Louvain/graph/karate.gr \
      --ng-ratio 0.3 
    #   --emit-auth-table

出力:
  - base/auth-many-server/node_to_starts.json （常に出力）
  - base/auth-many-server/auth_by_start.json （--emit-auth-table 指定時のみ）
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple


def make_edge_id(u: int, v: int) -> str:
    """(u, v) をソートして "edge_u_v" 形式のIDにする。"""
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def read_edge_list(path: Path) -> List[Tuple[int, int]]:
    """1行ごとに 'u v' なエッジリストを読む。"""
    edges: List[Tuple[int, int]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            u, v = int(parts[0]), int(parts[1])
            edges.append((u, v))
    return edges


"""
    隣接関係なく許可不許可を決めるのは「始点ノード」。
    すなわち、start から見たときに、
      - ng_ratio=0.0 → 全ノード・全エッジが許可（自分以外も含めて）
      - ng_ratio>0.0 → 各ノード・エッジを独立に確率的に NG にする
    というモデル。
    そのうえで、2部グラフ node ↔ edge として見たときに、
      - 許可されたノードは少なくとも1本の「許可された incident edge」を持つ
      - 許可されたエッジは少なくとも1つの「許可された端点ノード」を持つ
    ように補正を行う。
"""


def build_auth_table2(
    edges: List[Tuple[int, int]], ng_ratio: float = 0.0
) -> Dict[str, Dict[str, List]]:
    """
    Build mapping: start_node_int -> {"n": [nodes], "e": [edge_ids]}

    - ng_ratio=0.0:
        各 start から見て、自分自身を含む全ノード・全エッジが許可。
    - ng_ratio>0.0:
        各 start について、ノード/エッジごとに独立に確率で NG にする。
        ただし補正により、2部グラフ上で:
          * 許可ノード → 少なくとも1本の許可 incident edge
          * 許可エッジ → 少なくとも1つの許可端点ノード
        を必ず満たす。
    """
    by_start: Dict[int, Dict[str, Set]] = defaultdict(lambda: {"n": set(), "e": set()})
    all_nodes: Set[int] = set()
    all_edges: Set[str] = set()

    # ノードごとの incident edges, edge ごとの (u,v) を保持
    incident_edges: Dict[int, List[str]] = defaultdict(list)
    edge_endpoints: Dict[str, Tuple[int, int]] = {}

    # --- 前処理：全ノード・エッジ集合と incident 情報 ---
    for u, v in edges:
        all_nodes.update([u, v])
        edge_id = make_edge_id(u, v)
        all_edges.add(edge_id)
        incident_edges[u].append(edge_id)
        incident_edges[v].append(edge_id)
        edge_endpoints[edge_id] = (u, v)

    # --- (1) ランダムに許可/不許可を決めるフェーズ ---
    for start in all_nodes:
        nodes_ok: Set[int] = set()
        edges_ok: Set[str] = set()

        # ノード認可リスト（自分以外）
        for node in all_nodes:
            if node == start:
                continue
            # 確率 (1 - ng_ratio) で「許可」
            if random.random() >= ng_ratio:
                nodes_ok.add(node)

        # 自分自身は必ず許可
        nodes_ok.add(start)

        # エッジ認可リスト
        for edge_id in all_edges:
            if random.random() >= ng_ratio:
                edges_ok.add(edge_id)

        by_start[start]["n"] = nodes_ok
        by_start[start]["e"] = edges_ok

    # --- (2) 補正フェーズ ---
    #     2部グラフとして「許可頂点（ノード/エッジ）」が必ず
    #     少なくとも1つの許可隣接を持つように調整する。
    for start in all_nodes:
        nodes_set: Set[int] = by_start[start]["n"]
        edges_set: Set[str] = by_start[start]["e"]

        # 2-1: 許可ノードごとに、incident edge のうち
        #      少なくとも1本が許可されるようにする
        for node in list(nodes_set):
            incident = incident_edges.get(node)
            if not incident:
                # 元グラフで孤立ノードならそもそも隣接がないのでスキップ
                continue
            # すでに incident の中に1本でも許可エッジがあればOK
            if any(edge in edges_set for edge in incident):
                continue
            # 1本も許可されていない場合は、incident からランダムに1本許可する
            chosen_edge = random.choice(incident)
            edges_set.add(chosen_edge)

        # 2-2: 許可エッジごとに、端点ノードのうち
        #      少なくとも1つが許可ノードに含まれるようにする
        for edge_id in list(edges_set):
            u, v = edge_endpoints[edge_id]
            # すでにどちらかが許可ノードならOK
            if u in nodes_set or v in nodes_set:
                continue
            # 両端ともまだ許可ノードに含まれていない場合、
            # どちらか一方だけを許可ノードとして追加
            chosen_node = random.choice((u, v))
            nodes_set.add(chosen_node)

    # --- 出力用に整形 ---
    out: Dict[str, Dict[str, List]] = {}
    for start, sets in by_start.items():
        ns = sorted(int(x) for x in sets["n"])
        es = sorted(str(x) for x in sets["e"])
        out[str(start)] = {"n": ns, "e": es}
    return out


def build_node_to_starts(
    auth_table: Dict[str, Dict[str, List]],
) -> Dict[str, List[int]]:
    """
    auth_table (start -> { "n": [...], "e": [...] }) を反転して、
    node_to_starts: { "ノード or edge_id" : [そのエンティティにアクセス可能な start ノードの一覧] }
    を構築する。
    """
    mapping: Dict[str, Set[int]] = defaultdict(set)
    for start_str, entries in auth_table.items():
        try:
            start_node = int(start_str)
        except Exception:
            continue
        for node in entries.get("n", []):
            mapping[str(node)].add(start_node)
        for edge_id in entries.get("e", []):
            mapping[str(edge_id)].add(start_node)
    # ソートしてリストに変換
    return {entity: sorted(starts) for entity, starts in mapping.items()}


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Generate auth_by_start.json and node_to_starts.json "
            "from an edge list, with NG ratio."
        )
    )
    p.add_argument("edges", type=str, help="Path to edge list file (u v per line).")
    p.add_argument(
        "-o",
        "--out",
        type=str,
        default="auth_by_start.json",
        help="Output JSON path for auth_by_start.json (when --emit-auth-table).",
    )
    p.add_argument(
        "--ng-ratio",
        type=float,
        default=0.0,
        help="Ratio of nodes/edges to mark as NG per start (0.0–1.0).",
    )
    p.add_argument(
        "--emit-auth-table",
        action="store_true",
        help="Also write auth_by_start.json (start -> allowed nodes/edges).",
    )
    args = p.parse_args()

    if not 0.0 <= args.ng_ratio <= 1.0:
        raise SystemExit("ng-ratio must be between 0.0 and 1.0")

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise SystemExit(f"Edge list not found: {edge_path}")

    edges = read_edge_list(edge_path)

    # start -> {"n": [...], "e": [...]} を構築
    auth = build_auth_table2(edges, ng_ratio=args.ng_ratio)
    # そこから node_to_starts を構築
    node_to_starts = build_node_to_starts(auth)

    # 出力ディレクトリ（プロジェクトルートからの相対パス）
    out_dir = Path("./base/auth-many-server/")
    out_dir.mkdir(parents=True, exist_ok=True)

    # auth_by_start.json の出力（必要な場合のみ）
    if args.emit_auth_table:
        out_path = out_dir / "auth_by_start.json"
        out_path.write_text(
            json.dumps(auth, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        out_path = None

    # node_to_starts.json の出力（常に出す）
    node_to_starts_path = out_dir / "node_to_starts.json"
    node_to_starts_path.write_text(
        json.dumps(node_to_starts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # ログ表示
    if out_path is not None:
        print(
            f"Wrote {out_path} and {node_to_starts_path} "
            f"with {len(auth)} start entries (NG ratio={args.ng_ratio})."
        )
    else:
        print(
            f"Wrote {node_to_starts_path} with {len(auth)} start entries "
            f"(NG ratio={args.ng_ratio})."
        )


if __name__ == "__main__":
    main()
