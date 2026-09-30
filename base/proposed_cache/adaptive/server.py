#!/usr/bin/env python3
# =============================================================================
# server.py — 学習キャッシュ（learned_cache.py）を載せた実サーバ
#
#   ★ サーバ間の流れは既存と一切同じ。
#     /walk → /continue_walk で state(+rng_state) をバケツリレー →
#     /authorize はミスかつ他サーバ所有のときだけ。
#
#   既存 core/split_remote_server_reuse_score.py を import して3点だけ足す:
#     1. cache_extensions に "learned" を登録（--cache-policy learned で選べる）
#     2. PeerWalker._authorize_candidate を包み、キャッシュ参照の直前に
#        cache.set_context(key, hop, deg) を渡す（学習キャッシュ以外は素通り）
#     3. GET /learner_stats を追加（ヒット数・学習した重みだけの軽い統計。
#        /access_stats はアクセス表まで返すので、バッチごとに叩くには重い）
#
#   キャッシュはサーバ常駐なので、コントローラが /cache/reset を呼ばない限り
#   学習した重みは /walk をまたいで引き継がれる（＝walk を重ねるほど学習が進む）。
#
#   使い方: run_local.sh / run_cluster.sh から起動する。単体なら
#     python3 base/proposed_cache/adaptive/server.py --edges ... --server-id 0 \
#       --server-count 2 --server-endpoints 127.0.0.1:4100 127.0.0.1:4101 \
#       --port 4100 --cache-policy learned --cache-capacity 100 --learn-window 1000
# =============================================================================
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(os.path.dirname(_THIS_DIR), "core")
_MULTI = os.path.join(_CORE, "multi")
for _p in (_THIS_DIR, _CORE, _MULTI):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cache_extensions                                # noqa: E402
import partition as part                               # noqa: E402  (core/multi: ノード/エッジ分離配置)
import split_remote_server_reuse_score as srv          # noqa: E402
from learned_cache import LearnerConfig, LearnedLinearCache  # noqa: E402
from structured_cache import StructuredLearnedCache, StructuredSplitCache
import baselines                                               # noqa: E402  (関連研究の比較手法)            # noqa: E402

LEARNED_POLICIES = {"learned": LearnedLinearCache, "structured": StructuredLearnedCache,
                    "structured_split": StructuredSplitCache}


# =============================================================================
# 1. 学習キャッシュの登録
# =============================================================================
_CFG = LearnerConfig()


def _make_factory(cls):
    def factory(capacity, sizer):
        # /cache/reset のたびに作り直される = 重みも初期値に戻る
        return cls(capacity, sizer or srv.DecisionCacheSizer(), _CFG)
    return factory


# =============================================================================
# 2. hop と次数をキャッシュに渡す
# =============================================================================
def entity_degree(neighbor_map, entity: Any) -> float:
    """ノード = 次数、エッジ = 両端点の次数の平均（仮想サーバ adaptive/sim.py と同じ定義）。

    neighbor_map は全体グラフから構築されている（GraphShard の仕様）ので、
    他サーバ所有のエンティティでも引ける。
    """
    nbrs = neighbor_map.get(entity, ())
    if isinstance(entity, str) and entity.startswith("edge_"):
        if not nbrs:
            return 1.0
        return sum(len(neighbor_map.get(n.node_id, ())) or 1 for n in nbrs) / len(nbrs)
    return float(len(nbrs) or 1)


_ORIG_AUTHORIZE = srv.PeerWalker._authorize_candidate


def _authorize_with_context(self, start_node, candidate, hops_done=0, alpha=0.0,
                            walk_id=None):
    cache = getattr(self.server, "authz_cache", None) if self.server else None
    if isinstance(cache, LearnedLinearCache) and start_node is not None:
        target = candidate["node_id"]
        node_only = getattr(self.server, "cache_key_mode", "full") == "node_only"
        ckey = (int(start_node), srv.cache_entity_key(target, node_only=node_only))
        cache.set_context(ckey, hops_done, entity_degree(self.shard.neighbor_map, target))
    return _ORIG_AUTHORIZE(self, start_node, candidate, hops_done, alpha, walk_id)


# =============================================================================
# 4. ノード/エッジ分離配置（--node-servers Kn --edge-servers Ke）
#    server id 0..Kn-1 がノードのみ、Kn..K-1 がエッジのみを所有する（core/multi と同じ）。
#    本家 main() は ModuloPartitioner(server_count) を作るので、そのクラスを差し替える。
#    ※ キャッシュに入るのは「認可を問い合わせた候補」= 現在地の隣 = 反対の型なので、
#      ノード担当サーバのキャッシュにはエッジ、エッジ担当サーバにはノードだけが入る。
# =============================================================================
_SPLIT: Dict[str, Optional[int]] = {"kn": None, "ke": None}


class _SplitPartitionerShim(part.NodeEdgeSplitPartitioner):
    def __init__(self, server_count: int) -> None:
        kn, ke = _SPLIT["kn"], _SPLIT["ke"]
        if kn + ke != server_count:
            raise ValueError(f"--server-count ({server_count}) と Kn+Ke ({kn}+{ke}) が一致しません")
        super().__init__(kn, ke)


def install_split(kn: int, ke: int) -> None:
    _SPLIT.update(kn=int(kn), ke=int(ke))
    srv.ModuloPartitioner = _SplitPartitionerShim


# =============================================================================
# 3. GET /learner_stats
# =============================================================================
_ORIG_DO_GET = srv.EdgeAwareHandler.do_GET


def learner_stats(server, with_history: bool) -> Dict[str, Any]:
    cache = server.authz_cache
    out: Dict[str, Any] = {
        "server_id": server.server_id,
        "policy": getattr(server, "auth_cache_policy", "none"),
        "auth_cache_hit": server.auth_cache_hit,
        "auth_cache_miss": server.auth_cache_miss,
        "hit_node": getattr(server, "hit_node", 0),
        "miss_node": getattr(server, "miss_node", 0),
        "hit_edge": getattr(server, "hit_edge", 0),
        "miss_edge": getattr(server, "miss_edge", 0),
        "local_auth_calls": getattr(server, "local_auth_calls", 0),
        "remote_auth_calls": getattr(server, "remote_auth_calls", 0),
        "auth_time_total": getattr(server, "auth_time_total", 0.0),
        "walk_time_total": getattr(server, "walk_time_total", 0.0),
        "walk_calls": getattr(server, "walk_calls", 0),
        "cache_entries": len(cache),
        "placement": getattr(server, "_adaptive_placement", "modulo"),
    }
    if isinstance(cache, LearnedLinearCache):
        out["learner"] = cache.stats()
        out["learner"]["config"] = asdict(cache.cfg)
        if with_history:
            out["learner"]["history"] = cache.history
    return out


def _do_get(self) -> None:
    parsed = urlparse(self.path)
    if parsed.path == "/learner_stats":
        q = parse_qs(parsed.query)
        self._write_json(learner_stats(self.server, q.get("history", ["0"])[0] == "1"))
        return
    _ORIG_DO_GET(self)


# =============================================================================
# CLI → 本家 main()
# =============================================================================
def _split_own_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--learn-no-learn", action="store_true", help="重みを初期値で固定する")
    p.add_argument("--learn-init", default="auto",
                   help='auto（learned=lru / structured=prior）/ lru / zero / prior / 重みを直接 "b,..."')
    p.add_argument("--learn-forget", type=float, default=0.999)
    p.add_argument("--learn-p0", type=float, default=1.0)
    p.add_argument("--learn-window", type=int, default=1000)
    p.add_argument("--learn-sample-k", type=int, default=8)
    p.add_argument("--learn-miss-weight", choices=["rel_freq", "none"], default="rel_freq")
    p.add_argument("--learn-hop-agg", choices=["min", "last"], default="min")
    p.add_argument("--learn-z-clip", type=float, default=3.0)
    p.add_argument("--learn-c-max", type=float, default=10.0)
    p.add_argument("--learn-decay-halflife", type=float, default=1000.0,
                   help="freq-scope edc の半減期（キャッシュ時計）")
    p.add_argument("--learn-pair-lr", type=float, default=0.05,
                   help="target pairwise の学習率（AdaGrad）")
    p.add_argument("--learn-shared-model", action="store_true", help="node / edge で重みを共有する")
    p.add_argument("--learn-features", default="H,O,D,R", help="使う特徴量（外したものは z=0）")
    p.add_argument("--learn-target", choices=["dist", "reuse", "pairwise"], default="dist",
                   help="dist: 次回アクセス距離の log を回帰 / reuse: 滞在時間内に再利用されるかの 0/1")
    p.add_argument("--learn-log-features", default="",
                   help='log1p してから標準化する特徴量をカンマ区切りで（例 "O" / "O,R"）。空 = 素の値')
    p.add_argument("--learn-freq-scope", choices=["cache", "total", "edc"], default="cache",
                   help="O の数え方: cache=キャッシュに入ってから / total=このサーバでの全アクセス回数")
    p.add_argument("--learn-seed", type=int, default=0)
    p.add_argument("--learn-trace-every", type=int, default=200)
    p.add_argument("--node-servers", type=int, default=None,
                   help="ノード/エッジ分離配置: ノード担当の台数 Kn（--edge-servers と一緒に指定）")
    p.add_argument("--edge-servers", type=int, default=None,
                   help="ノード/エッジ分離配置: エッジ担当の台数 Ke")
    own, rest = p.parse_known_args(argv)
    sid = argparse.ArgumentParser(add_help=False)
    sid.add_argument("--server-id", type=int, default=0)
    server_id = sid.parse_known_args(rest)[0].server_id
    return own, rest, server_id


def install(cfg: LearnerConfig) -> None:
    global _CFG
    _CFG = cfg
    for name, cls in LEARNED_POLICIES.items():
        cache_extensions.register(name, _make_factory(cls))
    for name, factory in baselines.FACTORIES.items():      # lfu / lecar / cacheus
        cache_extensions.register(name, lambda cap, sizer, f=factory: f(cap, sizer or srv.DecisionCacheSizer()))
    srv.PeerWalker._authorize_candidate = _authorize_with_context
    srv.EdgeAwareHandler.do_GET = _do_get


def main() -> None:
    own, rest, server_id = _split_own_args(sys.argv[1:])
    sys.argv = [sys.argv[0]] + rest
    cfg = LearnerConfig(
        learn=not own.learn_no_learn, init=own.learn_init, forget=own.learn_forget,
        p0=own.learn_p0, window=own.learn_window, sample_k=own.learn_sample_k,
        miss_weight=own.learn_miss_weight, hop_agg=own.learn_hop_agg,
        z_clip=own.learn_z_clip, c_max=own.learn_c_max, freq_scope=own.learn_freq_scope, decay_halflife=own.learn_decay_halflife, pair_lr=own.learn_pair_lr, shared_model=own.learn_shared_model, features=own.learn_features, target=own.learn_target, log_features=own.learn_log_features,
        trace_every=own.learn_trace_every,
        seed=own.learn_seed * 1000 + server_id,   # サーバごとに候補抽出の乱数を分ける
    )
    install(cfg)
    placement = "modulo"
    if own.node_servers is not None or own.edge_servers is not None:
        if own.node_servers is None or own.edge_servers is None:
            raise SystemExit("--node-servers と --edge-servers は両方指定してください")
        install_split(own.node_servers, own.edge_servers)
        role = "node" if server_id < own.node_servers else "edge"
        placement = f"split(Kn={own.node_servers},Ke={own.edge_servers})"
        print(f"[adaptive] server {server_id}: 役割={role}（所有={role}、キャッシュに入るのは"
              f"{'エッジ' if role == 'node' else 'ノード'}）")
    _ORIG_INIT = srv.GraphShardServer.__init__

    def _init(self, *a, **k):
        _ORIG_INIT(self, *a, **k)
        self._adaptive_placement = placement
    srv.GraphShardServer.__init__ = _init
    print(f"[adaptive] server {server_id}: placement={placement} learner {asdict(cfg)}")
    srv.main()


if __name__ == "__main__":
    main()
