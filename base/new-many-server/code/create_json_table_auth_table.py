#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NG集合(deny)を生成しつつ、直接サーバごとの entity_to_denied_starts を出力する統合ツール。
さらに制約A（グローバル）を導入:

制約A:
  各 entity について「全startでNG」(denied == all_starts) になることを禁止し、
  そうなった場合はNGから start を1つ除外して必ず「少なくとも1つのstartで許可」を保証する。

出力（serverごと）:
  out_dir/entity_to_denied_starts_server{sid}.json
    entity(node or edge_id) -> [このentityをNGにしているstart一覧]

★追加:
- starts を任意指定できる:
    --starts "1-10"      # 1..10 を starts にする
    --starts "1,3,7"     # 指定ノードのみ
    --starts "all"       # グラフ中の全ノード（デフォルト）

背景：指定ノードを決めないと、爆発してしまう

./base/auth-many-server/prepare_auth_data.sh --graph com-friendster     --ng-ratio 0.3     --server-count 2     --partitioner-type node-edge-fixed     --mode deny-direct    --starts "388990" > run.log 2>&1 &


Usage:

"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple, Union

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]


# -----------------------------
# helpers
# -----------------------------
def make_edge_id(u: int, v: int) -> str:
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def read_edge_list(path: Path) -> List[Tuple[int, int]]:
    edges: List[Tuple[int, int]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) < 2:
                continue
            edges.append((int(parts[0]), int(parts[1])))
    return edges


def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, x))


def sample_nodes_excluding_self(
    nodes_list: List[int],
    self_index: int,
    k: int,
    rng: random.Random,
) -> List[int]:
    """
    nodes_list から self_index の要素を除外して k 個重複なしサンプル。
    """
    n = len(nodes_list)
    if n <= 1 or k <= 0:
        return []
    k = clamp_int(k, 0, n - 1)

    picked = rng.sample(range(n - 1), k)  # 0..n-2
    out: List[int] = []
    for idx_prime in picked:
        idx = idx_prime + 1 if idx_prime >= self_index else idx_prime
        out.append(nodes_list[idx])
    return out


def to_serializable(table: Dict[NodeOrEdgeId, Set[int]]) -> Dict[str, List[int]]:
    return {str(k): sorted(int(x) for x in v) for k, v in table.items()}


# -----------------------------
# partitioners
# -----------------------------
class ModuloPartitioner:
    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign_node(self, node_id: int) -> int:
        return node_id % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        a, b = sorted((u, v))
        return (a * 1_000_003 + b) % self.server_count

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            _, raw_u, raw_v = entity_id.split("_", 2)
            return self.assign_edge(int(raw_u), int(raw_v))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


class NodeEdgeFixedPartitioner:
    """
    ノードは server0、エッジは server1 に固定。
    server_count >= 2 必須。
    """

    def __init__(self, server_count: int) -> None:
        if server_count < 2:
            raise ValueError("node-edge-fixed requires server_count >= 2")
        self.server_count = server_count

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return 0
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            return 1
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


class MetisPartitioner:
    """
    METIS の part ID をサーバIDとして利用（part が server_count 以上なら modulo）。
    端点が同じならエッジも同じサーバ、それ以外はハッシュで分散。
    (only_split_auth_tables.py の同名クラスを踏襲。off-by-one 修正込み。)
    """

    def __init__(
        self,
        server_count: int,
        part_map: Dict[int, int],
        edge_metis_map: Dict[str, int] = None,
    ) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count
        self.part_map = part_map
        self.edge_metis_map = edge_metis_map or {}
        self._modulo = ModuloPartitioner(server_count)

    def assign_node(self, node_id: int) -> int:
        part = self.part_map.get(node_id)
        if part is None:
            return self._modulo.assign_node(node_id)
        return part % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        owner_u = self.assign_node(u)
        owner_v = self.assign_node(v)
        if owner_u == owner_v:
            return owner_u
        return self._modulo.assign_edge(u, v)

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            metis_id = self.edge_metis_map.get(entity_id)
            if metis_id is not None:
                part = self.part_map.get(metis_id)
                if part is not None:
                    return part % self.server_count
            _, raw_u, raw_v = entity_id.split("_", 2)
            return self.assign_edge(int(raw_u), int(raw_v))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")


def load_metis_partition(path, base_index: int = 0) -> Dict[int, int]:
    if path is None:
        return {}
    p = Path(path)
    mapping: Dict[int, int] = {}
    with p.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            s = line.strip()
            if not s:
                continue
            parts = s.split()
            if len(parts) == 1:
                try:
                    part = int(parts[0])
                except Exception:
                    continue
                node_id = idx + base_index
            else:
                try:
                    node_id = int(parts[0])
                    part = int(parts[1])
                except Exception:
                    continue
            mapping[node_id] = part
    return mapping


def build_edge_metis_map(
    edges: List[Tuple[int, int]], node_shift: int = 0
) -> Dict[str, int]:
    # metis.py はエッジ頂点を metis 頂点ID = max_node + idx + 1 (1始まり) で採番。
    # load_metis_partition は「行 index i → 頂点 i+1」で part をキー(= part_map[k] は頂点 k+1)。
    # よってエッジの lookup キーは「頂点ID − 1」= max_node + idx にする (off-by-one 修正)。
    max_node = 0
    for u, v in edges:
        max_node = max(max_node, u + node_shift, v + node_shift)
    edge_map: Dict[str, int] = {}
    for idx, (u, v) in enumerate(edges):
        a, b = sorted((u, v))
        edge_map[f"edge_{a}_{b}"] = max_node + idx
    return edge_map


# -----------------------------
# starts parsing
# -----------------------------
def parse_starts_spec(spec: str, nodes_list: List[int]) -> List[int]:
    """
    spec:
      - "all" or ""  : all nodes in graph (sorted)
      - "1-10"       : inclusive range
      - "1,3,7"      : explicit list
      - mixed is allowed: "1-3,10,20-22"
    """
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(nodes_list)

    nodes_set = set(nodes_list)
    out: Set[int] = set()

    parts = [p.strip() for p in spec.split(",") if p.strip()]
    for p in parts:
        if "-" in p:
            a_str, b_str = p.split("-", 1)
            a = int(a_str.strip())
            b = int(b_str.strip())
            lo, hi = (a, b) if a <= b else (b, a)
            for x in range(lo, hi + 1):
                if x in nodes_set:
                    out.add(x)
        else:
            x = int(p)
            if x in nodes_set:
                out.add(x)

    starts_list = sorted(out)
    if not starts_list:
        raise SystemExit(
            f"--starts '{spec}' produced empty starts (none exist in graph)."
        )
    return starts_list


# -----------------------------
# constraint A
# -----------------------------
def enforce_global_constraint_A(
    buckets: List[Dict[NodeOrEdgeId, Set[int]]],
    all_starts: Set[int],
    rng: random.Random,
) -> int:
    """
    制約A（グローバル）：
      各 entity が「全startでNG」になっていたら、NGから start を1つ外す。
    返り値: 修正した entity の数
    """
    if not all_starts:
        return 0

    fixed = 0
    all_starts_frozen = frozenset(all_starts)

    for sid in range(len(buckets)):
        tbl = buckets[sid]
        # list() にしてループ中の変更に安全にする
        for entity, denied in list(tbl.items()):
            # if denied and frozenset(denied) == all_starts_frozen:
            if (
                denied
                and frozenset(denied) == all_starts_frozen
                and len(all_starts) > 1
            ):
                # 1つだけ許可に戻す（denyから削除）
                keep_allowed = rng.choice(tuple(all_starts))
                denied.discard(keep_allowed)
                fixed += 1

                # もし空集合になったら、この entity は「誰もNGにしていない」
                # → ファイルを小さくするため削除
                if not denied:
                    del tbl[entity]

    return fixed


# -----------------------------
# main
# -----------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate deny table and split per-server (no intermediate files), with global constraint A."
    )
    p.add_argument(
        "--edges", required=True, type=str, help="Path to edge list (u v per line)."
    )
    p.add_argument(
        "--ng-ratio", type=float, default=0.0, help="NG ratio per start (0.0..1.0)"
    )
    p.add_argument("--seed", type=int, default=0, help="Random seed")
    p.add_argument("--server-count", required=True, type=int, help="Total servers")
    p.add_argument(
        "--partitioner-type",
        type=str,
        default="modulo",
        choices=["modulo", "node-edge-fixed", "metis"],
        help="How to assign entity owners",
    )
    p.add_argument(
        "--metis-partition-file",
        type=str,
        default=None,
        help="gpmetis の .part.K ファイル (--partitioner-type metis のとき必須)",
    )
    p.add_argument(
        "--metis-base", type=int, default=0, help="part ファイルの行→頂点 base index"
    )
    p.add_argument(
        "--metis-node-shift",
        type=int,
        default=1,
        help="0始まりグラフは 1 / 1始まりは 0。metis.py の --node-shift と一致させる",
    )
    p.add_argument("--out-dir", required=True, type=str, help="Output directory")
    p.add_argument(
        "--starts",
        type=str,
        default="all",
        help='Start nodes spec: "all" (default), "1-10", "1,3,7", or mixed "1-3,10,20-22".',
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.0 <= args.ng_ratio <= 1.0:
        raise SystemExit("--ng-ratio must be between 0.0 and 1.0")
    if args.server_count <= 0:
        raise SystemExit("--server-count must be positive")

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise SystemExit(f"Edge list not found: {edge_path}")

    rng = random.Random(args.seed)

    edges = read_edge_list(edge_path)

    # partitioner
    if args.partitioner_type == "node-edge-fixed":
        partitioner = NodeEdgeFixedPartitioner(args.server_count)
    elif args.partitioner_type == "metis":
        if not args.metis_partition_file:
            raise SystemExit("--partitioner-type metis requires --metis-partition-file")
        part_map = load_metis_partition(args.metis_partition_file, base_index=args.metis_base)
        edge_metis_map = build_edge_metis_map(edges, node_shift=args.metis_node_shift)
        partitioner = MetisPartitioner(args.server_count, part_map, edge_metis_map)
    else:
        partitioner = ModuloPartitioner(args.server_count)

    # all nodes / edges list
    all_nodes_set: Set[int] = set()
    all_edges_list: List[str] = []
    for u, v in edges:
        all_nodes_set.add(u)
        all_nodes_set.add(v)
        all_edges_list.append(make_edge_id(u, v))

    nodes_list = sorted(all_nodes_set)
    n = len(nodes_list)
    m = len(all_edges_list)
    node_index: Dict[int, int] = {node: i for i, node in enumerate(nodes_list)}

    # ★ starts を "1-10" などで限定
    starts_list = parse_starts_spec(args.starts, nodes_list)

    # start集合（制約Aで使う）
    all_starts: Set[int] = set(starts_list)

    # NG個数（サンプル方式）
    k_node = int(round(args.ng_ratio * max(0, n - 1)))
    k_edge = int(round(args.ng_ratio * m))
    k_node = clamp_int(k_node, 0, max(0, n - 1))
    k_edge = clamp_int(k_edge, 0, m)

    # serverごとのバケツ: entity -> denied starts
    buckets: List[Dict[NodeOrEdgeId, Set[int]]] = [
        defaultdict(set) for _ in range(args.server_count)
    ]

    # startごとにNGを作り、NGになったentityを owner server に振り分けて追加
    for start in starts_list:
        si = node_index[start]

        # ノードNG（自分以外から k_node 個）
        ng_nodes = sample_nodes_excluding_self(nodes_list, si, k_node, rng)

        # エッジNG（k_edge 個）
        ng_edges: List[str] = (
            rng.sample(all_edges_list, k_edge) if (k_edge > 0 and m > 0) else []
        )

        for node in ng_nodes:
            owner = partitioner.assign_entity(node)
            buckets[owner][node].add(start)

        for eid in ng_edges:
            owner = partitioner.assign_entity(eid)
            buckets[owner][eid].add(start)

    # ★ 制約A（グローバル）を適用：全startでNGなentityを潰す
    fixed = enforce_global_constraint_A(buckets, all_starts, rng)

    # write per-server
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for sid in range(args.server_count):
        # out_path = out_dir / f"entity_to_denied_starts_server{sid}.json"
        out_path = out_dir / f"node_to_starts_server{sid}.json"
        out_path.write_text(
            json.dumps(to_serializable(buckets[sid]), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[server {sid}] wrote {out_path} (entities={len(buckets[sid])})")

    print(
        f"[DONE] N={n}, M={m}, ng_ratio={args.ng_ratio}, "
        f"k_node={k_node}, k_edge={k_edge}, starts={len(starts_list)}, "
        f"constraintA_fixed_entities={fixed}"
    )


if __name__ == "__main__":
    main()
