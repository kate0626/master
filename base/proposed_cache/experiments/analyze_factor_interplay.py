#!/usr/bin/env python3
# =============================================================================
# analyze_factor_interplay.py
#
#   「各要素がアクセス回数と関係がある」ことが分かった後に、**要素同士の関係**を
#   測るための分析。3 段構えで、目的の違う 3 つの問いに答える。
#
#     (1) 素の依存     : 要素どうしがそもそも相関しているか
#                        → Spearman ρ と 正規化相互情報量 NMI
#     (2) 目的変数を介した重なり : その相関は「同じ情報」なのか「別の情報」なのか
#                        → 条件付き相互情報量 I(X;Y|Z) と 交互作用情報量
#                          II = I(X,Z;Y) − I(X;Y) − I(Z;Y)
#                          II<0 = 冗長 (redundant) / II>0 = 相乗 (synergy)
#     (3) 足し算になる分解 : 4 要素の寄与を重複なく配分する
#                        → Shapley 値 (2^4=16 部分集合, 交差適合 Poisson 逸脱度)
#
#   目的変数はカウント (将来アクセス回数) なので、予測性能は Poisson 逸脱度の
#   説明率 D² を使う。部分集合 S の予測子は「S のビンで層別したセル平均」で、
#   これは категор変数だけの Poisson GLM の最尤解と一致する。過適合を避けるため
#   2-fold の交差適合 (片方で当てはめ、他方で評価、両方向の平均) を行う。
#
#   入力は analyze_access_factors.py が出す keys CSV。
#     python3 analyze_factor_interplay.py \
#       --keys results/accessfactors_vldb_a0.1_w1000_s8_keys.csv
# =============================================================================
from __future__ import annotations

import argparse
import csv
import itertools
import math
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np

FEATURES = [("deg", "D 次数"), ("hop", "H hop"),
            ("past", "O 過去回数"), ("recency", "R 直近性")]
FNAMES = [f[0] for f in FEATURES]


# =============================================================================
# 読み込み
# =============================================================================
HOP_METRICS = {"walk": "hop_walk", "walk_min": "hop_walk_min", "bfs": "hop"}


def load(keys_csv: Path, kind: str, hop_metric: str = "walk"
         ) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    hop_col = HOP_METRICS.get(hop_metric, "hop_walk")
    cols: Dict[str, List[float]] = {c: [] for c in
                                    ("is_edge", "deg", "past", "future", "age")}
    cols[hop_col] = []
    with keys_csv.open() as fh:
        rd = csv.DictReader(fh)
        if hop_col not in (rd.fieldnames or []):
            raise SystemExit(f"{keys_csv.name} に {hop_col} 列が無い "
                             f"(--hop-metric を変えるか keys を作り直す)")
        for r in rd:
            for c in cols:
                cols[c].append(float(r[c]))
    arr = {c: np.asarray(v, dtype=float) for c, v in cols.items()}
    m = arr["past"] > 0                      # 観測窓で見えたキーだけ (載りうる集合)
    if kind == "node":
        m &= arr["is_edge"] < 0.5
    elif kind == "edge":
        m &= arr["is_edge"] > 0.5
    age = arr["age"][m]
    feats = {"deg": arr["deg"][m], "hop": arr[hop_col][m], "past": arr["past"][m],
             "recency": 1.0 / (1.0 + np.where(age < 0, np.nan, age))}
    return feats, arr["future"][m]


def quantile_bins(x: np.ndarray, nbins: int) -> np.ndarray:
    """分位ビン。tie が多い軸 (past, hop) では unique 値で潰れるのを許容する。"""
    q = np.unique(np.quantile(x[np.isfinite(x)], np.linspace(0, 1, nbins + 1)))
    if q.size < 2:
        return np.zeros(x.size, dtype=int)
    b = np.clip(np.digitize(x, q[1:-1], right=True), 0, q.size - 2)
    return b.astype(int)


# =============================================================================
# (1)(2) 情報量
# =============================================================================
def entropy(counts: np.ndarray) -> float:
    n = counts.sum()
    if n <= 0:
        return 0.0
    p = counts[counts > 0] / n
    return float(-(p * np.log2(p)).sum())


def mutual_info(a: np.ndarray, b: np.ndarray) -> float:
    """離散ラベル同士の相互情報量 [bit]。Miller-Madow で下駄を補正する。"""
    na, nb = a.max() + 1, b.max() + 1
    joint = np.bincount(a * nb + b, minlength=na * nb).astype(float)
    n = joint.sum()
    mi = (entropy(joint.reshape(na, nb).sum(axis=1))
          + entropy(joint.reshape(na, nb).sum(axis=0))
          - entropy(joint))
    # Miller-Madow: (占有セル数 − 行 − 列 + 1) / (2 n ln2) 分だけ過大評価される
    occ = (joint > 0).sum()
    r = (joint.reshape(na, nb).sum(axis=1) > 0).sum()
    c = (joint.reshape(na, nb).sum(axis=0) > 0).sum()
    bias = (occ - r - c + 1) / (2.0 * n * math.log(2)) if n > 0 else 0.0
    return float(max(mi - bias, 0.0))


def cond_mutual_info(a: np.ndarray, b: np.ndarray, z: np.ndarray) -> float:
    """I(A;B|Z) = Σ_z p(z) I(A;B | Z=z)"""
    tot = 0.0
    n = a.size
    for zv in np.unique(z):
        m = z == zv
        if m.sum() < 20:
            continue
        tot += (m.sum() / n) * mutual_info(a[m], b[m])
    return float(tot)


def joint_labels(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a * (b.max() + 1) + b).astype(int)


def rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    sa = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = rankdata(x), rankdata(y)
    if rx.std() < 1e-12 or ry.std() < 1e-12:
        return float("nan")
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (rx.std() * ry.std()))


# =============================================================================
# (3) Shapley (交差適合 Poisson 逸脱度の説明率 D²)
# =============================================================================
def poisson_deviance(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.maximum(mu, 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2.0 * (t - (y - mu)).sum())


def d2_of_subset(bins: Dict[str, np.ndarray], y: np.ndarray,
                 subset: Sequence[str], folds: np.ndarray, m_smooth: float) -> float:
    """部分集合 S のビンで層別したセル平均を予測子とし、交差適合で D² を出す。
    S=∅ なら全体平均 (= null model) なので D²=0。"""
    if not subset:
        return 0.0
    cell = np.zeros(y.size, dtype=np.int64)
    for f in subset:
        b = bins[f]
        cell = cell * (b.max() + 1) + b
    # ラベルを 0..K-1 に詰め直す
    _, cell = np.unique(cell, return_inverse=True)
    K = cell.max() + 1

    mu = np.empty(y.size, dtype=float)
    for k in (0, 1):
        tr, ev = folds != k, folds == k
        gm = y[tr].mean() if tr.sum() else y.mean()
        s = np.bincount(cell[tr], weights=y[tr], minlength=K)
        c = np.bincount(cell[tr], minlength=K).astype(float)
        cm = (s + m_smooth * gm) / (c + m_smooth)      # 小セルは全体平均へ縮小
        mu[ev] = cm[cell[ev]]
    dev_null = poisson_deviance(y, np.full(y.size, y.mean()))
    if dev_null <= 0:
        return 0.0
    return float(1.0 - poisson_deviance(y, mu) / dev_null)


def shapley(values: Dict[frozenset, float], names: Sequence[str]) -> Dict[str, float]:
    n = len(names)
    fact = [math.factorial(i) for i in range(n + 1)]
    out = {}
    for f in names:
        tot = 0.0
        others = [g for g in names if g != f]
        for r in range(n):
            for S in itertools.combinations(others, r):
                w = fact[len(S)] * fact[n - len(S) - 1] / fact[n]
                tot += w * (values[frozenset(S) | {f}] - values[frozenset(S)])
        out[f] = tot
    return out


# =============================================================================
# 作図
# =============================================================================
def plot(res: Dict, label: str, out_png: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 4, figsize=(19, 4.6))
    lbl = [f[1].split()[0] for f in FEATURES]

    def heat(ax, mat, title, cmap, vmin, vmax, fmt="{:+.2f}"):
        im = ax.imshow(mat, cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_xticks(range(4)); ax.set_yticks(range(4))
        ax.set_xticklabels(lbl, fontsize=8); ax.set_yticklabels(lbl, fontsize=8)
        for i in range(4):
            for j in range(4):
                if np.isfinite(mat[i][j]):
                    ax.text(j, i, fmt.format(mat[i][j]), ha="center", va="center",
                            fontsize=8)
        ax.set_title(title, fontsize=10)
        fig.colorbar(im, ax=ax, fraction=0.046)

    heat(axes[0], res["rho"], "(1a) Spearman rho between factors", "coolwarm", -1, 1)
    heat(axes[1], res["nmi"], "(1b) Normalized mutual information", "viridis", 0,
         max(0.05, np.nanmax(res["nmi"])), "{:.2f}")
    v = np.nanmax(np.abs(res["ii"]))
    heat(axes[2], res["ii"], "(2) Interaction information II [bit]\n<0 redundant  /  >0 synergistic",
         "coolwarm_r", -v, v, "{:+.3f}")

    ax = axes[3]
    sh = [res["shapley"][f] for f in FNAMES]
    ax.bar(range(4), sh, color="#4c72b0")
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(range(4)); ax.set_xticklabels(lbl, fontsize=9)
    ax.set_ylabel("Shapley value (share of D2)")
    ax.set_title(f"(3) Shapley decomposition  (total D2={res['d2_full']:.3f})", fontsize=10)
    ax.grid(alpha=0.3, axis="y")
    for i, s in enumerate(sh):
        ax.text(i, s, f"{s:+.3f}", ha="center",
                va="bottom" if s >= 0 else "top", fontsize=8)

    fig.suptitle(f"Interplay between factors - {label}", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


# =============================================================================
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="4 要素どうしの関係 (冗長/相補/相乗) を測る")
    ap.add_argument("--keys", required=True, help="analyze_access_factors.py の keys CSV")
    ap.add_argument("--kind", choices=["all", "node", "edge"], default="all")
    ap.add_argument("--hop-metric", choices=["walk", "walk_min", "bfs"],
                    default="walk",
                    help="H の定義。walk=初回アクセス時の歩数 (既定) / bfs=最短距離")
    ap.add_argument("--bins", type=int, default=6, help="各要素の分位ビン数")
    ap.add_argument("--ybins", type=int, default=6, help="目的変数のビン数 (情報量用)")
    ap.add_argument("--smooth", type=float, default=5.0, help="セル平均の縮小係数 m")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--label", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--fig-dir", default=None)
    ap.add_argument("--no-figs", action="store_true")
    a = ap.parse_args(argv)

    keys = Path(a.keys)
    base = Path(__file__).resolve().parent.parent
    out_dir = Path(a.out_dir) if a.out_dir else base / "results"
    fig_dir = (Path(a.fig_dir) if a.fig_dir
               else base / "figs" / "access_factors" / "04_interplay")
    out_dir.mkdir(parents=True, exist_ok=True); fig_dir.mkdir(parents=True, exist_ok=True)
    label = a.label or keys.name.replace("accessfactors_", "").replace("_keys.csv", "")
    tag = f"{label}_{a.kind}"

    feats, y = load(keys, a.kind, a.hop_metric)
    n = y.size
    print("=" * 78)
    print(f"keys={keys.name}  kind={a.kind}  hop={a.hop_metric}  n={n:,}  "
          f"将来アクセス合計={y.sum():,.0f}  ゼロ率={(y==0).mean()*100:.1f}%")
    print("=" * 78)

    bins = {f: quantile_bins(feats[f], a.bins) for f in FNAMES}
    # 目的変数はカウント (0 が過半を占める)。分位ビンだと 1 ビンに潰れるので値で切る。
    ybin = np.digitize(y, [1, 2, 3, 5, 9], right=False).astype(int)
    if np.unique(ybin).size < 2:
        ybin = quantile_bins(y, a.ybins)
    hy = entropy(np.bincount(ybin).astype(float))

    # ---- (1) 素の依存 ----
    rho = np.full((4, 4), np.nan); nmi = np.full((4, 4), np.nan)
    for i in range(4):
        rho[i][i] = 1.0; nmi[i][i] = 1.0
        for j in range(i + 1, 4):
            rho[i][j] = rho[j][i] = spearman(feats[FNAMES[i]], feats[FNAMES[j]])
            hi = entropy(np.bincount(bins[FNAMES[i]]).astype(float))
            hj = entropy(np.bincount(bins[FNAMES[j]]).astype(float))
            mij = mutual_info(bins[FNAMES[i]], bins[FNAMES[j]])
            nmi[i][j] = nmi[j][i] = mij / max(min(hi, hj), 1e-9)

    print("\n### (1) 要素どうしの素の依存")
    print(f"{'pair':<22}{'Spearman ρ':>14}{'正規化相互情報量':>20}")
    for i in range(4):
        for j in range(i + 1, 4):
            print(f"{FNAMES[i]+' - '+FNAMES[j]:<22}{rho[i][j]:>+14.3f}{nmi[i][j]:>20.3f}")

    # ---- (2) 目的変数を介した重なり ----
    mi_y = {f: mutual_info(bins[f], ybin) for f in FNAMES}
    print(f"\n### (2) 目的変数 Y との情報量  [bit]   (H(Y)={hy:.3f})")
    if hy < 1e-6:
        print("  ※ 将来アクセスがほぼ全ゼロで H(Y)=0。情報量は定義できないので "
              "(2)(2') は省略し、Shapley のみ出す。")
    else:
        print(f"{'factor':<10}{'I(X;Y)':>10}{'/H(Y)':>9}")
        for f in FNAMES:
            print(f"{f:<10}{mi_y[f]:>10.3f}{100*mi_y[f]/hy:>8.1f}%")

    ii = np.full((4, 4), np.nan)
    if hy < 1e-6:
        sh_skip = True
    else:
        sh_skip = False
    print(f"\n### (2') 2 要素の重なり方")
    print(f"{'pair (X,Z)':<22}{'I(X;Y)':>9}{'I(X;Y|Z)':>11}{'残存率':>9}"
          f"{'II':>9}  判定")
    for i in range(4):
        for j in range(i + 1, 4):
            if sh_skip:
                break
            fx, fz = FNAMES[i], FNAMES[j]
            cmi = cond_mutual_info(bins[fx], ybin, bins[fz])
            joint = mutual_info(joint_labels(bins[fx], bins[fz]), ybin)
            iiv = joint - mi_y[fx] - mi_y[fz]
            ii[i][j] = ii[j][i] = iiv
            keep = 100 * cmi / mi_y[fx] if mi_y[fx] > 1e-9 else float("nan")
            if iiv < -0.02:
                verdict = "冗長 (同じ情報)"
            elif iiv > 0.02:
                verdict = "相乗 (組で初めて効く)"
            else:
                verdict = "ほぼ独立 (足し算)"
            print(f"{fx+' , '+fz:<22}{mi_y[fx]:>9.3f}{cmi:>11.3f}{keep:>8.0f}%"
                  f"{iiv:>+9.3f}  {verdict}")

    # ---- (3) Shapley ----
    rng = np.random.default_rng(a.seed)
    folds = rng.integers(0, 2, size=n)
    vals: Dict[frozenset, float] = {}
    for r in range(5):
        for S in itertools.combinations(FNAMES, r):
            vals[frozenset(S)] = d2_of_subset(bins, y, S, folds, a.smooth)
    sh = shapley(vals, FNAMES)
    d2_full = vals[frozenset(FNAMES)]

    print(f"\n### (3) Shapley 分解 (交差適合 Poisson 逸脱度の説明率 D²)")
    print(f"{'factor':<10}{'単独 D²':>12}{'Shapley':>12}{'全体に占める':>14}")
    for f in FNAMES:
        solo = vals[frozenset([f])]
        share = 100 * sh[f] / d2_full if d2_full > 1e-9 else float("nan")
        print(f"{f:<10}{solo:>12.4f}{sh[f]:>+12.4f}{share:>13.1f}%")
    print(f"{'合計':<10}{'':>12}{sum(sh.values()):>+12.4f}   (D²_full={d2_full:.4f})")

    print(f"\n### (3') 2 要素の交互作用 D²(X,Z) − D²(X) − D²(Z)")
    print(f"{'pair':<22}{'D²(X)':>9}{'D²(Z)':>9}{'D²(X,Z)':>11}{'交互作用':>11}")
    for i in range(4):
        for j in range(i + 1, 4):
            fx, fz = FNAMES[i], FNAMES[j]
            dx, dz = vals[frozenset([fx])], vals[frozenset([fz])]
            dxz = vals[frozenset([fx, fz])]
            print(f"{fx+' , '+fz:<22}{dx:>9.4f}{dz:>9.4f}{dxz:>11.4f}"
                  f"{dxz-dx-dz:>+11.4f}")

    # ---- 保存 ----
    csv_p = out_dir / f"interplay_{tag}.csv"
    with csv_p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "x", "z", "value"])
        for i in range(4):
            for j in range(4):
                if i < j:
                    w.writerow(["spearman", FNAMES[i], FNAMES[j], f"{rho[i][j]:.6f}"])
                    w.writerow(["nmi", FNAMES[i], FNAMES[j], f"{nmi[i][j]:.6f}"])
                    w.writerow(["interaction_info", FNAMES[i], FNAMES[j],
                                f"{ii[i][j]:.6f}"])
        for f in FNAMES:
            w.writerow(["mi_y", f, "", f"{mi_y[f]:.6f}"])
            w.writerow(["shapley_d2", f, "", f"{sh[f]:.6f}"])
            w.writerow(["solo_d2", f, "", f"{vals[frozenset([f])]:.6f}"])
        w.writerow(["d2_full", "", "", f"{d2_full:.6f}"])
    print(f"\n[saved] {csv_p}")

    if not a.no_figs:
        png = fig_dir / f"interplay_{tag}.png"
        plot({"rho": rho, "nmi": nmi, "ii": ii, "shapley": sh, "d2_full": d2_full},
             tag, png)
        print(f"[saved] {png}")


if __name__ == "__main__":
    main()
