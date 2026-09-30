#!/usr/bin/env python3
# =============================================================================
# plot_wsweep_virtual.py
#
#   figs/wsweep_template.png (模式図) を **実測データに置き換えた** 版を作る。
#   縦軸 hit rate は時間非依存の論理指標なので、仮サーバ・シミュレータ
#   (virtual_server_sim_reuse_score.py) で測れる = 分散不要。
#
#   曲線 (テンプレートと同じ2本):
#     - hop    (V = Ĥ のみ) : theta=0, lambda=0, hop_exp=1, deg_exp=0
#     - degree (V = D̂ のみ) : theta=0, lambda=0, hop_exp=0, deg_exp=1
#   参照線: LRU (= reuse_w=0 = 厳密LRU) と memo (上限=再訪率)。
#   帯    : LRU〜memo = "capturable blind spot"。
#   横軸  : reuse_w (構造重み)。w=0 で厳密LRU、w過大で recency 破壊 → 山なり。
#
#   シミュレータ本体を import して cluster を1回だけ構築し、w を掃引して
#   run_policy を呼ぶ (グラフ再ロードを避け高速化)。既存ファイルは変更しない。
#
#   使い方:
#     python3 base/proposed_cache/figs/plot_wsweep_virtual.py \
#       --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB \
#       --server-count 2 --alpha 0.01 --walks 1000 --cache-capacity 100 \
#       --start-nodes 0 --seed 42
# =============================================================================
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# --- 隣の virtual_server_sim_reuse_score.py を import -------------------------
_SIM_PATH = Path(__file__).resolve().parent.parent / "core" / "virtual_server_sim_reuse_score.py"
_spec = importlib.util.spec_from_file_location("virtual_server_sim_reuse_score", _SIM_PATH)
vsim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vsim)


def base_args(a) -> SimpleNamespace:
    """run_policy が参照する属性を持つ Namespace を作る (reuse_score 既定値)。"""
    return SimpleNamespace(
        cache_capacity=a.cache_capacity, cache_key_mode=a.cache_key_mode,
        alpha=a.alpha, walks=a.walks, start_nodes=a.start_nodes, seed=a.seed,
        max_hops=a.max_hops, no_backtrack=False,
        # reuse_score パラメータ (構造のみを見るため O/L は指数0で無効化)
        theta=0.0, lambda_learn=0.0, delta=0.0,
        beta=1.0, gamma=1.0, cf=5.0, cl=5.0, cd=10.0, rho=0.0, cr=10.0,
        reuse_w=0.0, hop_exp=1.0, deg_exp=0.0,
    )


def hit_pct(cluster, policy: str, args: SimpleNamespace) -> float:
    return 100.0 * vsim.run_policy(cluster, policy, args)["hit_rate"]


def main() -> None:
    p = argparse.ArgumentParser(description="w-sweep hit-rate 図 (仮サーバ実測, テンプレ準拠)")
    p.add_argument("--edges", required=True)
    p.add_argument("--graph-name", default="graph")
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--alpha", type=float, default=0.01)
    p.add_argument("--walks", type=int, default=1000)
    p.add_argument("--start-nodes", type=int, nargs="+", default=[0])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--cache-capacity", type=int, default=100)
    p.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    p.add_argument("--w-grid", type=float, nargs="+",
                   default=[0, 50, 100, 140, 200, 400, 700, 1000, 2000])
    p.add_argument("--out", default=None)
    a = p.parse_args()

    edges = vsim.load_edge_list(Path(a.edges))
    cluster = vsim.VirtualCluster(
        edges, a.server_count,
        Path(a.partition_dir) if a.partition_dir else None,
    )
    print(f"[graph] {a.graph_name}: nodes={len(cluster.nodes)} edges={len(edges)} "
          f"K={a.server_count} walks={a.walks} cap={a.cache_capacity} alpha={a.alpha}")

    # --- 参照線: LRU (=w0) と memo (上限) ---
    lru = hit_pct(cluster, "lru", base_args(a))
    memo = hit_pct(cluster, "memo", base_args(a))
    print(f"[ref] LRU={lru:.1f}%  memo(upper)={memo:.1f}%  (blind spot={memo-lru:.1f}pt)")

    # --- 3信号を w 掃引で測定 ---
    #   hop/degree = 構造 (静的 prior)、freq = 観測頻度 O (=取り逃し量そのもの)。
    #   各信号だけを残すため他因子の指数は 0 にする。
    signals = {
        "freq":   dict(theta=1.0, hop_exp=0.0, deg_exp=0.0),  # V = O のみ
        "hop":    dict(theta=0.0, hop_exp=1.0, deg_exp=0.0),  # V = Ĥ のみ
        "degree": dict(theta=0.0, hop_exp=0.0, deg_exp=1.0),  # V = D̂ のみ
    }
    curves = {}
    for name, exps in signals.items():
        ys: List[float] = []
        for w in a.w_grid:
            args = base_args(a)
            args.reuse_w = float(w)
            args.theta = exps["theta"]
            args.hop_exp = exps["hop_exp"]
            args.deg_exp = exps["deg_exp"]
            y = hit_pct(cluster, "reuse_score", args)
            ys.append(y)
            print(f"  {name:<7} w={w:<7g} hit={y:.1f}%")
        curves[name] = ys

    # --- 作図 (テンプレート wsweep_template.png のレイアウトを踏襲) ---
    x = list(range(len(a.w_grid)))
    xlabels = [("%g" % w) for w in a.w_grid]
    fig, ax = plt.subplots(figsize=(10.5, 6.5))

    # blind spot 帯 (LRU〜memo)
    ax.axhspan(lru, memo, color="#fff4c2", zorder=0)
    ax.text(x[-1] * 0.62, lru + (memo - lru) * 0.72, "capturable\nblind spot",
            color="#b8860b", fontsize=12, ha="left", va="center")

    # 参照線
    ax.axhline(lru, ls="--", color="#1f77b4", lw=1.6,
               label=f"LRU (=w0 base) = {lru:.0f}%")
    ax.axhline(memo, ls="--", color="#888888", lw=1.6,
               label=f"memo (upper bound) = {memo:.0f}%")

    # 3曲線 (freq=O を主役に)
    ax.plot(x, curves["freq"], "-^", color="#8e44ad", lw=2.6, ms=9,
            label="freq  (V=O, direct reuse signal)")
    ax.plot(x, curves["hop"], "-o", color="#c0392b", lw=2.4, ms=8,
            label="hop  (V=Ĥ, hop_near)")
    ax.plot(x, curves["degree"], "-s", color="#27ae60", lw=2.4, ms=8,
            label="degree  (V=D̂)")

    # 最良 w の注釈 (freq 曲線のピーク=主役)
    freq_ys = curves["freq"]
    bi = max(range(len(freq_ys)), key=lambda i: freq_ys[i])
    if bi != 0:  # w=0 (=LRU) がピークなら注釈しない
        ax.annotate(f"optimal w={xlabels[bi]}  (+{freq_ys[bi]-lru:.1f}pt vs LRU)",
                    xy=(x[bi], freq_ys[bi]), xytext=(x[bi] + 0.3, freq_ys[bi] + 5),
                    color="#8e44ad", fontsize=11,
                    arrowprops=dict(arrowstyle="->", color="#8e44ad"))
    ax.annotate("w too large:\nrecency destroyed",
                xy=(x[-1], min(curves["hop"][-1], curves["degree"][-1])),
                xytext=(x[-1] - 1.6, lru - 4.5),
                color="#888888", fontsize=10,
                arrowprops=dict(arrowstyle="->", color="#888888"))

    ax.set_xticks(x)
    ax.set_xticklabels(xlabels)
    ax.set_xlabel("w  (strength of hop / degree consideration)   [w=0 is exactly LRU]")
    ax.set_ylabel("hit rate %")
    ax.set_title(f"hit rate vs reuse weight w  —  MEASURED (virtual servers)\n"
                 f"{a.graph_name}, walks={a.walks}, cap={a.cache_capacity}, "
                 f"K={a.server_count}, alpha={a.alpha}, key={a.cache_key_mode}")
    ymin = max(0, min(lru, *curves['freq'], *curves['hop'], *curves['degree']) - 6)
    ax.set_ylim(ymin, memo + 5)
    ax.legend(loc="upper right", framealpha=0.95)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()

    out = a.out or str(Path(__file__).resolve().parent /
                       f"wsweep_{a.graph_name.lower()}_{a.cache_key_mode}_measured.png")
    fig.savefig(out, dpi=130)
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
