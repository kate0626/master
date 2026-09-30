#!/usr/bin/env python3
# =============================================================================
# plot_hitrate_and_time.py — ヒット率 + 実行時間（RTT 模擬）の 1 枚図。
#
#   ヒット率だけでは「どれだけ速くなるか」が見えないので、同じ実験の
#   回数データ（results/time_stats_paperseed.csv、collect_time_stats.py が生成）
#   から実行時間を計算して並べる。
#
#   時間モデル（ユーザ指定）:
#       実行時間 = 元の RW 実行時間 + (RTT を払う遷移回数) × RTT
#         ・元の RW 実行時間 = 総遷移回数 × 1 遷移あたりのローカル処理時間
#           （既定 2.27 ms/遷移 = 実プロトタイプ実測 (walk_time−auth_time)/遷移）
#         ・RTT を払う遷移 = 認可キャッシュミス（ヒットなら手元で判定でき通信不要）
#         ・RTT は 50 ms と 100 ms の 2 通りを模擬
#
#   ヒット率が上がる = ミスが減る = 支払う RTT の回数が減る、という一本道なので
#   ヒット率改善がそのまま実行時間の短縮として読める。
#
#   使い方:
#     python3 base/proposed_cache/figs/collect_time_stats.py   # 先に回数を測る
#     python3 base/proposed_cache/figs/plot_hitrate_and_time.py
# =============================================================================
from __future__ import annotations

import argparse
import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"

# 日本語フォント（無ければ英字にフォールバック）
for _f in ["Hiragino Sans", "Hiragino Maru Gothic Pro", "Noto Sans CJK JP",
           "IPAGothic", "AppleGothic"]:
    if _f in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        matplotlib.rcParams["font.family"] = _f
        break
matplotlib.rcParams["axes.unicode_minus"] = False

# (CSV の policy, 表示名, 色)
BARS = [
    ("none", "認可なし",       "#D9D9D9"),
    ("lru",  "既存手法",       "grey"),
    ("prop", "提案手法",       "red"),
    ("memo", "理想的キャッシュ", "#4C72B0"),
]
GRAPH_ORDER = ["VLDB", "Amazon"]
# 実行時間には出さない方式（認可なしは時間の比較対象から外す）
TIME_SKIP = {"none"}


def load(path: Path):
    per = defaultdict(list)
    for r in csv.DictReader(open(path)):
        per[(r["graph"], r["policy"])].append(
            dict(hops=int(r["hops"]), misses=int(r["misses"]),
                 remote=int(r["remote"]), hit=100 * float(r["hit_rate"])))
    return per


def times(rows, hop_ms: float, rtt_ms: float, charge: str):
    """seed ごとの実行時間 [s] と、その内訳（ベース / RTT）を返す。"""
    key = "remote" if charge == "remote-miss" else "misses"
    base = [r["hops"] * hop_ms / 1000.0 for r in rows]
    rtt = [r[key] * rtt_ms / 1000.0 for r in rows]
    return base, rtt


def plot_overlay(a, per, graphs):
    """ヒット率の棒に、実行時間の折れ線を第2軸で重ねた 1 枚図。"""
    rtt = a.rtt[0]
    nb, w = len(BARS), 0.20
    fig, ax = plt.subplots(figsize=(4.2 + 3.4 * len(graphs), 5.6))
    ax2 = ax.twinx()

    rows_txt = []
    for gi, g in enumerate(graphs):
        xs, ts, labs = [], [], []
        for k, (pol, lbl, col) in enumerate(BARS):
            rows = per[(g, pol)]
            x = gi + (k - (nb - 1) / 2) * w
            hv = [r["hit"] for r in rows]
            m = st.mean(hv)
            ax.bar(x, m, w * 0.92, yerr=st.stdev(hv) if len(hv) > 1 else 0,
                   capsize=3, color=col, edgecolor="white", linewidth=0.7,
                   label=lbl if gi == 0 else None, zorder=2)
            if m < 0.5:
                ax.annotate("0.0", (x, m), ha="center", va="bottom",
                            xytext=(0, 4), textcoords="offset points",
                            fontsize=8.5, fontweight="bold")
            if pol in TIME_SKIP:
                continue
            b, r = times(rows, a.hop_ms, rtt, a.charge)
            t = st.mean([x1 + y1 for x1, y1 in zip(b, r)])
            xs.append(x); ts.append(t); labs.append(pol)
            rows_txt.append((g, lbl, m, t))
        ax2.plot(xs, ts, "-o", color="#111111", lw=2.0, ms=7, zorder=5,
                 label=f"推定実行時間（RTT={rtt:g} ms）" if gi == 0 else None)
        for x, t in zip(xs, ts):
            # 棒の天面と数字が重なりやすいので白地を敷く
            ax2.annotate(f"{t:.0f} s", (x, t), ha="center", va="bottom",
                         xytext=(0, 9), textcoords="offset points",
                         fontsize=8.5, fontweight="bold", color="#111111",
                         zorder=6,
                         bbox=dict(boxstyle="round,pad=0.18", fc="white",
                                   ec="none", alpha=0.85))
        # 既存手法 → 提案手法 の短縮量は図中に表示しない。
        # i_lru, i_pro = labs.index("lru"), labs.index("prop")
        # t_lru, t_pro = ts[i_lru], ts[i_pro]
        # ax2.annotate(f"{t_pro - t_lru:+.0f} s ({100 * (t_pro - t_lru) / t_lru:+.1f}%)",
        #              ((xs[i_lru] + xs[i_pro]) / 2, (t_lru + t_pro) / 2),
        #              ha="center", va="top", xytext=(0, -14),
        #              textcoords="offset points", fontsize=9, color="#B00000",
        #              bbox=dict(boxstyle="round,pad=0.3", fc="white",
        #                        ec="#B00000", lw=0.7), zorder=6)

    ax.set_xticks(range(len(graphs)))
    ax.set_xticklabels(graphs, fontsize=11)
    ax.set_xlim(-0.55, len(graphs) - 0.45)
    ax.set_ylim(0, 100)
    ax.set_ylabel("キャッシュのヒット率 (%)  ← 棒")
    ax2.set_ylim(0, max(t for _, _, _, t in rows_txt) * 1.30)
    ax2.set_ylabel(f"推定実行時間 (s)  → 折れ線（RTT = {rtt:g} ms）")
    ax.grid(axis="y", alpha=0.3, lw=0.5); ax.set_axisbelow(True)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8.5, loc="upper center",
              bbox_to_anchor=(0.5, -0.09), ncol=3, framealpha=0.95,
              borderaxespad=0.0)

    charge_txt = "全ミス" if a.charge == "all-miss" else "遠隔ミスのみ"
    ax.set_title("ヒット率と実行時間（α=0.1, RW 1,000本, 容量100, 始点0, seed 5点）\n"
                 f"実行時間 = 元の RW 実行時間（{a.hop_ms:g} ms/遷移 × 遷移回数）"
                 f" + {charge_txt}回数 × {rtt:g} ms", fontsize=11)
    fig.tight_layout()
    fig.savefig(a.out, dpi=200)
    print(f"[saved] {a.out}")
    print()
    print(f"{'graph':<8}{'方式':<10}{'hit[%]':>9}{'時間[s]':>10}")
    print("-" * 37)
    for g, lbl, m, t in rows_txt:
        print(f"{g:<8}{lbl:<10}{m:>9.2f}{t:>10.1f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stats", default=str(_RES / "time_stats_paperseed.csv"))
    p.add_argument("--rtt", type=float, nargs="+", default=[50.0, 100.0],
                   help="模擬する RTT [ms]")
    p.add_argument("--hop-ms", type=float, default=2.27,
                   help="1 遷移あたりのローカル処理時間 [ms]（既定は実測値）")
    p.add_argument("--charge", choices=["all-miss", "remote-miss"],
                   default="all-miss",
                   help="RTT を課す対象。all-miss=全ミス（既定）、"
                        "remote-miss=他サーバ所有のミスのみ")
    p.add_argument("--layout", choices=["panels", "overlay"], default="panels",
                   help="panels=ヒット率と実行時間を別パネル、"
                        "overlay=ヒット率の棒に実行時間の折れ線を重ねる（1枚）")
    p.add_argument("--out", default=None)
    a = p.parse_args()
    if a.out is None:
        a.out = str(_FIGS / ("hitrate_with_time.png" if a.layout == "overlay"
                             else "hitrate_and_time.png"))

    per = load(Path(a.stats))
    graphs = [g for g in GRAPH_ORDER if (g, "lru") in per]

    if a.layout == "overlay":
        plot_overlay(a, per, graphs)
        return

    fig, axes = plt.subplots(1, 1 + len(graphs),
                             figsize=(5.4 + 4.6 * len(graphs), 5.6),
                             gridspec_kw={"width_ratios": [1.15] + [1] * len(graphs)})

    # ---------- パネル1: ヒット率 ----------
    ax = axes[0]
    nb, w = len(BARS), 0.20
    for gi, g in enumerate(graphs):
        for k, (pol, lbl, col) in enumerate(BARS):
            v = [r["hit"] for r in per[(g, pol)]]
            x = gi + (k - (nb - 1) / 2) * w
            m = st.mean(v)
            ax.bar(x, m, w * 0.92, yerr=st.stdev(v) if len(v) > 1 else 0,
                   capsize=3, color=col, edgecolor="white", linewidth=0.7,
                   label=lbl if gi == 0 else None)
            if m < 0.5:      # 高さ 0 の棒は見えないので注記
                ax.annotate("0.0", (x, m), ha="center", va="bottom",
                            xytext=(0, 4), textcoords="offset points",
                            fontsize=8.5, fontweight="bold")
    ax.set_xticks(range(len(graphs)))
    ax.set_xticklabels(graphs, fontsize=11)
    ax.set_ylim(0, 100)
    ax.set_ylabel("キャッシュのヒット率 (%)")
    ax.set_title("① ヒット率", fontsize=12)
    ax.legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.09),
              ncol=2, framealpha=0.95, borderaxespad=0.0)
    ax.grid(axis="y", alpha=0.3, lw=0.5)
    ax.set_axisbelow(True)

    # ---------- パネル2以降: 実行時間 ----------
    table = []
    tops = []
    single = (len(a.rtt) == 1)      # RTT 1 通りなら棒を方式名で並べた方が読みやすい
    t_bars = [b for b in BARS if b[0] not in TIME_SKIP]
    nb = len(t_bars)
    bw = 0.62 if single else w * 0.92
    for gi, g in enumerate(graphs):
        ax = axes[1 + gi]
        for ri, rtt in enumerate(a.rtt):
            for k, (pol, lbl, col) in enumerate(t_bars):
                rows = per[(g, pol)]
                b, r = times(rows, a.hop_ms, rtt, a.charge)
                tot = [x + y for x, y in zip(b, r)]
                x = k if single else ri + (k - (nb - 1) / 2) * w
                mb, mt = st.mean(b), st.mean(tot)
                # 下段 = 元の RW 実行時間、上段 = 認可の RTT 分
                ax.bar(x, mb, bw, color=col, alpha=0.35,
                       edgecolor="white", linewidth=0.7,
                       label="元の RW 実行時間" if (gi == 0 and ri == 0 and k == 0) else None)
                ax.bar(x, mt - mb, bw, bottom=mb, color=col,
                       edgecolor="white", linewidth=0.7,
                       yerr=st.stdev(tot) if len(tot) > 1 else 0, capsize=3,
                       label="認可の RTT 分" if (gi == 0 and ri == 0 and k == 0) else None)
                ax.annotate(f"{mt:.0f}", (x, mt), ha="center", va="bottom",
                            xytext=(0, 5), textcoords="offset points",
                            fontsize=8, fontweight="bold")
                tops.append(mt)
                table.append((g, rtt, lbl, st.mean([q["hit"] for q in rows]),
                              st.mean([q["misses"] for q in rows]), mb, mt))
            # 既存手法 → 提案手法 の短縮量
            t_lru = st.mean([sum(v) for v in zip(*times(per[(g, "lru")], a.hop_ms, rtt, a.charge))])
            t_pro = st.mean([sum(v) for v in zip(*times(per[(g, "prop")], a.hop_ms, rtt, a.charge))])
            ax.annotate(f"提案−既存\n{t_pro - t_lru:+.0f} s ({100*(t_pro-t_lru)/t_lru:+.1f}%)",
                        (0.5 if single else ri + 0.5 * w, max(t_lru, t_pro)),
                        ha="center", va="bottom",
                        xytext=(0, 26), textcoords="offset points", fontsize=8.5,
                        color="#B00000",
                        bbox=dict(boxstyle="round,pad=0.3", fc="white",
                                  ec="#B00000", lw=0.7))
        if single:
            ax.set_xticks(range(len(t_bars)))
            ax.set_xticklabels([lbl for _, lbl, _ in t_bars], fontsize=9.5)
        else:
            ax.set_xticks(range(len(a.rtt)))
            ax.set_xticklabels([f"RTT = {r:g} ms" for r in a.rtt], fontsize=11)
        ax.set_ylabel("推定実行時間 (s)")
        ax.set_title(f"② 実行時間（{g}" + (f", RTT = {a.rtt[0]:g} ms）" if single else "）"),
                     fontsize=12)
        ax.grid(axis="y", alpha=0.3, lw=0.5)
        ax.set_axisbelow(True)
        if gi == 0:
            ax.legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.09),
                      ncol=2, framealpha=0.95, borderaxespad=0.0)
    for gi in range(len(graphs)):
        axes[1 + gi].set_ylim(0, max(tops) * 1.22)

    charge_txt = "全ミス" if a.charge == "all-miss" else "遠隔ミスのみ"
    fig.suptitle(
        "ヒット率と実行時間（α=0.1, RW 1,000本, 容量100, 始点0, seed 5点）\n"
        f"実行時間 = 元の RW 実行時間（{a.hop_ms:g} ms/遷移 × 遷移回数）"
        f" + {charge_txt}回数 × RTT",
        fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(a.out, dpi=200)
    print(f"[saved] {a.out}")

    # --- 数値テーブル ---
    print()
    hdr = (f"{'graph':<8}{'RTT[ms]':>8}{'方式':<10}{'hit[%]':>9}{'miss回':>9}"
           f"{'ベース[s]':>11}{'合計[s]':>10}")
    print(hdr); print("-" * 66)
    for g, rtt, lbl, hit, miss, mb, mt in table:
        print(f"{g:<8}{rtt:>8.0f}{lbl:<10}{hit:>9.2f}{miss:>9.0f}{mb:>11.1f}{mt:>10.1f}")


if __name__ == "__main__":
    main()
