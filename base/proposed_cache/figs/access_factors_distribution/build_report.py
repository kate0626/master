#!/usr/bin/env python3
"""Render the distribution summaries and a local, dependency-free figure browser."""
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
import analyze as a

HERE = Path(__file__).resolve().parent


def read(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def render_distributions(tag, rows):
    graph, alpha = tag.split("_a")
    alpha = alpha.split("_w")[0]
    for kind in ("node", "edge"):
        fig, axes = a.plt.subplots(2, 3, figsize=(18, 10))
        drows = [r for r in rows if r["kind"] == kind and r["unit"] == "key" and r["factor"] == "D"]
        positions = [a.LABELS.index(r["total_band"]) for r in drows]
        axes[0, 0].bar(positions, [int(r["n"]) for r in drows], color="#5193b5")
        axes[0, 0].set_xticks(positions, [r["total_band"] for r in drows], rotation=30, fontsize=8)
        axes[0, 0].set_title("① どのアクセス回数帯に何個あるか", loc="left")
        axes[0, 0].set_ylabel("キー数（始点×実体）")
        axes[0, 0].set_xlabel("全体のアクセス回数 T")
        axes[0, 0].set_yscale("log")
        panels = [(axes[0, 1], "key", "D", "② よくアクセスされる対象の次数", "D 次数"),
                  (axes[0, 2], "key", "H", "③ よくアクセスされる対象の初回歩数", "H 初回の歩数"),
                  (axes[1, 0], "event", "H", "④ アクセスした時点の歩数", a.NAMES["H"]),
                  (axes[1, 1], "event", "O", "⑤ アクセス直前の頻度【Tと定義上重複】", a.NAMES["O"]),
                  (axes[1, 2], "event", "A", "⑥ アクセス直前の直近性（初回は欠測）", "間隔 A（小さいほど最近 / R=1/(1+A)）")]
        for ax, unit, factor, title, ylabel in panels:
            selected = [r for r in rows if r["kind"] == kind and r["unit"] == unit and r["factor"] == factor]
            pos = [a.LABELS.index(r["total_band"]) for r in selected]
            boxes = [dict(med=float(r["median"]), q1=float(r["p25"]), q3=float(r["p75"]),
                          whislo=float(r["p10"]), whishi=float(r["p90"]), fliers=[]) for r in selected]
            ax.bxp(boxes, positions=pos, showfliers=False, widths=.55, patch_artist=True,
                   boxprops={"facecolor": "#d7e8f2"}, medianprops={"color": "#005985", "linewidth": 2})
            ax.set_xticks(pos, [f"{r['total_band']}\nn={int(r['n']):,}" for r in selected], fontsize=7)
            ax.set_title(title, loc="left", fontsize=11)
            ax.set_xlabel("全体のアクセス回数 T")
            ax.set_ylabel(ylabel)
            if factor == "O":
                ax.set_yscale("symlog", linthresh=1)
                ax.set_ylim(bottom=0)
            else:
                ax.set_yscale("log")
            ax.grid(axis="y", alpha=.2)
        fig.suptitle(f"{graph} / α={alpha} / {kind} — 全アクセス回数別の分布", fontsize=18)
        fig.text(.02, .015, "箱＝25–75%、中央線＝中央値、ひげ＝10–90%。②③はキー各1票、④⑤⑥はアクセス各1票。nは有効な点の数。\n"
                 "全期間を使用（前半・後半に分割しない）。Oは同一キーで0〜T−1を取るため、⑤の関連は予測力の証拠ではない。横軸は回数帯、縦軸は対数系。", fontsize=10)
        fig.tight_layout(rect=(0, .08, 1, .95))
        fig.savefig(HERE / "01_total_distributions" / f"{tag}_{kind}.png", dpi=135)
        a.plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-render", action="store_true", help="既存の図を使い、文章と一覧だけ更新")
    args = parser.parse_args()
    manifests = sorted((HERE / "data").glob("*_manifest.json"))
    summary = []
    evidence = []
    counts = [0, 0]
    for p in manifests:
        meta = json.loads(p.read_text())
        tag = meta["tag"]
        counts[0] += sum(x["n_access"] for x in meta["replay_checks"])
        counts[1] += sum(x["n_keys"] for x in meta["replay_checks"])
        assert all(x["exact_match"] for x in meta["replay_checks"])
        if not args.no_render:
            render_distributions(tag, read(HERE / "data" / f"{tag}_distributions.csv"))
        corrs = read(HERE / "data" / f"{tag}_correlations.csv")
        for kind in ("node", "edge"):
            r = dict(condition=tag.replace("_w1000_s8", ""), kind=kind)
            for unit, factor in [("key", "D"), ("key", "H"), ("event", "H"), ("event", "A")]:
                vals = [float(x["rho_total"]) for x in corrs if x["kind"] == kind and x["unit"] == unit and x["factor"] == factor]
                r[f"{unit}_{factor}"] = statistics.mean(vals)
            summary.append(r)
        cells = read(HERE / "data" / f"{tag}_conditional_by_start.csv")
        # Compare strata using exactly the same starts, with >=20 observations in both cells.
        for kind in ("node", "edge"):
            for z in ("D", "H", "A"):
                for horizon in (10, 100, 1000):
                    for band in ("1", "2–3", "4–7", "8–15", "16–31"):
                        selected = [r for r in cells if r["kind"] == kind and r["x"] == "O" and r["control"] == z
                                    and int(r["horizon"]) == horizon and r["x_band"] == band and int(r["n"]) >= 20
                                    and int(r["levels"]) == 3]
                        by_start = defaultdict(dict)
                        for r in selected:
                            by_start[r["start"]][int(r["level"])] = r
                        pairs = [r for r in by_start.values() if 0 in r and 2 in r]
                        if len(pairs) < 3:
                            continue
                        low = [float(r[0]["rate"]) * 100 for r in pairs]
                        high = [float(r[2]["rate"]) * 100 for r in pairs]
                        diffs = [h - l for l, h in zip(low, high)]
                        evidence.append(dict(condition=tag.replace("_w1000_s8", ""), kind=kind, control=z,
                                             frequency_band=band, horizon=horizon, n_starts=len(pairs),
                                             low_rate_pct=statistics.mean(low), high_rate_pct=statistics.mean(high),
                                             high_minus_low_pp=statistics.mean(diffs), sd_difference_pp=statistics.pstdev(diffs)))
    a.write_csv(HERE / "data/overview_correlations.csv", summary)
    a.write_csv(HERE / "data/matched_start_contrasts.csv", evidence)
    lines = ["# 分析結果", "", f"{len(manifests)}条件・{len(manifests)*8}始点条件、{counts[0]:,}アクセスを集計。",
             "既存CSVとキー別総回数・次数・初回歩数、およびアクセス総数がすべて一致した。", ""]
    if len(manifests) == 12:
        lines += ["## 図から読み取れる具体例", "",
                  "- **次数の関係はノード・条件で違う。** 全回数とのキー単位の順位相関は、VLDB・α=0.1のノードでは−0.040、Enron・α=0.01では+0.505。全体の回数を使っても条件依存性が見える。",
                  "- **初回歩数と、毎回の歩数は区別が必要。** VLDB・α=0.1のノードでは、T=256以上の初回歩数中央値は2、アクセス時点の歩数中央値は4。T=1ではどちらも16。多アクセス対象は早い歩数で現れやすいが、毎回同じ歩数ではない。",
                  "- **直近性と全回数の関係は単調とは限らない。** VLDB・α=0.1のノードでは、T=2–3の再訪間隔中央値は2、T=32–63では24、T=256以上では4。総回数が少なくても直後に戻るケースが含まれ、中央値だけでなく箱とひげも見る必要がある。エッジでは同じ帯の中央値が592.5、112、5と異なる。",
                  "- **頻度をそろえた比較も型によって変わる。** VLDB・α=0.1、O=2–3、100アクセス先では、エッジの次数下位層40.0%に対して上位層24.4%（共通6始点）。一方ノードは53.8%と56.0%（共通6始点）。これは追加情報の候補であり、次数の因果効果や最適係数ではない。",
                  "- **頻度Oと全回数Tの分布は定義上つながっている。** Oの有効性はこの箱ひげ図では評価せず、条件別の次回再アクセスの図で検討する。", "",
                  "上の中央値は始点をまとめた分布の値、相関は始点ごとの相関の平均。母集団の重みの違いを区別する。", ""]
    lines += [
             "## 全アクセス回数との関係（論点2）", "",
             "下表は始点別のSpearman順位相関を8始点で平均した補助値。関係の形・ばらつきは分布図で見る。",
             "Dと初回Hはキー単位、今回HとAはアクセス単位なので、列の絶対値を重要度として直接比較しない。",
             "Aは前回からの間隔で、負なら総回数が多い対象ほど間隔が短い傾向。Rの順位相関は符号が逆。", "",
             "| 条件 | 型 | D（キー） | 初回H（キー） | 今回H（アクセス） | 間隔A（アクセス） |",
             "|---|---|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f"| {r['condition']} | {r['kind']} | {r['key_D']:+.3f} | {r['key_H']:+.3f} | {r['event_H']:+.3f} | {r['event_A']:+.3f} |")
    lines += ["", "Oは全体回数Tと定義上重複する（各キーで0〜T−1）。この相関をOの有効性の証拠にはしない。", "",
              "## 同じ頻度帯での違い（論点3）", "",
              "以下はO=2–3の帯で、もう一つの要素の下位層と上位層を比較した例。",
              "縦軸は次の100アクセス以内の再アクセス率。両層20件以上の同じ始点だけで平均した。",
              "Hの上位層は歩数が大きい。Aの上位層は前回からの間隔が長い（Rが低い）。", "",
              "| 条件 | 型 | 比較要素 | 始点数 | 下位層の率 | 上位層の率 | 上位−下位 |",
              "|---|---|---|---:|---:|---:|---:|"]
    for r in evidence:
        if r["condition"] not in ("vldb_a0.1", "enron_a0.01") or r["horizon"] != 100 or r["frequency_band"] != "2–3":
            continue
        lines.append(f"| {r['condition']} | {r['kind']} | {r['control']} | {r['n_starts']} | {r['low_rate_pct']:.1f}% | {r['high_rate_pct']:.1f}% | {r['high_minus_low_pp']:+.1f} pt |")
    lines += ["", "全条件・複数頻度帯・10/100/1000アクセス先の比較は `data/matched_start_contrasts.csv`。",
              "図で差が残ることは追加情報の候補になるが、全要素を同時に統制していないので独立性の証明ではない。",
              "全体の回数との関係と、直近の再訪の関係は異なる問いであり、結果の違いを区別する。",
              "この分析から係数、足し算・掛け算、退去方策の改善幅はまだ決めない。"]
    (HERE / "RESULTS.md").write_text("\n".join(lines) + "\n")
    html = '''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>アクセス回数と各要素の分布</title><style>
body{font-family:system-ui,sans-serif;background:#f6f8fa;color:#172d3b;margin:0;padding:24px}main{max-width:1500px;margin:auto}
h1{font-size:25px;margin-bottom:8px}p{line-height:1.7}nav{display:flex;gap:18px;flex-wrap:wrap;background:white;padding:18px;border-radius:10px;position:sticky;top:0}
select{font-size:16px;padding:8px;border:1px solid #aab8c2;border-radius:5px}label{display:flex;align-items:center;gap:8px}
img{display:block;width:100%;background:white;margin-top:16px;border-radius:10px}a{color:#00669b}.hint{background:#e8f1f6;padding:14px;border-radius:8px}footer{margin:25px 0}</style>
<main><h1>アクセス回数と各要素の分布</h1><p>全期間のアクセスを使い、ノードとエッジを分けて比較します。<a href="RESULTS.md">集計結果</a> · <a href="README.md">指標の定義と読み方</a></p>
<section><h2>各要素は、他の要素とは別の情報を持つか？</h2><p><a href="03_information_overlap/index.html">全体比較：全6ペアの相関・組み合わせの追加価値・残り3要素への追加価値</a><br><a href="03_information_overlap/RESULTS.md">結果の数値まとめ</a> · <a href="03_information_overlap/README.md">方法と読み方</a></p></section>
<section><h2>まず見るサマリ図</h2>
<p><a href="00_summary/q2_correlation_heatmap.png">論点2：相関係数の一覧</a> · <a href="00_summary/q2_correlation_heatmap.pdf">PDF</a></p>
<p><a href="00_summary/q2_distributions.png">論点2：全アクセス回数ごとの値の変化</a> · <a href="00_summary/q2_distributions.pdf">PDF</a><br>
<a href="00_summary/q3_conditional_effects.png">論点3：同じ頻度帯でも残る再アクセス率の差</a> · <a href="00_summary/q3_conditional_effects.pdf">PDF</a></p>
<details open><summary>論点2：相関の方向と強さ</summary><a href="00_summary/q2_correlation_heatmap.png"><img src="00_summary/q2_correlation_heatmap.png" alt="全アクセス回数との順位相関"></a></details>
<details><summary>論点2：関係の形</summary><a href="00_summary/q2_distributions.png"><img src="00_summary/q2_distributions.png" alt="全条件を重ねた中央値の曲線"></a></details>
<details><summary>論点3のサマリ図</summary><a href="00_summary/q3_conditional_effects.png"><img src="00_summary/q3_conditional_effects.png" alt="頻度をそろえた再アクセス率の差"></a></details></section>
<h2>条件ごとの詳細</h2>
<nav><label>グラフ<select id="graph"><option>vldb</option><option>amazon</option><option>enron</option><option>slashdot</option><option>ba_mid</option><option>er_mid</option></select></label>
<label>停止確率 α<select id="alpha"><option>0.1</option><option>0.01</option></select></label>
<label>対象<select id="kind"><option value="node">ノード</option><option value="edge">エッジ</option></select></label>
<label>論点<select id="view"><option value="01_total_distributions">2：全アクセス回数別の分布</option><option value="02_conditional_reuse">3：条件をそろえた再アクセス比較</option></select></label></nav>
<p class="hint" id="hint"></p><a id="full" target="_blank"><img id="chart" alt="選択した条件の分析図"></a>
<footer>図をクリックすると原寸で開きます。<a id="csv">対応する数値CSV</a> · <a id="check">アクセス列の再現確認</a></footer></main>
<script>
const ids=['graph','alpha','kind','view'];const get=id=>document.getElementById(id);
function update(){const tag=`${get('graph').value}_a${get('alpha').value}_w1000_s8`;const view=get('view').value;
const src=`${view}/${tag}_${get('kind').value}.png`;get('chart').src=src;get('full').href=src;
get('csv').href=`data/${tag}_${view.startsWith('01')?'distributions':'conditional_by_start'}.csv`;
get('check').href=`data/${tag}_manifest.json`;
get('hint').textContent=view.startsWith('01')?'横軸は全体のアクセス回数。箱は中央50%、ひげは10〜90%。右下の間隔Aは小さいほど最近です。頻度Oと総回数Tには定義上の重なりがあります。':'同じ横軸帯で色の線が分かれるかを見ます。縦軸は次の100アクセス以内の再アクセス率。帯は始点間のばらつきです。線の欠落はデータ不足で、効果ゼロではありません。';}
ids.forEach(id=>get(id).addEventListener('change',update));update();
</script></html>'''
    (HERE / "index.html").write_text(html)
    print(f"Report: {len(manifests)} conditions; {counts[0]:,} accesses; {len(evidence)} matched contrasts")


if __name__ == "__main__":
    main()
