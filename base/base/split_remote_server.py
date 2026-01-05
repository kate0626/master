#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import random
import re
import resource
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_entity_id(raw: Any) -> NodeOrEdgeId:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.startswith("edge_"):
        return raw
    try:
        return int(raw)
    except Exception:
        return str(raw)


def make_edge_id(u: int, v: int) -> str:
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    edges: List[Tuple[int, int]] = []
    with edge_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) != 2:
                raise ValueError(f"Edge list line is malformed: {line!r}")
            u, v = map(int, parts)
            edges.append((u, v))
    return edges


@dataclass(frozen=True)
class Neighbor:
    node_id: NodeId
    server_id: int


def split_owned_entities(local_entities: Set[NodeId]) -> Tuple[List[int], List[str]]:
    nodes: List[int] = []
    edges: List[str] = []
    for ent in local_entities:
        if isinstance(ent, int):
            nodes.append(ent)
        elif isinstance(ent, str) and ent.startswith("edge_"):
            edges.append(ent)
    nodes.sort()
    edges.sort()
    return nodes, edges


def get_process_rss_kb() -> int:
    """Current RSS (kB)."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return -1


def get_process_peak_rss_kb() -> int:
    """Peak RSS (kB). Linux: VmHWM. Fallback to ru_maxrss."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except Exception:
        pass
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return -1


def deep_getsizeof(obj: Any, seen: Optional[Set[int]] = None) -> int:
    """
    Pythonオブジェクトの概算サイズ(byte)を再帰的に推定する。
    比較目的なので厳密でなくてOK。
    """
    if obj is None:
        return 0
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)

    size = sys.getsizeof(obj)

    if isinstance(obj, dict):
        for k, v in obj.items():
            size += deep_getsizeof(k, seen)
            size += deep_getsizeof(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for x in obj:
            size += deep_getsizeof(x, seen)

    return size


def safe_file_size(path: Optional[Union[str, Path]]) -> Optional[int]:
    if not path:
        return None
    try:
        p = Path(path)
        return p.stat().st_size
    except Exception:
        return None


def summarize_tables(server: Any) -> Dict[str, Any]:
    """
    サーバが保持している「グラフ隣接」「認可テーブル/索引」「owner_map」「Counter」などの
    空間使用量をまとめる。

    - rss_kb: Peak RSS (VmHWM) を優先（論文の peak memory に対応）
    - rss_kb_current: 現在RSS (VmRSS)
    - bytes_est: Pythonオブジェクトの概算deep size（比較用）
    - bytes_est_total: bytes_estの合計
    - normalized: bytes/edge, bytes/node（グラフ規模で正規化）
    - graph: |V|, |E| など “グラフを変えたら必ず変わる” 指標も含める
    - files: 入力ファイルの path/bytes/mtime を含め、別グラフを読めているか検証可能にする
    """

    def _read_proc_status_kb(key: str) -> Optional[int]:
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(key):
                        # e.g., "VmRSS:\t  12345 kB"
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(parts[1])
        except Exception:
            return None
        return None

    def _rss_current_kb() -> int:
        v = _read_proc_status_kb("VmRSS:")
        if v is not None:
            return v
        try:
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except Exception:
            return -1

    def _rss_peak_kb() -> int:
        # Linux peak RSS high-water mark
        v = _read_proc_status_kb("VmHWM:")
        if v is not None:
            return v
        try:
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        except Exception:
            return -1

    def _safe_mtime(path: Optional[Union[str, Path]]) -> Optional[float]:
        if not path:
            return None
        try:
            return Path(path).stat().st_mtime
        except Exception:
            return None

    shard = server.shard

    # ---- メイン構造（無ければ空で扱う）----
    nm = getattr(shard, "neighbor_map", {}) or {}
    nts = getattr(server, "node_to_starts", {}) or {}
    owner_map = getattr(server, "owner_map", {}) or {}

    auth_cache = getattr(server, "auth_cache", {}) or {}
    cache_entries = len(auth_cache) if isinstance(auth_cache, dict) else 0

    # ---- Counters bundle（まとめてサイズ推定）----
    counters_bundle: Dict[str, Any] = {}
    for name in [
        "access_counter",
        "authorized_counter",
        "authorization_attempt_counter",
        "authorization_denied_counter",
        "transition_counter",
    ]:
        c = getattr(server, name, None)
        if c is not None:
            counters_bundle[name] = c

    # ---- 規模指標（グラフ）----
    graph_entities = len(nm)
    graph_total_neighbors = 0
    try:
        graph_total_neighbors = sum(len(v) for v in nm.values())
    except Exception:
        graph_total_neighbors = 0

    local_entities = len(getattr(shard, "local_entities", set()) or set())

    # ---- 規模指標（認可）----
    auth_entities = len(nts)
    auth_total_starts = 0
    try:
        auth_total_starts = sum(len(v) for v in nts.values())
    except Exception:
        auth_total_starts = 0

    counters_total_keys = 0
    try:
        counters_total_keys = sum(
            len(c) for c in counters_bundle.values() if hasattr(c, "__len__")
        )
    except Exception:
        counters_total_keys = 0

    # ---- bytes推定（比較用）----
    bytes_est = {
        "neighbor_map": deep_getsizeof(nm),
        "node_to_starts": deep_getsizeof(nts),
        "owner_map": deep_getsizeof(owner_map),
        "auth_cache": deep_getsizeof(auth_cache),
        "counters": deep_getsizeof(counters_bundle),
    }
    bytes_est_total = int(sum(int(v) for v in bytes_est.values()))

    # ---- グラフの “必ず変わる” 指標（mainで埋める想定。無ければ0/None）----
    num_edges = int(getattr(server, "graph_num_edges", 0) or 0)
    num_nodes = int(getattr(server, "graph_num_nodes", 0) or 0)

    normalized: Dict[str, float] = {}
    if num_edges > 0:
        normalized["bytes_est_per_edge"] = bytes_est_total / num_edges
    if num_nodes > 0:
        normalized["bytes_est_per_node"] = bytes_est_total / num_nodes

    # ---- 入力ファイル情報（別グラフを読んでいるか検証できる）----
    edges_path = getattr(server, "edges_path", None)
    nts_path = getattr(server, "node_to_starts_path", None)
    auth_path = getattr(server, "auth_file_path", None)

    return {
        "pid": os.getpid(),
        # Peak をメイン指標として出す（論文の peak memory に対応）
        "rss_kb": _rss_peak_kb(),
        "rss_kb_current": _rss_current_kb(),
        # グラフ・認可の規模
        "graph": {
            "num_nodes": num_nodes,
            "num_edges": num_edges,
            "entities_in_neighbor_map": graph_entities,
            "total_neighbors": graph_total_neighbors,
            "local_entities": local_entities,
        },
        "auth": {
            "auth_entities": auth_entities,
            "auth_total_starts": auth_total_starts,
            "owner_map_size": len(owner_map),
            "cache_entries": cache_entries,
        },
        "counters_total_keys": counters_total_keys,
        # 推定bytes（比較用）
        "bytes_est": bytes_est,
        "bytes_est_total": bytes_est_total,
        "normalized": normalized,
        # 参照ファイル（ディスク）: path/bytes/mtime
        "files": {
            "edges": {
                "path": edges_path,
                "bytes": safe_file_size(edges_path),
                "mtime": _safe_mtime(edges_path),
            },
            "node_to_starts": {
                "path": nts_path,
                "bytes": safe_file_size(nts_path),
                "mtime": _safe_mtime(nts_path),
            },
            "auth_file": {
                "path": auth_path,
                "bytes": safe_file_size(auth_path),
                "mtime": _safe_mtime(auth_path),
            },
        },
    }


# ---------------------------------------------------------------------------
# Partitioners
# ---------------------------------------------------------------------------
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
            try:
                _, raw_u, raw_v = entity_id.split("_", 2)
                return self.assign_edge(int(raw_u), int(raw_v))
            except ValueError as exc:
                raise ValueError(f"Malformed edge id: {entity_id!r}") from exc
        raise TypeError(f"Unsupported entity id type: {entity_id!r}")


class StaticPartitioner:
    def __init__(
        self,
        server_count: int,
        mapping: Dict[str, int],
        fallback: Optional[ModuloPartitioner] = None,
    ) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count
        self.mapping = {str(k): int(v) % server_count for k, v in mapping.items()}
        self.fallback = fallback or ModuloPartitioner(server_count)

    def assign_entity(self, entity_id: NodeId) -> int:
        key = str(entity_id)
        if key in self.mapping:
            return self.mapping[key]
        return self.fallback.assign_entity(entity_id)


# ---------------------------------------------------------------------------
# Shard
# ---------------------------------------------------------------------------
class GraphShard:
    """
    Bipartite expansion: node <-> edge_entity(edge_u_v) <-> node

    目的:
      - 隣接(neighbor_map)は「全体グラフ(エッジリスト)」から必ず構築する
      - 所有(local_entities)は別に決める（owned_hints_only / owner_map）
      - get_neighbors は
          * グラフに存在しない entity -> None
          * 存在するが次数0 -> []
        を返して、原因切り分けができるようにする
    """

    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_id: int,
        server_count: int,
        partitioner: Optional[Any] = None,
        owner_map: Optional[Dict[str, int]] = None,
        owned_hints_only: bool = False,
        owned_hints: Optional[Set[NodeOrEdgeId]] = None,
    ) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")

        self.server_id = server_id
        self.partitioner = partitioner or ModuloPartitioner(server_count)
        self.owner_map: Dict[str, int] = owner_map or {}
        self.owned_hints_only = owned_hints_only
        self.owned_hints: Set[NodeOrEdgeId] = owned_hints or set()

        self.neighbor_map: Dict[NodeId, List[Neighbor]] = {}
        self.local_entities: Set[NodeId] = set()

        def normalize_entity(ent: Any) -> NodeId:
            if isinstance(ent, int):
                return ent
            if isinstance(ent, str):
                if ent.startswith("edge_"):
                    return ent
                if ent.isdigit():
                    try:
                        return int(ent)
                    except Exception:
                        return ent
            return ent

        def owner_of(ent: Any) -> int:
            key = str(ent)
            if key in self.owner_map:
                return int(self.owner_map[key])
            return self.partitioner.assign_entity(ent)

        def ensure_key(ent: NodeId) -> None:
            if ent not in self.neighbor_map:
                self.neighbor_map[ent] = []

        for u, v in edges:
            u = normalize_entity(u)
            v = normalize_entity(v)
            edge_id = normalize_entity(make_edge_id(int(u), int(v)))

            ensure_key(u)
            ensure_key(v)
            ensure_key(edge_id)

            edge_owner = owner_of(edge_id)
            u_owner = owner_of(u)
            v_owner = owner_of(v)

            self.neighbor_map[u].append(Neighbor(edge_id, edge_owner))
            self.neighbor_map[v].append(Neighbor(edge_id, edge_owner))
            self.neighbor_map[edge_id].append(Neighbor(u, u_owner))
            self.neighbor_map[edge_id].append(Neighbor(v, v_owner))

        if self.owned_hints_only:
            hints_str = {str(x) for x in self.owned_hints}

            for x in self.owned_hints:
                nx = normalize_entity(x)
                ensure_key(nx)

            for ent in self.neighbor_map.keys():
                if str(ent) in hints_str:
                    self.local_entities.add(ent)
        else:
            for ent in self.neighbor_map.keys():
                if owner_of(ent) == self.server_id:
                    self.local_entities.add(ent)

    def get_neighbors(self, entity_id: Any) -> Optional[List[Neighbor]]:
        ent = entity_id
        if isinstance(ent, str) and (not ent.startswith("edge_")) and ent.isdigit():
            try:
                ent = int(ent)
            except Exception:
                pass

        if ent not in self.neighbor_map:
            print(f"[GraphShard] entity NOT IN GRAPH: {ent!r}")
            return None

        neigh = list(self.neighbor_map.get(ent, []))
        return neigh


# ---------------------------------------------------------------------------
# Auth tables
# ---------------------------------------------------------------------------
def load_entity_auth_table(
    path: Optional[Union[str, Path]],
) -> Dict[int, Dict[str, Set[Any]]]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Auth table not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[int, Dict[str, Set[Any]]] = {}
    for k, v in raw.items():
        try:
            start = int(k)
        except Exception:
            continue
        nodes = set(int(x) for x in v.get("n", []) if x is not None)
        edges = set(str(x) for x in v.get("e", []) if x is not None)
        out[start] = {"n": nodes, "e": edges}
    return out


def load_node_to_starts_table(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"node_to_starts file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[NodeOrEdgeId, Set[int]] = {}
    for k, values in raw.items():
        try:
            entity_key: NodeOrEdgeId = int(k)
        except Exception:
            entity_key = str(k)
        starts: Set[int] = set()
        for v in values:
            try:
                starts.add(int(v))
            except Exception:
                continue
        out[entity_key] = starts
    return out


def build_owner_map_from_sibling_node_to_starts_files(
    base_path: Path,
) -> Dict[str, int]:
    dir_path = base_path.parent
    owner_map: Dict[str, int] = {}
    pat = re.compile(r"node_to_starts_server(\d+)\.json$")

    for p in sorted(dir_path.glob("node_to_starts_server*.json")):
        m = pat.search(p.name)
        if not m:
            continue
        sid = int(m.group(1))
        tbl = load_node_to_starts_table(p)
        for ent in tbl.keys():
            key = str(ent)
            if key in owner_map and owner_map[key] != sid:
                print(
                    f"[WARN] entity {key} appears in multiple files: {owner_map[key]} and {sid}"
                )
            owner_map[key] = sid

    return owner_map


def resolve_node_to_starts_path(base_path: Path, server_id: int) -> Path:
    stem = base_path.stem
    suffix = base_path.suffix
    candidates = [
        base_path.with_name(f"{stem}_server{server_id}{suffix}"),
        base_path.with_name(f"{stem}{server_id}{suffix}"),
        base_path,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"node_to_starts file not found. Tried: {[str(c) for c in candidates]}"
    )


def filter_auth_table_for_shard(
    auth_table: Dict[int, Dict[str, Set[Any]]],
    partitioner: Any,
    server_id: int,
) -> Dict[int, Dict[str, Set[Any]]]:
    filtered: Dict[int, Dict[str, Set[Any]]] = {}
    for start, entries in auth_table.items():
        local_nodes = {
            n
            for n in entries.get("n", set())
            if partitioner.assign_entity(n) == server_id
        }
        local_edges = {
            e
            for e in entries.get("e", set())
            if partitioner.assign_entity(e) == server_id
        }
        if local_nodes or local_edges:
            filtered[start] = {"n": local_nodes, "e": local_edges}
    return filtered


# ---------------------------------------------------------------------------
# RNG serialization
# ---------------------------------------------------------------------------
def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    return obj


def _from_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return tuple(_from_jsonable(x) for x in obj)
    return obj


def serialize_rng_state(rng: random.Random) -> Any:
    return _to_jsonable(rng.getstate())


def deserialize_rng_state(jsonable_state: Any) -> tuple:
    return _from_jsonable(jsonable_state)


# ---------------------------------------------------------------------------
# PeerWalker
# ---------------------------------------------------------------------------
class PeerWalker:
    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        auth_table: Optional[Dict[int, Dict[str, Set[str]]]] = None,
        stats_collector: Optional[Any] = None,
        ppr_mode: bool = False,
        node_to_starts: Optional[Dict[NodeOrEdgeId, Set[int]]] = None,
        owner_map: Optional[Dict[str, int]] = None,
    ):
        self.shard = shard
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
        self.request_timeout = request_timeout
        self.max_hops = max_hops
        self.auth_table = auth_table or {}
        self.stats_collector = stats_collector
        self.ppr_mode = ppr_mode
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = node_to_starts or {}
        self.owner_map: Dict[str, int] = owner_map or {}

    def _record_auth_cost(self, duration: float) -> None:
        if self.stats_collector is None:
            return
        if hasattr(self.stats_collector, "auth_time_total"):
            self.stats_collector.auth_time_total += duration
        if hasattr(self.stats_collector, "auth_calls"):
            self.stats_collector.auth_calls += 1

    def _is_locally_allowed(self, start_node: Optional[int], entity: Any) -> bool:
        if start_node is None:
            return False
        allowed_starts = self.node_to_starts.get(entity)
        return bool(allowed_starts and start_node in allowed_starts)

    def _check_remote_authorization(
        self, target_server: int, start_node: Optional[int], entity: Any
    ) -> bool:
        if target_server < 0 or target_server >= len(self.endpoints):
            return False
        url = f"{self.endpoints[target_server].rstrip('/')}/authorize"
        payload = {"entity": entity, "start_node": start_node}
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
                body = json.loads(resp.read())
            return bool(body.get("allowed"))
        except Exception:
            return False

    def _authorize_candidate(
        self, start_node: Optional[int], candidate: Dict[str, Any]
    ) -> bool:
        target = candidate["node_id"]
        owner_sid: Optional[int] = None
        if self.owner_map:
            owner_sid = self.owner_map.get(str(target))
        if owner_sid is None:
            try:
                owner_sid = int(candidate.get("server_id"))
            except Exception:
                owner_sid = self.shard.partitioner.assign_entity(target)

        t0 = time.perf_counter()
        if owner_sid == self.shard.server_id:
            allowed = self._is_locally_allowed(start_node, target)
        else:
            allowed = self._check_remote_authorization(owner_sid, start_node, target)
        self._record_auth_cost(time.perf_counter() - t0)
        return allowed

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def _bump(self, attr: str, key: Any) -> None:
        if self.stats_collector is None:
            return
        counter = getattr(self.stats_collector, attr, None)
        if counter is None:
            return
        counter[key] += 1

    def _record_entity_visit(self, entity_id: Any) -> None:
        self._bump("access_counter", entity_id)

    def _record_authorization_attempt(self, source: Any) -> None:
        self._bump("authorization_attempt_counter", source)

    def _record_authorization_success(self, current: Any, target: Any) -> None:
        self._bump("authorized_counter", target)
        self._bump("transition_counter", f"{current}->{target}")

    def _record_authorization_denial(self, source: Any) -> None:
        self._bump("authorization_denied_counter", source)

    def _resolve_start_node(
        self, state: Dict[str, Any], path: List[NodeId]
    ) -> Optional[int]:
        if "start_node" in state:
            try:
                return int(state["start_node"])
            except Exception:
                return None
        if path:
            try:
                return int(path[0])
            except Exception:
                return None
        return None

    def _select_next_neighbor(
        self,
        rng: random.Random,
        neighbors: List[Dict[str, Any]],
        start_node: Optional[int],
        current_entity: NodeId,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not neighbors:
            return None, None

        # シャッフルした配列を前から取ることによって、自動的にランダムに選んでいることになる
        indices = list(range(len(neighbors)))
        rng.shuffle(indices)
        # print("next node is", indices[0])
        cand = neighbors[indices[0]]
        return cand, None
        # 認可部分をコメントオフ
        # for idx in indices:
        #     self._record_authorization_attempt(current_entity)
        #     cand = neighbors[idx]
        #     cid = cand["node_id"]
        #     if self._authorize_candidate(start_node, cand):
        #         self._record_authorization_success(current_entity, cid)
        #         return cand, None
        #     else:
        #         self._record_authorization_denial(current_entity)

        # denial = {
        #     "denied": True,
        #     "denied_reason": f"no authorized neighbors from {current_entity}",
        # }
        # return None, denial

    # 実際のWalk処理を行う
    def continue_from_state(self, state: dict) -> dict:
        current_sid = self.shard.server_id

        rng_state_json = state.get("rng_state")
        rng = random.Random()
        if rng_state_json is not None:
            loaded = deserialize_rng_state(rng_state_json)
            if loaded:
                rng.setstate(loaded)
        else:
            rng = random.Random(state.get("seed"))

        current_entity: NodeId = state["current_node"]
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))
        start_node = self._resolve_start_node(state, path)

        self._record_entity_visit(current_entity)
        # 終了確率よりも大きかったら継続
        while rng.random() > alpha:
            hops_done += 1
            owner = self.shard.partitioner.assign_entity(current_entity)
            if owner != current_sid:
                state_out = {
                    "start_node": start_node,
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                return self._post_continue(owner, state_out)

            neighbors = self._get_local_neighbors(current_entity)
            # この処理では、確実到達することのできるノードが認可ずみで出てくる
            next_choice, denial_payload = self._select_next_neighbor(
                rng, neighbors, start_node, current_entity
            )
            if next_choice is None:
                result = {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                }
                if denial_payload:
                    result.update(denial_payload)
                return result

            next_entity = next_choice["node_id"]
            next_server = int(next_choice["server_id"])

            self._record_entity_visit(next_entity)

            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            if next_server != current_sid:
                state_out = {
                    "start_node": start_node,
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                }
                return self._post_continue(next_server, state_out)

        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class EdgeAwareHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json({"status": "ok", "server_id": self.server.server_id})
            return

        if parsed.path == "/access_stats":
            payload = {
                "access": dict(self.server.access_counter),
                "authorized": dict(self.server.authorized_counter),
                "authorization_attempts": dict(
                    self.server.authorization_attempt_counter
                ),
                "authorization_denied": dict(self.server.authorization_denied_counter),
                "transition": dict(self.server.transition_counter),
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
                "walk_time_total": self.server.walk_time_total,
                "walk_calls": self.server.walk_calls,
                "memory": summarize_tables(self.server),
            }
            self._write_json(payload)
            return

        if parsed.path != "/neighbors":
            self.send_error(404, "Unknown path")
            return

        query = parse_qs(parsed.query)
        raw_entity = query.get("node", [None])[0]
        if raw_entity is None:
            self.send_error(400, "Missing 'node' query parameter")
            return

        if raw_entity.startswith("edge_"):
            entity: NodeId = raw_entity
        else:
            try:
                entity = int(raw_entity)
            except ValueError:
                self.send_error(
                    400, "'node' must be an integer id or an edge id (edge_u_v)"
                )
                return

        neighbors = self.server.shard.get_neighbors(entity)
        if neighbors is None:
            self.send_error(404, f"Entity {entity} not owned by this shard")
            return

        payload = {
            "node_id": entity,
            "server_id": self.server.server_id,
            "neighbors": [asdict(n) for n in neighbors],
        }
        self._write_json(payload)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/walk":
            self._handle_walk_start()
            return
        if parsed.path == "/continue_walk":
            self._handle_continue_walk()
            return
        if parsed.path == "/authorize":
            self._handle_authorize()
            return
        self.send_error(404, "Unknown path")

    def _handle_walk_start(self) -> None:
        params = self._read_json_body()
        if params is None:
            return

        start_node = params.get("start_node")
        alpha = params.get("alpha")
        walks = int(params.get("walks", 1))
        seed = params.get("seed", None)
        endpoints = params.get("endpoints")
        server_count = params.get("server_count")

        if (
            start_node is None
            or alpha is None
            or endpoints is None
            or server_count is None
        ):
            self.send_error(
                400,
                "Missing required parameters: start_node, alpha, endpoints, server_count",
            )
            return

        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            ppr_mode=getattr(self.server, "ppr_mode", False),
            node_to_starts=getattr(self.server, "node_to_starts", None),
            owner_map=getattr(self.server, "owner_map", None),
        )

        start_ts = time.perf_counter()
        wall_start_epoch = time.time()
        wall_start_iso = now_iso()

        results = []
        for i in range(walks):
            rng = random.Random(seed if seed is None else (seed + i))
            initial_state = {
                "start_node": int(start_node),
                "current_node": int(start_node),
                "path": [int(start_node)],
                "servers": [self.server.server_id],
                "alpha": float(alpha),
                "rng_state": serialize_rng_state(rng),
                "hops_done": 0,
            }
            t0 = time.perf_counter()
            res = walker.continue_from_state(initial_state)
            dt = time.perf_counter() - t0

            # 実行時間を加算
            self.server.walk_time_total += dt
            self.server.walk_calls += 1
            results.append(res)

        wall_end_epoch = time.time()
        wall_end_iso = now_iso()
        duration = time.perf_counter() - start_ts

        payload = {
            "walks": results,
            "metrics": {
                "server_id": self.server.server_id,
                "duration_sec": duration,
                "wall_start_epoch": wall_start_epoch,
                "wall_end_epoch": wall_end_epoch,
                "wall_start_time": wall_start_iso,
                "wall_end_time": wall_end_iso,
                "walks_requested": walks,
                "walks_completed": len(results),
                "alpha": float(alpha),
            },
        }
        self._write_json(payload)

    def _handle_continue_walk(self) -> None:
        state = self._read_json_body()
        if state is None:
            return

        walker = PeerWalker(
            self.server.shard,
            endpoints=self.server.endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            ppr_mode=getattr(self.server, "ppr_mode", False),
            node_to_starts=getattr(self.server, "node_to_starts", None),
            owner_map=getattr(self.server, "owner_map", None),
        )
        try:
            # print("ここが/continue_walk")
            # 本当はいらない時間
            t0 = time.perf_counter()
            res = walker.continue_from_state(state)
            _ = time.perf_counter() - t0
        except Exception as exc:
            print("エラー")
            self.send_error(500, f"Error during continue_walk: {exc}")
            return
        self._write_json(res)

    def _handle_authorize(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        raw_entity = payload.get("entity")
        start_node = payload.get("start_node")
        entity = parse_entity_id(raw_entity)
        try:
            start_int = int(start_node)
        except Exception:
            self.send_error(400, "'start_node' must be an integer")
            return

        allowed_starts = self.server.node_to_starts.get(entity, set())
        allowed = bool(start_int in allowed_starts)
        self._write_json(
            {"allowed": allowed, "entity": entity, "server_id": self.server.server_id}
        )

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json_body(self) -> Optional[dict]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return None
        body = self.rfile.read(content_length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return None

    def _write_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class GraphShardServer(ThreadingHTTPServer):
    def __init__(
        self,
        host: str,
        port: int,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
    ) -> None:
        super().__init__((host, port), EdgeAwareHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout

        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
        self.owner_map: Dict[str, int] = {}

        self.access_counter = Counter()
        self.authorized_counter = Counter()
        self.authorization_attempt_counter = Counter()
        self.authorization_denied_counter = Counter()
        self.transition_counter = Counter()

        self.auth_time_total = 0.0
        self.auth_calls = 0
        self.walk_time_total = 0.0
        self.walk_calls = 0

        # ---- 計測用：このサーバが参照している入力ファイル ----
        self.edges_path: Optional[str] = None
        self.node_to_starts_path: Optional[str] = None
        self.auth_file_path: Optional[str] = None

        # ---- 認可キャッシュ（キャッシュ実装がある場合に使う） ----
        # ないモデルでは空のままでOK（サイズ0として観測できる）
        self.auth_cache: Dict[str, bool] = {}

        self.graph_num_edges: int = 0
        self.graph_num_nodes: int = 0
        self.edges_mtime: Optional[float] = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (node + edge bipartite model)."
    )

    parser.add_argument(
        "--owned-hints-only",
        action="store_true",
        help="If set, shard owns ONLY entities listed in node_to_starts_serverX.json keys.",
    )
    parser.add_argument(
        "--edges",
        default="./../../dataset/Louvain/graph/karate.gr",
        help="Path to the shared edge list file.",
    )
    parser.add_argument(
        "--server-id",
        type=int,
        required=True,
        help="Unique id of this server within the cluster (0-indexed).",
    )
    parser.add_argument(
        "--server-count",
        type=int,
        required=True,
        help="Total number of servers participating in the cluster.",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host/IP address to bind the shard server."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to expose the shard server."
    )
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for all servers in order (host:port).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=5.0,
        help="Timeout (sec) when this server queries other shards.",
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default=None,
        help="Optional JSON file path mapping start_node -> allowed entities (n/e).",
    )
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        default=None,
        help="Optional JSON path mapping target_node/edge -> allowed start nodes.",
    )
    parser.add_argument(
        "--dump-auth",
        action="store_true",
        help="Dump filtered auth/node_to_starts to auth_dump_server{sid}.json for debugging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise FileNotFoundError(f"Edge list not found: {edge_path}")

    edges = load_edge_list(edge_path)

    # ★追加：グラフ基本統計（必ず変化する）
    unique_nodes = set()
    for u, v in edges:
        unique_nodes.add(u)
        unique_nodes.add(v)
    print(f"[Server {args.server_id}] EDGE_FILE={edge_path.resolve()}")
    print(
        f"[Server {args.server_id}] |E|={len(edges)}  |V|={len(unique_nodes)}  bytes={edge_path.stat().st_size}"
    )
    print(
        f"[Server {args.server_id}] mtime={datetime.fromtimestamp(edge_path.stat().st_mtime)}"
    )

    base_partitioner = ModuloPartitioner(args.server_count)
    partitioner: Any = base_partitioner

    filtered_node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
    owned_hints: Set[NodeOrEdgeId] = set()
    owner_map: Dict[str, int] = {}

    if args.node_to_starts_file:
        base_nts_path = Path(args.node_to_starts_file)
        nts_path = resolve_node_to_starts_path(base_nts_path, args.server_id)
        loaded_nts = load_node_to_starts_table(nts_path)

        filtered_node_to_starts = loaded_nts
        owned_hints = set(loaded_nts.keys())

        owner_map = build_owner_map_from_sibling_node_to_starts_files(nts_path)

        partitioner = StaticPartitioner(
            server_count=args.server_count,
            mapping=owner_map,
            fallback=base_partitioner,
        )

    shard = GraphShard(
        edges,
        server_id=args.server_id,
        server_count=args.server_count,
        partitioner=partitioner,
        owned_hints=owned_hints,
        owned_hints_only=bool(args.owned_hints_only),
        owner_map=owner_map,
    )

    owned_nodes, owned_edges = split_owned_entities(shard.local_entities)

    print(
        f"[Server {args.server_id}] OWNED entity counts: nodes={len(owned_nodes)}, edges={len(owned_edges)}, total={len(shard.local_entities)}"
    )
    print(f"[Server {args.server_id}] OWNED nodes sample: {owned_nodes[:5]}")
    print(f"[Server {args.server_id}] OWNED edges sample: {owned_edges[:5]}")

    server = GraphShardServer(
        host=args.host,
        port=args.port,
        shard=shard,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
    )
    server.edges_path = str(edge_path)
    server.graph_num_edges = len(edges)
    server.graph_num_nodes = len(unique_nodes)
    server.edges_mtime = edge_path.stat().st_mtime

    if args.auth_file:
        auth_table = load_entity_auth_table(Path(args.auth_file))
        server.auth_table = filter_auth_table_for_shard(
            auth_table, shard.partitioner, shard.server_id
        )
        server.auth_file_path = str(Path(args.auth_file))

    if args.node_to_starts_file:
        server.node_to_starts = filtered_node_to_starts
        server.node_to_starts_path = str(nts_path)

    if owner_map:
        server.owner_map = owner_map

    if args.dump_auth:
        dump = {
            "server_id": server.server_id,
            "auth_table": {
                str(k): {"n": sorted(v.get("n", [])), "e": sorted(v.get("e", []))}
                for k, v in server.auth_table.items()
            },
            "node_to_starts": {
                str(k): sorted(list(v)) for k, v in server.node_to_starts.items()
            },
            "owner_map_size": len(server.owner_map),
        }
        dump_path = Path(f"auth_dump_server{server.server_id}.json")
        dump_path.write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def dump_access_stats() -> None:
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "authorization_attempts": dict(server.authorization_attempt_counter),
            "authorization_denied": dict(server.authorization_denied_counter),
            "transition": dict(server.transition_counter),
            "auth_time_total": server.auth_time_total,
            "auth_calls": server.auth_calls,
            "walk_time_total": server.walk_time_total,
            "walk_calls": server.walk_calls,
            "memory": summarize_tables(server),
        }
        out_path = Path(f"access_stats_server{server.server_id}.json")
        out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(
            f"[Server {server.server_id}] auth summary: {server.auth_calls} calls, total {server.auth_time_total:.6f}s"
        )
        print(f"[Server {server.server_id}] Access stats saved to {out_path}")

    atexit.register(dump_access_stats)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
