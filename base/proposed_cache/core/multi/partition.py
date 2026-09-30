#!/usr/bin/env python3
# =============================================================================
# partition.py — ノードとエッジを *別のサーバ群* に置くパーティショナ（任意 K 台）
#
#   二部グラフとして扱う: エッジ "edge_u_v" もノードと対等な1エンティティ。
#   そのうえで所有を型で分ける。
#
#       server id  0 .. Kn-1        → ノードのみ所有
#       server id  Kn .. Kn+Ke-1    → エッジのみ所有
#       合計 K = Kn + Ke            （任意）
#
#   既存 ModuloPartitioner は node%K / edgehash%K で両方を全 K 台に散らすので、
#   ノード専用/エッジ専用サーバは作れない。既存
#   base/auth-many-server/only_split_auth_tables.py の NodeEdgeFixedPartitioner は
#   「ノード→server0 / エッジ→server1」の2台固定だったので、それを任意 Kn/Ke に
#   一般化したのがこのファイル。
#
#   エッジのハッシュは既存 ModuloPartitioner.assign_edge と同じ
#   (min*1_000_003 + max) を使う。エッジの散らばり方が既存実験と揃うため。
# =============================================================================
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

EntityId = Union[int, str]

_EDGE_RE = re.compile(r"^edge_(\d+)_(\d+)$")

NODE_ROLE = "node"
EDGE_ROLE = "edge"


def is_edge_id(entity_id: Any) -> bool:
    return isinstance(entity_id, str) and entity_id.startswith("edge_")


def parse_edge_id(entity_id: str) -> Tuple[int, int]:
    m = _EDGE_RE.match(entity_id)
    if not m:
        raise ValueError(f"Malformed edge id: {entity_id!r}")
    return int(m.group(1)), int(m.group(2))


# =============================================================================
class NodeEdgeSplitPartitioner:
    """ノードは 0..Kn-1、エッジは Kn..Kn+Ke-1 に割り当てる。

    既存コードが要求するインタフェースは assign_entity だけ
    （GraphShard.owner_of / PeerWalker / continue_from_state がこれを呼ぶ）。
    対称性のため assign_node / assign_edge も提供する。
    """

    def __init__(self, node_servers: int, edge_servers: int) -> None:
        if node_servers <= 0:
            raise ValueError("node_servers must be positive")
        if edge_servers <= 0:
            raise ValueError("edge_servers must be positive")
        self.node_servers = int(node_servers)
        self.edge_servers = int(edge_servers)
        self.server_count = self.node_servers + self.edge_servers

    # --- 割り当て ---------------------------------------------------------
    def assign_node(self, node_id: int) -> int:
        return int(node_id) % self.node_servers

    def assign_edge(self, u: int, v: int) -> int:
        a, b = sorted((int(u), int(v)))
        # 既存 ModuloPartitioner.assign_edge と同じハッシュ
        return self.node_servers + ((a * 1_000_003 + b) % self.edge_servers)

    def assign_entity(self, entity_id: EntityId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str):
            if is_edge_id(entity_id):
                u, v = parse_edge_id(entity_id)
                return self.assign_edge(u, v)
            if entity_id.isdigit():
                return self.assign_node(int(entity_id))
        raise TypeError(f"Unsupported entity id: {entity_id!r}")

    # --- 役割 -------------------------------------------------------------
    def role_of_server(self, server_id: int) -> str:
        if not 0 <= server_id < self.server_count:
            raise ValueError(
                f"server_id must satisfy 0 <= id < {self.server_count}, got {server_id}"
            )
        return NODE_ROLE if server_id < self.node_servers else EDGE_ROLE

    def servers_with_role(self, role: str) -> List[int]:
        if role == NODE_ROLE:
            return list(range(self.node_servers))
        if role == EDGE_ROLE:
            return list(range(self.node_servers, self.server_count))
        raise ValueError(f"unknown role: {role!r}")

    def __repr__(self) -> str:
        return (f"NodeEdgeSplitPartitioner(node_servers={self.node_servers}, "
                f"edge_servers={self.edge_servers}, K={self.server_count})")


# =============================================================================
# servers.conf (role 列つき) の読み込み
# =============================================================================
class ServerEntry(NamedTuple):
    host: str
    sid: int
    ip: str
    port: int
    role: str

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{self.port}"


def parse_servers_conf(path: Path) -> List[ServerEntry]:
    """`host id ip port role` を1行1台で読む。'#' 以降と空行は無視。

    検証（ここで落とすことで、実行してから気づくのを防ぐ）:
      - id が 0 から K-1 の連番であること
        （owner_map は node_to_starts_server{id}.json を glob して作るため欠番不可）
      - role が node / edge のいずれかであること
      - **ノード役が先、エッジ役が後**の並びであること
        （NodeEdgeSplitPartitioner は id < Kn をノード役とみなす）
    """
    entries: List[ServerEntry] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            raise ValueError(
                f"servers.conf の行は 'host id ip port role' の5カラムが必要です: {raw!r}"
            )
        host, sid, ip, port, role = parts[0], parts[1], parts[2], parts[3], parts[4]
        role = role.lower()
        if role not in (NODE_ROLE, EDGE_ROLE):
            raise ValueError(f"role は node / edge のいずれか: {raw!r}")
        entries.append(ServerEntry(host, int(sid), ip, int(port), role))

    if not entries:
        raise ValueError(f"servers.conf からサーバを読めません: {path}")

    entries.sort(key=lambda e: e.sid)
    ids = [e.sid for e in entries]
    if ids != list(range(len(entries))):
        raise ValueError(
            f"server id は 0 から連番でなければなりません（欠番・重複不可）: {ids}"
        )

    roles = [e.role for e in entries]
    kn = roles.count(NODE_ROLE)
    if roles != [NODE_ROLE] * kn + [EDGE_ROLE] * (len(roles) - kn):
        raise ValueError(
            "role の並びは「node 役をすべて先、edge 役をすべて後」にしてください。"
            f"現在: {roles}"
        )
    if kn == 0 or kn == len(roles):
        raise ValueError("node 役と edge 役をそれぞれ1台以上置いてください。")
    return entries


def counts_from_entries(entries: List[ServerEntry]) -> Tuple[int, int]:
    """(Kn, Ke) を返す。"""
    kn = sum(1 for e in entries if e.role == NODE_ROLE)
    return kn, len(entries) - kn


def partitioner_from_conf(path: Path) -> Tuple[NodeEdgeSplitPartitioner, List[ServerEntry]]:
    entries = parse_servers_conf(path)
    kn, ke = counts_from_entries(entries)
    return NodeEdgeSplitPartitioner(kn, ke), entries


# =============================================================================
# 自己検証 (python3 partition.py で走る)
# =============================================================================
def _selftest() -> None:
    p = NodeEdgeSplitPartitioner(node_servers=2, edge_servers=3)
    assert p.server_count == 5
    # ノードは 0..1
    for n in range(50):
        assert p.assign_entity(n) in (0, 1), n
    # エッジは 2..4
    for u in range(10):
        for v in range(u + 1, 10):
            sid = p.assign_entity(f"edge_{u}_{v}")
            assert sid in (2, 3, 4), (u, v, sid)
    # 役割
    assert p.role_of_server(0) == NODE_ROLE
    assert p.role_of_server(4) == EDGE_ROLE
    assert p.servers_with_role(NODE_ROLE) == [0, 1]
    assert p.servers_with_role(EDGE_ROLE) == [2, 3, 4]
    # エッジのハッシュが既存 ModuloPartitioner と同じ規則か
    a, b = 3, 7
    assert p.assign_edge(a, b) - p.node_servers == (a * 1_000_003 + b) % p.edge_servers
    # 端点の順序に依存しない
    assert p.assign_entity("edge_7_3") == p.assign_entity("edge_3_7")
    print("partition.py selftest OK  ", p)


if __name__ == "__main__":
    _selftest()
