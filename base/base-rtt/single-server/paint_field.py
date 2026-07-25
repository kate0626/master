"""
距離場ペインタ(base-rtt) ―― 設計曲線 g_t を RW で1歩ずつ消費し, 各エンティティに焼き付ける。
================================================================================
手順:
このRWは単一サーバにおけるRWの経路、試験的な運用
  1) 開始ノード S から普通に RW する(base/base の GraphShard, node<->edge 二部展開)。
  2) 設計曲線 g_t = μ + a·λ^t(rtt_model と同じ)を 1 歩ずつ消費する。
  3) そのステップで「初めて訪れた」エンティティに g_t を割り当てて固定(= node-consistent)。
     - 複数 walk を回す場合, 初回訪問時刻は全 walk の最小(= S からの到達の早さ ≒ 距離)。
  4) 未訪問エンティティは μ(= g_∞, 収束値)にフォールバック。

これは design_and_run.py が内部で呼ぶ関数(paint)を提供する。単体でも走らせられ,
距離場を results/field.json に書き出せる。

注意: これは「S からの距離場」であって walk 非依存の大域サーバ配置ではない。
      設計曲線 g_t を厳密再現するのは "S から出た最初のフロンティア" 上だけ。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

BASE_BASE = Path(__file__).resolve().parents[1] / "base"
sys.path.insert(0, str(BASE_BASE))
from split_remote_server import GraphShard, load_edge_list  # noqa: E402

from rtt_model import make_curve, lam_from_converge_steps, auto_steps  # noqa: E402


def paint(shard, start, g, walks, seed):
    """初回訪問時刻に応じて g_t を焼き付ける。返り値 {entity_str: rtt}。"""
    first_rtt = {}
    for w in range(walks):
        rng = random.Random(seed + w)
        cur = start
        for t in range(len(g)):
            key = str(cur)
            if key not in first_rtt:          # 初回訪問で固定(以後上書きしない)
                first_rtt[key] = float(g[t])
            neigh = shard.get_neighbors(cur)
            if not neigh:
                break
            cur = neigh[rng.randrange(len(neigh))].node_id
    return first_rtt


def parse_args():
    p = argparse.ArgumentParser(description="Paint a distance-field RTT by consuming g_t along RW.")
    p.add_argument("--graph", required=True)
    p.add_argument("--start", type=int, required=True, help="ペイント基準ノード S")
    p.add_argument("--mu", type=float, default=50.0)
    p.add_argument("--amp", type=float, default=40.0)
    p.add_argument("--direction", default="above", choices=["above", "below", "zigzag"])
    p.add_argument("--converge-steps", type=int, default=100)
    p.add_argument("--lam", type=float, default=None)
    p.add_argument("--steps", type=int, default=None, help="消費する曲線長(既定=1.3N)")
    p.add_argument("--walks", type=int, default=40, help="ペイント用 walk 本数(多いほど広く塗れる)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="results/field.json")
    return p.parse_args()


def main():
    args = parse_args()
    edges = load_edge_list(Path(args.graph).expanduser())
    shard = GraphShard(edges, server_id=0, server_count=1)

    lam = args.lam if args.lam is not None else lam_from_converge_steps(args.converge_steps)
    steps = args.steps or auto_steps(args.converge_steps)
    _, g, _, lam_signed = make_curve(args.mu, args.amp, args.direction, lam, steps)

    field = paint(shard, args.start, g, args.walks, args.seed)
    total = sum(1 for _ in shard.neighbor_map)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "mu": args.mu, "start": args.start, "direction": args.direction,
        "lam": lam_signed, "map": field,
    }), encoding="utf-8")
    print(f"[paint] start={args.start} dir={args.direction} |λ|={lam:.4f} steps={steps} walks={args.walks}")
    print(f"[paint] painted {len(field)} / {total} entities ({100*len(field)/max(1,total):.1f}%), "
          f"unvisited -> μ={args.mu}")
    print(f"[paint] saved {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
