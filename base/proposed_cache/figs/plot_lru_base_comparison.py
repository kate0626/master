#!/usr/bin/env python3
# =============================================================================
# plot_lru_base_comparison.py — 「LRU 土台がある時 / ない時」の比較図。
#
#   提案手法は  Priority(e) = Base(e) + w · Value(e)  で、Base = LRU の最終アクセス時刻。
#   Base を外す（= 純スコア退去）と何が起きるかを、LRU 単体・提案 と並べて示す。
#
#     ① LRU only        スコアなし・recency のみ            （= Base のみ）
#     ② score only      LRU 土台なし・スコアのみで退去        （= Value のみ）
#     ③ proposed        LRU 土台 + スコア                    （= Base + w·Value）
#
#   これにより「recency と構造スコアは相補的で、両方必要」を1枚で示せる。
#   論文 §4.2 の「加法形式にしたのは既存方式の優先順位を維持したまま
#   構造情報による補正のみを行うため」という設計判断の直接の裏づけになる。
#
#   入力: run_loo_ablation.py --out-raw の CSV（"−Base (LRU最終アクセス)" 行が必要）
#
#   使い方（既定パスで実行するだけ）:
#     python3 base/proposed_cache/figs/plot_lru_base_comparison.py
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

ROW = {
    "none":  "none (キャッシュなし)",
    "lru":   "LRU (共有)",
    "arc":   "ARC (共有)",
    "memo":  "memo (上限)",
    "prop":  "提案 (全要素)",
    "nobase": "−Base (LRU最終アクセス)",
}
BARS = [
    ("none",   "none\n(no cache)",                "#D9D9D9"),
    ("lru",    "LRU only\n(recency, no score)",   "grey"),
    # ("nobase", "score only\n(no LRU base)",       "#DD8452"),
    ("prop",   "proposed\n(LRU base + score)",    "red"),
    ("memo",   "memo\n(unlimited, ceiling)",      "#4C72B0"),
]


def load(path):
    per = defaultdict(list)
    graph = "?"
    for r in csv.DictReader(open(path)):
        graph = r["graph"]
        per[r["name"]].append(float(r["overall_hit"]))
    need = {k for k, _, _ in BARS}
    missing = [k for k in need if not per.get(ROW[k])]
    if missing:
        raise SystemExit(
            f"{path}: 行が足りません {missing}\n"
            f"  → run_loo_ablation.py を回し直して --out-raw を作り直してください")
    return graph, {k: per[nm] for k, nm in ROW.items() if per.get(nm)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", nargs="+",
                   default=[str(_RES / "loo_VLDB_paperseed_raw.csv"),
                            str(_RES / "loo_Amazon_paperseed_raw.csv")])
    p.add_argument("--out", default=str(_FIGS / "lru_base_comparison.png"))
    p.add_argument("--values", action="store_true",
                   help="各棒の上に数値を出す（既定は none の棒のみ）")
    a = p.parse_args()

    data = [load(x) for x in a.raw]
    fig, ax = plt.subplots(figsize=(3.0 + 3.0 * len(data), 5.2))

    nb = len(BARS)
    w = min(0.24, 0.88 / nb)
    centers = list(range(len(data)))
    for gi, (g, d) in zip(centers, data):
        for k, (key, lbl, col) in enumerate(BARS):
            x = gi + (k - (nb - 1) / 2) * w
            m, s = st.mean(d[key]), st.stdev(d[key])
            ax.bar(x, m, w * 0.92, yerr=s, capsize=3, color=col,
                   edgecolor="white", linewidth=0.7,
                   label=lbl if gi == 0 else None)
            if a.values or m < 0.5:   # 高さ 0 の棒は見えないので必ず注記する
                ax.annotate(f"{m:.1f}", (x, m), ha="center", va="bottom",
                            xytext=(0, 4 + (12 if s > 0.8 else 0)),
                            textcoords="offset points", fontsize=8.5,
                            fontweight="bold")
            # 旧: 常時注記していた版（--values で復活）
            # ax.annotate(f"{m:.1f}", (x, m), ha="center", va="bottom",
            #             xytext=(0, 4 + (12 if s > 0.8 else 0)),
            #             textcoords="offset points", fontsize=9,
            #             fontweight="bold")
        # 参照線
        # ax.hlines(st.mean(d["memo"]), gi - 0.42, gi + 0.42,
        #           color="black", ls="--", lw=1.2)
        # ax.annotate(f"memo {st.mean(d['memo']):.1f}%", (gi, st.mean(d["memo"])),
        #             ha="center", va="bottom", xytext=(0, 3),
        #             textcoords="offset points", fontsize=8)
        # ax.hlines(st.mean(d["arc"]), gi - 0.42, gi + 0.42,
        #           color="#3A3A3A", ls=":", lw=1.4)
        # ax.annotate(f"ARC {st.mean(d['arc']):.1f}%", (gi + 0.44, st.mean(d["arc"])),
        #             ha="left", va="center", fontsize=8)

    # 縦軸は 0-100%（ヒット率のフルレンジ）に固定。
    # 旧: データ最大に合わせて自動伸縮していた版
    # _tops = [st.mean(d[k]) + st.stdev(d[k])
    #          for _, d in data for k, _, _ in BARS]
    # ax.set_ylim(0, max(_tops) * 1.16)
    ax.set_ylim(0, 100)
    # note_y = ax.get_ylim()[1] * 0.88
    # for gi, (g, d) in zip(centers, data):
    #     d_lru = st.mean([x - y for x, y in zip(d["prop"], d["lru"])])
    #     d_nob = st.mean([x - y for x, y in zip(d["prop"], d["nobase"])])
    #     ax.annotate(f"proposed − LRU only   = {d_lru:+.2f} pt\n"
    #                 f"proposed − score only = {d_nob:+.2f} pt",
    #                 (gi, note_y), ha="center", va="center", fontsize=8.5,
    #                 bbox=dict(boxstyle="round,pad=0.35", fc="white",
    #                           ec="#999999", lw=0.7))

    ax.set_xticks(centers)
    ax.set_xticklabels([g for g, _ in data], fontsize=11)
    ax.set_ylabel("cache hit rate  [%]")
    ax.set_xlim(-0.62, len(data) - 0.38 + 0.35)
    ax.set_title("Cache hit rate by policy\n"
                 "(α=0.1, 1,000 RWers, capacity 100, start 0)", fontsize=11)
    ax.legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.10),
              ncol=min(nb, 4), framealpha=0.95, borderaxespad=0.0)
    ax.grid(axis="y", alpha=0.3, lw=0.5)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(a.out, dpi=200)
    print(f"[saved] {a.out}")

    print()
    hdr = (f"{'graph':<9}{'LRU only':>11}{'score only':>13}{'proposed':>11}"
           f"{'ARC':>8}{'memo':>8}{'prop−LRU':>11}{'prop−score':>12}")
    print(hdr); print("-" * len(hdr))
    for g, d in data:
        if "nobase" not in d:
            continue
        d_lru = [x - y for x, y in zip(d["prop"], d["lru"])]
        d_nob = [x - y for x, y in zip(d["prop"], d["nobase"])]
        print(f"{g:<9}{st.mean(d['lru']):>11.2f}{st.mean(d['nobase']):>13.2f}"
              f"{st.mean(d['prop']):>11.2f}{st.mean(d['arc']):>8.2f}"
              f"{st.mean(d['memo']):>8.2f}"
              f"{st.mean(d_lru):>+11.2f}{st.mean(d_nob):>+12.2f}")
    print()
    for g, d in data:
        if "nobase" not in d:
            continue
        diff = st.mean(d["nobase"]) - st.mean(d["lru"])
        verdict = ("スコア単体では LRU に届かない"
                   if diff < 0 else "スコア単体でも LRU を上回る")
        print(f"  {g}: score only − LRU only = {diff:+.2f} pt  → {verdict}")


if __name__ == "__main__":
    main()
