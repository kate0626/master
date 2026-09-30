#!/usr/bin/env python3
# =============================================================================
# plot_walks.py — walk 数を縦軸にして、ヒット率とパラメータ（重み）がどう変わったかを描く
#
#   入力: run_all_graphs.sh / run_all_cluster.sh の OUT_ROOT（<graph>_a<α>_seed<s>/curve_<方策>_*.csv）
#   出力: <OUT_ROOT>/walks_a<α>.png  （1 行 = 1 グラフ）
#     左   : 横 = walk 数、縦 = 累積ヒット率。関連研究と提案を比べる。赤の点線 = Base（Belady = 上限、最終値）
#     中・右: 横 = walk 数、縦 = 考慮の割合 |w_i| / Σ|w|（合計 1・非負の積み上げ。--weights raw で値そのもの）。PARAM_POLICY の重みを
#             サーバの役割ごと（エッジをキャッシュするサーバ / ノードをキャッシュするサーバ）に描く。
#             灰色の点線 = 重みの絶対値の合計 Σ|w|（合計は一定ではない）。バイアス b は順番に効かないので描かない
#     seed が複数あれば平均（帯 = ±1 標準偏差）
#
#   使い方:
#     python3 base/proposed_cache/adaptive/plot_walks.py base/proposed_cache/adaptive/results/allgraphs \
#       --graphs vldb amazon0601 email-enron-connected --alpha 0.1
# =============================================================================
from __future__ import annotations

import argparse
import csv
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

_AVAILABLE = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP"):
    if _f in _AVAILABLE:
        matplotlib.rcParams["font.family"] = _f
        break

INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"
# 方策 → (表示名, 色, 提案か)。色は方策ごとに固定（図をまたいで同じ方策は同じ色）
POLICIES = [
    ("lru", "LRU", "#8a8980", False),
    ("arc", "ARC", "#eb6834", False),
    ("lfu", "LFU", "#1baf7a", False),
    ("lecar", "LeCaR", "#eda100", False),
    ("cacheus", "CACHEUS（簡略版）", "#e87ba4", False),
    ("structured", "提案 structured（初期重み 0）", "#4a3aa7", True),
    ("structured_prior", "提案 structured（分析の向きを初期値）", "#2a78d6", True),
]
BELADY_C = "#e34948"
TERMS = [("O", "回数 O", "#2a78d6"), ("R", "経過時間 R", "#eb6834"), ("H", "距離 H", "#1baf7a"),
         ("H_node", "距離 H（ノード分）", "#eda100"), ("D_cond", "次数 D（条件付き）", "#e87ba4")]
GRAPH_LABEL = {"er_mid": "er_mid", "amazon0601": "amazon", "ba_mid": "ba_mid", "vldb": "vldb",
               "soc-slashdot": "slashdot", "email-enron-connected": "enron"}
DIR_RE = re.compile(r"^(?P<g>.+)_a(?P<a>[\d.]+)_seed(?P<s>\d+)$")
RESULT_RE = re.compile(r"overall=([-\d.]+)")


def load_curves(root: Path, graph: str, alpha: float):
    """{方策: [seed ごとの行リスト]}、Belady の最終値のリスト"""
    curves = defaultdict(list)
    belady = []
    for d in sorted(root.iterdir()):
        m = DIR_RE.match(d.name)
        if not (d.is_dir() and m and m["g"] == graph and abs(float(m["a"]) - alpha) < 1e-12):
            continue
        for p, *_ in POLICIES:
            for f in d.glob(f"curve_{p}_start*.csv"):
                rows = list(csv.DictReader(f.open(encoding="utf-8")))
                if rows and rows[0].get("tag") == p:
                    curves[p].append(rows)
        summ = d / "summary.tsv"
        if summ.exists():
            for line in summ.read_text().splitlines():
                if line.startswith("RESULT\tbelady"):
                    belady.append(float(RESULT_RE.search(line).group(1)))
    return curves, belady


def by_walks(seed_rows, col):
    """walks_done → [seed ごとの値]"""
    out = defaultdict(list)
    for rows in seed_rows:
        for r in rows:
            v = r.get(col)
            if v not in (None, "", "nan"):
                out[int(r["walks_done"])].append(float(v))
    ws = sorted(out)
    return ws, [st.mean(out[w]) for w in ws], [st.stdev(out[w]) if len(out[w]) > 1 else 0.0 for w in ws]


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def _col(cols, sid, term):
    return next((c for c in cols if c.startswith(f"w_s{sid}_") and c.split("_", 3)[3] == term), None)


def _val(r, c):
    v = r.get(c) if c else None
    return float(v) if v not in (None, "", "nan") else 0.0


def role_terms(role):
    """そのサーバのキャッシュに実際に効く項。
    ノードをキャッシュするサーバ（エッジ担当）では z_H と [node]·z_H が同じ値なので、H と H_node を
    1つの H（= w_H + w_H_node）にまとめる（別々だと和しか決まらず、個々の値は意味がない）。
    エッジをキャッシュするサーバ（ノード担当）では H_node と D_cond の特徴量が常に 0 なので外す。"""
    if role == "edge":
        return [("O", ["O"]), ("R", ["R"]), ("H", ["H", "H_node"]), ("D_cond", ["D_cond"])]
    return [("O", ["O"]), ("R", ["R"]), ("H", ["H"])]


def draw_share(ax, rows_p, cols, sids, role):
    """考慮の割合 |w_i|/Σ|w| を、seed × サーバごとに計算してから平均し、積み上げで描く。"""
    terms = role_terms(role)
    per_walk = defaultdict(lambda: defaultdict(list))   # walks -> term -> [割合]
    signed = defaultdict(list)                          # term -> 最終時点の符号つきの値
    last_w = 0
    for rows in rows_p:
        for sid in sids:
            for r in rows:
                w = int(r["walks_done"])
                vals = {t: sum(_val(r, _col(cols, sid, x)) for x in parts) for t, parts in terms}
                tot = sum(abs(v) for v in vals.values())
                if tot <= 1e-12:
                    continue
                for t, v in vals.items():
                    per_walk[w][t].append(abs(v) / tot)
                last_w = max(last_w, w)
            rl = [r for r in rows if int(r["walks_done"]) == last_w]
            for r in rl:
                for t, parts in terms:
                    signed[t].append(sum(_val(r, _col(cols, sid, x)) for x in parts))
    ws = sorted(per_walk)
    if not ws:
        return
    colors = {t: c for t, _, c in TERMS}
    names = {t: n for t, n, _ in TERMS}
    ys, labels, cs = [], [], []
    for t, _ in terms:
        y = [st.mean(per_walk[w][t]) if per_walk[w][t] else 0.0 for w in ws]
        sgn = st.mean(signed[t]) if signed[t] else 0.0
        direction = "大きいほど残す" if sgn < 0 else "大きいほど追い出す"
        ys.append(y)
        labels.append(f"{names.get(t, t)}（最終 {100 * y[-1]:.0f}%、{direction}）")
        cs.append(colors.get(t, MUTED))
    ax.stackplot(ws, *ys, colors=cs, labels=labels, alpha=0.85, edgecolor="white", linewidth=0.8)
    # 最終時点の割合を帯の中に書く
    acc = 0.0
    for y, t in zip(ys, [t for t, _ in terms]):
        if y[-1] >= 0.06:
            ax.text(ws[-1], acc + y[-1] / 2, f"{100 * y[-1]:.0f}% ", ha="right", va="center",
                    fontsize=8, color="white", fontweight="bold")
        acc += y[-1]
    ax.set_ylim(0, 1)
    ax.set_xlim(ws[0], ws[-1])
    ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(0, -0.18), ncol=2, frameon=False)


def draw_raw(ax, rows_p, cols, sids, n_seed):
    total = None
    ws = []
    for term, tlabel, color in TERMS:
        series = [by_walks(rows_p, c) for c in (_col(cols, s, term) for s in sids) if c]
        if not series:
            continue
        ws = series[0][0]
        m = [st.mean(x[1][k] for x in series) for k in range(len(ws))]
        if len({round(v, 12) for v in m}) <= 1:     # そのサーバに来ない型の項は初期値のまま
            continue
        sd = [st.mean(x[2][k] for x in series) for k in range(len(ws))]
        if n_seed > 1:
            ax.fill_between(ws, [x - s for x, s in zip(m, sd)], [x + s for x, s in zip(m, sd)],
                            color=color, alpha=0.12, lw=0)
        ax.plot(ws, m, color=color, lw=2, marker=".", ms=5, label=f"w_{term}：{tlabel}")
        total = [abs(v) for v in m] if total is None else [t + abs(v) for t, v in zip(total, m)]
    if total:
        ax.plot(ws, total, color=MUTED, lw=1.2, ls="--", label="Σ|w|（合計の大きさ）")
    ax.axhline(0, color=MUTED, lw=0.8)
    ax.legend(fontsize=7, loc="best", frameon=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--graphs", nargs="+", default=["vldb", "amazon0601", "email-enron-connected"])
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--param-policy", default="structured_prior", help="重みを描く方策")
    ap.add_argument("--weights", choices=["share", "raw"], default="share",
                    help="share = 考慮の割合 |w_i|/Σ|w|（合計 1・非負、積み上げ。既定）/ raw = 重みの値そのもの")
    ap.add_argument("--hit", choices=["cum", "batch"], default="cum",
                    help="cum = 累積ヒット率（既定、なめらか）/ batch = バッチ内ヒット率")
    a = ap.parse_args()
    root = Path(a.root)
    hit_col = "cum_hit" if a.hit == "cum" else "batch_hit"

    graphs = [g for g in a.graphs if any(DIR_RE.match(d.name) and DIR_RE.match(d.name)["g"] == g
                                         for d in root.iterdir())]
    if not graphs:
        raise SystemExit("指定したグラフの結果がありません")
    fig, axes = plt.subplots(len(graphs), 3, figsize=(16, 4.6 * len(graphs)), squeeze=False,
                             gridspec_kw={"width_ratios": [1.25, 1, 1]})
    for i, g in enumerate(graphs):
        curves, belady = load_curves(root, g, a.alpha)
        n_seed = max((len(v) for v in curves.values()), default=0)

        # --- 左: ヒット率 ---------------------------------------------------------
        ax = axes[i][0]
        for p, label, color, ours in POLICIES:
            if p not in curves:
                continue
            ws, m, sd = by_walks(curves[p], hit_col)
            if n_seed > 1:
                ax.fill_between(ws, [x - s for x, s in zip(m, sd)], [x + s for x, s in zip(m, sd)],
                                color=color, alpha=0.12, lw=0)
            ax.plot(ws, m, color=color, lw=2.6 if ours else 1.4, marker="o" if ours else ".",
                    ms=5 if ours else 4, label=f"{label}（{m[-1]:.1f}%）", zorder=3 if ours else 2)
        if belady:
            # Base = Belady（将来を知っている最適な追い出し。どの方策もこれを超えられない上限）
            b = st.mean(belady)
            ax.axhline(b, color=BELADY_C, ls=":", lw=1.8, label=f"Base：Belady（上限）（{b:.1f}%）")
        ax.set_xlabel("walk 本数（累積）", fontsize=9, color=INK)
        ax.set_ylabel(("累積" if a.hit == "cum" else "バッチ内") + "ヒット率 [%]", fontsize=9, color=INK)
        ax.set_title(f"{GRAPH_LABEL.get(g, g)}：ヒット率（seed {n_seed} 本）", fontsize=10, color=INK, loc="left")
        ax.legend(fontsize=7, loc="best", frameon=True, framealpha=0.85, edgecolor="none", ncol=2)
        style(ax)

        # --- 中・右: 重み ----------------------------------------------------------
        rows_p = curves.get(a.param_policy, [])
        cols = list(rows_p[0][0].keys()) if rows_p else []
        kn = int(rows_p[0][0].get("node_servers") or 1) if rows_p else 1
        roles = [("node", "ノード担当サーバ（キャッシュ = エッジ）"),
                 ("edge", "エッジ担当サーバ（キャッシュ = ノード）")]
        for j, (role, title) in enumerate(roles, start=1):
            ax = axes[i][j]
            sids = sorted({int(c.split("_")[1][1:]) for c in cols if c.startswith("w_s")})
            sids = [s for s in sids if (s < kn) == (role == "node")]
            if a.weights == "share":
                draw_share(ax, rows_p, cols, sids, role)
                ax.set_ylabel("考慮の割合 |w_i| / Σ|w|（合計 1）", fontsize=8, color=INK)
                ax.set_title(f"{a.param_policy} の考慮の割合：{title}", fontsize=9, color=INK, loc="left")
            else:
                draw_raw(ax, rows_p, cols, sids, n_seed)
                ax.set_ylabel("重みの値（正 = 追い出しやすい／負 = 残しやすい）", fontsize=8, color=INK)
                ax.set_title(f"{a.param_policy} の重み：{title}", fontsize=9, color=INK, loc="left")
            ax.set_xlabel("walk 本数（累積）", fontsize=9, color=INK)
            style(ax)

    fig.suptitle(f"walk を重ねるとヒット率と重みがどう変わるか（α={a.alpha:g}、localhost 実サーバ、"
                 "ノード/エッジ分離配置）  帯 = seed 間の ±1 標準偏差", fontsize=11, color=INK)
    fig.tight_layout()
    out = root / f"walks_a{a.alpha:g}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
