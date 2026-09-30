#!/usr/bin/env python3
# =============================================================================
# plot_contribution_stack.py — 「改善 pt の内訳」積み上げ棒グラフ（論文用）。
#
#   run_shapley_decomposition.py が出した CSV を読み、
#   LRU に対する改善を 4 要素に分解して積み上げる。
#   Shapley 値なので **積み上げの合計は総改善に厳密に一致する**。
#
#     Freq   使用回数            (θ)
#     Close  始点からの近さ       (β)
#     Deg    ノード次数           (γ)
#     Pool   ノード/エッジ別キャッシュ (§4.3)
#
#   ※ Base（LRU の最終アクセス時刻）は基準側なので内訳に含まない。
#
#   2 つの見せ方:
#     --mode gain      (既定) 0 を起点に「改善 pt」を積む。合計 = +6.56 / +7.05
#     --mode absolute  LRU の絶対ヒット率を土台に積んで提案の値まで到達させる。
#                      ARC と memo(上限) の水平線も引く。
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_contribution_stack.py
#     python3 base/proposed_cache/figs/plot_contribution_stack.py --mode absolute
# =============================================================================
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_FIGS = Path(__file__).resolve().parent
_RES = _FIGS.parent / "results"

# 論文 §4.2 の式の順（Freq^θ · Close^β · Deg^γ）＋ §4.3 のプール分離
ELEMS = ["Freq", "Close", "Deg"
        
        #  , "Pool"
        
         ]
LABEL = {
    "Freq":  "Freq  (usage count)",
    "Close": "Close  (proximity to start)",
    "Deg":   "Deg  (node degree)",
    # "Pool":  "Pool  (node/edge split)",
}
COLOR = {"Freq": "red", "Close": "grey",
         "Deg": "blue", 
        #  "Pool": "#4C72B0"
         }


def load(path):
    rows = list(csv.DictReader(open(path)))
    g = rows[0]["graph"]
    d = {r["element"]: float(r["shapley"]) for r in rows}
    sd = {r["element"]: float(r["shapley_sd"]) for r in rows}
    meta = dict(lru=float(rows[0]["lru"]), arc=float(rows[0]["arc"]),
                memo=float(rows[0]["memo"]), full=float(rows[0]["v_full"]),
                gain=float(rows[0]["gain_vs_empty"]))
    return g, d, sd, meta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", nargs="+",
                   default=[str(_RES / "shapley_VLDB.csv"),
                            str(_RES / "shapley_Amazon.csv")])
    p.add_argument("--mode", choices=["gain", "absolute"], default="gain")
    p.add_argument("--out", default=None)
    p.add_argument("--no-pct", action="store_true", help="セグメントの % を出さない")
    a = p.parse_args()

    out = a.out or str(_FIGS / f"contribution_stack_{a.mode}.png")
    data = [load(x) for x in a.csv]

    fig, ax = plt.subplots(figsize=(3.1 + 2.1 * len(data), 5.2))
    xs = list(range(len(data)))
    bw = 0.46

    span = (max(m["memo"] for _, _, _, m in data) if a.mode == "absolute"
            else max(m["gain"] for _, _, _, m in data))
    min_gap = span * 0.055          # 引き出しラベルの最小間隔

    for xi, (g, d, sd, meta) in zip(xs, data):
        bottom = meta["lru"] if a.mode == "absolute" else 0.0
        last_label_y = None
        if a.mode == "absolute":
            ax.bar(xi, meta["lru"], bw, color="#BFBFBF", edgecolor="white",
                   linewidth=0.8, label="LRU baseline" if xi == 0 else None)
            ax.annotate(f"LRU {meta['lru']:.1f}%", (xi, meta["lru"] / 2),
                        ha="center", va="center", fontsize=9, color="#333333")
        for e in ELEMS:
            h = d[e]
            ax.bar(xi, h, bw, bottom=bottom, color=COLOR[e],
                   edgecolor="white", linewidth=0.9,
                   label=LABEL[e] if xi == 0 else None)
            pct = 100 * h / meta["gain"]
            txt = f"{h:+.2f}" + ("" if a.no_pct else f"\n({pct:.1f}%)")
            inside = (h >= 0.55) and a.mode == "gain"
            if inside:
                ax.annotate(txt, (xi, bottom + h / 2), ha="center", va="center",
                            fontsize=8.5, color="white", fontweight="bold",
                            linespacing=1.25)
            elif a.mode == "absolute":
                # absolute は縦軸が 0〜80% なので帯が潰れる。値は gain 図と
                # 標準出力の表にあるので、ここでは色分けだけ見せて注記しない。
                pass
            else:                       # 薄いセグメントは外側に引き出す
                ly = bottom + h / 2
                if last_label_y is not None and ly - last_label_y < min_gap:
                    ly = last_label_y + min_gap
                last_label_y = ly
                ax.annotate(txt.replace("\n", " "),
                            (xi + bw / 2 + 0.06, ly),
                            xytext=(xi + bw / 2 + 0.06, ly),
                            ha="left", va="center", fontsize=8, color=COLOR[e])
                ax.plot([xi + bw / 2, xi + bw / 2 + 0.05],
                        [bottom + h / 2, ly], color=COLOR[e], lw=0.8)
            bottom += h

        top = bottom
        cap = (f"{meta['lru']:.1f}% → {meta['full']:.1f}%  ({meta['gain']:+.2f} pt)"
               if a.mode == "absolute" else f"total {meta['gain']:+.2f} pt")
        ax.annotate(cap, (xi, top),
                    ha="center", va="bottom",
                    fontsize=9.5 if a.mode == "absolute" else 10.5,
                    fontweight="bold", xytext=(0, 5), textcoords="offset points")

    if a.mode == "absolute":
        for xi, (g, d, sd, meta) in zip(xs, data):
            ax.hlines(meta["memo"], xi - bw / 2 - 0.1, xi + bw / 2 + 0.1,
                      color="black", ls="--", lw=1.2)
            ax.annotate(f"memo {meta['memo']:.1f}%",
                        (xi, meta["memo"]), ha="center", va="bottom",
                        xytext=(0, 3), textcoords="offset points", fontsize=8)
            ax.hlines(meta["arc"], xi - bw / 2 - 0.1, xi + bw / 2 + 0.1,
                      color="#444444", ls=":", lw=1.4)
            ax.annotate(f"ARC {meta['arc']:.1f}%",
                        (xi - bw / 2 - 0.14, meta["arc"]), ha="right",
                        va="center", fontsize=8)
        ax.set_ylabel("cache hit rate  [%]")
        ax.set_ylim(0, max(m["memo"] for _, _, _, m in data) * 1.14)
        title = ("Decomposition of the proposed method's hit rate\n"
                 "(LRU baseline + contribution of each element)")
    else:
        ax.axhline(0, color="black", lw=1)
        ax.set_ylabel("contribution to the gain over LRU  [pt]")
        ax.set_ylim(0, max(m["gain"] for _, _, _, m in data) * 1.20)
        title = ("Where the gain over LRU comes from\n"
                 "(Shapley values — the segments sum exactly to the total)")

    ax.set_xlim(-0.95 if a.mode == "absolute" else -0.62,
                len(data) - 0.38 + (0.55 if a.mode == "absolute" else 0))
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{g}\nLRU {m['lru']:.1f}% → proposed {m['full']:.1f}%"
                        for g, _, _, m in data], fontsize=9.5)
    ax.set_title(title, fontsize=11)
    handles, lbls = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], lbls[::-1], fontsize=8.5,
              loc="upper center", bbox_to_anchor=(0.5, -0.14),
              ncol=2, framealpha=0.95, borderaxespad=0.0)
    ax.grid(axis="y", alpha=0.3, lw=0.5)
    ax.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(f"[saved] {out}")

    # --- 論文に貼れる表を標準出力へ ---
    print()
    hdr = f"{'element':<26}"
    for g, _, _, _ in data:
        hdr += f"{g+' pt':>11}{g+' %':>9}"
    print(hdr)
    print("-" * len(hdr))
    for e in ELEMS:
        line = f"{LABEL[e]:<26}"
        for _, d, _, m in data:
            line += f"{d[e]:>+11.2f}{100*d[e]/m['gain']:>8.1f}%"
        print(line)
    print("-" * len(hdr))
    line = f"{'total':<26}"
    for _, d, _, m in data:
        line += f"{sum(d[e] for e in ELEMS):>+11.2f}{100.0:>8.1f}%"
    print(line)
    for g, d, _, m in data:
        diff = sum(d[e] for e in ELEMS) - m["gain"]
        print(f"  [check] {g}: Σ = {sum(d[e] for e in ELEMS):+.4f} / "
              f"総改善 = {m['gain']:+.4f}  (差 {diff:+.4f})")


if __name__ == "__main__":
    main()
