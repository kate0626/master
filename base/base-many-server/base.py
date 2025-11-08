from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import networkx as nx


"""

目的：
  巨大なグラフを複数サーバに分散し、サーバ間をまたぐランダムウォークを模擬する。

構成：
  - GraphServer：1台のサーバ（シャード）の局所グラフを管理
  - DistributedGraphCluster：サーバ群をまとめて分散グラフを構成
  - RemoteGraphCluster：HTTP経由でリモートサーバと通信して隣接情報を取得
  - DistributedRandomWalker：分散グラフ全体でランダムウォークを実行
  - ModuloPartitioner：ノードIDをサーバに割り当て（node_id % server_count）


前提: コントローラ側は Python 3.9+ と networkx をインストールし、各シャードホストが同じエッジリストにアクセスできる状態にしてください。
各シャードホスト k (0 ≤ k < N): 
python3 base/base-many-server/remote_server.py --server-id k --server-count N --edges /path/to/graph.gr --host 0.0.0.0 --port 90k0 を起動します。
任意で疎通確認: curl http://host_k:90k0/health。
コントローラ: python3 base/base-many-server/base.py --mode remote --servers N --server-endpoints host0:9000 host1:9001 ... --walks 100 --alpha 0.1 --start-node 1 --seed 42 
を実行し、サーバ間をまたぐランダムウォークを集計します。
実際のサーバ環境でシャードプロセスを起動し、上記コントローラコマンドを流して挙動を確認してください。
分散ポリシーを変更したくなった場合は GraphShard の割当て処理を差し替え、必要なら外部パーティション結果を読み込むよう拡張するのがおすすめです。
"""


@dataclass(frozen=True)
class Neighbor:
    """隣接ノードの情報を保持する。
    - node_id: 隣接ノードのID
    - server_id: そのノードを保持するサーバID
    """

    node_id: int
    server_id: int


@dataclass
class WalkResult:
    """1回のランダムウォークの結果を保持。
    - path: 通過したノードのリスト
    - servers: 各ステップで訪れたサーバのリスト
    """

    path: List[int]
    servers: List[int]


class GraphServer:
    """1台のサーバ（シャード）を表し、ローカルの部分グラフを保持する。"""

    def __init__(self, server_id: int) -> None:
        self.server_id = server_id
        self.graph = nx.Graph()
        self._remote_neighbors: Dict[int, Set[Neighbor]] = defaultdict(set)

    def add_node(self, node_id: int) -> None:
        self.graph.add_node(node_id)

    def add_local_edge(self, u: int, v: int) -> None:
        self.graph.add_edge(u, v)

    def add_remote_neighbor(self, source: int, target: int, target_server: int) -> None:
        self._remote_neighbors[source].add(
            Neighbor(node_id=target, server_id=target_server)
        )

    def has_node(self, node_id: int) -> bool:
        return self.graph.has_node(node_id)

    def fetch_neighbors(self, node_id: int) -> List[Neighbor]:
        """Return all local and remote neighbors for the given node."""
        neighbors: List[Neighbor] = [
            Neighbor(n, self.server_id) for n in self.graph.neighbors(node_id)
        ]
        neighbors.extend(self._remote_neighbors.get(node_id, set()))
        return neighbors


class ModuloPartitioner:
    """1台のサーバ（シャード）を表し、ローカルの部分グラフを保持する。"""

    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign(self, node_id: int) -> int:
        return node_id % self.server_count


class DistributedGraphCluster:
    """Coordinates a collection of graph servers and manages cross-server edges."""

    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_count: int,
        partitioner: Optional[ModuloPartitioner] = None,
    ) -> None:
        self.server_count = server_count
        self.partitioner = partitioner or ModuloPartitioner(server_count)
        self.servers: Dict[int, GraphServer] = {
            sid: GraphServer(sid) for sid in range(server_count)
        }
        self.node_to_server: Dict[int, int] = {}
        self._load_nodes(edges)
        self._load_edges(edges)

    def _load_nodes(self, edges: Sequence[Tuple[int, int]]) -> None:
        for node_id in self._enumerate_nodes(edges):
            server_id = self.partitioner.assign(node_id)
            self.node_to_server[node_id] = server_id
            self.servers[server_id].add_node(node_id)

    @staticmethod
    def _enumerate_nodes(edges: Sequence[Tuple[int, int]]) -> Iterable[int]:
        seen: Set[int] = set()
        for u, v in edges:
            if u not in seen:
                seen.add(u)
                yield u
            if v not in seen:
                seen.add(v)
                yield v

    def _load_edges(self, edges: Sequence[Tuple[int, int]]) -> None:
        for u, v in edges:
            server_u = self.node_to_server[u]
            server_v = self.node_to_server[v]
            if server_u == server_v:
                self.servers[server_u].add_local_edge(u, v)
            else:
                self.servers[server_u].add_remote_neighbor(u, v, server_v)
                self.servers[server_v].add_remote_neighbor(v, u, server_u)

    def locate_node(self, node_id: int) -> Optional[int]:
        return self.node_to_server.get(node_id)

    def fetch_neighbors(self, server_id: int, node_id: int) -> List[Neighbor]:
        server = self.servers[server_id]
        return server.fetch_neighbors(node_id)


##TODO: 隣接エッジのつながりを意識できているのか確認の必要あり
class RemoteGraphCluster:
    """HTTP経由で隣接ノード情報を取得するリモート用クラス。
    1. ノードIDから担当サーバIDを特定
    2. そのサーバのエンドポイントにHTTP GETでアクセス
    → 例: http://host0:9000/neighbors?node=5
    3. 返ってきたJSONを Neighbor オブジェクトに変換
    """

    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_count: int,
        endpoints: Sequence[str],
        partitioner: Optional[ModuloPartitioner] = None,
        request_timeout: float = 5.0,
    ) -> None:

        if len(endpoints) != server_count:
            raise ValueError("Number of endpoints must match server_count")
        self.server_count = server_count
        self.partitioner = partitioner or ModuloPartitioner(server_count)
        self.node_to_server: Dict[int, int] = {}
        self.endpoints = [
            (
                endpoint
                if endpoint.startswith(("http://", "https://"))
                else f"http://{endpoint}"
            )
            for endpoint in endpoints
        ]
        self.request_timeout = request_timeout
        self._load_nodes(edges)

    def _load_nodes(self, edges: Sequence[Tuple[int, int]]) -> None:
        for node_id in DistributedGraphCluster._enumerate_nodes(edges):  # type: ignore[attr-defined]
            server_id = self.partitioner.assign(node_id)
            self.node_to_server[node_id] = server_id

    def locate_node(self, node_id: int) -> Optional[int]:
        return self.node_to_server.get(node_id)

    def fetch_neighbors(self, server_id: int, node_id: int) -> List[Neighbor]:
        if server_id < 0 or server_id >= self.server_count:
            raise ValueError(f"Server id {server_id} is out of range")

        endpoint = self.endpoints[server_id]
        query = urllib_parse.urlencode({"node": node_id})
        url = f"{endpoint.rstrip('/')}/neighbors?{query}"

        try:
            with urllib_request.urlopen(url, timeout=self.request_timeout) as response:
                payload = response.read()
        except urllib_error.URLError as exc:
            raise ConnectionError(
                f"Failed to contact server {server_id} at {endpoint}: {exc}"
            ) from exc

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON from server {server_id}: {payload!r}"
            ) from exc

        if "neighbors" not in data:
            raise KeyError(f"Malformed response from server {server_id}: {data!r}")

        return [
            Neighbor(node_id=item["node_id"], server_id=item["server_id"])
            for item in data["neighbors"]
        ]


class DistributedRandomWalker:
    """分散グラフ全体でランダムウォークを行う。"""

    """
    アルゴリズム（walkメソッド）：

        開始ノードの属するサーバを特定

        以下を繰り返す：

        現在ノードの隣接ノードを取得

        乱数 r = random() を生成

        r > alpha なら次のノードに移動

        r <= alpha なら停止

        結果として WalkResult(path, servers) を返す。

        式的説明：

        alpha = 停止確率（再スタート確率）
        → 1 - alpha の確率で次ノードへ遷移。
        （Personalized PageRank と同じ確率モデル）

        サーバをまたぎながらノードを選び続ける分散ランダムウォーク。
    """

    def __init__(
        self,
        cluster: DistributedGraphCluster,
        alpha: float,
        rng: Optional[random.Random] = None,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in the open interval (0, 1)")
        self.cluster = cluster
        self.alpha = alpha
        self.rng = rng or random.Random()

    def walk(self, start_node: int) -> WalkResult:
        # スタートノードのあるサーバを特定
        start_server = self.cluster.locate_node(start_node)
        if start_server is None:
            raise ValueError(
                f"Start node {start_node} is not present in the distributed graph"
            )

        current_node = start_node
        current_server = start_server
        path: List[int] = [current_node]
        servers: List[int] = [current_server]
        # 終了確率に達するまで繰り返す
        while self.rng.random() > self.alpha:
            neighbors = self.cluster.fetch_neighbors(current_server, current_node)
            if not neighbors:
                break
            # 隣接ノードの中からランダムに選ぶ
            # self.rng は random.Random のインスタンスであり、その .choice(seq) は与えたシーケンス seq の要素を一様（均等）確率でランダムに1つ選ぶメソッド
            next_neighbor = self.rng.choice(neighbors)
            path.append(next_neighbor.node_id)
            servers.append(next_neighbor.server_id)
            current_node = next_neighbor.node_id
            current_server = next_neighbor.server_id

        return WalkResult(path=path, servers=servers)


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    """エッジリストファイル（u v形式）を読み込む。"""
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


def resolve_edge_path(edge_arg: str) -> Path:
    """ファイルパスをカレントディレクトリやスクリプト基準で解決する。"""
    candidate = Path(edge_arg).expanduser()

    if candidate.is_absolute() and candidate.exists():
        return candidate

    search_paths = []
    if not candidate.is_absolute():
        search_paths.append((Path.cwd() / candidate).resolve())
        search_paths.append((Path(__file__).resolve().parent / candidate).resolve())

    for path in search_paths:
        if path.exists():
            return path

    # Fall back to the first attempted path to surface a helpful error message later.
    return search_paths[0] if search_paths else candidate


def run_simulation(
    edges_file: Path,
    server_count: int,
    alpha: float,
    start_node: int,
    walk_iterations: int,
    seed: Optional[int],
    mode: str,
    endpoints: Optional[List[str]],
    request_timeout: float,
) -> None:
    edges = load_edge_list(edges_file)
    partitioner = ModuloPartitioner(server_count=server_count)

    if mode == "local":
        cluster = DistributedGraphCluster(
            edges, server_count=server_count, partitioner=partitioner
        )
    elif mode == "remote":
        if not endpoints:
            raise ValueError("Remote mode requires --server-endpoints to be specified")
        cluster = RemoteGraphCluster(
            edges,
            server_count=server_count,
            endpoints=endpoints,
            partitioner=partitioner,
            request_timeout=request_timeout,
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    walker = DistributedRandomWalker(cluster, alpha=alpha, rng=random.Random(seed))

    total_length = 0
    server_visits: Dict[int, int] = defaultdict(int)
    start_time = time.perf_counter()

    for _ in range(walk_iterations):
        result = walker.walk(start_node)
        total_length += len(result.path)
        for server_id in result.servers:
            server_visits[server_id] += 1

    elapsed = time.perf_counter() - start_time
    average_length = total_length / walk_iterations

    print(f"Average walk length: {average_length:.3f}")
    print(f"Total steps taken: {total_length}")
    print(f"Completed in: {elapsed:.6f}s")
    print("Server visit counts:")
    for server_id in range(server_count):
        count = server_visits.get(server_id, 0)
        print(f"  Server {server_id}: {count}")


def parse_arguments() -> argparse.Namespace:
    """コマンドライン引数を解析し、実行パラメータを取得する。"""
    parser = argparse.ArgumentParser(description="Distributed random walk simulator.")
    parser.add_argument(
        "--edges",
        default="./../../dataset/Louvain/graph/karate.gr",
        type=str,
        help="Path to the edge list file.",
    )
    parser.add_argument(
        "--servers",
        default=3,
        type=int,
        help="Number of servers to partition the graph across.",
    )
    parser.add_argument(
        "--alpha",
        default=0.1,
        type=float,
        help="Stopping probability for the random walk (0 < alpha < 1).",
    )
    parser.add_argument(
        "--start-node",
        default=1,
        type=int,
        help="Node id to start each random walk from.",
    )
    parser.add_argument(
        "--walks",
        default=100,
        type=int,
        help="Number of random walks to simulate.",
    )
    parser.add_argument(
        "--seed",
        default=None,
        type=int,
        help="Optional random seed for reproducibility.",
    )
    parser.add_argument(
        "--mode",
        choices=("local", "remote"),
        default="local",
        help="Execution mode. Use 'remote' when graph shards run on external servers.",
    )
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        help="Endpoints for remote graph servers in order (e.g., host1:8000 host2:8001). Required in remote mode.",
    )
    parser.add_argument(
        "--request-timeout",
        default=5.0,
        type=float,
        help="HTTP request timeout in seconds when contacting remote servers.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    run_simulation(
        edges_file=resolve_edge_path(args.edges),
        server_count=args.servers,
        alpha=args.alpha,
        start_node=args.start_node,
        walk_iterations=args.walks,
        seed=args.seed,
        mode=args.mode,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
    )


if __name__ == "__main__":
    main()
