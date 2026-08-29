#!/usr/bin/env python3
# =============================================================================
# run_loo_ablation.py  —  提案手法から要素を「1つずつ外す」対照実験 (leave-one-out)
#
#   目的: 論文の目的2「性能向上がどの構造情報に由来するか」を、
#         完成版から1要素だけ落とした構成と比較して定量化する。
#         Δ = (提案) − (その要素を外した構成) = その要素の限界寄与。
#
#   外す要素 (5つ):
#     Freq  使用回数        θ=0
#     Close 始点からの近さ  node_hop_exp=0 かつ edge_use_hop=False
#     Deg   ノード次数      node_deg_exp=0
#     Pool  ノード/エッジ別キャッシュ → 共有1プール (容量は合計、実効 w は同一に揃える)
#     Base  LRU の最終アクセス時刻 → 0 (= 純スコア退去)
#
#   参照: LRU(共有) / ARC(共有) / memo(上限) / 分離LRU(w=0)
#         「分離LRU」は容量を分けただけの構成。提案 − 分離LRU = スコアの寄与、
#         分離LRU − LRU = プール分離だけの寄与、に分解できる。
#
#   既存ファイルは一切変更しない (import して dp.authorize_dual だけ差し替える)。
#   歩行ロジックは dp._run_walk / dp._select_next をそのまま使うので、
#   rng 消費・候補選択は本家と完全に同一。
#
#   使い方:
#     python3 base/proposed_cache/run_loo_ablation.py \
#       --edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon \
#       --alpha 0.1 --walks 1000 --cache-capacity 100 --seeds 1 7 13 99 123
#
#   注意 (seed): 本家と同じ rng 規則 random.Random(seed + i) を使うため、
#     seed の差が walks より小さいとウォーク集合が重複する。
#     独立な反復が欲しい場合は --seeds 1 2001 4001 6001 8001 のように
#     walks 以上離した値を渡すこと (--disjoint で自動生成)。
# =============================================================================
from __future__ import annotations

import argparse
import csv
import importlib.util
import random
import statistics as st
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load("virtual_server_sim_reuse_score")
dp = _load("virtual_server_dualpool")


# =============================================================================
# 認可 (dp.authorize_dual の差し替え版: 共有プールにも対応)
#   split : server.node_cache / server.edge_cache, reuse_w=w, prior 倍率=1
#   shared: server.shared_cache 1本, reuse_w=1, prior に w を畳み込む
#           → Priority = last_access + w·V/size が split と厳密に一致する
# =============================================================================
def authorize_flex(cluster, server, start_node, target, hops_done, node_only) -> bool:
    is_edge = isinstance(target, str) and target.startswith("edge_")
    shared = getattr(server, "shared_cache", None)
    ckey = (int(start_node), base.cache_entity_key(target, node_only=node_only))
    if shared is not None:
        cache = shared
        # 共有プールでは reuse_w = w_node（単一 w）。さらに prior に占有量 size を
        # 掛けて _h_value の /size を打ち消す。こうすると:
        #   スコア全 OFF (V=size) → Priority = last_access + w   （全キー同一定数）
        #     = 厳密に共有 LRU と同順 ← 分解の基準 v(∅) が LRU に一致する
        #   スコア ON        → Priority = last_access + w·O^θ·H^β·D^γ（型に依らず対等）
        # 打ち消さないと node は +w/2、edge は +w/3 の定数差が付き、
        # スコア無しでもエッジ優先のバイアスが残ってしまう。
        wmul = float(cache.sizer.key_weight(ckey))
    else:
        cache = server.edge_cache if is_edge else server.node_cache
        wmul = 1.0
    prior_fn = dp.edge_prior if is_edge else dp.node_prior

    if ckey in cache:
        server.auth_cache_hit += 1
        if is_edge:
            server.hit_edge += 1
        else:
            server.hit_node += 1
        if isinstance(cache, base.PPRBoundedCache) and cache.use_hop_prior:
            cache.bump_prior(ckey, prior_fn(cluster, server, target, hops_done) * wmul)
        return bool(cache[ckey])

    server.auth_cache_miss += 1
    if is_edge:
        server.miss_edge += 1
    else:
        server.miss_node += 1
    owner_t = cluster.owner(target)
    if owner_t == server.sid:
        server.local_auth_calls += 1
        srv = server
    else:
        server.remote_auth_calls += 1
        srv = cluster.servers[owner_t]
    allowed = base.is_locally_allowed(srv, start_node, target)
    if isinstance(cache, base.PPRBoundedCache) and cache.use_hop_prior:
        cache.bump_prior(ckey, prior_fn(cluster, server, target, hops_done) * wmul)
    cache[ckey] = bool(allowed)
    return bool(allowed)


dp.authorize_dual = authorize_flex  # dp._select_next はモジュールグローバルを引く


def _h_value_no_base(self, key, size):
    """Base(LRU の最終アクセス時刻) を外した退去優先度 = w·V/size のみ。"""
    return self.reuse_w * (self._score(key) / size)


# =============================================================================
# 1 構成 (1 seed) を実行
# =============================================================================
def run_case(cluster, a, *, theta: float, hop_exp: float, deg_exp: float,
             edge_hop: bool, pool: str, base_mode: str,
             w_node: float, w_edge: float, seed: int) -> Dict[str, Any]:
    sizer = base.DecisionCacheSizer()

    def mk(cap: int, w: float):
        return base.build_authz_cache(
            "reuse_score", cap, sizer=sizer, theta=theta, lambda_learn=0.0,
            beta=1, gamma=1, cf=a.cf, cl=5, cd=10, rho=0, cr=10, reuse_w=w)

    servers: List[Any] = []
    for sid in range(cluster.server_count):
        s = base.VirtualServer(sid, base.build_authz_cache("none", 1),
                               cluster._denied_by_sid[sid],
                               hop_exp=hop_exp, deg_exp=deg_exp)
        s.edge_use_hop = edge_hop
        s.w_node = w_node
        s.w_edge = w_edge
        if pool == "split":
            s.shared_cache = None
            s.node_cache = mk(a.cap_node, w_node)
            s.edge_cache = mk(a.cap_edge, w_edge)
            pools = [s.node_cache, s.edge_cache]
        else:
            s.shared_cache = mk(a.cap_node + a.cap_edge, w_node)
            s.node_cache = s.edge_cache = s.shared_cache
            pools = [s.shared_cache]
        if base_mode == "none":
            for c in pools:
                c._h_value = types.MethodType(_h_value_no_base, c)
        servers.append(s)
    cluster.servers = servers

    node_only = (a.cache_key_mode == "node_only")
    for start in a.start_nodes:
        for i in range(a.walks):
            rng = random.Random(seed + i)
            dp._run_walk(cluster, int(start), a.alpha, rng, a.max_hops,
                         node_only, a.no_backtrack)

    hn = sum(s.hit_node for s in servers); mn = sum(s.miss_node for s in servers)
    he = sum(s.hit_edge for s in servers); me = sum(s.miss_edge for s in servers)
    hits, miss = hn + he, mn + me
    return {
        "hit_rate": hits / (hits + miss) if hits + miss else 0.0,
        "node_hit_rate": hn / (hn + mn) if hn + mn else 0.0,
        "edge_hit_rate": he / (he + me) if he + me else 0.0,
        "remote_auth_calls": sum(s.remote_auth_calls for s in servers),
        "local_auth_calls": sum(s.local_auth_calls for s in servers),
        "acc_node": hn + mn,
        "acc_edge": he + me,
    }


def args_for(a, seed):
    """base.run_policy 用 (LRU/ARC/memo のベースライン)。"""
    return SimpleNamespace(
        cache_capacity=a.cache_capacity, cache_key_mode=a.cache_key_mode,
        alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes, seed=seed,
        max_hops=a.max_hops, no_backtrack=a.no_backtrack,
        theta=a.theta, lambda_learn=0.0, delta=0.0, beta=1, gamma=1,
        cf=a.cf, cl=5, cd=10, rho=0, cr=10, reuse_w=1.0,
        hop_exp=1.0, deg_exp=1.0)


# =============================================================================
# ケース定義
# =============================================================================
def build_cases(a):
    """(表示名, 種別, kwargs) のリスト。full からの1要素除去 = leave-one-out。"""
    full = dict(theta=a.theta, hop_exp=1.0, deg_exp=1.0, edge_hop=True,
                pool="split", base_mode="lru", w_node=a.w_node, w_edge=a.w_edge)

    def var(**over):
        d = dict(full); d.update(over); return d

    return [
        ("提案 (全要素)",            "full", full),
        ("−Freq (使用回数)",         "loo",  var(theta=0.0)),
        ("−Close (始点からの近さ)",  "loo",  var(hop_exp=0.0, edge_hop=False)),
        ("−Deg (ノード次数)",        "loo",  var(deg_exp=0.0)),
        ("−Pool (プール分離)",       "loo",  var(pool="shared")),
        ("−Base (LRU最終アクセス)",  "loo",  var(base_mode="none")),
        # 共有プールで w を単一にした版。−Pool の Δ に「型別 w」の効果が
        # 混ざっていないかを切り分けるための参照。
        ("  ↑共有かつ単一w",         "ref",  var(pool="shared", w_edge=a.w_node)),
        ("分離LRU (w=0)",            "ref",  var(w_node=0.0, w_edge=0.0)),
    ]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default="graph")
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None,
                   help="node_to_starts_serverX.json のディレクトリ。"
                        "指定すると実グラフ分割 + deny テーブル(30%%等)が有効になる。"
                        "未指定だと ModuloPartitioner かつ deny 空(=全許可)。")
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cap-node", type=int, default=40)
    p.add_argument("--cap-edge", type=int, default=60)
    p.add_argument("--w-node", type=float, default=100.0)
    p.add_argument("--w-edge", type=float, default=300.0)
    p.add_argument("--theta", type=float, default=1.0)
    p.add_argument("--cf", type=float, default=5.0)
    p.add_argument("--cache-key-mode", default="full")
    p.add_argument("--no-backtrack", action="store_true")
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 7, 13, 99, 123])
    p.add_argument("--disjoint", action="store_true",
                   help="seed を walks 以上離して自動生成 (独立反復にする)")
    p.add_argument("--n-seeds", type=int, default=5, help="--disjoint 時の反復数")
    p.add_argument("--out-csv", default=None)
    p.add_argument("--out-raw", default=None, help="seed別の生値CSV (作図用)")
    p.add_argument("--deny-mode", choices=["auto", "off"], default="auto",
                   help="off: partition-dir を指定しても deny を空にする (要因切り分け用)")
    p.add_argument("--owner-mode", choices=["auto", "modulo"], default="auto",
                   help="modulo: partition-dir を指定しても所有はモジュロにする (要因切り分け用)")
    a = p.parse_args()

    if a.disjoint:
        a.seeds = [1 + k * (a.walks + 1) for k in range(a.n_seeds)]

    edges = base.load_edge_list(Path(a.edges))
    cluster = base.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None)
    if a.owner_mode == "modulo":
        cluster.partitioner = base.ModuloPartitioner(a.server_count)
        cluster._owner_cache.clear()
    if a.deny_mode == "off":
        cluster._denied_by_sid = {i: {} for i in range(a.server_count)}
    ndeny = [len(d) for d in cluster._denied_by_sid.values()]
    print(f"[graph] {a.graph_name}  nodes={len(cluster.nodes)}  edges={len(edges)}")
    print(f"[part ] {type(cluster.partitioner).__name__}  deny件数={ndeny}"
          + ("  (deny 空 = 全許可)" if sum(ndeny) == 0 else ""))
    from collections import Counter as _C
    _c = _C()
    for _n in cluster.nodes:
        _c[(cluster.owner(_n), "node")] += 1
    for (_u, _v) in edges:
        _c[(cluster.owner(base.make_edge_id(_u, _v)), "edge")] += 1
    print("[own  ] " + "  ".join(
        f"srv{sid}: node={_c[(sid,'node')]} edge={_c[(sid,'edge')]}"
        for sid in range(a.server_count)))
    print(f"[cond ] alpha={a.alpha} walks={a.walks} starts={a.start_nodes} "
          f"cap={a.cache_capacity} (node {a.cap_node}/edge {a.cap_edge}) "
          f"w={a.w_node}/{a.w_edge} theta={a.theta} C_F={a.cf}")
    print(f"[seeds] {a.seeds}"
          + ("  (独立: 重複なし)" if min(
              abs(x - y) for i, x in enumerate(a.seeds)
              for y in a.seeds[i + 1:]) > a.walks else
             f"  ※ 差が walks({a.walks}) 未満の組があり、ウォーク集合が重複します"))
    print()

    rows: List[Dict[str, Any]] = []

    # --- ベースライン (共有1プール, 既存方式) ---
    for pol, label in (("lru", "LRU (共有)"), ("arc", "ARC (共有)"),
                       ("memo", "memo (上限)")):
        res = [base.run_policy(cluster, pol, args_for(a, s)) for s in a.seeds]
        rows.append(dict(name=label, kind="baseline", res=res))
        print(f"  done: {label}")

    # --- 提案 + leave-one-out + 分離LRU ---
    for name, kind, kw in build_cases(a):
        res = [run_case(cluster, a, seed=s, **kw) for s in a.seeds]
        rows.append(dict(name=name, kind=kind, res=res))
        print(f"  done: {name}")

    # --- 集計 ---
    def agg(res, key):
        v = [100 * r[key] for r in res]
        return st.mean(v), (st.stdev(v) if len(v) > 1 else 0.0)

    for r in rows:
        for key, tag in (("node_hit_rate", "node"), ("edge_hit_rate", "edge"),
                         ("hit_rate", "overall")):
            m, s = agg(r["res"], key)
            r[f"{tag}_mean"], r[f"{tag}_sd"] = m, s

    full = next(r for r in rows if r["kind"] == "full")
    lru = next(r for r in rows if r["name"].startswith("LRU"))
    split_lru = next(r for r in rows if r["name"].startswith("分離LRU"))

    # --- 対応のある Δ (同一 seed 同士の差) → これが正しい誤差評価 -------------
    for r in rows:
        for key, tag in (("node_hit_rate", "node"), ("edge_hit_rate", "edge"),
                         ("hit_rate", "overall")):
            d = [100 * (f[key] - c[key])
                 for f, c in zip(full["res"], r["res"])]
            r[f"d_{tag}_mean"] = st.mean(d)
            r[f"d_{tag}_sd"] = st.stdev(d) if len(d) > 1 else 0.0

    print("\n" + "=" * 84)
    print(f"leave-one-out 対照実験   {a.graph_name}  "
          f"(alpha={a.alpha}, RW {a.walks}本, 容量{a.cache_capacity})")
    print("=" * 84)
    print(f"{'構成':<26}{'node':>13}{'edge':>13}{'overall':>13}{'Δoverall':>11}")
    print("-" * 84)
    for r in rows:
        d = ""
        if r["kind"] == "loo":
            d = f"{full['overall_mean'] - r['overall_mean']:+.2f}"
        elif r["kind"] == "full":
            d = f"({full['overall_mean'] - lru['overall_mean']:+.2f} vs LRU)"
        print(f"{r['name']:<26}"
              f"{r['node_mean']:>7.2f}±{r['node_sd']:<5.2f}"
              f"{r['edge_mean']:>7.2f}±{r['edge_sd']:<5.2f}"
              f"{r['overall_mean']:>7.2f}±{r['overall_sd']:<5.2f}"
              f"{d:>11}")
    print("-" * 84)
    print("Δoverall = (提案) − (その要素を外した構成) = その要素の限界寄与 (大きいほど効いている)")

    # --- 効いた順 (対応のある Δ ± sd) ---
    loo = [r for r in rows if r["kind"] == "loo"]
    loo.sort(key=lambda r: -r["d_overall_mean"])
    print("\n[効いた順]  Δ = 提案 − 除去版 (同一seedの差, ±sd)")
    print(f"  {'要素':<8}{'node':>14}{'edge':>14}{'overall':>14}")
    for r in loo:
        nm = r["name"].lstrip("−").split(" ")[0]
        print(f"  {nm:<8}"
              f"{r['d_node_mean']:>+8.2f}±{r['d_node_sd']:<5.2f}"
              f"{r['d_edge_mean']:>+8.2f}±{r['d_edge_sd']:<5.2f}"
              f"{r['d_overall_mean']:>+8.2f}±{r['d_overall_sd']:<5.2f}")

    # --- 改善の分解 ---
    print("\n[改善の分解 (overall)]")
    print(f"  LRU(共有)            {lru['overall_mean']:6.2f}")
    print(f"  + プール分離のみ      {split_lru['overall_mean']:6.2f}   "
          f"({split_lru['overall_mean'] - lru['overall_mean']:+.2f})")
    print(f"  + スコア (=提案)      {full['overall_mean']:6.2f}   "
          f"({full['overall_mean'] - split_lru['overall_mean']:+.2f})")
    print(f"  合計                 {full['overall_mean'] - lru['overall_mean']:+.2f} pt vs LRU")

    # --- 越境率 (参考) ---
    fr = full["res"][0]
    tot = fr["local_auth_calls"] + fr["remote_auth_calls"]
    if tot:
        print(f"\n[参考] ミス時の認可: local={fr['local_auth_calls']} "
              f"remote={fr['remote_auth_calls']} → 越境率 {100*fr['remote_auth_calls']/tot:.1f}%")
        print(f"       アクセス比 node:edge = 1:{fr['acc_edge']/max(1,fr['acc_node']):.2f}")

    if a.out_csv:
        with open(a.out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["graph", "alpha", "walks", "cap", "name", "kind",
                        "node_mean", "node_sd", "edge_mean", "edge_sd",
                        "overall_mean", "overall_sd",
                        "d_node_mean", "d_node_sd", "d_edge_mean", "d_edge_sd",
                        "d_overall_mean", "d_overall_sd"])
            for r in rows:
                w.writerow([a.graph_name, a.alpha, a.walks, a.cache_capacity,
                            r["name"], r["kind"]]
                           + [f"{r[k]:.4f}" for k in
                              ("node_mean", "node_sd", "edge_mean", "edge_sd",
                               "overall_mean", "overall_sd",
                               "d_node_mean", "d_node_sd",
                               "d_edge_mean", "d_edge_sd",
                               "d_overall_mean", "d_overall_sd")])
        print(f"\n[CSV] {a.out_csv}")

    if a.out_raw:
        with open(a.out_raw, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["graph", "alpha", "walks", "cap", "name", "kind", "seed",
                        "node_hit", "edge_hit", "overall_hit"])
            for r in rows:
                for s, res in zip(a.seeds, r["res"]):
                    w.writerow([a.graph_name, a.alpha, a.walks, a.cache_capacity,
                                r["name"], r["kind"], s,
                                f"{100*res['node_hit_rate']:.4f}",
                                f"{100*res['edge_hit_rate']:.4f}",
                                f"{100*res['hit_rate']:.4f}"])
        print(f"[RAW] {a.out_raw}")


if __name__ == "__main__":
    main()
