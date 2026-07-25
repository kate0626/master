"""
walks_*.jsonl(各 walk の path/rtt)から RTT を「遷移ステップごと」に可視化。
  左 : ステップ t ごとの RTT g_t = 各歩の RTT を walk 平均(= 漸近モデル above/below/zigzag の形)
  右 : ステップ t までの累積 RTT 時間(= cumsum g_t。walk が進むほどの実行時間)
使い方:
  python3 plot_rtt_time.py --walks results/walks_vldb_walks100_alpha0.05_above.jsonl \
      --out results/rtt_time_step_vldb_walks100_alpha0.05.png
"""
from __future__ import annotations

import argparse
import json
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--walks", required=True, help="walks_*.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-walks", type=int, default=5,
                    help="各ステップで平均に必要な最小 walk 本数(尾の雑音を切る)")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.walks)]
    series = [np.array(r["rtt"], dtype=float) for r in rows]     # 始点 t=0 を含む
    N = len(series)
    maxlen = max(len(s) for s in series)

    # NaN パディングして 2D 化 → 各ステップの平均(その歩に到達した walk のみ)
    M = np.full((N, maxlen), np.nan)
    for i, s in enumerate(series):
        M[i, :len(s)] = s
    counts = np.sum(~np.isnan(M), axis=0)                        # 各 t に到達した walk 数
    g_t = np.nanmean(M, axis=0)                                  # ステップごと平均 RTT

    # 十分な本数がある範囲だけ表示(尾は雑音)
    thr = max(args.min_walks, int(0.05 * N))
    valid = counts >= thr
    Tcut = int(np.max(np.where(valid))) if valid.any() else maxlen - 1
    t = np.arange(Tcut + 1)
    g = g_t[:Tcut + 1]
    cum = np.cumsum(g)                                           # ステップまでの累積 RTT

    print(f"[plot] walks={N} maxlen={maxlen} 表示Tcut={Tcut}(>= {thr}本) "
          f"g_t[0]={g[0]:.1f} -> g_t[Tcut]={g[-1]:.1f}  累積(t=Tcut)={cum[-1]:.1f}")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.8))

    for s in series[:40]:                                        # 個々の walk(薄く)
        tt = np.arange(min(len(s), Tcut + 1))
        ax1.plot(tt, s[:Tcut + 1], color="0.8", lw=0.5, alpha=0.5)
    ax1.plot(t, g, "-o", ms=3, color="#d23c3c", label="平均 RTT g_t")
    mu_est = np.nanmean(M)
    ax1.axhline(mu_est, color="k", ls="--", lw=1, alpha=0.6, label=f"μ≈{mu_est:.1f}")
    ax1.set_title("遷移ステップごとの RTT  g_t(walk 平均)= 漸近モデルの形")
    ax1.set_xlabel("遷移ステップ t"); ax1.set_ylabel("RTT / ステップ")
    ax1.legend(fontsize=9); ax1.grid(alpha=0.25)

    ax2.plot(t, cum, "-o", ms=3, color="#1D9E75")
    ax2.set_title("累積 RTT 時間(ステップ t まで = Σ g_t)")
    ax2.set_xlabel("遷移ステップ t"); ax2.set_ylabel("累積 RTT 時間")
    ax2.grid(alpha=0.25)

    out = args.out or (str(Path(args.walks).with_suffix("")) + "_rtt_time_step.png")
    fig.suptitle(f"RTT のステップ推移  ({Path(args.walks).name})", y=1.02)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"[plot] saved {Path(out).resolve()}")


if __name__ == "__main__":
    main()
