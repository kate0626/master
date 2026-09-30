#!/usr/bin/env python3
# =============================================================================
# sim.py — 学習キャッシュ（adaptive/learned_cache.py）を仮想サーバで回す
#
#   既存の core/virtual_server_sim_reuse_score.py を import して使う（既存は無変更）。
#   差し込みは2点だけ:
#     1. cache_extensions に "learned" を登録（build_authz_cache から生成される）
#     2. vs.authorize_candidate を包み、アクセス直前に cache.set_context(key, hop, deg)
#        を渡す（学習キャッシュ以外は素通り＝既存方策の結果は変わらない）
#
#   使い方（リポジトリルートから）:
#     python3 base/proposed_cache/adaptive/sim.py \
#       --edges dataset/Louvain/graph/vldb.gr --alpha 0.1 --walks 1000 \
#       --n-seeds 3 --disjoint --policies lru arc reuse_score learned
#
#   正しさの確認（学習停止 + 初期重み LRU が LRU と厳密一致するか）:
#     python3 base/proposed_cache/adaptive/sim.py --edges ... --selfcheck
# =============================================================================
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics as st
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(os.path.dirname(_THIS_DIR), "core")
for _p in (_THIS_DIR, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cache_extensions                                # noqa: E402
import virtual_server_sim_reuse_score as vs            # noqa: E402
from learned_cache import LearnerConfig, LearnedLinearCache  # noqa: E402
from structured_cache import StructuredLearnedCache, StructuredSplitCache            # noqa: E402

# 学習キャッシュの方策名 → クラス
LEARNED_POLICIES = {"learned": LearnedLinearCache, "structured": StructuredLearnedCache,
                    "structured_split": StructuredSplitCache}


# =============================================================================
# 差し込み
# =============================================================================
_CFG = LearnerConfig()
_INSTANCES = 0


def _make_factory(cls):
    def factory(capacity, sizer):
        # build_servers はサーバを sid 順に作るので、install 後の生成順 = sid。
        # 実サーバ（adaptive/server.py）と同じく seed = learn_seed*1000 + sid にする。
        global _INSTANCES
        cfg = LearnerConfig(**{**_CFG.__dict__, "seed": _CFG.seed * 1000 + _INSTANCES})
        _INSTANCES += 1
        return cls(capacity, sizer or vs.DecisionCacheSizer(), cfg)
    return factory


def entity_degree(cluster, entity) -> float:
    """ノード = 次数、エッジ = 両端点の次数の平均（cluster.degree は edge に常に 2 を返すため）。"""
    if isinstance(entity, str) and entity.startswith("edge_"):
        ends = cluster.neighbor_map.get(entity, ())
        if not ends:
            return 1.0
        return sum(cluster.degree(u) for u in ends) / len(ends)
    return float(cluster.degree(entity))


_orig_authorize = vs.authorize_candidate


def _authorize_with_context(cluster, server, start_node, target, hops_done, node_only):
    cache = server.cache
    if isinstance(cache, LearnedLinearCache):
        ckey = (int(start_node), vs.cache_entity_key(target, node_only=node_only))
        cache.set_context(ckey, hops_done, entity_degree(cluster, target))
    return _orig_authorize(cluster, server, start_node, target, hops_done, node_only)


# --- walk 数に対する重みの推移（--trace-walks N 本ごとに記録）-------------------
_ORIG_RUN_WALK = vs.run_walk
_TRACE: Dict[str, Any] = {"every": 0, "walks": 0, "rows": []}


def _snapshot(cluster) -> None:
    hits = sum(s.auth_cache_hit for s in cluster.servers)
    miss = sum(s.auth_cache_miss for s in cluster.servers)
    for s in cluster.servers:
        if not isinstance(s.cache, LearnedLinearCache):
            continue
        c = s.cache
        items = [("all", c.models["node"])] if c.shared_model() else c.models.items()
        for typ, m in items:
            names = ("b",) + tuple(c.terms_for("node" if typ == "all" else typ))
            _TRACE["rows"].append({
                "walks_done": _TRACE["walks"], "sid": s.sid, "type": typ,
                "updates": m.updates, "cum_hits": hits, "cum_misses": miss,
                **{n: v for n, v in zip(names, m.w)},
            })


def _run_walk_traced(cluster, *args, **kwargs):
    r = _ORIG_RUN_WALK(cluster, *args, **kwargs)
    _TRACE["walks"] += 1
    if _TRACE["every"] and _TRACE["walks"] % _TRACE["every"] == 0:
        _snapshot(cluster)
    return r


def install(cfg: LearnerConfig, trace_every: int = 0) -> None:
    global _CFG, _INSTANCES
    _CFG = cfg
    _INSTANCES = 0
    _TRACE.update(every=int(trace_every), walks=0, rows=[])
    vs.run_walk = _run_walk_traced
    for name, cls in LEARNED_POLICIES.items():
        cache_extensions.register(name, _make_factory(cls))
    vs.authorize_candidate = _authorize_with_context


# =============================================================================
# 実行
# =============================================================================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="学習キャッシュ（方式A / F1）の仮想サーバ実験")
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default=None)
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    # 引き返し禁止（エッジ直後に元のノードへ戻らない）で統一。実機スクリプトの NO_BACKTRACK=1 と同じ
    p.add_argument("--allow-backtrack", action="store_true",
                   help="引き返しを許す（旧条件の再現用。既定は禁止）")
    p.add_argument("--policies", nargs="+",
                   default=["lru", "arc", "memo", "reuse_score", "learned"])
    # 比較用の現行提案（README の動作点: --size-neutral --reuse-edge-no-deg --reuse-w 100）
    p.add_argument("--reuse-w", type=float, default=100.0)
    # 学習器
    p.add_argument("--no-learn", action="store_true", help="重みを初期値で固定する")
    p.add_argument("--init", default="auto",
                   help='auto（learned=lru / structured=prior）/ lru / zero / prior / 重みを直接 "b,..."')
    p.add_argument("--forget", type=float, default=0.999)
    p.add_argument("--p0", type=float, default=1.0)
    p.add_argument("--window", type=int, default=1000)
    p.add_argument("--sample-k", type=int, default=8)
    p.add_argument("--miss-weight", choices=["rel_freq", "none"], default="rel_freq")
    p.add_argument("--hop-agg", choices=["min", "last"], default="min")
    p.add_argument("--z-clip", type=float, default=3.0)
    p.add_argument("--c-max", type=float, default=10.0)
    p.add_argument("--decay-halflife", type=float, default=1000.0,
                   help="freq-scope edc の半減期（キャッシュ時計）")
    p.add_argument("--pair-lr", type=float, default=0.05,
                   help="target pairwise の学習率（AdaGrad）")
    p.add_argument("--shared-model", action="store_true", help="node / edge で重みを共有する")
    p.add_argument("--features", default="H,O,D,R", help="使う特徴量（外したものは z=0）")
    p.add_argument("--target", choices=["dist", "reuse", "pairwise"], default="dist",
                   help="dist: 次回アクセス距離の log を回帰 / reuse: 滞在時間内に再利用されるかの 0/1")
    p.add_argument("--log-features", default="",
                   help='log1p してから標準化する特徴量をカンマ区切りで（例 "O" / "O,R"）。空 = 素の値')
    p.add_argument("--freq-scope", choices=["cache", "total", "edc"], default="cache",
                   help="O の数え方: cache=キャッシュに入ってから / total=このサーバでの全アクセス回数")
    p.add_argument("--learn-seed", type=int, default=0,
                   help="候補抽出の乱数 seed（実サーバの --learn-seed と同じ意味）")
    # seed
    p.add_argument("--seeds", type=int, nargs="+", default=None)
    p.add_argument("--n-seeds", type=int, default=1)
    p.add_argument("--disjoint", action="store_true")
    # 出力
    p.add_argument("--out-csv", default=None)
    p.add_argument("--out-json", default=None, help="学習した重みと履歴を保存")
    p.add_argument("--trace-walks", type=int, default=50,
                   help="learned の重みを N walk ごとに記録する（0 で記録しない）。--out-json に入る")
    p.add_argument("--selfcheck", action="store_true",
                   help="学習停止 + 初期重み LRU が LRU と厳密一致するか確かめて終了")
    a = p.parse_args()
    a.no_backtrack = not a.allow_backtrack
    return a


def resolve_seeds(a) -> List[int]:
    if a.seeds:
        return list(a.seeds)
    step = a.walks if a.disjoint else 1
    return [1 + i * step for i in range(a.n_seeds)]


def policy_args(a, seed: int) -> SimpleNamespace:
    return SimpleNamespace(
        cache_capacity=a.cache_capacity, cache_key_mode=a.cache_key_mode,
        alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes, seed=seed,
        max_hops=a.max_hops, no_backtrack=a.no_backtrack,
        theta=1.0, beta=1.0, gamma=1.0, hop_exp=1.0, deg_exp=1.0,
        cf=5.0, cd=10.0, rho=0.0, cr=10.0, reuse_w=a.reuse_w,
        size_neutral=True, reuse_edge_no_deg=True,
        delta=0.0, lambda_learn=0.0, cl=5.0,
    )


def learner_config(a, seed: int, learn: bool = True, init: str = None) -> LearnerConfig:
    return LearnerConfig(
        learn=learn and not a.no_learn, init=init or a.init, forget=a.forget,
        p0=a.p0, window=a.window, sample_k=a.sample_k, miss_weight=a.miss_weight,
        hop_agg=a.hop_agg, z_clip=a.z_clip, c_max=a.c_max, freq_scope=a.freq_scope, decay_halflife=a.decay_halflife, pair_lr=a.pair_lr, shared_model=a.shared_model, features=a.features, target=a.target, log_features=a.log_features, seed=a.learn_seed,
    )


def selfcheck(a, cluster) -> None:
    ok = True
    for sd in resolve_seeds(a):
        install(learner_config(a, sd, learn=False, init="lru"))
        r_lru = vs.run_policy(cluster, "lru", policy_args(a, sd))
        r_lrn = vs.run_policy(cluster, "learned", policy_args(a, sd))
        same = (r_lru["hits"] == r_lrn["hits"] and r_lru["accesses"] == r_lrn["accesses"]
                and r_lru["node_hit"] == r_lrn["node_hit"])
        ok &= same
        print(f"  seed={sd}: lru hits={r_lru['hits']}  learned(no-learn) hits={r_lrn['hits']}"
              f"  accesses {r_lru['accesses']}/{r_lrn['accesses']}  -> {'一致' if same else '不一致'}")
    print("selfcheck", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


def main() -> None:
    a = parse_args()
    seeds = resolve_seeds(a)
    graph = a.graph_name or Path(a.edges).stem
    edges = vs.load_edge_list(Path(a.edges))
    cluster = vs.VirtualCluster(edges, a.server_count,
                                Path(a.partition_dir) if a.partition_dir else None)
    print("=" * 96)
    print(f"学習キャッシュ（方式A / F1: 加算・z 標準化）  graph={graph}")
    print(f"  alpha={a.alpha} walks={a.walks} starts={a.start_nodes} cap/台={a.cache_capacity}"
          f" K={a.server_count} seeds={seeds}"
          f" backtrack={'許可' if a.allow_backtrack else '禁止'}")
    print(f"  learner: init={a.init} forget={a.forget} p0={a.p0} W={a.window}"
          f" k={a.sample_k} miss_weight={a.miss_weight} hop={a.hop_agg}"
          f"{' (学習停止)' if a.no_learn else ''}")
    print("=" * 96)
    if a.selfcheck:
        selfcheck(a, cluster)

    rows: List[Dict[str, Any]] = []
    models: List[Dict[str, Any]] = []
    weight_trace: List[Dict[str, Any]] = []
    for policy in a.policies:
        ov, nd, eg = [], [], []
        for sd in seeds:
            install(learner_config(a, sd), a.trace_walks if policy in LEARNED_POLICIES else 0)
            r = vs.run_policy(cluster, policy, policy_args(a, sd))
            if policy in LEARNED_POLICIES:
                weight_trace.extend({"graph": graph, "alpha": a.alpha, "seed": sd,
                                     "policy": policy, **row}
                                    for row in _TRACE["rows"])
            ov.append(100 * r["hit_rate"])
            nd.append(100 * r["node_hit_rate"])
            eg.append(100 * r["edge_hit_rate"])
            if policy in LEARNED_POLICIES:
                for s in cluster.servers:
                    models.append({"seed": sd, "sid": s.sid, **s.cache.stats(),
                                   "history": s.cache.history})
        sd_ = st.stdev(ov) if len(ov) > 1 else 0.0
        rows.append({"graph": graph, "alpha": a.alpha, "walks": a.walks,
                     "cap": a.cache_capacity, "policy": policy,
                     "overall": st.mean(ov), "overall_sd": sd_,
                     "node": st.mean(nd), "edge": st.mean(eg), "n_seeds": len(seeds)})
        print(f"  {policy:<12} overall {st.mean(ov):6.2f}±{sd_:.2f}"
              f"   node {st.mean(nd):6.2f}   edge {st.mean(eg):6.2f}")

    for pol in sorted({m["policy"] for m in models}):
        ms_pol = [m for m in models if m["policy"] == pol]
        names = ["b"] + ms_pol[0]["term_names"]   # 見出し（型ごとに無い項は空欄）
        print(f"\n  [{pol}] 学習後の重み（サーバ・型ごと、seed 平均。特徴量は z 値。正 = 追い出しやすい）")
        print(f"  {'sid':>3} {'type':<5} {'updates':>8} {'mae':>6} "
              + " ".join(f"{n:>7}" for n in names))
        for sid in sorted({m["sid"] for m in ms_pol}):
            for t in ms_pol[0]["models"]:
                ms = [m["models"][t] for m in ms_pol if m["sid"] == sid]
                if not any(x["updates"] for x in ms):
                    continue
                avg = {k: (st.mean(x[k] for x in ms) if k in ms[0] else float("nan"))
                       for k in names + ["updates"]}
                maes = [x["mae"] for x in ms if x["mae"] is not None]
                mae = st.mean(maes) if maes else float("nan")
                print(f"  {sid:>3} {t:<5} {int(avg['updates']):>8} {mae:>6.3f} "
                      + " ".join(f"{avg[n]:>7.3f}" for n in names))

    if a.out_csv:
        out = Path(a.out_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wtr.writeheader()
            wtr.writerows(rows)
        print(f"[saved] {out}")
    if a.out_json:
        out = Path(a.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"config": vars(a), "rows": rows, "models": models,
                                   "weight_trace": weight_trace},
                                  ensure_ascii=False, indent=1))
        print(f"[saved] {out}")


if __name__ == "__main__":
    main()
