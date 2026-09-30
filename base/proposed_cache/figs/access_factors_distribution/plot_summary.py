#!/usr/bin/env python3
"""Two question-specific overview figures, using saved distribution/conditional CSVs."""
import csv
from collections import defaultdict
from pathlib import Path
import analyze as a

a.plt.rcParams["pdf.fonttype"] = 42

HERE = Path(__file__).resolve().parent
OUT = HERE / "00_summary"
GRAPHS = ["er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron"]
COLORS = ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#d55e00", "#333333"]
CONDITIONS = [(g, alpha) for alpha in ("0.1", "0.01") for g in GRAPHS]


def read(tag, suffix):
    with (HERE / "data" / f"{tag}_w1000_s8_{suffix}.csv").open() as f:
        return list(csv.DictReader(f))


def question2():
    fig, axes = a.plt.subplots(4, 3, figsize=(17, 17))
    export = []
    columns = [("key", "D", "次数 D", "大きいほど高次数"),
               ("event", "H", "アクセスした時点の歩数 H", "小さいほど歩行の早い段階"),
               ("event", "A", "直近性：前回からの間隔 A", "小さいほど最近（Rが大きい）")]
    for row, (kind, alpha) in enumerate([(k, al) for k in ("node", "edge") for al in ("0.1", "0.01")]):
        for col, (unit, factor, title, note) in enumerate(columns):
            ax = axes[row, col]
            for graph, color in zip(GRAPHS, COLORS):
                tag = f"{graph}_a{alpha}"
                data = read(tag, "distributions")
                ys = a.np.full(9, a.np.nan)
                for r in data:
                    if r["kind"] != kind or r["unit"] != unit or r["factor"] != factor:
                        continue
                    b = a.LABELS.index(r["total_band"]) - 1
                    shown = int(r["n"]) >= 20
                    if shown:
                        ys[b] = float(r["median"])
                    export.append(dict(condition=tag, kind=kind, unit=unit, factor=factor,
                                       total_band=r["total_band"], n=r["n"], median=r["median"],
                                       p25=r["p25"], p75=r["p75"], displayed=shown))
                ax.plot(range(9), ys, "o-", color=color, lw=1.7, markersize=4, label=graph)
            ax.set_yscale("log")
            ax.set_xticks(range(9), a.LABELS[1:], rotation=35, fontsize=8)
            ax.grid(alpha=.18)
            ax.set_xlabel("全体のアクセス回数 T", fontsize=9)
            ax.set_ylabel(f"{'ノード' if kind == 'node' else 'エッジ'} / α={alpha}\n{factor} の中央値", fontsize=10)
            if row == 0:
                ax.set_title(f"{title}\n{note}", fontsize=13, pad=12)
            # Fixed scale per column across all four regimes for honest comparison.
            ax.set_ylim(*[(1, 4000), (1, 400), (1, 30000)][col])
    fig.suptitle("論点2｜よくアクセスされる対象は、各要素がどんな値を取るか", fontsize=19, y=.985)
    fig.text(.5, .949, "右に進む＝全体のアクセス回数が増える。線の上昇・下降と、グラフ条件による違いを見る。", ha="center", fontsize=11)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5, .939), ncol=6, frameon=False, fontsize=11)
    fig.text(.025, .02, "点＝各アクセス回数帯の中央値（有効20件以上）。分布の幅は個別の箱ひげ図で確認。線の欠落はデータ不足・初回の間隔欠測。\n"
             "Dはキー各1票、H・Aはアクセス各1票。始点をまとめた記述統計で、予測力や係数の比較ではない。縦軸は列ごとに共通の対数目盛。\n"
             "O（累積頻度）は同じキーで0〜T−1を取るため、Tとの関係は定義上の重複。この図の有効性比較からは除外。Hは今回の歩数で、初回歩数・BFS距離ではない。", fontsize=10)
    fig.tight_layout(rect=(0, .085, 1, .905), h_pad=2)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"q2_distributions.{ext}", dpi=150)
    a.plt.close(fig)
    a.write_csv(OUT / "q2_values.csv", export)


def question3():
    fig, axes = a.plt.subplots(2, 3, figsize=(18, 13))
    export = []
    columns = [("D", "高次数 − 低次数", 1),
               ("H", "歩数が小さい − 歩数が大きい", -1),
               ("A", "最近 − 最近でない", -1)]
    ylabels = [f"{g}  α={al}" for g, al in CONDITIONS]
    all_stats = {}
    for kind in ("node", "edge"):
        for control, title, sign in columns:
            for graph, alpha in CONDITIONS:
                tag = f"{graph}_a{alpha}"
                cells = defaultdict(dict)
                for r in read(tag, "conditional_by_start"):
                    if (r["kind"] == kind and r["x"] == "O" and r["control"] == control
                        and r["horizon"] == "100" and r["x_band"] == "2–3"
                        and int(r["n"]) >= 20 and r["levels"] == "3"):
                        cells[r["start"]][int(r["level"])] = r
                pairs = [r for r in cells.values() if 0 in r and 2 in r]
                diffs = [sign * 100 * (float(r[2]["rate"]) - float(r[0]["rate"])) for r in pairs]
                n = len(diffs)
                mean = float(a.np.mean(diffs)) if n else float("nan")
                sd = float(a.np.std(diffs)) if n else float("nan")
                all_stats[kind, control, tag] = (mean, sd, n)
                export.append(dict(condition=tag, kind=kind, control=control, contrast=title,
                                   frequency_band="2–3", horizon=100, n_starts=n,
                                   mean_difference_pp=mean, sd_difference_pp=sd, displayed=n >= 3))
    extent = max(abs(m) + s for m, s, n in all_stats.values() if n >= 3)
    limit = max(20, a.np.ceil(extent / 10) * 10)
    for row, kind in enumerate(("node", "edge")):
        for col, (control, title, sign) in enumerate(columns):
            ax = axes[row, col]
            for i, (graph, alpha) in enumerate(CONDITIONS):
                mean, sd, n = all_stats[kind, control, f"{graph}_a{alpha}"]
                if n >= 3:
                    color = "#0072b2" if alpha == "0.1" else "#d55e00"
                    ax.errorbar(mean, i, xerr=sd, fmt="o", color=color, capsize=3, markersize=5)
                    ax.text(1.02, i, f"{mean:+.1f}  n={n}", transform=ax.get_yaxis_transform(), va="center", fontsize=8)
                else:
                    ax.text(1.02, i, f"不足 n={n}", transform=ax.get_yaxis_transform(), va="center", color="#666", fontsize=8)
            ax.axvline(0, color="#888", lw=1)
            ax.axhline(5.5, color="#ccc", ls="--")
            ax.set_yticks(range(12), ylabels, fontsize=9)
            ax.set_ylim(11.7, -.7)
            ax.set_xlim(-limit, limit)
            ax.grid(axis="x", alpha=.2)
            ax.set_title(f"{'ノード' if kind == 'node' else 'エッジ'}｜{title}", fontsize=12, pad=13)
            ax.set_xlabel("再アクセス率の差 [パーセントポイント]", fontsize=10)
    fig.suptitle("論点3｜同じ頻度帯でも、他の要素による違いは残るか", fontsize=19, y=.985)
    fig.text(.5, .943, "O=2〜3回のアクセス時点を比較。右側＝高次数・小さい歩数・最近の側で、次の100アクセス以内の再訪が多い。", ha="center", fontsize=11)
    fig.text(.025, .02, "点＝同じ始点で比較した再アクセス率の差の平均、横線＝始点間の標準偏差（信頼区間ではない）。n＝比較できた始点数／最大8。\n"
             "各要素の下位・上位三分位を比較（始点・型別）。両側20件以上・3始点以上を表示。青＝α0.1、橙＝α0.01。数値の単位はpt。\n"
             "O以外の全要素を同時に統制した結果ではなく、追加情報の候補を見る図。差が0でも独立情報がないとは断定できない。\n"
             "これは再訪の有無・短い時間範囲の比較であり、総アクセス回数やキャッシュのヒット率とは別。Oの独自寄与と、O以外の要素同士の重複はこの図の対象外。", fontsize=10)
    fig.tight_layout(rect=(0, .105, .97, .915), h_pad=3, w_pad=4)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"q3_conditional_effects.{ext}", dpi=150)
    a.plt.close(fig)
    a.write_csv(OUT / "q3_values.csv", export)


def correlation_heatmap():
    columns = [("event", "D", "D 次数\nアクセス単位", 1),
               ("event", "H", "H 今回歩数\nアクセス単位", 1),
               ("event", "O", "O 累積回数※\nアクセス単位", 1),
               ("event", "A", "R 直近性\n再アクセス単位", -1)]
    fig, axes = a.plt.subplots(1, 2, figsize=(18, 10))
    export = []
    for ax, kind in zip(axes, ("node", "edge")):
        mat = a.np.full((12, len(columns)), a.np.nan)
        for i, (g, alpha) in enumerate(CONDITIONS):
            rows = read(f"{g}_a{alpha}", "correlations")
            for j, (unit, factor, label, sign) in enumerate(columns):
                vals = [sign * float(r["rho_total"]) for r in rows
                        if r["kind"] == kind and r["unit"] == unit and r["factor"] == factor]
                vals = a.np.asarray(vals)
                vals = vals[a.np.isfinite(vals)]
                mat[i, j] = vals.mean() if len(vals) else a.np.nan
                export.append(dict(condition=f"{g}_a{alpha}", kind=kind, unit=unit,
                                   factor="R" if factor == "A" else factor,
                                   mean_rho=mat[i, j], sd_rho=float(vals.std()), n_starts=len(vals)))
        im = ax.imshow(mat, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(columns)), [c[2] for c in columns], fontsize=10)
        ax.set_yticks(range(12), [f"{g}  α={al}" for g, al in CONDITIONS], fontsize=10)
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=17, pad=15)
        ax.axhline(5.5, color="#333", linewidth=1.5)
        ax.axvspan(1.5, 2.5, facecolor="none", edgecolor="#777", hatch="..", linewidth=0)
        for i in range(12):
            for j in range(len(columns)):
                val = mat[i, j]
                ax.text(j, i, f"{val:+.2f}", ha="center", va="center", fontsize=12,
                        color="white" if abs(val) > .6 else "#111")
    fig.suptitle("論点2｜全アクセス回数との相関の方向・強さ", fontsize=21, y=.965)
    fig.text(.5, .915, "Spearman順位相関 ρ：赤＝正、青＝負、白に近い＝単調な関連が弱い。各セルは8始点の相関の平均。", ha="center", fontsize=12)
    fig.subplots_adjust(left=.11, right=.90, top=.845, bottom=.23, wspace=.38)
    cax = fig.add_axes([.93, .28, .013, .51])
    fig.colorbar(im, cax=cax, label="順位相関 ρ", ticks=[-1, -.5, 0, .5, 1])
    fig.text(.025, .045, "すべてアクセス1回を1票として集計。アクセスの多い対象ほど結果に強く反映される。\n"
             "R=1/(1+A) は大きいほど最近。Aとの順位相関を符号反転して表示。Rは初回アクセスを除く。Hは歩数でありBFS距離ではない。\n"
             "※ Oは同じキーで0〜T−1を取るため、全回数Tとの相関には定義上の重複がある。高い相関をそのまま有効性の証拠にはしない。\n"
             "白いセルでもU字などの非単調な関係はありうる。因果・他要素から独立した寄与・最適重みはこの図では決まらない。", fontsize=11, linespacing=1.8)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"q2_correlation_heatmap.{ext}", dpi=150)
    a.plt.close(fig)
    a.write_csv(OUT / "q2_correlation_values.csv", export)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    question2()
    question3()
    correlation_heatmap()
    print(f"Saved question 2/3 overview figures, PDFs and source values: {OUT}")
