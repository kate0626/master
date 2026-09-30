#!/usr/bin/env python3
# =============================================================================
# plot_compare.py — run_all_graphs.sh の結果を、グラフ横断で関連研究と比べる
#
#   入力: <OUT_ROOT>/<graph>_a<α>_seed<s>/summary.tsv（RESULT 行）と servers_structured*_*.csv
#   出力（<OUT_ROOT> に保存）:
#     compare_table.csv          グラフ × α × 方策の平均・標準偏差・seed 数・Belady との差の詰め具合
#     compare_hit_a<α>.png       グラフごとのヒット率（点 = seed 平均、横線 = ±1 標準偏差、
#                                縦の点線 = Belady〔上限〕、灰色の縦線 = LRU）
#     compare_gap_a<α>.png       Belady との差をどれだけ詰めたか（(方策 − LRU) / (Belady − LRU) × 100 %）
#     compare_weights_a<α>.png   structured の学習後の重みの比（|w_O| = 1）。グラフ × サーバの役割
#
#   使い方:
#     python3 base/proposed_cache/adaptive/plot_compare.py base/proposed_cache/adaptive/results/allgraphs
# =============================================================================
from __future__ import annotations

import csv
import math
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm  # noqa: E402

_AVAILABLE = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP"):
    if _f in _AVAILABLE:
        matplotlib.rcParams["font.family"] = _f
        break

# 表示順（上から）。関連研究 → 提案。上限の Belady は線で描くので点には含めない
POLICY_ORDER = ["lru", "arc", "lfu", "lecar", "cacheus", "proposed",
                "structured_oonly", "structured", "structured_prior", "structured_edc", "structured_pair"]
LABEL = {"lru": "LRU", "arc": "ARC", "lfu": "LFU", "lecar": "LeCaR", "cacheus": "CACHEUS（簡略版）",
         "proposed": "現行 proposed（固定重み）", "structured_oonly": "O だけ（学習なし）",
         "structured": "structured（初期重み 0）", "structured_prior": "structured（分析の向きを初期値）",
         "structured_edc": "structured（減衰回数）", "structured_pair": "structured（組単位の学習）",
         "belady": "Belady（上限）"}
OURS = {"structured", "structured_prior", "structured_edc", "structured_pair"}
GRAPH_ORDER = ["er_mid", "amazon0601", "ba_mid", "vldb", "soc-slashdot", "email-enron-connected"]
GRAPH_LABEL = {"er_mid": "er_mid", "amazon0601": "amazon", "ba_mid": "ba_mid", "vldb": "vldb",
               "soc-slashdot": "slashdot", "email-enron-connected": "enron"}
TERMS = ["O", "R", "H", "H_node", "D_cond"]
TERM_LABEL = {"O": "回数 O", "R": "経過時間 R", "H": "距離 H", "H_node": "距離 H\n(ノード分)",
              "D_cond": "次数 D\n(条件付き)"}

INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"
OURS_C, BASE_C, BELADY_C = "#2a78d6", "#8a8980", "#e34948"
DIVERGING = LinearSegmentedColormap.from_list("div", ["#e34948", "#f0efec", "#2a78d6"])

RESULT_RE = re.compile(r"(\w+)=([-\d.]+)")
DIR_RE = re.compile(r"^(?P<g>.+)_a(?P<a>[\d.]+)_seed(?P<s>\d+)$")


def load(root: Path):
    hit = defaultdict(list)          # (graph, alpha, policy) -> [overall]
    weights = defaultdict(list)      # (graph, alpha, role, term) -> [比]
    for d in sorted(root.iterdir()):
        m = DIR_RE.match(d.name)
        if not d.is_dir() or not m:
            continue
        g, a = m["g"], float(m["a"])
        summ = d / "summary.tsv"
        if summ.exists():
            for line in summ.read_text().splitlines():
                if not line.startswith("RESULT"):
                    continue
                parts = line.split("\t")
                kv = dict(RESULT_RE.findall("\t".join(parts[2:])))
                hit[(g, a, parts[1])].append(float(kv["overall"]))
        for f in d.glob("servers_structured_start*.csv"):
            for r in csv.DictReader(f.open(encoding="utf-8")):
                for t in TERMS:
                    for col in (f"ratio_all_{t}", f"ratio_node_{t}", f"ratio_edge_{t}"):
                        v = r.get(col)
                        if v not in (None, ""):
                            weights[(g, a, r["role"], t)].append(float(v))
                            break
    return hit, weights


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def ms(xs):
    return (st.mean(xs), st.stdev(xs) if len(xs) > 1 else 0.0, len(xs))


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: plot_compare.py <OUT_ROOT>")
    root = Path(sys.argv[1])
    hit, weights = load(root)
    if not hit:
        raise SystemExit(f"RESULT 行が見つかりません: {root}")
    alphas = sorted({k[1] for k in hit}, reverse=True)

    # --- 表 ----------------------------------------------------------------------
    rows = []
    for (g, a, p), xs in sorted(hit.items()):
        m, sd, n = ms(xs)
        lru = hit.get((g, a, "lru"))
        bel = hit.get((g, a, "belady"))
        gap = ""
        if lru and bel and st.mean(bel) - st.mean(lru) > 1e-9:
            gap = 100.0 * (m - st.mean(lru)) / (st.mean(bel) - st.mean(lru))
        rows.append({"graph": g, "alpha": a, "policy": p, "mean": round(m, 3), "sd": round(sd, 3),
                     "n_seeds": n, "gap_closed_pct": round(gap, 1) if gap != "" else ""})
    with (root / "compare_table.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[saved] {root / 'compare_table.csv'}")

    for a in alphas:
        graphs = [g for g in GRAPH_ORDER if any(k[0] == g and k[1] == a for k in hit)] + \
            sorted({k[0] for k in hit if k[1] == a and k[0] not in GRAPH_ORDER})
        pols = [p for p in POLICY_ORDER if any(k[2] == p and k[1] == a for k in hit)]
        if not graphs or not pols:
            continue

        # --- ヒット率（グラフごとの小さな図）---------------------------------------
        ncol = min(3, len(graphs))
        nrow = math.ceil(len(graphs) / ncol)
        fig, axes = plt.subplots(nrow, ncol, figsize=(5.2 * ncol, 0.42 * len(pols) * nrow + 1.4 * nrow),
                                 squeeze=False, sharey=True)
        for i, g in enumerate(graphs):
            ax = axes[i // ncol][i % ncol]
            ys = list(range(len(pols)))[::-1]
            for y, p in zip(ys, pols):
                xs = hit.get((g, a, p))
                if not xs:
                    continue
                m, sd, n = ms(xs)
                c = OURS_C if p in OURS else BASE_C
                ax.errorbar(m, y, xerr=sd if n > 1 else None, fmt="o", ms=7, color=c,
                            ecolor=c, elinewidth=2, capsize=0, zorder=3)
                ax.text(m, y + 0.28, f"{m:.1f}", ha="center", va="bottom", fontsize=7, color=INK)
            if (g, a, "belady") in hit:
                b = st.mean(hit[(g, a, "belady")])
                ax.axvline(b, color=BELADY_C, ls=":", lw=1.5)
                ax.text(b, ys[0] + 0.7, f"Belady {b:.1f}", color=BELADY_C, fontsize=7, ha="right")
            if (g, a, "lru") in hit:
                ax.axvline(st.mean(hit[(g, a, "lru")]), color=MUTED, lw=0.8, alpha=0.6)
            ax.set_yticks(ys)
            ax.set_yticklabels([LABEL.get(p, p) for p in pols], fontsize=8, color=INK)
            ax.set_ylim(-0.7, len(pols) - 0.1)
            n_seed = max(len(hit.get((g, a, p), [])) for p in pols)
            ax.set_title(f"{GRAPH_LABEL.get(g, g)}（seed {n_seed} 本）", fontsize=10, color=INK, loc="left")
            ax.set_xlabel("全体ヒット率 [%]", fontsize=8, color=INK)
            style(ax)
        for j in range(len(graphs), nrow * ncol):
            axes[j // ncol][j % ncol].axis("off")
        fig.suptitle(f"関連研究との比較（α={a:g}、ノード/エッジ分離配置、localhost 実サーバ）  "
                     "青 = 提案（学習する式）／灰 = 比較手法、横線 = ±1 標準偏差",
                     fontsize=11, color=INK)
        fig.tight_layout()
        out = root / f"compare_hit_a{a:g}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out}")

        # --- Belady との差の詰め具合 -------------------------------------------------
        mat, ann = [], []
        for g in graphs:
            row, arow = [], []
            lru, bel = hit.get((g, a, "lru")), hit.get((g, a, "belady"))
            for p in pols:
                xs = hit.get((g, a, p))
                if xs and lru and bel and st.mean(bel) - st.mean(lru) > 1e-9:
                    v = 100.0 * (st.mean(xs) - st.mean(lru)) / (st.mean(bel) - st.mean(lru))
                    row.append(v)
                    arow.append(f"{v:.0f}")
                else:
                    row.append(float("nan"))
                    arow.append("")
            mat.append(row)
            ann.append(arow)
        vals = [v for r in mat for v in r if not math.isnan(v)]
        if vals:
            lim = max(10.0, max(abs(v) for v in vals))
            fig, ax = plt.subplots(figsize=(1.0 * len(pols) + 2.5, 0.6 * len(graphs) + 1.8))
            im = ax.imshow(mat, cmap=DIVERGING, norm=TwoSlopeNorm(0, -lim, lim), aspect="auto")
            for i in range(len(graphs)):
                for j in range(len(pols)):
                    ax.text(j, i, ann[i][j], ha="center", va="center", fontsize=8, color=INK)
            ax.set_xticks(range(len(pols)))
            ax.set_xticklabels([LABEL.get(p, p) for p in pols], rotation=35, ha="right", fontsize=8)
            ax.set_yticks(range(len(graphs)))
            ax.set_yticklabels([GRAPH_LABEL.get(g, g) for g in graphs], fontsize=9)
            cb = fig.colorbar(im, ax=ax, shrink=0.8)
            cb.set_label("LRU → Belady の差を詰めた割合 [%]", fontsize=8)
            ax.set_title(f"Belady（最適解）との差をどれだけ詰めたか（α={a:g}）  "
                         "0 = LRU と同じ、100 = 最適解、負 = LRU より悪い", fontsize=10, loc="left")
            fig.tight_layout()
            out = root / f"compare_gap_a{a:g}.png"
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"[saved] {out}")

        # --- structured の重みの比 ------------------------------------------------------
        roles = [("node", "ノード担当（キャッシュ=エッジ）"), ("edge", "エッジ担当（キャッシュ=ノード）")]
        ylabels, mat = [], []
        for g in graphs:
            for role, rl in roles:
                r = []
                for t in TERMS:
                    xs = weights.get((g, a, role, t))
                    r.append(st.mean(xs) if xs else float("nan"))
                if not all(math.isnan(v) for v in r):
                    ylabels.append(f"{GRAPH_LABEL.get(g, g)} / {rl}")
                    mat.append(r)
        if mat:
            vals = [v for r in mat for v in r if not math.isnan(v)]
            lim = max(1.0, max(abs(v) for v in vals))
            fig, ax = plt.subplots(figsize=(7.5, 0.42 * len(mat) + 2.0))
            im = ax.imshow(mat, cmap=DIVERGING.reversed(), norm=TwoSlopeNorm(0, -lim, lim), aspect="auto")
            for i, r in enumerate(mat):
                for j, v in enumerate(r):
                    if not math.isnan(v):
                        ax.text(j, i, f"{v:+.2f}", ha="center", va="center", fontsize=8, color=INK)
            ax.set_xticks(range(len(TERMS)))
            ax.set_xticklabels([TERM_LABEL[t] for t in TERMS], fontsize=8)
            ax.set_yticks(range(len(mat)))
            ax.set_yticklabels(ylabels, fontsize=8)
            cb = fig.colorbar(im, ax=ax, shrink=0.8)
            cb.set_label("重みの比（|w_O| = 1）  赤 = 追い出しやすい／青 = 残しやすい", fontsize=8)
            ax.set_title(f"structured の学習後の重みの比（α={a:g}、seed・同じ役割のサーバで平均）",
                         fontsize=10, loc="left")
            fig.tight_layout()
            out = root / f"compare_weights_a{a:g}.png"
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"[saved] {out}")


if __name__ == "__main__":
    main()
