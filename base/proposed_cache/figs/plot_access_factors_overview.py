#!/usr/bin/env python3
# =============================================================================
# plot_access_factors_overview.py
#   access-factor 系の図は条件 (6グラフ × α2種) × 母集団 (all/node/edge) で
#   数が多くなるので、**まず見る 3 枚**に畳んだ俯瞰図を作る。
#   行 = 条件、列 = 要素 (またはペア)、パネル = 母集団。
#
#   python3 figs/plot_access_factors_overview.py
#   → figs/access_factors/00_overview/{per_factor,pair_correlation,shapley}.png
# =============================================================================
from __future__ import annotations
import csv, glob, os, statistics as st
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PC = Path(__file__).resolve().parent.parent
RES = PC / "results"
OUT = PC / "figs" / "access_factors" / "00_overview"
KINDS = ["all", "node", "edge"]
# 次数不均一さ CV の小さい順 (= 次数が効きにくい順) に並べる
ORDER = ["er_mid", "amazon", "ba_mid", "vldb", "slashdot", "enron"]
ALPHAS = ["0.1", "0.01"]


def conditions():
    for a in ALPHAS:
        for g in ORDER:
            yield g, a, f"{g}_a{a}_w1000_s8", f"{g}  a={a}"


FACTOR_DEFS = (
    "【4要素の定義】"
    "D = 次数（ノード: 隣接エッジ数 / エッジ: 両端点次数の平均）   "
    "H = 始点からの hop = 歩数（RW が何ホップ目でそれを踏んだか。小さい＝始点の近く）   "
    "O = 過去アクセス回数（観測窓の前半で何回踏まれたか）   "
    "R = 直近性 = 1/(1+経過時間)（大きい＝最近踏まれた）\n"
    "【目的変数】将来アクセス回数 = 観測窓の後半で何回踏まれたか（＝キャッシュに残す価値）   "
    "【母集団】観測窓で1回以上踏まれたキー（= キャッシュに載りうるもの）。"
    "node=ノード / edge=エッジ / all=両方\n"
    "【行の並び】上半分 α=0.1（停止確率大＝始点近傍に集中）／下半分 α=0.01（拡散）。"
    "各半分の中はグラフを次数のばらつき CV の小さい順に並べた"
    "（er_mid 0.32 < amazon 0.96 < ba_mid 1.32 < vldb 1.51 < slashdot 3.43 < enron 3.50）"
)


def grid(fig, ax, mat, rows, cols, title, vmax, fmt="{:+.2f}"):
    im = ax.imshow(mat, cmap="coolwarm", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(cols))); ax.set_xticklabels(cols, fontsize=9)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(rows, fontsize=8)
    for i in range(len(rows)):
        for j in range(len(cols)):
            if np.isfinite(mat[i][j]):
                ax.text(j, i, fmt.format(mat[i][j]), ha="center", va="center",
                        fontsize=7.5)
    ax.axhline(len(rows) / 2 - 0.5, color="k", lw=1.2)   # α の境目
    ax.set_title(title, fontsize=11)
    fig.colorbar(im, ax=ax, fraction=0.046)


def mean_of(rowsrc, kind, key):
    v = [float(r[key]) for r in rowsrc
         if r["kind"] == kind and r["start"] != "POOLED"
         and r.get(key, "") not in ("", "nan")]
    return st.mean(v) if v else float("nan")


# --- 1) 各要素 → 将来アクセス回数 (Spearman ρ) --------------------------------
def fig_per_factor():
    cols = ["D\n次数", "H\nhop(歩数)", "O\n過去アクセス回数", "R\n直近性"]
    keys = ["rho_deg", "rho_hop", "rho_past", "rho_recency"]
    fig, axes = plt.subplots(1, 3, figsize=(17, 8.6))
    rows = [lab for _, _, _, lab in conditions()]
    for ax, kind in zip(axes, KINDS):
        mat = np.full((len(rows), 4), np.nan)
        for i, (_, _, tag, _) in enumerate(conditions()):
            p = RES / f"accessfactors_{tag}_summary.csv"
            if not p.exists():
                continue
            src = list(csv.DictReader(p.open()))
            for j, k in enumerate(keys):
                mat[i][j] = mean_of(src, kind, k)
        grid(fig, ax, mat, rows, cols, f"kind = {kind}", 0.8)
    fig.suptitle("① 各要素は「将来のアクセス回数」をどれだけ予測できるか"
                 "（順位相関 Spearman ρ）", fontsize=14)
    fig.text(0.5, 0.945, "赤=正の相関（大きいほど将来よく踏まれる） / "
             "青=負の相関（大きいほど踏まれない） / 白=無関係。|ρ|≧0.3 が実用的な目安",
             ha="center", fontsize=10)
    fig.tight_layout(rect=(0, 0.17, 1, 0.925))
    fig.text(0.012, 0.015, FACTOR_DEFS, fontsize=9, va="bottom", linespacing=1.6)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "per_factor.png", dpi=150); plt.close(fig)


# --- 2) 要素同士の相関 (6 ペア) ------------------------------------------------
def fig_pair_correlation():
    pairs = [("deg", "hop"), ("deg", "past"), ("deg", "recency"),
             ("hop", "past"), ("hop", "recency"), ("past", "recency")]
    cols = ["D×H\n次数×hop", "D×O\n次数×回数", "D×R\n次数×直近性",
            "H×O\nhop×回数", "H×R\nhop×直近性", "O×R\n回数×直近性"]
    fig, axes = plt.subplots(1, 3, figsize=(19, 8.6))
    rows = [lab for _, _, _, lab in conditions()]
    for ax, kind in zip(axes, KINDS):
        mat = np.full((len(rows), 6), np.nan)
        ns = np.zeros((len(rows), 6), dtype=bool)
        for i, (_, _, tag, _) in enumerate(conditions()):
            p = RES / f"paircorr_{tag}_{kind}_hwalk.csv"
            if not p.exists():
                continue
            d = {(r["x"], r["z"]): r for r in csv.DictReader(p.open())}
            for j, pr in enumerate(pairs):
                r = d.get(pr)
                if r is None:
                    continue
                mat[i][j] = float(r["rho"])
                ns[i][j] = float(r["ci_lo"]) <= 0 <= float(r["ci_hi"])
        grid(fig, ax, mat, rows, cols, f"kind = {kind}", 0.6)
        for i in range(len(rows)):       # CI が 0 をまたぐセルに枠
            for j in range(6):
                if ns[i][j]:
                    ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                                               ec="gray", lw=1.4, ls=":"))
    fig.suptitle("② 要素どうしに相関はあるか（4要素から2つ選ぶ 6 ペア・順位相関 Spearman ρ）",
                 fontsize=14)
    fig.text(0.5, 0.945, "点線枠 = 95%信頼区間が 0 をまたぐ＝「相関あり」と言えない。"
             "赤=負 / 青=正。|ρ|<0.1 は実質無相関とみなす",
             ha="center", fontsize=10)
    fig.tight_layout(rect=(0, 0.17, 1, 0.925))
    fig.text(0.012, 0.015, FACTOR_DEFS, fontsize=9, va="bottom", linespacing=1.6)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "pair_correlation.png", dpi=150); plt.close(fig)


# --- 3) Shapley (予測寄与の配分) ----------------------------------------------
def fig_shapley():
    cols = ["D\n次数", "H\nhop(歩数)", "O\n過去アクセス回数", "R\n直近性"]
    names = ["deg", "hop", "past", "recency"]
    fig, axes = plt.subplots(1, 3, figsize=(17, 8.6))
    rows = [lab for _, _, _, lab in conditions()]
    for ax, kind in zip(axes, KINDS):
        mat = np.full((len(rows), 4), np.nan)
        for i, (_, _, tag, _) in enumerate(conditions()):
            p = RES / f"interplay_{tag}_{kind}.csv"
            if not p.exists():
                continue
            d = {(r["metric"], r["x"]): float(r["value"])
                 for r in csv.DictReader(p.open())}
            for j, n in enumerate(names):
                mat[i][j] = d.get(("shapley_d2", n), float("nan"))
        grid(fig, ax, mat, rows, cols, f"kind = {kind}", 0.35, "{:+.3f}")
    fig.suptitle("③ 予測力への貢献の配分（Shapley 値）"
                 "— 重複を除いて各要素の取り分に分けたもの", fontsize=14)
    fig.text(0.5, 0.945, "4要素をすべて使ったときの予測力（Poisson 逸脱度の説明率 D²）を、"
             "重複なく4つに配分した値。合計＝全体の予測力。"
             "赤=貢献あり / 青=マイナス（入れると予測が悪化する）",
             ha="center", fontsize=10)
    fig.tight_layout(rect=(0, 0.17, 1, 0.925))
    fig.text(0.012, 0.015, FACTOR_DEFS, fontsize=9, va="bottom", linespacing=1.6)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "shapley.png", dpi=150); plt.close(fig)


if __name__ == "__main__":
    matplotlib.rcParams["font.family"] = ["Hiragino Sans", "Yu Gothic",
                                          "IPAexGothic", "DejaVu Sans"]
    fig_per_factor(); fig_pair_correlation(); fig_shapley()
    for f in ("per_factor.png", "pair_correlation.png", "shapley.png"):
        print(f"[saved] {OUT / f}")
