#!/usr/bin/env python3
# =============================================================================
# server.py — ノード/エッジを別サーバ群に置く RW サーバ（任意 K = Kn + Ke 台）
#
#   ★ サーバ間の流れは既存と一切同じです。
#     /walk 1回 → /continue_walk で state(+rng_state) をバケツリレー →
#     /authorize は「キャッシュミス かつ 対象が他サーバ所有」のときだけ。
#     歩行ロジック (continue_from_state / _select_next_neighbor)、ハンドオフ、
#     認可セマンティクス（node_to_starts = 拒否リスト）には手を入れていません。
#
#   既存 core/split_remote_server_reuse_score.py を import して、
#   **2箇所だけ**差し替えます。
#
#     1. パーティショナ → multi/partition.py  (ノードは 0..Kn-1、エッジは Kn..K-1)
#     2. スコア式       → multi/score.py      (V = O^θ · H^β · D^γ、式はそこだけ)
#
#   使い方（単体）:
#     python3 base/proposed_cache/core/multi/server.py \
#       --edges dataset/Louvain/graph/karate.gr \
#       --node-servers 2 --edge-servers 3 \
#       --server-id 0 --host 127.0.0.1 --port 4300 \
#       --server-endpoints 127.0.0.1:4300 ... (K個) \
#       --cache-policy reuse_score --cache-capacity 100 --score-w 100
#
#   通常は run_local.sh / run_cluster.sh から起動します。
# =============================================================================
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Optional, Tuple

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.dirname(_THIS_DIR)                 # base/proposed_cache/core
for _p in (_THIS_DIR, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import partition as part                              # noqa: E402
import score as sc                                    # noqa: E402
import split_remote_server_reuse_score as srv         # noqa: E402


# =============================================================================
# 1. パーティショナの差し替え
# =============================================================================
# 本家 main() は `ModuloPartitioner(args.server_count)` を作り、
#   - --node-to-starts-file なし → それをそのまま使う
#   - あり                       → StaticPartitioner(fallback=それ) に包む
# という2経路。ModuloPartitioner を置き換えれば両経路をまとめて押さえられる。
_SPLIT_COUNTS: dict = {"kn": None, "ke": None}


class _SplitPartitionerShim(part.NodeEdgeSplitPartitioner):
    """本家が `ModuloPartitioner(server_count)` として作るのを乗っ取る。"""

    def __init__(self, server_count: int) -> None:
        kn, ke = _SPLIT_COUNTS["kn"], _SPLIT_COUNTS["ke"]
        if kn is None or ke is None:
            raise RuntimeError("node/edge のサーバ数が設定されていません")
        if kn + ke != server_count:
            raise ValueError(
                f"--server-count ({server_count}) が Kn+Ke ({kn}+{ke}={kn + ke}) と一致しません"
            )
        super().__init__(kn, ke)


# =============================================================================
# 2. スコア式の差し替え
# =============================================================================
_PARAMS = sc.ScoreParams()


def _norm_of(server) -> sc.RunningNorm:
    """サーバ1台につき1つの Welford 統計。"""
    n = getattr(server, "_score_norm", None)
    if n is None:
        n = sc.RunningNorm()
        setattr(server, "_score_norm", n)
    return n


def _hop_prior(self, target: Any, hops_done: int, alpha: float) -> float:
    """PeerWalker._hop_prior の差し替え。構造項 H^β·D^γ を score.py で計算する。

    ⚠️ ここで扱う target は「現在地の隣接候補」= 現在地と **反対の型**。
       型分離配置では、ノードを所有するサーバがエッジをキャッシュすることになる。
       次数を使うかはサーバの役割ではなく target の型で決める（score.py 参照）。
    """
    is_edge = part.is_edge_id(target)
    nm = getattr(self.shard, "neighbor_map", None)
    if nm is not None:
        deg = len(nm.get(target, ())) or 1
    else:
        deg = len(self._get_local_neighbors(target)) or 1
    return sc.structural_prior(hops_done, deg, _norm_of(self.server), _PARAMS, is_edge)


def _cache_score(self, key: Tuple[int, str]) -> float:
    """PPRBoundedCache._score の差し替え。V = O^θ · prior。"""
    if self.combine != "reuse_score":
        return _ORIG_SCORE(self, key)
    freq = self.freq.get(key, 1)
    prior = self._prior(key)
    return sc.value(freq, prior, _PARAMS)


def _cache_h_value(self, key: Tuple[int, str], size: int) -> float:
    """PPRBoundedCache._h_value の差し替え。H = last_access + w·V（/size しない）。"""
    if self.combine != "reuse_score":
        return _ORIG_H_VALUE(self, key, size)
    freq = self.freq.get(key, 1)
    prior = self._prior(key)
    last = self.last_access.get(key, self.clock)
    return sc.priority(last, freq, prior, _PARAMS)


_ORIG_SCORE = srv.PPRBoundedCache._score
_ORIG_H_VALUE = srv.PPRBoundedCache._h_value


def install(kn: int, ke: int, params: sc.ScoreParams) -> None:
    """パーティショナとスコアを差し替える。sim.py からも呼ぶ。"""
    global _PARAMS
    _PARAMS = params
    _SPLIT_COUNTS["kn"] = int(kn)
    _SPLIT_COUNTS["ke"] = int(ke)
    srv.ModuloPartitioner = _SplitPartitionerShim
    srv.PeerWalker._hop_prior = _hop_prior
    srv.PPRBoundedCache._score = _cache_score
    srv.PPRBoundedCache._h_value = _cache_h_value


# =============================================================================
# 3. CLI → 本家 main()
# =============================================================================
def _split_own_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--node-servers", type=int, required=True,
                   help="ノードを所有するサーバ数 Kn (server id 0..Kn-1)")
    p.add_argument("--edge-servers", type=int, required=True,
                   help="エッジを所有するサーバ数 Ke (server id Kn..Kn+Ke-1)")
    p.add_argument("--score-w", type=float, default=100.0, help="重み w。0 で純 LRU")
    p.add_argument("--score-theta", type=float, default=1.0, help="θ: 観測項 O の指数")
    p.add_argument("--score-beta", type=float, default=1.0, help="β: 距離項 H の指数")
    p.add_argument("--score-gamma", type=float, default=1.0, help="γ: 次数項 D の指数")
    p.add_argument("--score-cf", type=float, default=5.0, help="C_F: 頻度の飽和点")
    p.add_argument("--edge-use-degree", action="store_true",
                   help="エッジにも次数項を与える（アブレーション用。既定 off）")
    return p.parse_known_args(argv)


def main() -> None:
    own, rest = _split_own_args(sys.argv[1:])
    sys.argv = [sys.argv[0]] + rest

    params = sc.ScoreParams(
        w=own.score_w, theta=own.score_theta, beta=own.score_beta,
        gamma=own.score_gamma, cf=own.score_cf,
        edge_use_degree=own.edge_use_degree,
    )
    install(own.node_servers, own.edge_servers, params)

    k = own.node_servers + own.edge_servers
    partitioner = part.NodeEdgeSplitPartitioner(own.node_servers, own.edge_servers)
    print(f"[multi] {partitioner}")
    print(f"[multi] node役 = server {partitioner.servers_with_role(part.NODE_ROLE)} / "
          f"edge役 = server {partitioner.servers_with_role(part.EDGE_ROLE)}")
    print(f"[multi] {params.describe()}")
    print(f"[multi] 合計 K = {k}")

    srv.main()


if __name__ == "__main__":
    main()
