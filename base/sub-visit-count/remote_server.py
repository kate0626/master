#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict, Counter  # ★ defaultdict, Counter をまとめて import
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
import atexit
from datetime import datetime, timezone

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]

# ホットサブグラフ判定に使うデフォルト値
DEFAULT_WARMUP_RATIO = 0.5  # 全ウォークのうち何割をウォームアップとして使うか
DEFAULT_HOT_MIN_VISITS = 2  # ホット扱いするグループ訪問回数しきい値


"""
  単一サーバでも分散でも動くが、ここでは主に server-count=1 を想定。

  例:
    python3 base/sub-visit-count/remote_server.py \
      --edges ./dataset/Louvain/graph/karate.gr \
      --server-count 1 \
      --server-id 0 \
      --host 10.58.60.6 \
      --port 3000 \
      --server-endpoints 10.58.60.6:3000 \
      --node-to-starts-file base/auth-many-server/karate/node_to_starts.json \
      --subgraph-file base/auth-subgraph-server/subgraph_index_karate_size6.json \
      --warmup-ratio 0.3 \
      --hot-min-visits 3

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


class ModuloPartitioner:
    """
    ノードとエッジのサーバごとへの割り当て
    assign_node(node_id)：ノードを node_id % server_count で割り当てる（シンプル均等割り当て）。
    assign_edge(u, v)：エッジは (a * 1_000_003 + b) % server_count のハッシュで割り当てる。1_000_003 は大きな素数で衝突分散に寄与する。
    assign_entity(entity_id)：与えられた entity_id が整数ならノード割り当て、文字列で edge_* ならエッジ割当を行う。形式不正や未対応型なら例外を投げる。
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


class GraphShard:
    """
    各シャードは 2 種類のエンティティを持つ:
      * graph nodes (int id)
      * synthetic edge nodes (edge_u_v)
    Random walks traverse: node -> edge -> node -> ...
    """

    def __init__(
        self, edges: Sequence[Tuple[int, int]], server_id: int, server_count: int
    ) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")

        self.server_id = server_id
        self.partitioner = ModuloPartitioner(server_count)
        self.local_entities: Set[NodeId] = set()
        self.neighbor_map: Dict[NodeId, List[Neighbor]] = defaultdict(list)

        for u, v in edges:
            edge_id = make_edge_id(u, v)
            u_owner = self.partitioner.assign_node(u)
            v_owner = self.partitioner.assign_node(v)
            edge_owner = self.partitioner.assign_edge(u, v)

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
        return list(self.neighbor_map.get(entity_id, []))


# ---------------------------------------------------------------------------
# Authorization / Subgraph helpers
# ---------------------------------------------------------------------------
def load_entity_auth_table(
    path: Optional[Union[str, Path]],
) -> Dict[int, Dict[str, Set[Any]]]:
    """
    元々のエンティティ単位の認可テーブル（ここでは groups も追加で読むが、
    現状は subgraph 認可では node_to_starts を使うので補助的扱い）。
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
            continue
        nodes = set(int(x) for x in v.get("n", []) if x is not None)
        edges = set(str(x) for x in v.get("e", []) if x is not None)
        groups = set(int(x) for x in v.get("groups", []) if x is not None)
        out[start] = {"n": nodes, "e": edges, "groups": groups}
    return out


def load_subgraph_index(path: Optional[Union[str, Path]]) -> Dict[str, Any]:
    """
    サブグラフ定義（node_to_group, groups）を読み込む。
    Format:
    {
      "node_to_group": {"1": 0, "edge_1_2": 0},
      "groups": [{ "id": 0, "nodes": [1,2,3], "edges": ["edge_1_2"]}]
    }
    """
    if path is None:
        return {"node_to_group": {}, "groups": {}}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Subgraph index not found: {p}")

    data = json.loads(p.read_text(encoding="utf-8"))
    node_to_group: Dict[NodeOrEdgeId, int] = {}
    for node, gid in data.get("node_to_group", {}).items():
        try:
            entity_key: NodeOrEdgeId = int(node)
        except Exception:
            entity_key = str(node)
        try:
            node_to_group[entity_key] = int(gid)
        except Exception:
            continue

    group_map: Dict[int, Dict[str, Set[NodeOrEdgeId]]] = {}
    for item in data.get("groups", []):
        gid = item.get("id")
        if gid is None:
            continue
        try:
            gid_int = int(gid)
        except Exception:
            continue
        nodes = set(int(x) for x in item.get("nodes", []) if x is not None)
        edges = set(str(x) for x in item.get("edges", []) if x is not None)
        group_map[gid_int] = {"nodes": nodes, "edges": edges}

    return {"node_to_group": node_to_group, "groups": group_map}


def load_node_to_starts_table(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
    """
    node / edge -> {許可された start_node 群} のテーブル。
    これを使って「個別認可」を行う。
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
    サブグラフ認可 + ホットサブグラフ履歴キャッシュを持つ RW 実行クラス。

    - 前半のウォーク（ウォームアップ）で group_visit_counter を貯める
    - 一定数を超えてよく通るサブグラフを hot_groups としてマーク
    - hot_groups に対しては group 認可ではなく「個別ノード認可」に切り替えることで
      PPR の歪みを抑える
    """

    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        auth_table: Optional[Dict[int, Dict[str, Set[str]]]] = None,
        stats_collector: Optional[Any] = None,
        subgraph_index: Optional[Dict[str, Any]] = None,
        node_to_starts: Optional[Dict[NodeOrEdgeId, Set[int]]] = None,
        warmup_walks: int = 0,  # ★★ ウォームアップとして扱うウォーク本数
        hot_min_visits: int = DEFAULT_HOT_MIN_VISITS,  # ★★ あるグループがホットとみなされる訪問回数
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

        # サブグラフ定義の展開
        subgraph_index = subgraph_index or {}
        self.entity_to_group: Dict[NodeOrEdgeId, int] = {}
        raw_map = subgraph_index.get("node_to_group", {})
        if isinstance(raw_map, dict):
            for key, gid in raw_map.items():
                try:
                    entity_key: NodeOrEdgeId = int(key)
                except Exception:
                    entity_key = str(key)
                try:
                    self.entity_to_group[entity_key] = int(gid)
                except Exception:
                    continue

        self.group_members: Dict[int, Dict[str, Set[NodeOrEdgeId]]] = {}
        raw_groups = subgraph_index.get("groups", {})

        if isinstance(raw_groups, list):
            for entry in raw_groups:
                gid = entry.get("id")
                if gid is None:
                    continue
                try:
                    gid_int = int(gid)
                except Exception:
                    continue
                node_members = set(
                    int(x) for x in entry.get("nodes", []) if x is not None
                )
                edge_members = set(
                    str(x) for x in entry.get("edges", []) if x is not None
                )
                self.group_members[gid_int] = {
                    "nodes": node_members,
                    "edges": edge_members,
                }
        elif isinstance(raw_groups, dict):
            for gid_key, members in raw_groups.items():
                try:
                    gid_int = int(gid_key)
                except Exception:
                    gid_int = gid_key  # 念のため

                node_members = set(
                    int(x) for x in members.get("nodes", []) if x is not None
                )
                edge_members = set(
                    str(x) for x in members.get("edges", []) if x is not None
                )
                self.group_members[gid_int] = {
                    "nodes": node_members,
                    "edges": edge_members,
                }
        else:
            self.group_members = {}

        # グループ単位キャッシュ（これは従来通り）
        self.granted_groups: Set[int] = set()
        self.denied_groups: Set[int] = set()

        # 個別ノード/エッジ認可で使うテーブル
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = node_to_starts or {}

        # ★★ ホットサブグラフ関係の状態
        self.warmup_walks = max(0, int(warmup_walks))
        self.hot_min_visits = max(1, int(hot_min_visits))
        self.group_visit_counter: Counter[int] = Counter()
        self.hot_groups: Set[int] = set()
        self.current_walk_index: int = 0  # 今何本目の RW を走っているか

    # === Hot subgraph: ウォーク開始時に walk index をセット ===
    def start_new_walk(self, walk_index: int) -> None:
        """新しい RW を始めるたびに呼び出して walk_index を記録。"""
        self.current_walk_index = walk_index

    # --- Authorization helpers ---------------------------------------------
    def _record_auth_cost(self, duration: float) -> None:
        if self.stats_collector is None:
            return
        server = self.stats_collector
        if hasattr(server, "auth_time_total"):
            server.auth_time_total += duration
        if hasattr(server, "auth_calls"):
            server.auth_calls += 1

    def _is_allowed_entity(self, start_node: Optional[int], entity: Any) -> bool:
        """
        個別ノード/エッジに対する認可。

        node_to_starts[entity] に start_node が含まれているかで判定。
        """
        if start_node is None:
            return False
        allowed_starts = self.node_to_starts.get(entity)
        return bool(allowed_starts and start_node in allowed_starts)

    def _load_group_cache(self, state: Dict[str, Any]) -> None:
        granted = state.get("granted_groups") or []
        denied = state.get("denied_groups") or []
        self.granted_groups = {int(g) for g in granted}
        self.denied_groups = {int(g) for g in denied}

    def _group_state_payload(self) -> Dict[str, List[int]]:
        if not self.entity_to_group:
            return {}
        return {
            "granted_groups": sorted(self.granted_groups),
            "denied_groups": sorted(self.denied_groups),
        }

    def _evaluate_group_access(
        self,
        start_node: Optional[int],
        entity: NodeOrEdgeId,
    ) -> Optional[bool]:
        """
        グループ丸ごと認可を行う。
        ただし、ホットサブグラフに対しては後段でスキップする（_is_entity_authorizedを参照）。
        """
        print(
            f"[GROUP]Evaluating group access for entity {entity} with start_node {start_node}"
        )
        if start_node is None:
            return False
        gid = self.entity_to_group.get(entity)
        if gid is None:
            return None

        # すでに判定済みならそのまま返す
        if gid in self.granted_groups:
            return True
        if gid in self.denied_groups:
            return False

        members = self.group_members.get(gid, {})
        member_nodes = members.get("nodes", set())
        member_edges = members.get("edges", set())

        allowed = True
        for node in member_nodes:
            if not self._is_allowed_entity(start_node, node):
                allowed = False
                break
        if allowed:
            for edge in member_edges:
                if not self._is_allowed_entity(start_node, edge):
                    allowed = False
                    break

        if allowed:
            self.granted_groups.add(gid)
        else:
            self.denied_groups.add(gid)
        return allowed

    def _is_entity_authorized(
        self,
        start_node: Optional[int],
        entity: NodeOrEdgeId,
    ) -> bool:
        """
        実際の認可判定:
          - entity が属する group が hot_groups に入っている場合:
              → サブグラフ丸ごと認可は行わず、個別ノード認可のみ (_is_allowed_entity)
                (= PPR 精度優先)
          - それ以外:
              → 従来通り group 認可 (_evaluate_group_access) を使い、
                 group が定義されていない場合にだけ個別ノード認可へフォールバック
                (= 性能優先)
        """
        gid = self.entity_to_group.get(entity)

        # ★★ ホットサブグラフに対しては個別認可のみ
        if gid is not None and gid in self.hot_groups:
            return self._is_allowed_entity(start_node, entity)

        # 通常のサブグラフ認可
        group_result = self._evaluate_group_access(start_node, entity)
        if group_result is None:
            # グループに属していない or 定義なし → 個別認可
            print(
                f"[FALLBACK] Entity {entity} not in any group グループに属さないので個別で認可を行う"
            )
            return self._is_allowed_entity(start_node, entity)
        return bool(group_result)

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    # NOTE: ここが認可アルゴリズムの重要な部分
    def _select_next_neighbor(
        self,
        rng: random.Random,
        neighbors: List[Dict[str, Any]],
        start_node: Optional[int],
        current_entity: NodeId,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not neighbors:
            return None, None
        indices = list(range(len(neighbors)))
        rng.shuffle(indices)

        max_retries = len(neighbors)
        next_choice: Optional[Dict[str, Any]] = None

        for idx in indices:
            self._record_authorization_attempt(current_entity)
            candidate = neighbors[idx]
            cid = candidate["node_id"]

            t0 = time.perf_counter()
            allowed = self._is_entity_authorized(start_node, cid)
            t1 = time.perf_counter()
            self._record_auth_cost(t1 - t0)

            if allowed:
                next_choice = candidate
                self._record_authorization_success(current_entity, cid)
                break
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
        self._load_group_cache(state)

        # 訪問を記録（サブグラフごとのカウントも更新）
        self._record_entity_visit(current_entity)

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
                state_out.update(self._group_state_payload())
                return self._post_continue(owner, state_out)

            neighbors = self._get_local_neighbors(current_entity)
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
                state_out.update(self._group_state_payload())
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
        """
        実際 RW で通ったエンティティの訪問回数を記録。
        ついでに group_visit_counter を更新し、ホットサブグラフを判定する。
        """
        self._bump_server_counter("access_counter", entity_id)

        gid = self.entity_to_group.get(entity_id)
        if gid is not None:
            self.group_visit_counter[gid] += 1
            # 訪問回数をチェック
            print(
                f"[VISIT] Entity {entity_id} in group {gid} visited {self.group_visit_counter[gid]} times"
            )

            # ウォームアップ区間を過ぎていて、かつ一定回数以上出現しているグループをホット扱い
            if (
                self.current_walk_index >= self.warmup_walks
                and self.group_visit_counter[gid] >= self.hot_min_visits
            ):
                self.hot_groups.add(gid)

    def _record_authorization_attempt(self, source: Any) -> None:
        self._bump_server_counter("authorization_attempt_counter", source)

    def _record_authorization_success(self, current: Any, target: Any) -> None:
        self._bump_server_counter("authorized_counter", target)
        self._bump_server_counter("transition_counter", f"{current}->{target}")

    def _record_authorization_denial(self, source: Any) -> None:
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
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
                # ★★ デバッグ用途: ホットサブグラフ情報も返しておく
                "group_visits": dict(self.server.group_visit_counter),
                "hot_groups": sorted(self.server.hot_groups),
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

        print(
            f"[Server {self.server.server_id}] /walk start node={start_node} alpha={alpha} walks={walks} seed={seed}"
        )

        # ★★ ここで「1回の実行の中でホットサブグラフを作る」設定を決める
        warmup_ratio = max(
            0.0,
            float(
                params.get(
                    "warmup_ratio",
                    getattr(self.server, "warmup_ratio", DEFAULT_WARMUP_RATIO),
                )
            ),
        )
        warmup_walks = max(0, min(walks, int(walks * warmup_ratio)))
        hot_min_visits = max(
            1,
            int(
                params.get(
                    "hot_min_visits",
                    getattr(self.server, "hot_min_visits", DEFAULT_HOT_MIN_VISITS),
                )
            ),
        )
        # Continue API でも同じ値を使えるようにサーバ側へも保存
        self.server.warmup_walks = warmup_walks
        self.server.hot_min_visits = hot_min_visits
        self.server.warmup_ratio = warmup_ratio

        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            subgraph_index=getattr(self.server, "subgraph_index", None),
            node_to_starts=getattr(self.server, "node_to_starts", None),
            warmup_walks=warmup_walks,
            hot_min_visits=hot_min_visits,
        )

        start_ts = time.perf_counter()
        wall_start_epoch = time.time()
        wall_start_iso = now_iso()
        results = []
        propagate_group_state = bool(walker.entity_to_group)

        for i in range(walks):
            # ★★ 何本目の RW かを Walker に知らせる
            walker.start_new_walk(i)
            walker.granted_groups = set()
            walker.denied_groups = set()

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
            if propagate_group_state:
                initial_state["granted_groups"] = []
                initial_state["denied_groups"] = []
            res = walker.continue_from_state(initial_state)
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
        print(
            f"[Server {self.server.server_id}] finished /walk in {duration:.3f}s; returning {len(results)} walks"
        )
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
            subgraph_index=getattr(self.server, "subgraph_index", None),
            node_to_starts=getattr(self.server, "node_to_starts", None),
            # ★ continue では warmup/hot の設定を引き継げるように server 側の値を利用
            warmup_walks=getattr(self.server, "warmup_walks", 0),
            hot_min_visits=getattr(self.server, "hot_min_visits", 2),
        )
        try:
            res = walker.continue_from_state(state)
        except Exception as exc:
            self.send_error(500, f"Error during continue_walk: {exc}")
            return

        self._write_json(res)

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
        warmup_ratio: float = DEFAULT_WARMUP_RATIO,
        hot_min_visits: int = DEFAULT_HOT_MIN_VISITS,
    ) -> None:
        super().__init__((host, port), EdgeAwareHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout
        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}
        self.subgraph_index: Dict[str, Any] = {}
        self.ppr_mode = False
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}

        # 認可・アクセス統計
        self.access_counter = Counter()
        self.authorized_counter = Counter()
        self.authorization_attempt_counter = Counter()
        self.authorization_denied_counter = Counter()
        self.transition_counter = Counter()
        self.auth_time_total = 0.0
        self.auth_calls = 0

        # ★★ ホットサブグラフ用の集約情報をサーバにも持たせておく
        self.group_visit_counter = Counter()
        self.hot_groups: Set[int] = set()
        self.warmup_walks = 0
        self.hot_min_visits = max(1, int(hot_min_visits))
        self.warmup_ratio = max(0.0, float(warmup_ratio))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (node + edge bipartite model, subgraph-based auth with hot-subgraph refinement)."
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
        "--subgraph-file",
        type=str,
        default=None,
        help="Optional JSON file describing node->subgraph mapping.",
    )
    parser.add_argument(
        "--ppr-mode",
        action="store_true",
        help="(reserved) Use restart-style random walk (alpha == restart probability).",
    )
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        default=None,
        help="Optional JSON path mapping target_node -> allowed start nodes.",
    )
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=DEFAULT_WARMUP_RATIO,
        help="Fraction of walks used as warmup before hot subgraph detection.",
    )
    parser.add_argument(
        "--hot-min-visits",
        type=int,
        default=DEFAULT_HOT_MIN_VISITS,
        help="Minimum visits to a subgraph before treating it as hot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise FileNotFoundError(f"Edge list not found: {edge_path}")

    edges = load_edge_list(edge_path)
    shard = GraphShard(edges, server_id=args.server_id, server_count=args.server_count)
    server = GraphShardServer(
        host=args.host,
        port=args.port,
        shard=shard,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
        warmup_ratio=args.warmup_ratio,
        hot_min_visits=args.hot_min_visits,
    )

    if args.auth_file:
        server.auth_table = load_entity_auth_table(Path(args.auth_file))
    if args.subgraph_file:
        server.subgraph_index = load_subgraph_index(Path(args.subgraph_file))
    server.ppr_mode = bool(args.ppr_mode)
    if args.node_to_starts_file:
        server.node_to_starts = load_node_to_starts_table(
            Path(args.node_to_starts_file)
        )

    # dump_access_stats: 終了時に統計を JSON へ書き出す
    def dump_access_stats():
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "authorization_attempts": dict(server.authorization_attempt_counter),
            "authorization_denied": dict(server.authorization_denied_counter),
            "transition": dict(server.transition_counter),
            "auth_time_total": server.auth_time_total,
            "auth_calls": server.auth_calls,
            "group_visits": dict(server.group_visit_counter),
            "hot_groups": sorted(server.hot_groups),
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
