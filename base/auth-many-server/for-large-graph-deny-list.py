# #!/usr/bin/env python3
# # -*- coding: utf-8 -*-
# """
# Scalable deny-table generator (table-based, memory-light, compressed).

# - Deny table
# - Nodes and edges handled separately (NodeEdgeFixed: nodes->server0, edges->server1)
# - Starts are ALL nodes
# - Output is compressed binary + index (no huge JSON lists)

# Output:
#   out_dir/
#     starts.map.json              # original node_id -> start_idx
#     starts.list.json             # start_idx -> original node_id (array)
#     server0_nodes.deny.bin       # compressed records: node entity -> denied starts (delta-varint)
#     server0_nodes.deny.idx.tsv   # entity \t offset \t length
#     server1_edges.deny.bin
#     server1_edges.deny.idx.tsv

# Record format (.bin):
#   [uvarint entity_len][entity_bytes][uvarint payload_len][payload_bytes]
# payload_bytes:
#   delta-varint encoded sorted start_idx list

# How deny list is generated (scalable):
#   For each entity, sample k = round(ng_ratio * n_starts) distinct start indices from range(n).
#   Constraint A (global): ensure NOT all starts are denied:
#     - if k == n: force k = n-1
#   Deterministic per entity:
#     - RNG seeded by (global seed, entity string) so reproducible without storing giant intermediate state.

# Usage:
# nohup python3 ./base/auth-many-server/for-large-graph-deny-list.py \
#   --edges ./dataset/Louvain/graph/vldb.gr \
#   --ng-ratio 0.3 \
#   --seed 0 \
#   --out-dir ./base/auth-many-server/data/splits/vldb/0.3 \
#   --print-stats > run.log 2>&1 &


# Notes:
#   - This avoids OOM by not building dict/set of all pairs.
#   - Time and disk still scale with total denied pairs (~ entities * k).
# """

# from __future__ import annotations

# import argparse
# import hashlib
# import json
# import math
# import os
# import struct
# from pathlib import Path
# from typing import Dict, Iterable, Iterator, List, Set, Tuple


# # -----------------------------
# # basic graph IO
# # -----------------------------
# def read_edge_list(path: Path) -> Iterator[Tuple[int, int]]:
#     with path.open("r", encoding="utf-8") as f:
#         for line in f:
#             s = line.strip()
#             if not s:
#                 continue
#             parts = s.split()
#             if len(parts) < 2:
#                 continue
#             yield int(parts[0]), int(parts[1])


# def make_edge_id(u: int, v: int) -> str:
#     a, b = sorted((u, v))
#     return f"edge_{a}_{b}"


# # -----------------------------
# # uvarint + delta encoding
# # -----------------------------
# def uvarint_encode(x: int) -> bytes:
#     # unsigned LEB128
#     if x < 0:
#         raise ValueError("uvarint requires non-negative")
#     out = bytearray()
#     while True:
#         b = x & 0x7F
#         x >>= 7
#         if x:
#             out.append(b | 0x80)
#         else:
#             out.append(b)
#             break
#     return bytes(out)


# def delta_varint_encode(sorted_ints: List[int]) -> bytes:
#     # encode sorted ints as deltas: x0, x1-x0, x2-x1, ...
#     out = bytearray()
#     prev = 0
#     first = True
#     for x in sorted_ints:
#         if x < 0:
#             raise ValueError("negative in delta list")
#         if first:
#             d = x
#             first = False
#         else:
#             d = x - prev
#             if d < 0:
#                 raise ValueError("list must be sorted ascending")
#         out += uvarint_encode(d)
#         prev = x
#     return bytes(out)


# # -----------------------------
# # deterministic RNG per entity
# # -----------------------------
# def seed_for_entity(global_seed: int, entity: str) -> int:
#     h = hashlib.blake2b(digest_size=8)
#     h.update(struct.pack("<q", int(global_seed)))
#     h.update(b"|")
#     h.update(entity.encode("utf-8"))
#     return int.from_bytes(h.digest(), "little", signed=False)


# class XorShift64Star:
#     # tiny deterministic PRNG (fast, reproducible, no big state)
#     def __init__(self, seed: int) -> None:
#         if seed == 0:
#             seed = 0x9E3779B97F4A7C15
#         self.x = seed & 0xFFFFFFFFFFFFFFFF

#     def next_u64(self) -> int:
#         x = self.x
#         x ^= (x >> 12) & 0xFFFFFFFFFFFFFFFF
#         x ^= (x << 25) & 0xFFFFFFFFFFFFFFFF
#         x ^= (x >> 27) & 0xFFFFFFFFFFFFFFFF
#         self.x = x
#         return (x * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF

#     def randint(self, lo: int, hi: int) -> int:
#         # inclusive lo..hi
#         if lo > hi:
#             raise ValueError("lo>hi")
#         span = hi - lo + 1
#         return lo + (self.next_u64() % span)


# def sample_k_unique_from_range(n: int, k: int, rng: XorShift64Star) -> List[int]:
#     """
#     Sample k distinct integers from [0, n).
#     Uses Floyd's algorithm: O(k) time, O(k) memory.
#     Good when k << n (which is what you want for scalable deny tables).
#     """
#     if k <= 0:
#         return []
#     if k >= n:
#         return list(range(n))
#     chosen: Set[int] = set()
#     # Floyd: for j in [n-k, n-1], pick t in [0..j], add t if new else add j
#     for j in range(n - k, n):
#         t = rng.randint(0, j)
#         if t in chosen:
#             chosen.add(j)
#         else:
#             chosen.add(t)
#     out = sorted(chosen)
#     return out


# # -----------------------------
# # writer: bin + idx
# # -----------------------------
# class BinKVWriter:
#     def __init__(self, bin_path: Path, idx_path: Path) -> None:
#         self.bin_path = bin_path
#         self.idx_path = idx_path
#         self._bf = bin_path.open("wb")
#         self._if = idx_path.open("w", encoding="utf-8")
#         self._offset = 0

#     def close(self) -> None:
#         self._bf.close()
#         self._if.close()

#     def write_record(self, entity: str, payload: bytes) -> None:
#         eb = entity.encode("utf-8")
#         rec = uvarint_encode(len(eb)) + eb + uvarint_encode(len(payload)) + payload
#         off = self._offset
#         self._bf.write(rec)
#         self._offset += len(rec)
#         # index: entity \t offset \t length
#         self._if.write(f"{entity}\t{off}\t{len(rec)}\n")


# # -----------------------------
# # main
# # -----------------------------
# def parse_args() -> argparse.Namespace:
#     p = argparse.ArgumentParser()
#     p.add_argument(
#         "--edges", required=True, type=str, help="Edge list file (u v per line)"
#     )
#     p.add_argument("--ng-ratio", type=float, default=0.0, help="Deny ratio (0..1)")
#     p.add_argument("--seed", type=int, default=0, help="Global seed")
#     p.add_argument("--out-dir", required=True, type=str, help="Output directory")
#     p.add_argument(
#         "--max-entities",
#         type=int,
#         default=0,
#         help="For debug: limit number of entities processed (0=all)",
#     )
#     p.add_argument("--print-stats", action="store_true", help="Print summary stats")
#     return p.parse_args()


# def main() -> None:
#     args = parse_args()
#     if not (0.0 <= args.ng_ratio <= 1.0):
#         raise SystemExit("--ng-ratio must be in [0,1]")

#     edges_path = Path(args.edges).expanduser()
#     if not edges_path.exists():
#         raise SystemExit(f"Edge list not found: {edges_path}")

#     out_dir = Path(args.out_dir)
#     out_dir.mkdir(parents=True, exist_ok=True)

#     # 1) scan graph once to collect nodes and edges list (edge ids)
#     nodes_set: Set[int] = set()
#     edge_ids: List[str] = []
#     for u, v in read_edge_list(edges_path):
#         nodes_set.add(u)
#         nodes_set.add(v)
#         edge_ids.append(make_edge_id(u, v))

#     starts_list = sorted(nodes_set)  # original node ids
#     n_starts = len(starts_list)
#     if n_starts == 0:
#         raise SystemExit("No nodes found (empty graph?)")

#     # 2) build start index mapping (dense 0..n-1)
#     start_to_idx: Dict[int, int] = {nid: i for i, nid in enumerate(starts_list)}

#     # save mapping (small JSON; ok)
#     (out_dir / "starts.map.json").write_text(
#         json.dumps(start_to_idx, indent=2, ensure_ascii=False), encoding="utf-8"
#     )
#     (out_dir / "starts.list.json").write_text(
#         json.dumps(starts_list, indent=2, ensure_ascii=False), encoding="utf-8"
#     )

#     # 3) decide deny sample size k (constraint A: cannot be all starts denied)
#     # Deny table (so k ~ ng_ratio * n)
#     k = int(round(args.ng_ratio * n_starts))
#     if k >= n_starts:
#         k = n_starts - 1  # constraint A: at least one allowed
#     if k < 0:
#         k = 0

#     # writers: NodeEdgeFixed -> server0 nodes, server1 edges
#     w_nodes = BinKVWriter(
#         out_dir / "server0_nodes.deny.bin", out_dir / "server0_nodes.deny.idx.tsv"
#     )
#     w_edges = BinKVWriter(
#         out_dir / "server1_edges.deny.bin", out_dir / "server1_edges.deny.idx.tsv"
#     )

#     # 4) generate deny lists per entity (entity-centric, scalable)
#     # Nodes as entities (stringify for uniformity; you can keep "1" etc)
#     nodes_entities = [str(nid) for nid in starts_list]
#     edges_entities = edge_ids

#     max_ent = args.max_entities if args.max_entities and args.max_entities > 0 else None

#     denied_pairs_est = 0
#     processed_nodes = 0
#     processed_edges = 0

#     # ---- nodes ----
#     for i, ent in enumerate(nodes_entities):
#         if max_ent is not None and processed_nodes >= max_ent:
#             break
#         # deterministic RNG per entity
#         rng = XorShift64Star(seed_for_entity(args.seed, "NODE|" + ent))
#         denied = sample_k_unique_from_range(n_starts, k, rng)
#         # (optional) you might want to ensure "start==entity node" is never denied; uncomment if desired:
#         # node_id = int(ent); denied = [x for x in denied if x != start_to_idx[node_id]]
#         payload = delta_varint_encode(denied)
#         w_nodes.write_record(ent, payload)
#         processed_nodes += 1
#         denied_pairs_est += len(denied)

#     # ---- edges ----
#     for i, ent in enumerate(edges_entities):
#         if max_ent is not None and processed_edges >= max_ent:
#             break
#         rng = XorShift64Star(seed_for_entity(args.seed, "EDGE|" + ent))
#         denied = sample_k_unique_from_range(n_starts, k, rng)
#         payload = delta_varint_encode(denied)
#         w_edges.write_record(ent, payload)
#         processed_edges += 1
#         denied_pairs_est += len(denied)

#     w_nodes.close()
#     w_edges.close()

#     if args.print_stats:
#         m_edges = len(edges_entities)
#         print(f"[STATS] starts(all nodes) = {n_starts}")
#         print(f"[STATS] edges = {m_edges}")
#         print(f"[STATS] ng_ratio = {args.ng_ratio} -> k(denied per entity) = {k}")
#         print(
#             f"[STATS] processed nodes = {processed_nodes}, processed edges = {processed_edges}"
#         )
#         print(f"[STATS] approx denied pairs written = {denied_pairs_est}")
#         print(f"[OUT] {out_dir}")


# if __name__ == "__main__":
#     main()
