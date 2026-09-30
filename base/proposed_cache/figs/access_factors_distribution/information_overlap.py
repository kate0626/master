#!/usr/bin/env python3
"""Event-level factor dependence and held-out-start subset prediction comparison."""
import argparse
import csv
import hashlib
import itertools
import json
from collections import defaultdict
from pathlib import Path
import analyze as a

np = a.np
OUT = a.HERE / "03_information_overlap"
FACTORS = ["D", "H", "O", "R"]
GRAPHS = ["er_mid", "ba_mid", "amazon", "vldb", "slashdot", "enron"]
CONDITIONS = [(g, al) for al in ("0.1", "0.01") for g in GRAPHS]
PAIRS = list(itertools.combinations(range(4), 2))


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def subset_predictions(xtrain, ytrain, xtest):
    """Train-only quartiles, cell frequency shrunk by 20 pseudo-observations.

    Each subset uses the same features/bins and training-only baseline. Empty cells
    fall back to the baseline. Four bins and smoothing=20 fixed before evaluation.
    """
    train_bins, test_bins = [], []
    for j in range(4):
        cuts = np.unique(np.quantile(xtrain[:, j], [.25, .5, .75]))
        train_bins.append(np.searchsorted(cuts, xtrain[:, j], side="left"))
        test_bins.append(np.searchsorted(cuts, xtest[:, j], side="left"))
    baseline = float(ytrain.mean())
    result = {0: np.full(len(xtest), baseline)}
    for mask in range(1, 16):
        ti = np.zeros(len(xtrain), dtype=np.int32)
        vi = np.zeros(len(xtest), dtype=np.int32)
        size = 1
        for j in range(4):
            if mask & (1 << j):
                ti += size * train_bins[j]
                vi += size * test_bins[j]
                size *= 4
        counts = np.bincount(ti, minlength=size)
        hits = np.bincount(ti, weights=ytrain, minlength=size)
        probs = (hits + 20 * baseline) / (counts + 20)
        result[mask] = probs[vi]
    return result


def collect(graph, cluster, deg_map):
    for alpha in ("0.1", "0.01"):
        tag = f"{graph}_a{alpha}"
        original = a.PC / "results/cap" / f"accessfactors_{tag}_w1000_s8_keys.csv"
        meta = json.loads(original.with_suffix(".meta.json").read_text())
        cfg = meta["config"]
        assert cfg["owner_filter"] is None and cfg["partition_dir"] is None and cfg["server_count"] == 2
        expected = defaultdict(dict)
        for r in rows(original):
            expected[int(r["start"])][r["entity"]] = (int(float(r["total"])), float(r["hop_walk"]), float(r["deg"]))
        by_kind = {k: [] for k in ("node", "edge")}
        correlations, checks = [], []
        for start in meta["starts"]:
            seq = a.source.run_walks(cluster, [start], cfg["walks"], float(alpha), cfg["seed"], cfg["max_hops"], cfg["no_backtrack"])[start]
            ev, _, total, first = a.records(seq, deg_map)
            assert {str(e): (n, float(first[e]), deg_map[e]) for e, n in total.items()} == expected[start], (tag, start)
            assert len(seq) == meta["per_start"][str(start)]["n_access"]
            checks.append(dict(start=start, n_access=len(seq), exact_match=True))
            for kind, edge in (("node", 0), ("edge", 1)):
                # Common population for all four features and every model.
                valid = (ev["edge"] == edge) & np.isfinite(ev["A"]) & (ev["remaining"] >= 100)
                x = np.column_stack([ev["D"][valid], ev["H"][valid], ev["O"][valid], 1 / (1 + ev["A"][valid])])
                y = (ev["gap"][valid] <= 100).astype(float)
                by_kind[kind].append((start, x, y))
                ranks = np.column_stack([a.source.rankdata(x[:, j]) for j in range(4)])
                corr = np.corrcoef(ranks, rowvar=False)
                for j, k in PAIRS:
                    correlations.append(dict(condition=tag, kind=kind, start=start, pair=FACTORS[j]+"×"+FACTORS[k],
                                             n=len(y), rho=float(corr[j, k])))
            print(f"  {tag} / {start}: replay OK", flush=True)
        scores = []
        for kind, datasets in by_kind.items():
            for heldout, (start, xtest, ytest) in enumerate(datasets):
                train = [d for i, d in enumerate(datasets) if i != heldout]
                xtrain = np.concatenate([d[1] for d in train])
                ytrain = np.concatenate([d[2] for d in train])
                pred = subset_predictions(xtrain, ytrain, xtest)
                base = float(np.mean((ytest - pred[0]) ** 2))
                for mask, prob in pred.items():
                    brier = float(np.mean((ytest - prob) ** 2))
                    scores.append(dict(condition=tag, kind=kind, start=start, mask=mask,
                                       features="+".join(FACTORS[j] for j in range(4) if mask & (1 << j)) or "constant",
                                       n_train=len(ytrain), n_test=len(ytest), test_prevalence=float(ytest.mean()),
                                       brier=brier, baseline_brier=base, skill=1-brier/base))
        a.write_csv(OUT / f"{tag}_pair_correlations.csv", correlations)
        a.write_csv(OUT / f"{tag}_subset_scores.csv", scores)
        (OUT / f"{tag}_manifest.json").write_text(json.dumps(dict(
            condition=tag, checks=checks, source_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
            target="next access within 100 authorization accesses", population="repeat accesses with >=100 accesses remaining",
            validation="leave one start out: 7 train / 1 test", bins="training-set quartiles; ties unsplit",
            smoothing=20, seed_config=cfg), ensure_ascii=False, indent=2))
        print(f"  {tag}: 16 feature subsets, 8 held-out starts, both entity types saved", flush=True)


def plot_matrix(filename, title, subtitle, labels, table, limit, note, cmap="RdBu_r", stacked=False):
    a.plt.rcParams["pdf.fonttype"] = 42
    fig, axes = a.plt.subplots(2 if stacked else 1, 1 if stacked else 2,
                              figsize=(18, 16) if stacked else (18, 10))
    for ax, kind in zip(axes.flat, ("node", "edge")):
        matrix = np.array([[table[kind, f"{g}_a{al}", label][0] for label in labels] for g, al in CONDITIONS])
        im = ax.imshow(matrix, cmap=cmap, vmin=-limit, vmax=limit, aspect="auto")
        ax.set_xticks(range(len(labels)), labels, fontsize=10)
        ax.set_yticks(range(12), [f"{g}  α={al}" for g, al in CONDITIONS], fontsize=10)
        ax.set_title("ノード" if kind == "node" else "エッジ", fontsize=17, pad=10)
        ax.axhline(5.5, color="#555", lw=1.5)
        for i, (g, al) in enumerate(CONDITIONS):
            for j, label in enumerate(labels):
                mean, sd = table[kind, f"{g}_a{al}", label]
                ax.text(j, i, f"{mean:+.2f}" if filename.startswith("01") else f"{mean:+.1f}",
                        ha="center", va="center", fontsize=11,
                        color="white" if abs(mean) > .65 * limit else "#111")
    fig.suptitle(title, fontsize=20, y=.965)
    fig.text(.5, .922 if not stacked else .937, subtitle, ha="center", fontsize=11)
    fig.subplots_adjust(left=.115, right=.90, top=.84 if not stacked else .89, bottom=.23 if not stacked else .17,
                        wspace=.33, hspace=.25)
    cax = fig.add_axes([.925, .28, .012, .48])
    fig.colorbar(im, cax=cax)
    fig.text(.025, .04, note, fontsize=11, linespacing=1.7)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{filename}.{ext}", dpi=150)
    a.plt.close(fig)


def report():
    corr_table, pair_table, removal_table = {}, {}, {}
    exports = []
    performance = []
    pair_labels = [FACTORS[j]+"×"+FACTORS[k] for j, k in PAIRS]
    directed = [(j, k) for j in range(4) for k in range(4) if j != k]
    add_labels = [f"{FACTORS[j]}→+{FACTORS[k]}" for j, k in directed]
    for g, al in CONDITIONS:
        tag = f"{g}_a{al}"
        corr = rows(OUT / f"{tag}_pair_correlations.csv")
        scores = rows(OUT / f"{tag}_subset_scores.csv")
        for kind in ("node", "edge"):
            for label in pair_labels:
                vals = [float(r["rho"]) for r in corr if r["kind"] == kind and r["pair"] == label]
                mean, sd = float(np.mean(vals)), float(np.std(vals))
                corr_table[kind, tag, label] = (mean, sd)
                exports.append(dict(condition=tag, kind=kind, metric="pair_rho", comparison=label, mean=mean, sd=sd, n_starts=len(vals)))
            starts = defaultdict(dict)
            for r in scores:
                if r["kind"] == kind:
                    starts[r["start"]][int(r["mask"])] = float(r["skill"])
            performance.append(dict(condition=tag, kind=kind, full_skill_pct=float(np.mean([s[15] for s in starts.values()])*100),
                                    sd_skill_pct=float(np.std([s[15] for s in starts.values()])*100)))
            for j, k in directed:
                label = f"{FACTORS[j]}→+{FACTORS[k]}"
                vals = [100*(s[(1<<j)|(1<<k)]-s[1<<j]) for s in starts.values()]
                mean, sd = float(np.mean(vals)), float(np.std(vals))
                pair_table[kind, tag, label] = (mean, sd)
                exports.append(dict(condition=tag, kind=kind, metric="pair_add_skill_pp", comparison=label, mean=mean, sd=sd, n_starts=len(vals)))
            for j, factor in enumerate(FACTORS):
                vals = [100*(s[15]-s[15^(1<<j)]) for s in starts.values()]
                mean, sd = float(np.mean(vals)), float(np.std(vals))
                removal_table[kind, tag, factor] = (mean, sd)
                exports.append(dict(condition=tag, kind=kind, metric="add_to_other_three_skill_pp", comparison=factor, mean=mean, sd=sd, n_starts=len(vals)))
    a.write_csv(OUT / "summary.csv", exports)
    a.write_csv(OUT / "full_model_performance.csv", performance)
    common = "アクセス単位。初回（R未定義）と末尾100アクセスは除外。各セルは8始点の平均。ばらつきはsummary.csvのsd。"
    plot_matrix("01_pair_correlations", "論点2①｜要素同士がどれだけ関連しているか", "全6ペアのSpearman順位相関：赤＝正、青＝負。相関の強さだけで情報の重複は確定しない。",
                pair_labels, corr_table, 1,
                common+"\nD＝次数、H＝今回の歩数、O＝累積頻度、R＝直近性（大きいほど最近）。\n全回数との相関ではなく、要素同士の相関。正でも負でも、絶対値が大きければ単調な関連が強い。\n無相関でも非線形な関連がありうる。次の2枚で、予測に追加する意味が残るかを確認する。")
    max_pair = max(abs(v[0]) for v in pair_table.values())
    plot_matrix("02_pair_addition", "論点2②｜一つ使っていても、もう一つを加える意味があるか", "例：O→+R ＝ Oだけの予測にRを加えた改善。緑＝改善、紫＝悪化。単位：Brier skillの差 ×100。",
                add_labels, pair_table, max(1, np.ceil(max_pair)),
                common+"\n7始点で学習し、未使用の1始点で評価。目的は『次の100アクセス以内の再訪』。\nすべての組み合わせを両方向で比較。A→+BとB→+Aは意味が異なる。\n改善はこの予測モデルでの追加価値であり、因果・最適重み・キャッシュヒット率の改善ではない。", cmap="PRGn", stacked=True)
    max_removal = max(abs(v[0]) for v in removal_table.values())
    plot_matrix("03_add_to_other_three", "論点2③｜残り3要素があっても、追加価値は残るか", "列の要素を加える前後を比較：緑＝追加で改善、紫＝追加で悪化。単位：Brier skillの差 ×100。",
                FACTORS, removal_table, max(1, np.ceil(max_removal)),
                common+"\n例：R列＝D・H・Oの3要素だけの場合と、D・H・O・Rの4要素を使った場合の差。\nBrier skill＝1−予測の二乗誤差／定数予測の二乗誤差。7始点で学習、残り1始点で評価する。\n0付近・負でも『情報がない』とは断定できない。モデルの表現力・データ量に依存するため、係数への直接換算はしない。", cmap="PRGn")
    text = ["# 論点2：各要素は他の要素とは別の情報を持つか", "", "アクセス単位・ノード/エッジ別・全12条件を、次の順に確認する。", "",
            "1. [要素同士の相関](01_pair_correlations.png)：関連の強いペアを探す。相関だけでは重複とは決めない。",
            "2. [1要素への追加効果](02_pair_addition.png)：全6ペアを両方向で比較し、既存要素に何を足すと改善するかを見る。",
            "3. [残り3要素への追加効果](03_add_to_other_three.png)：全体を使う場合に各要素の追加価値が残るかを見る。", "",
            "## 方法", "", "全3枚で母集団をそろえる。各アクセス直前のD・H・O・Rを使用し、Rのない初回と、100件先まで観測できない末尾を除く。これはキャッシュ候補全体ではなく、アクセスされた対象の比較。",
            "相関は始点内でSpearmanを計算して8始点で平均。Rは1/(1+前回からのアクセス間隔)。Dはノードの次数／エッジの両端点次数の平均。Hは今回の歩数。",
            "予測対象は次の100アクセス以内の同一対象への再アクセス（0/1）。全16部分集合（定数予測を含む）を、同じテストデータで比較する。",
            "検証はleave-one-start-out：7始点で学習して残り1始点で評価し、8通りの結果を等重み平均。始点をまたぐ学習の一般化を見るもので、時間順のオンライン検証ではない。同じ実体が別始点で現れることはある。",
            "予測モデルは学習データだけで決めた四分位ビンの組合せ別の再訪率。同値を分割せず、最大4^4セル。各セルは20件分の学習データ全体の率で平滑化し、未出現セルは全体率に戻す。ビン数・平滑化量はテスト結果から選んでいない。",
            "Brier skill = 1 - Brier(model)/Brier(training prevalence)。Brierはテストデータでの確率予測の平均二乗誤差。追加効果はskillの差×100であり、再アクセス率そのものの差ではない。",
            "相関・予測評価は記述的/モデル依存。差の標準偏差は信頼区間ではなく、始点間のばらつき。悪化は真の情報が有害という意味ではなく、細分化による推定誤差やモデル不適合も含む。", "",
            "## 数値", "", "summary.csvに全サマリ値と始点間SD、*_subset_scores.csvに始点別の全16モデルのBrier/skill、*_pair_correlations.csvに始点別の全6ペアの相関、manifestに既存アクセス列との一致確認を保存。", "",
            "## 再生成", "", "```bash", "python3 base/proposed_cache/figs/access_factors_distribution/information_overlap.py", "# 既存の集計から図だけ再生成", "python3 base/proposed_cache/figs/access_factors_distribution/information_overlap.py --plots-only", "```"]
    (OUT / "README.md").write_text("\n".join(text)+"\n")
    results = ["# 全体比較の結果", "", "## 読む順序", "",
               "①で要素同士の関係、②で1要素に対する追加効果、③で残り3要素に対する追加効果を見る。①が強くても②③に改善があれば完全な代替ではない。",
               "改善が小さい場合も『完全に同じ情報』とは断定しない。今回の四分位セルモデルで、その追加情報を活用できなかった可能性を含む。", "",
               "## 残り3要素への追加効果の概観", "",
               "各型の12条件を同じ重みで集計。単位はBrier skill差×100。正の条件数は有意差の検定ではない。", "",
               "| 型 | 追加要素 | 12条件平均 | 最小〜最大 | 平均差が正の条件数 |", "|---|---|---:|---:|---:|"]
    for kind in ("node", "edge"):
        for factor in FACTORS:
            vals = [removal_table[kind, f"{g}_a{al}", factor][0] for g, al in CONDITIONS]
            results.append(f"| {kind} | {factor} | {np.mean(vals):+.2f} | {min(vals):+.2f}〜{max(vals):+.2f} | {sum(v>0 for v in vals)}/12 |")
    results += ["", "## 4要素モデル自体の予測性能", "",
                "skill×100が正なら、学習データ全体の再訪率を常に出す定数予測より誤差が小さい。負なら定数予測より悪い。",
                "この値も併せて確認し、性能の低いモデル内での差だけを有効性と解釈しない。", "",
                "| 条件 | 型 | 4要素モデルのskill×100 | 始点間SD |", "|---|---|---:|---:|"]
    for r in performance:
        results.append(f"| {r['condition']} | {r['kind']} | {r['full_skill_pct']:+.2f} | {r['sd_skill_pct']:.2f} |")
    results += ["", "係数や足し算・掛け算はこの分析だけでは決めない。実際の退去候補の比較やキャッシュ性能での検証は別途必要。"]
    (OUT / "RESULTS.md").write_text("\n".join(results)+"\n")
    (OUT / "index.html").write_text('''<!doctype html><html lang="ja"><meta charset="utf-8"><title>論点2：要素の重複と追加価値</title><style>body{font:16px system-ui;margin:30px;background:#f4f7fa;color:#182c3b}main{max-width:1500px;margin:auto}img{width:100%;background:white}a{color:#006ba0}p{line-height:1.7}</style><main><h1>論点2：要素の重複と追加価値</h1><p>①要素同士の相関 → ②1要素への追加 → ③残り3要素への追加、の順に確認します。<a href="README.md">方法と指標の読み方</a></p>''' + "".join(f'<h2>{title}</h2><p><a href="{name}.pdf">PDF</a> · <a href="{name}.png">原寸PNG</a></p><a href="{name}.png"><img src="{name}.png" alt="{title}"></a>' for name,title in [("01_pair_correlations","① 全6ペアの相関"),("02_pair_addition","② 各要素を組み合わせる追加価値"),("03_add_to_other_three","③ 残り3要素がある場合の追加価値")])+"</main></html>")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plots-only", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if not args.plots_only:
        for graph in GRAPHS:
            print(f"Loading {graph}", flush=True)
            edges = a.source.srv.load_edge_list(a.ROOT / "dataset/Louvain/graph" / a.GRAPHS[graph])
            cluster = a.source.sim.VirtualCluster(edges, 2, None)
            cluster.build_servers(lambda: a.source.srv.build_authz_cache("none", 0), 1., 1.)
            degree = a.source.build_degree_map(cluster)
            collect(graph, cluster, degree)
            del cluster, degree, edges
            a.gc.collect()
    report()
    print(f"Saved overview: {OUT}", flush=True)
