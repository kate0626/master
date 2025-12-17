#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
import atexit  # ← 追加
from collections import Counter  # ← 追加
from datetime import datetime, timezone

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]


def parse_entity_id(raw: Any) -> NodeOrEdgeId:
    """
    Convert a raw node/edge identifier to the normalized form used in shards.
    Integers stay ints; edge ids keep their string form (edge_u_v); everything
    else falls back to string.
    """
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.startswith("edge_"):
        return raw
    try:
        return int(raw)
    except Exception:
        return str(raw)


"""
    python3 base/auth-many-server/remote_server.py --edges ./dataset/Louvain/graph/karate.gr --server-count 2 --server-id 1 --host 10.58.60.6 --port 3000 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --auth-file base/auth-many-server/auth_by_start.json
    python3 base/auth-many-server/remote_server.py --edges ./dataset/Louvain/graph/karate.gr --server-count 2 --server-id 1 --host 10.58.60.6 --port 3000 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --auth-file base/auth-many-server/auth_by_start.json


1台で行う時
    python3 base/auth-many-server/remote_server.py   --server-id 0 --server-count 1  --edges dataset/Louvain/graph/karate.gr   --host 10.58.60.5 --port 3000   --server-endpoints 10.58.60.5:3000   --auth-file base/auth-many-server/auth_by_start.json --node-to-starts-file base/auth-many-server/karate/node_to_starts.json

2台で行うとき
    python3 base/auth-many-server/remote_server.py   --server-id 0 --server-count 2   --edges dataset/Louvain/graph/karate.gr   --host 10.58.60.5 --port 3000   --server-endpoints 10.58.60.5:3000   --auth-file base/auth-many-server/auth_by_start.json --node-to-starts-file base/auth-many-server/karate/node_to_starts.json
    python3 base/auth-many-server/remote_server.py   --server-id 1 --server-count 2   --edges dataset/Louvain/graph/karate.gr   --host 10.58.60.11 --port 3000   --server-endpoints 10.58.60.11:3000   --auth-file base/auth-many-server/auth_by_start.json --node-to-starts-file base/auth-many-server/karate/node_to_starts.json


    形式的にauthを渡しているだけで実際にはnode_to_startしか持てていない
    認可データは server_id でフィルタされ、リモートのエンティティは /authorize 経由で所有サーバに確認する

    パーティショナ:
      - デフォルト: modulo のみ（他のパーティショナは未実装につき無効）
"""


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
def now_iso() -> str:
    """Timezone-aware ISO8601 timestamp for logging/metrics."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_edge_id(u: int, v: int) -> str:
    """
    u, v をソートして昇順に並べ、f"edge_{a}_{b}" という文字列を返す。
    これにより (1,2) と (2,1) が同じエッジIDになる。副作用なし。
    """
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    """
    指定パスを UTF-8 で開き、空行をスキップしつつ各行を空白で分割して 2 要素でない行は ValueError を投げる。
    u, v を int に変換してタプルとして edges に追加し、最後にリストで返す。
    """
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
    """
    node_id（int か str）と server_id（int）を持つ不変オブジェクト。
    asdict() で辞書化できるため JSON レスポンス作成に便利である。
    """

    node_id: NodeId
    server_id: int


## TODO: この分け方をどの程度変えることができるのかも指標になりそう
class ModuloPartitioner:
    """
    ノードとエッジのサーバごとへの割り当て
    assign_node(node_id)：ノードを node_id % server_count で割り当てる（シンプル均等割り当て）。
    assign_edge(u, v)：エッジは (a * 1_000_003 + b) % server_count のハッシュで割り当てる。1_000_003 は大きな素数で衝突分散に寄与する。
    assign_entity(entity_id)：与えられた entity_id が整数ならノード割り当て、文字列で edge_* ならエッジ割当を行う。形式不正や未対応型なら例外を投げる。

    auth-server/のremote_serverに関して考える

        使用するグラフはtest/以下の部分である
        どのノードにアクセスできるのか（ノード：アクセスできる始点）を示したnode_to_start.jsonがあり、これはstartの後についている数字のサーバに別々に配置されるようにしたい

        例えば、node_to_start0.jsonに書いてあるものはサーバ０に配置されるようにしたい

        これを参照して現在のpeerwalkを行いたいので修正して
    """

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
    """
    Explicit entity -> server mapping. Falls back to modulo for unknown ids.
    """

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

    def assign_node(self, node_id: int) -> int:
        key = str(node_id)
        if key in self.mapping:
            return self.mapping[key]
        return self.fallback.assign_node(node_id)

    def assign_edge(self, u: int, v: int) -> int:
        edge_id = make_edge_id(u, v)
        if edge_id in self.mapping:
            return self.mapping[edge_id]
        return self.fallback.assign_edge(u, v)

    def assign_entity(self, entity_id: NodeId) -> int:
        key = str(entity_id)
        if key in self.mapping:
            return self.mapping[key]
        return self.fallback.assign_entity(entity_id)


class GraphShard:
    """
    Each shard owns two types of entities:
      * graph nodes (int id)
      * synthetic edge nodes (edge_u_v)
    Random walks traverse the bipartite expansion node -> edge -> node -> ...
    __init__(edges, server_id, server_count)：与えられた全エッジ列を走査し、各ノードと各合成エッジ（edge_u_v）の持ち主をパーティショナで判定する。

        自身が所有するエンティティは local_entities に追加し、neighbor_map に空リストを準備する（_ensure_entity）。
        ノード側から見て隣接にはエッジID（合成エンティティ）を追加し、エッジ側から見て隣接には両端ノードを追加する（各 Neighbor には相手のサーバID を記録）。
        これによりグラフは「二部展開（node ↔ edge）」として内部表現される。
        _ensure_entity(entity_id)：ローカルエンティティ集合に加え、neighbor_map のキーを確実に作る。副作用：集合とマップを更新する。
        get_neighbors(entity_id) -> Optional[List[Neighbor]]：entity_id がこのシャードのローカルエンティティなら、その隣接の Neighbor リスト（コピー）を返す。所有していないなら None を返す。
    """

    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_id: int,
        server_count: int,
        partitioner: Optional[Any] = None,
        owned_hints: Optional[Set[NodeOrEdgeId]] = None,
    ) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")

        self.server_id = server_id
        self.partitioner = partitioner or ModuloPartitioner(server_count)
        self.owned_hints: Set[NodeOrEdgeId] = owned_hints or set()
        self.local_entities: Set[NodeId] = set()
        self.neighbor_map: Dict[NodeId, List[Neighbor]] = defaultdict(list)

        for u, v in edges:
            edge_id = make_edge_id(u, v)
            # ヒントにあれば自サーバ所有を優先
            if u in self.owned_hints:
                u_owner = self.server_id
            else:
                u_owner = self.partitioner.assign_node(u)
            if v in self.owned_hints:
                v_owner = self.server_id
            else:
                v_owner = self.partitioner.assign_node(v)
            if edge_id in self.owned_hints:
                edge_owner = self.server_id
            else:
                # 端点が異なる場合でもエッジは「端点のどちらか」に必ず置く（ここでは小さいserver_id側に寄せる）
                edge_owner = u_owner if u_owner <= v_owner else v_owner

            if u_owner == self.server_id:
                self._ensure_entity(u)
            if v_owner == self.server_id:
                self._ensure_entity(v)
            if edge_owner == self.server_id:
                self._ensure_entity(edge_id)

            if u_owner == self.server_id:
                self.neighbor_map[u].append(Neighbor(edge_id, edge_owner))
            if v_owner == self.server_id:
                self.neighbor_map[v].append(Neighbor(edge_id, edge_owner))
            if edge_owner == self.server_id:
                self.neighbor_map[edge_id].append(Neighbor(u, u_owner))
                self.neighbor_map[edge_id].append(Neighbor(v, v_owner))

    def _ensure_entity(self, entity_id: NodeId) -> None:
        self.local_entities.add(entity_id)
        self.neighbor_map.setdefault(entity_id, [])

    def get_neighbors(self, entity_id: NodeId) -> Optional[List[Neighbor]]:
        if entity_id not in self.local_entities:
            return None
        resolved: List[Neighbor] = []
        for n in self.neighbor_map.get(entity_id, []):
            try:
                owner = self.partitioner.assign_entity(n.node_id)
            except Exception:
                owner = n.server_id
            resolved.append(Neighbor(n.node_id, owner))
        return resolved


# ---------------------------------------------------------------------------
# Authorization (entity-granular) helpers
# ---------------------------------------------------------------------------
def load_entity_auth_table(
    path: Optional[Union[str, Path]],
) -> Dict[int, Dict[str, Set[Any]]]:
    """
    JSON format:
    {
      "1": { "n": [1,2,5], "e": ["edge_2_5","edge_5_6"] },
      "2": { "n": [2,3], "e": [] }
    }
    Returns: { start_node_int: { "n": set_of_node_strs, "e": set_of_edge_strs } }
    """
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
            # skip non-integer keys
            continue
        nodes = set(int(x) for x in v.get("n", []) if x is not None)
        edges = set(str(x) for x in v.get("e", []) if x is not None)
        out[start] = {"n": nodes, "e": edges}
    return out


def load_node_to_starts_table(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
    """
    JSON format:
      { "3": [1,5,7], "edge_1_2": [1,2] }
    Returns: { target_entity (int or str) -> set(start_nodes) }
    """
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
        starts = set()
        for v in values:
            try:
                starts.add(int(v))
            except Exception:
                continue
        out[entity_key] = starts
    return out


def resolve_node_to_starts_path(base_path: Path, server_id: int) -> Path:
    """
    Prefer server-specific node_to_starts files when present.
    e.g. node_to_starts_server0.json or node_to_starts0.json next to the base file.
    Falls back to the provided base_path if no variant exists.
    """
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


def filter_node_to_starts_for_shard(
    node_to_starts: Dict[NodeOrEdgeId, Set[int]],
    partitioner: Any,
    server_id: int,
) -> Dict[NodeOrEdgeId, Set[int]]:
    """
    Keep only the authorization entries for entities owned by this shard.
    This lets each server avoid holding permissions for entities it does not host.
    """
    filtered: Dict[NodeOrEdgeId, Set[int]] = {}
    for entity, starts in node_to_starts.items():
        try:
            owner = partitioner.assign_entity(entity)
        except Exception:
            continue
        if owner == server_id:
            filtered[entity] = set(starts)
    return filtered


def filter_auth_table_for_shard(
    auth_table: Dict[int, Dict[str, Set[Any]]],
    partitioner: Any,
    server_id: int,
) -> Dict[int, Dict[str, Set[Any]]]:
    """
    Trim auth_table so each shard only keeps entries for entities it owns.
    """
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
# RNG (de)serialization helpers
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


class PeerWalker:
    """
    Same control flow as the base `remote_server_edge` walker, with the addition
    of authorization-aware neighbor selection and per-entity statistics.
    """

    def __init__(
        self,
        shard,
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

    # --- Authorization helpers ---------------------------------------------
    def _record_auth_cost(self, duration: float) -> None:
        if self.stats_collector is None:
            return
        server = self.stats_collector
        if hasattr(server, "auth_time_total"):
            server.auth_time_total += duration
        if hasattr(server, "auth_calls"):
            server.auth_calls += 1

    def _is_locally_allowed(self, start_node: Optional[int], entity: Any) -> bool:
        if start_node is None:
            return False
        allowed_starts = self.node_to_starts.get(entity)
        return bool(allowed_starts and start_node in allowed_starts)

    # 遷移予定のサーバが異なるサーバだった時の流れ
    def _check_remote_authorization(
        self, target_server: int, start_node: Optional[int], entity: Any
    ) -> bool:
        # ここでそのサーバに行かないと認可テーブルがないので確認できないのではないか
        # きちんとターゲットサーバがあることを確認
        print(
            f"[Server {self.shard.server_id}] Checking remote auth on server {target_server} for entity {entity} from start_node {start_node}"
        )
        if target_server < 0 or target_server >= len(self.endpoints):
            print(
                f"[Server {self.shard.server_id}] auth check skipped: endpoint for server {target_server} is missing"
            )
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
        except Exception as exc:
            print(
                f"[Server {self.shard.server_id}] auth check failed on server {target_server} for {entity}: {exc}"
            )
            return False

    # def _authorize_candidate(
    #     self, start_node: Optional[int], candidate: Dict[str, Any]
    # ) -> bool:
    #     owner_sid = int(candidate["server_id"])
    #     target = candidate["node_id"]
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
        print("認可")
        if owner_sid == self.shard.server_id:
            # 移動さきが同じサーバだったらローカルで認可判定
            allowed = self._is_locally_allowed(start_node, target)
            print(
                f"  → ローカル認可結果: {'allowed' if allowed else 'denied'} for entity {target} from start_node {start_node}"
            )
        else:
            # 異なるサーバだったらリモート認可確認
            print(
                f" サーバが異なるのでリモート認可確認 on server {owner_sid} for entity {target} from start_node {start_node},今のサーバとノードは {self.shard.server_id}, {target}移動前のノードは {start_node}"
            )
            allowed = self._check_remote_authorization(owner_sid, start_node, target)
        t1 = time.perf_counter()
        self._record_auth_cost(t1 - t0)
        return allowed

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    def _select_next_neighbor(
        self,
        rng: random.Random,
        neighbors: List[Dict[str, Any]],
        start_node: Optional[int],
        current_entity: NodeId,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not neighbors:
            return None, None

        # 「まだ選んでいないものを選ぶ」ために、インデックスをシャッフルして順番に試す
        indices = list(range(len(neighbors)))
        rng.shuffle(indices)

        max_retries = len(neighbors)  # ログ用に残しておく
        next_choice: Optional[Dict[str, Any]] = None
        # 認可は行わず、最初の neighbor をそのまま採用ー＞使用せずにBaseを使用
        # for idx in indices:
        #     candidate = neighbors[idx]
        #     cid = candidate["node_id"]

        #     # 認可カウンタだけは更新（不要なら削除してOK）
        #     self._record_authorization_attempt(current_entity)
        #     self._record_authorization_success(current_entity, cid)

        #     # 認可なしなので allowed = True に強制
        #     next_choice = candidate
        #     break
        # ここから
        # 隣接が全部NGになるまで繰り返すが、
        # 同じ neighbor は二度と選ばない（indices を一度ずつ見る）
        for idx in indices:
            self._record_authorization_attempt(current_entity)

            candidate = neighbors[idx]
            cid = candidate["node_id"]

            if self._authorize_candidate(start_node, candidate):
                next_choice = candidate
                # 選んだノードに関して認可を行う
                self._record_authorization_success(current_entity, cid)
                break
            else:
                self._record_authorization_denial(current_entity)

        if next_choice is None:
            print(
                f"[Server {self.shard.server_id}] All {max_retries} neighbors denied → stop"
            )
            denial = {
                "denied": True,
                "denied_reason": f"no authorized neighbors from {current_entity}",
            }
            return None, denial
        # ここまで

        return next_choice, None

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    # --- Main walk ----------------------------------------------------------
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
        # 終了確率をクリアした時にのみ遷移
        while hops_done < self.max_hops and rng.random() > alpha:
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
            # TODO:
            print(
                f"[DBG] neighbors of {current_entity} on sid={current_sid}: "
                + ", ".join(
                    [f"{n['node_id']}@{n['server_id']}" for n in neighbors[:30]]
                )
                + (" ..." if len(neighbors) > 30 else "")
            )
            for n in neighbors[:200]:
                pred = self.shard.partitioner.assign_entity(n["node_id"])
                if int(n["server_id"]) != pred:
                    print(
                        f"[DBG][MISMATCH] neighbor {n['node_id']} says sid={n['server_id']} but partitioner says {pred}"
                    )
            ## TODO:
            # あるノードが認可を通るか→通るまで繰り返す
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

        if hops_done >= self.max_hops:
            print(f"[Server {current_sid}] reached max hops {self.max_hops} → finish")
        else:
            print(f"[Server {current_sid}] stopped by alpha after {hops_done} hops")

        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }

    # --- Stats helpers ------------------------------------------------------
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

    def _bump_server_counter(self, attr: str, key: Any) -> None:
        """Safely increment shared counters if the server exposed them."""
        if self.stats_collector is None:
            return
        counter = getattr(self.stats_collector, attr, None)
        if counter is None:
            return
        counter[key] += 1

    def _record_entity_visit(self, entity_id: Any) -> None:
        """Track only the entities that were actually part of the walk path."""
        self._bump_server_counter("access_counter", entity_id)

    def _record_authorization_attempt(self, source: Any) -> None:
        """Track how many times we tried leaving each entity."""
        self._bump_server_counter("authorization_attempt_counter", source)

    def _record_authorization_success(self, current: Any, target: Any) -> None:
        """Track successful authorizations and resulting transitions."""
        self._bump_server_counter("authorized_counter", target)
        self._bump_server_counter("transition_counter", f"{current}->{target}")

    def _record_authorization_denial(self, source: Any) -> None:
        """Track failed attempts at leaving an entity."""
        self._bump_server_counter("authorization_denied_counter", source)


# ---------------------------------------------------------------------------
# HTTP handlers
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
                # ★ 認可時間（秒）と呼び出し回数
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
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

        entity: NodeId
        if raw_entity.startswith("edge_"):
            entity = raw_entity
        else:
            try:
                entity = int(raw_entity)
            except ValueError:
                self.send_error(
                    400, "'node' must be an integer id or an edge id (edge_u_v)"
                )
                return

        # print(
        #     f"[Server {self.server.server_id}] neighbor request for entity {entity} from {self.client_address}"
        # )

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
        # コントローラによるここから N 回のウォークを始めてくれとの開始命令
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

        print(
            f"[Server {self.server.server_id}] /walk start node={start_node} alpha={alpha} walks={walks} seed={seed}"
        )

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
            res = walker.continue_from_state(initial_state)
            results.append(res)
            # time.sleep(0.01)
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
        print(
            f"[Server {self.server.server_id}] finished /walk in {duration:.3f}s; returning {len(results)} walks"
        )
        self._write_json(payload)

    # 他のサーバから来たRwerを受け取る
    def _handle_continue_walk(self) -> None:

        state = self._read_json_body()
        if state is None:
            return

        # print(
        #     f"[Server {self.server.server_id}] /continue_walk from {self.client_address} hops_done={state.get('hops_done', 0)}"
        # )
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
            res = walker.continue_from_state(state)
        except Exception as exc:
            self.send_error(500, f"Error during continue_walk: {exc}")
            return

        self._write_json(res)

    def _handle_authorize(self) -> None:
        payload = self._read_json_body()
        # リクエストボディの読解
        print(f"[Server {self.server.server_id}] /authorize request: {payload}")
        if payload is None:
            return

        raw_entity = payload.get("entity")
        start_node = payload.get("start_node")
        entity = parse_entity_id(raw_entity)
        print("確認ノード、スタートノード", entity, start_node, type(start_node))
        print(self.server.node_to_starts)
        try:
            start_int = int(start_node)
        except Exception:
            self.send_error(400, "'start_node' must be an integer")
            return

        allowed_starts = self.server.node_to_starts.get(entity, set())
        allowed = bool(start_int in allowed_starts)
        print("確認ノード、スタートノード、うむ", allowed_starts, start_node, allowed)
        self._write_json(
            {
                "allowed": allowed,
                "entity": entity,
                "server_id": self.server.server_id,
            }
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
        # auth_table will be attached by main() if provided
        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
        self.owner_map: Dict[str, int] = {}

        # 認可およびアクセスの統計カウンタを初期化
        self.access_counter = Counter()  # 各ノード・エッジへのアクセス回数
        self.authorized_counter = Counter()  # 認可成功したノード・エッジ回数
        self.authorization_attempt_counter = Counter()  # 認可試行回数
        self.authorization_denied_counter = Counter()  # 認可失敗回数
        self.transition_counter = Counter()  # 遷移 (from→to) のペア頻度
        # === 追加ここまで ===
        # ★ 認可にかかった時間の合計（秒）と呼び出し回数
        self.auth_time_total = 0.0
        self.auth_calls = 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (node + edge bipartite model)."
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
        help="Optional JSON path mapping target_node -> allowed start nodes.",
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
    print(
        f"[Server {args.server_id}] loaded edges from {edge_path} (count={len(edges)})"
    )

    # パーティショナはデフォルト Modulo。サーバ専用 node_to_starts がある場合は StaticPartitioner で強制上書き。
    base_partitioner = ModuloPartitioner(args.server_count)
    partitioner: Any = base_partitioner

    filtered_node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
    owned_hints: Set[NodeOrEdgeId] = set()
    node_to_starts_path: Optional[Path] = None
    loaded_nts: Dict[NodeOrEdgeId, Set[int]] = {}
    static_mapping: Dict[str, int] = {}
    is_server_specific = False
    if args.node_to_starts_file:
        base_nts_path = Path(args.node_to_starts_file)
        node_to_starts_path = resolve_node_to_starts_path(base_nts_path, args.server_id)
        print(
            f"[Server {args.server_id}] loading node_to_starts from {node_to_starts_path}"
        )
        loaded_nts = load_node_to_starts_table(node_to_starts_path)
        print(f"(entries={len(loaded_nts)}), {loaded_nts}")
        is_server_specific = node_to_starts_path.resolve() != base_nts_path.resolve()
        # if is_server_specific:
        print("サーバ専用ファイル")
        print(f"[Server {args.server_id}] detected server-specific node_to_starts file")
        # サーバ専用ファイルは全件をこのサーバに紐付ける
        filtered_node_to_starts = loaded_nts
        owned_hints = set(filtered_node_to_starts.keys())
        static_mapping = {str(ent): int(args.server_id) for ent in loaded_nts}
        partitioner = StaticPartitioner(
            server_count=args.server_count,
            mapping=static_mapping,
            fallback=base_partitioner,
        )
        ownership_lines = [
            f"  {ent} -> server {args.server_id}"
            for ent in sorted(loaded_nts, key=lambda x: str(x))
        ]
        print(
            f"[Server {args.server_id}] node_to_starts ownership map (forced local):\n"
            + "\n".join(ownership_lines)
        )
        # else:
        #     print("bbbbbbb")
        #     filtered_node_to_starts = filter_node_to_starts_for_shard(
        #         loaded_nts, partitioner, args.server_id
        #     )
        #     owned_hints = set(filtered_node_to_starts.keys())
        #     ownership_lines = []
        #     for ent in sorted(loaded_nts, key=lambda x: str(x)):
        #         try:
        #             owner = partitioner.assign_entity(ent)
        #             ownership_lines.append(f"  {ent} -> server {owner}")
        #         except Exception:
        #             ownership_lines.append(f"  {ent} -> (assign failed)")
        #     if ownership_lines:
        #         print(
        #             f"[Server {args.server_id}] node_to_starts ownership map:\n"
        #             + "\n".join(ownership_lines)
        #         )

    shard = GraphShard(
        edges,
        server_id=args.server_id,
        server_count=args.server_count,
        partitioner=partitioner,
        owned_hints=owned_hints,
    )
    server = GraphShardServer(
        host=args.host,
        port=args.port,
        shard=shard,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
    )

    # load auth table if provided
    auth_table = {}
    if args.auth_file:
        auth_table = load_entity_auth_table(Path(args.auth_file))
        server.auth_table = filter_auth_table_for_shard(
            auth_table, shard.partitioner, shard.server_id
        )
        print(
            f"[Server {server.server_id}] auth_table: loaded {len(auth_table)} starts, keeping {len(server.auth_table)} local entries"
        )
    if args.node_to_starts_file:
        server.node_to_starts = filtered_node_to_starts
        print(
            f"[Server {server.server_id}] node_to_starts: loaded {len(filtered_node_to_starts)} local entities"
            + (
                f" from {node_to_starts_path}"
                if node_to_starts_path is not None
                else ""
            )
        )
    if static_mapping:
        server.owner_map = static_mapping

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
        }
        dump_path = Path(f"auth_dump_server{server.server_id}.json")
        dump_path.write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[Server {server.server_id}] dumped auth/node_to_starts to {dump_path}")

    # print(
    #     f"[Server {server.server_id}] Serving {len(shard.local_entities)} entities (nodes + edges) on {args.host}:{args.port} / {args.server_count} servers"
    # )

    def dump_access_stats():
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "authorization_attempts": dict(server.authorization_attempt_counter),
            "authorization_denied": dict(server.authorization_denied_counter),
            "transition": dict(server.transition_counter),
            # ★ 追加
            "auth_time_total": server.auth_time_total,
            "auth_calls": server.auth_calls,
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
