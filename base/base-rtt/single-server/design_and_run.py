"""
設計 → 実行 → 一致確認 を一本で(base-rtt) ―― above/below/zigzag 限定。
================================================================================
やること:
  1) 対象 RW の歩数 T(horizon)で RTT モデルを設計 … converge_steps=T で
     g_t = μ + a·λ^t(above/below/zigzag)を作る(=T歩で概ね収束する曲線)。
  2) その曲線を RW で 1 歩ずつ消費して各エンティティに焼き付け(paint, 初回固定)。
  3) 別シードで RW を実行し, 各歩に焼き付けた RTT を組み込んで軌跡を記録。
  4) 実行時の RTT 軌跡(平均 g_t)が設計モデルに概ね一致することを重ね描き + RMSE で確認。

出力: results/design_run.png(設計曲線 vs 実測 g_t を above/below/zigzag で並べる)。
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "YuGothic", "Arial Unicode MS"):
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.family"] = [_f, "sans-serif"]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

BASE_BASE = Path(__file__).resolve().parents[1] / "base"
sys.path.insert(0, str(BASE_BASE))
from split_remote_server import GraphShard, load_edge_list  # noqa: E402

from rtt_model import make_curve, lam_from_converge_steps  # noqa: E402
from paint_field import paint  # noqa: E402


def run_experiment(shard, start, field, mu, steps, walks, seed):
    """焼き付けた field を使って RW を実行し, 各歩の RTT を記録。返り値 [walks, steps+1]。"""
    rows = []
    for w in range(walks):
        rng = random.Random(seed + w)
        cur = start
        row = [field.get(str(cur), mu)]
        for _ in range(steps):
            neigh = shard.get_neighbors(cur)
            if neigh:
                cur = neigh[rng.randrange(len(neigh))].node_id
            row.append(field.get(str(cur), mu))
        rows.append(row)
    return np.array(rows, dtype=float)


def one_direction(shard, start, direction, mu, amp, T, walks, seed):
    lam = lam_from_converge_steps(T)                         # T歩で収束する|λ|
    _, g, _, _ = make_curve(mu, amp, direction, lam, T + 1)  # 設計曲線
    field = paint(shard, start, g, walks, seed)              # 1歩ずつ焼き付け(paintシード)
    M = run_experiment(shard, start, field, mu, T, walks, seed + 10_000)  # 別シードで実行
    measured = M.mean(axis=0)
    rmse = float(np.sqrt(np.mean((measured - g) ** 2)))
    cover = len(field) / max(1, sum(1 for _ in shard.neighbor_map))
    return g, measured, M, rmse, cover, lam


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--mu", type=float, default=50.0)
    ap.add_argument("--amp", type=float, default=40.0)
    ap.add_argument("--steps", type=int, default=100, help="対象RWの歩数 T(=設計 horizon)")
    ap.add_argument("--walks", type=int, default=80)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--directions", nargs="+", default=["above", "below", "zigzag"])
    ap.add_argument("--out", default="results/design_run.png")
    args = ap.parse_args()

    edges = load_edge_list(Path(args.graph).expanduser())
    shard = GraphShard(edges, server_id=0, server_count=1)
    T = args.steps

    fig, axes = plt.subplots(1, len(args.directions), figsize=(5.2 * len(args.directions), 4.6),
                             squeeze=False)
    for ax, d in zip(axes[0], args.directions):
        g, measured, M, rmse, cover, lam = one_direction(
            shard, args.start, d, args.mu, args.amp, T, args.walks, args.seed)
        print(f"[{d}] |λ|={lam:.4f} coverage={cover*100:.1f}% RMSE(実測 vs 設計)={rmse:.2f}")
        t = np.arange(T + 1)
        for row in M[:8]:                                     # 個々の walk 軌跡(薄く)
            ax.plot(t, row, color="0.75", lw=0.6, alpha=0.5)
        ax.plot(t, g, "k--", lw=1.6, label="設計モデル g_t")
        ax.plot(t, measured, color="#d23c3c", lw=1.8, label="実行RWの平均RTT")
        ax.axhline(args.mu, color="gray", ls=":", lw=1, alpha=0.7)
        ax.set_title(f"{d}  (RMSE={rmse:.1f})"); ax.set_xlabel("RWステップ t")
        ax.grid(alpha=0.25); ax.legend(fontsize=8)
    axes[0][0].set_ylabel("RTT")
    fig.suptitle(f"RTT設計モデル vs 実行RWのRTT軌跡(start={args.start}, T={T}歩で設計)", y=1.02)
    fig.tight_layout()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=140, bbox_inches="tight")
    print(f"[design_and_run] saved {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
