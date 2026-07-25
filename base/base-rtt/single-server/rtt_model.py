"""
RTT 漸近「設計」モデル(base-rtt) ―― above / below / zigzag の 3 形状だけ。
================================================================================
体験 RTT の 1 歩ごとの期待値を

    g_t = μ + a · λ^t            (a = ±amp, λ の正負で単調 / 交互)

として直接パラメトライズする。3 つのノブを "自分で" 決められる:

  ・振幅        amp   … 初期の μ からのズレ幅(|g_0 − μ|)
  ・向き        direction ∈ {above, below, zigzag}
                        above  : a=+amp, λ>0   → 上から単調に μ へ
                        below  : a=−amp, λ>0   → 下から単調に μ へ
                        zigzag : a=+amp, λ<0   → 毎歩 高低交互に減衰
  ・収束速度    converge_steps N … 「約 N 歩で収束」を指定 → |λ| = 0.01^(1/N)
                                   (t=N で λ^N=0.01 = 99% 収束)。lam 直接指定も可。

歩数 R は収束速度に合わせて自動割り当て(既定 = 1.3·N、指定も可)。

注) これは "設計 / 目標" 曲線。実 RW(design_and_run)に node-consistent に載せると
    振幅=amp / 向き は直接対応するが、速度 λ は「距離場の粒度」で近似的に合わせる
    (任意の λ をノード割り当てで厳密に作れるわけではない = λ はグラフの混合速度に縛られる)。
"""
from __future__ import annotations

import argparse

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

DIRECTIONS = ["above", "below", "zigzag"]


def lam_from_converge_steps(n: int, decades: float = 2.0) -> float:
    """約 n 歩で収束(t=n で 10^-decades まで減衰)する |λ|。"""
    return float(10.0 ** (-decades / max(1, n)))


def auto_steps(n: int) -> int:
    """収束速度に合わせた既定の表示 / 消費歩数 R。"""
    return int(round(1.3 * n)) + 2


def make_curve(mu: float, amp: float, direction: str, lam_abs: float, steps: int):
    """設計曲線 g_t と走行平均 Ā_t を返す。平均は常に μ に収束。

    above  : μ + amp·λ^t       上から単調
    below  : μ − amp·λ^t       下から単調
    zigzag : μ + amp·(−λ)^t    毎歩 高低交互
    返り値: (t, g_t, Ā_t, λ_signed)
    """
    t = np.arange(steps)
    if direction == "above":
        lam = lam_abs
        g = mu + amp * (lam ** t)
    elif direction == "below":
        lam = lam_abs
        g = mu - amp * (lam ** t)
    elif direction == "zigzag":
        lam = -lam_abs
        g = mu + amp * (lam ** t)
    else:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    A = np.cumsum(g) / np.arange(1, steps + 1)
    return t, g, A, lam


def main() -> None:
    ap = argparse.ArgumentParser(description="RTT 設計モデル(above/below/zigzag)。")
    ap.add_argument("--mu", type=float, default=50.0)
    ap.add_argument("--amp", type=float, default=40.0)
    ap.add_argument("--direction", default="above", choices=DIRECTIONS)
    ap.add_argument("--converge-steps", type=int, default=100, help="約N歩で収束")
    ap.add_argument("--lam", type=float, default=None, help="|λ| を直接指定(あれば優先)")
    ap.add_argument("--steps", type=int, default=None, help="表示歩数R(既定=1.3N)")
    ap.add_argument("--out", default="results/rtt_model.png")
    ap.add_argument("--demo", action="store_true", help="速度・振幅・向きの制御を一覧表示")
    args = ap.parse_args()

    if args.demo:
        make_demo(args.mu, args.out)
        return

    lam_abs = args.lam if args.lam is not None else lam_from_converge_steps(args.converge_steps)
    steps = args.steps or auto_steps(args.converge_steps)
    t, g, A, lam = make_curve(args.mu, args.amp, args.direction, lam_abs, steps)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7))
    ax1.plot(t, g, "o-", ms=3, color="#185FA5")
    ax2.plot(t, A, "-", color="#185FA5")
    for ax in (ax1, ax2):
        ax.axhline(args.mu, color="k", ls="--", lw=1, alpha=0.6)
        ax.grid(alpha=0.25); ax.set_xlabel("RWステップ t")
    ax1.set_title(f"g_t = μ + a·λ^t  (dir={args.direction}, |λ|={lam_abs:.4f}, amp={args.amp}, R={steps})")
    ax1.set_ylabel("g_t"); ax2.set_ylabel("Ā_t"); ax2.set_title("走行平均 Ā_t → μ")
    fig.tight_layout(); fig.savefig(args.out, dpi=140)
    print(f"[model] dir={args.direction} |λ|={lam_abs:.4f} amp={args.amp} R={steps} -> {args.out}")


def make_demo(mu: float, out: str) -> None:
    """3 ノブ(速度・振幅・向き)を自分で決められることの一覧。"""
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    # (1) 収束速度: 遅 / 中 / 速(振幅・向き固定=above)
    ax = axes[0]
    for n, col in [(400, "#6D2E46"), (100, "#185FA5"), (25, "#1D9E75")]:
        lam = lam_from_converge_steps(n)
        t, g, _, _ = make_curve(mu, 40, "above", lam, auto_steps(400))  # 共通長で比較
        ax.plot(t, g, lw=1.8, color=col, label=f"約{n}歩で収束 (|λ|={lam:.3f})")
    ax.set_title("① 収束速度を指定"); ax.legend(fontsize=8)

    # (2) 振幅: 大 / 中 / 小(速度・向き固定)
    ax = axes[1]
    lam = lam_from_converge_steps(100)
    for amp, col in [(60, "#6D2E46"), (30, "#185FA5"), (10, "#1D9E75")]:
        t, g, _, _ = make_curve(mu, amp, "above", lam, auto_steps(100))
        ax.plot(t, g, lw=1.8, color=col, label=f"amp={amp}")
    ax.set_title("② 振幅を指定"); ax.legend(fontsize=8)

    # (3) 向き: above / below / zigzag(速度・振幅固定)
    ax = axes[2]
    lam = lam_from_converge_steps(60)
    for d, col in [("above", "#d23c3c"), ("below", "#2f6fd0"), ("zigzag", "#1f9e50")]:
        t, g, _, _ = make_curve(mu, 40, d, lam, auto_steps(60))
        ax.plot(t, g, lw=1.6, color=col, label=d)
    ax.set_title("③ above / below / zigzag を指定"); ax.legend(fontsize=8)

    for ax in axes:
        ax.axhline(mu, color="k", ls="--", lw=1, alpha=0.6)
        ax.grid(alpha=0.25); ax.set_xlabel("RWステップ t"); ax.set_ylabel("g_t")
    fig.suptitle("RTT 設計モデル: 収束速度・振幅・向き を自分で決める(歩数Rは速度に合わせる)", y=1.02)
    fig.tight_layout(); fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"[model] demo -> {out}")


if __name__ == "__main__":
    main()
