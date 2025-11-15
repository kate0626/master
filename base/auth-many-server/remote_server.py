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

NodeId = Union[int, str]

"""
    python3 base/auth-many-server/remote_server.py --edges ./dataset/Louvain/graph/karate.gr --server-count 2 --server-id 1 --host 10.58.60.6 --port 3000 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --auth-file base/auth-many-server/auth_by_start.json
    python3 base/auth-many-server/remote_server.py --edges ./dataset/Louvain/graph/karate.gr --server-count 2 --server-id 1 --host 10.58.60.6 --port 3000 --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 --auth-file base/auth-many-server/auth_by_start.json
"""


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Walk execution
# ---------------------------------------------------------------------------


def _list_to_tuple(obj):
    if isinstance(obj, list):
        return tuple(_list_to_tuple(x) for x in obj)
    return obj


def serialize_rng_state(rng: random.Random) -> str:
    # getstate() はタプルなので、JSON化のためリストに変換
    return json.dumps(_tuple_to_list(rng.getstate()))


def _tuple_to_list(obj):
    if isinstance(obj, tuple):
        return [_tuple_to_list(x) for x in obj]
    return obj


def deserialize_rng_state(state_json: str):
    try:
        state = json.loads(state_json)
        # JSONから読み込んだリストをタプルに戻す
        return _list_to_tuple(state)
    except Exception:
        return None


## TODO: ここからRW
class PeerWalker:
    def __init__(
        self,
        shard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        auth_table: Optional[Dict[int, Dict[str, Set[str]]]] = None,
        stats_collector: Optional[Any] = None,
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

        # === 認可統計の初期化 ===
        self.access_counter = Counter()  # どのエンティティにアクセスしたか
        self.authorized_counter = Counter()  # 認可を通過したもの
        self.transition_counter = Counter()  # from→to の遷移

    # --- 認可判定 ---
    def _is_allowed_entity(self, start_node: int, entity: Any) -> bool:
        # 認可テーブルからスタートノードを参照して許可できるものとってくる
        entry = self.auth_table.get(start_node)
        if entry is None:
            return False
        # エンティティがノードの場合、もしエンティティに対象のノードが含まれていればTrue
        if isinstance(entity, int):
            return entity in entry.get("n", set())
        # エンティティがエッジの場合
        if isinstance(entity, str):
            return entity in entry.get("e", set())
        return False

    # --- 隣接取得（ノード→エッジ、エッジ→ノード） ---
    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        # 次の隣接ノード、エッジの候補を上げる
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    # --- サーバ間転送 ---
    def _post_continue(self, server_id: int, state: dict) -> dict:
        # 他のサーバに ランダムウォークの続き を委譲するための HTTP POST 処理、server_id に応じて URL を作り、state（現在のウォーク情報）を JSON で送信。
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    # --- メインロジック ---
    def continue_from_state(self, state: dict) -> dict:
        # 現在のサーバのID取得
        current_sid = self.shard.server_id
        # RWが継続の時もあるので、現在のエンティティを取得
        rng = random.Random(state.get("seed"))
        rng_state_json = state.get("rng_state")
        if rng_state_json is not None:
            loaded = deserialize_rng_state(rng_state_json)
            if loaded:
                rng.setstate(loaded)
        # print(state["current_node"])
        current_entity = state["current_node"]
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))
        # 現在位置を訪問済みとして記録
        self._record_entity_visit(current_entity)

        # --- start_node 確定 ---
        start_node = None
        if "start_node" in state:
            try:
                start_node = int(state["start_node"])
            except Exception:
                pass
        elif path:
            try:
                start_node = int(path[0])
            except Exception:
                pass

        # print(
        #     f"[Server {current_sid}] continue_from_state: start={start_node}, current={current_entity}, hops={hops_done}"
        # )

        # --- ランダムウォーク ---
        # 終了確率に達するまで行う
        while hops_done < self.max_hops and rng.random() > alpha:
            hops_done += 1

            # 所有サーバ確認
            owner = self.shard.partitioner.assign_entity(current_entity)
            if owner != current_sid:
                # print(
                #     f"[Server {current_sid}] entity {current_entity} not local (owner={owner}) → delegate"
                # )
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

            # --- 隣接ノードもしくはエッジを取得
            neighbors = self._get_local_neighbors(current_entity)
            # if not neighbors:
            #     print(
            #         f"[Server {current_sid}] {current_entity} has no neighbors → finish"
            #     )
            #     return {
            #         "finished": True,
            #         "path": path,
            #         "servers": servers,
            #         "hops_done": hops_done,
            #     }

            # --- 認可を考慮したランダム選択 ---
            max_retries = len(neighbors)
            next_choice = None
            # === ここから追加: アクセス・認可・遷移カウンタ ===
            for attempt in range(max_retries):
                # 乱数で選ぶ
                candidate = rng.choice(neighbors)
                print("候補", candidate)
                cid = candidate["node_id"]
                print(self._is_allowed_entity(start_node, cid))
                # print(
                #     f"[Server {current_sid}] Attempt {attempt+1}/{max_retries}: candidate={cid}"
                # )
                # ランダムに選んだものが許可されているか
                if self._is_allowed_entity(start_node, cid):
                    # print(f"[Server {current_sid}] Authorized → move to {cid}")
                    next_choice = candidate
                    # 認可成功したエンティティをカウント
                    self._bump_server_counter("authorized_counter", cid)
                    # 遷移関係（from→to）も記録
                    self._bump_server_counter(
                        "transition_counter", f"{current_entity}->{cid}"
                    )
                    break
                else:
                    print(f"[Server {current_sid}] Not authorized → retry")
            # === ここまで追加 ===

            # 認可が失敗しても繰り返すforループ
            for attempt in range(max_retries):
                # 乱数で選ぶ
                candidate = rng.choice(neighbors)
                cid = candidate["node_id"]
                # print(
                #     f"[Server {current_sid}] Attempt {attempt+1}/{max_retries}: candidate={cid}"
                # )
                # ランダムに選んだものが許可されているか
                if self._is_allowed_entity(start_node, cid):
                    # print(f"[Server {current_sid}] Authorized → move to {cid}")
                    next_choice = candidate
                    break
                # 許可されていない場合は指定回数だけ再試行
                else:
                    print(f"[Server {current_sid}] Not authorized → retry")

            if next_choice is None:
                print(
                    f"[Server {current_sid}] All {max_retries} neighbors denied → stop"
                )
                return {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                    "denied": True,
                    "denied_reason": f"no authorized neighbors from {current_entity}",
                }

            # --- 移動処理 ---
            next_entity = next_choice["node_id"]
            next_server = int(next_choice["server_id"])
            # 実際に訪問したエンティティのみ記録
            self._record_entity_visit(next_entity)
            # print(
            #     f"[Server {current_sid}] hop {hops_done}: {current_entity} -> {next_entity} (server {next_server})"
            # )

            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            # サーバ移動が発生したら委譲
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
                # print(
                #     f"[Server {current_sid}] delegating walk to server {next_server} (entity={current_entity})"
                # )
                return self._post_continue(next_server, state_out)

        # --- 終了条件処理 ---
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
                "transition": dict(self.server.transition_counter),
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
        )
        start_ts = time.perf_counter()
        # print(start_ts)
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
        duration = time.perf_counter() - start_ts
        # print(duration)
        payload = {"walks": results, "duration": duration}
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
    ) -> None:
        super().__init__((host, port), EdgeAwareHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout
        # auth_table will be attached by main() if provided
        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}

        # 認可およびアクセスの統計カウンタを初期化
        self.access_counter = Counter()  # 各ノード・エッジへのアクセス回数
        self.authorized_counter = Counter()  # 認可成功したノード・エッジ回数
        self.transition_counter = Counter()  # 遷移 (from→to) のペア頻度
        # === 追加ここまで ===


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
    )

    # load auth table if provided
    auth_table = {}
    if args.auth_file:
        auth_table = load_entity_auth_table(Path(args.auth_file))
        server.auth_table = auth_table

    # print(
    #     f"[Server {server.server_id}] Serving {len(shard.local_entities)} entities (nodes + edges) on {args.host}:{args.port} / {args.server_count} servers"
    # )

    def dump_access_stats():
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "transition": dict(server.transition_counter),
        }
        out_path = Path(f"access_stats_server{server.server_id}.json")
        out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        # print(f"[Server {server.server_id}] Access stats saved to {out_path}")

    atexit.register(dump_access_stats)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
