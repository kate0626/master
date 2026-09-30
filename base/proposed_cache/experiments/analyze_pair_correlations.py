#!/usr/bin/env python3
# =============================================================================
# analyze_pair_correlations.py
#
#   4 要素 (次数 D / 始点からの hop H / アクセス回数 O / 時間 R) から 2 つ選ぶ
#   **4C2 = 6 ペア** について、「その 2 要素に相関があるか」を確かめる。
#   目的変数 (将来アクセス回数) は使わない。要素そのもの同士の関係だけを見る。
#
#   各ペアについて出すもの:
#     - Spearman ρ      : 単調な相関の強さ (視点ごとに計算 → 視点間 mean±sd)
#     - Kendall τ-b     : tie が多い軸 (O, H) 向けの頑健版
#     - NMI             : 正規化相互情報量。**単調でない依存**も拾う
#                         (ρ≈0 なのに NMI が大きい = U 字などの非単調な関係)
#     - 偏相関 ρ|others : 残り 2 要素を統制した順位偏相関
#                         (第三の要素が作った見かけの相関かを切り分ける)
#     - 95% CI          : 視点 (start) をクラスタ単位にしたブートストラップ
#     - 判定            : CI が 0 をまたがない かつ |ρ|>=--rho-min なら「相関あり」
#
#   n が数千〜数十万あるので p 値はほぼ必ず有意になる。**効果量 |ρ| と CI で判定する。**
#
#   使い方:
#     python3 analyze_pair_correlations.py \
#       --keys results/accessfactors_vldb_a0.1_w1000_s8_keys.csv --kind node
# =============================================================================
from __future__ import annotations

import argparse
import csv
import itertools
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

try:
    from scipy.stats import kendalltau as _kendalltau
except ImportError:
    _kendalltau = None

HOP_METRICS = {"walk": "hop_walk", "walk_min": "hop_walk_min", "bfs": "hop"}
FEATURES = [("deg", "D 次数"), ("hop", "H 始点hop数"),
            ("past", "O アクセス回数"), ("recency", "R 時間(直近性)")]
FNAMES = [f[0] for f in FEATURES]
LABEL = dict(FEATURES)
LABEL_EN = {"deg": "D degree", "hop": "H hop from start",
            "past": "O access count", "recency": "R recency (1/age)"}


# =============================================================================
def load(keys_csv: Path, kind: str, hop_metric: str
         ) -> Dict[int, Dict[str, np.ndarray]]:
    """視点 (start) ごとの特徴量。母集団は観測窓で 1 回以上触れたキー。"""
    hop_col = HOP_METRICS.get(hop_metric, "hop_walk")
    by: Dict[int, Dict[str, List[float]]] = defaultdict(
        lambda: {k: [] for k in ("deg", "hop", "past", "age", "is_edge")})
    with keys_csv.open() as fh:
        rd = csv.DictReader(fh)
        if hop_col not in (rd.fieldnames or []):
            raise SystemExit(f"{keys_csv.name} に {hop_col} 列が無い")
        for r in rd:
            if float(r["past"]) <= 0:          # 観測窓で見えていないキーは除く
                continue
            ie = float(r["is_edge"])
            if kind == "node" and ie > 0.5:
                continue
            if kind == "edge" and ie < 0.5:
                continue
            d = by[int(r["start"])]
            d["deg"].append(float(r["deg"]))
            d["hop"].append(float(r[hop_col]))
            d["past"].append(float(r["past"]))
            d["age"].append(float(r["age"]))
            d["is_edge"].append(ie)
    out: Dict[int, Dict[str, np.ndarray]] = {}
    for s, d in by.items():
        age = np.asarray(d["age"], dtype=float)
        out[s] = {"deg": np.asarray(d["deg"]), "hop": np.asarray(d["hop"]),
                  "past": np.asarray(d["past"]),
                  "recency": 1.0 / (1.0 + np.where(age < 0, np.nan, age))}
    return out


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
    if x.size < 3:
        return float("nan")
    rx, ry = rankdata(x), rankdata(y)
    if rx.std() < 1e-12 or ry.std() < 1e-12:
        return float("nan")
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (rx.std() * ry.std()))


def partial_spearman(x: np.ndarray, y: np.ndarray, zs: List[np.ndarray]) -> float:
    """zs (複数) を統制した順位偏相関。順位に直して線形回帰の残差同士で相関。"""
    if x.size < 5 or not zs:
        return spearman(x, y)
    Z = np.column_stack([rankdata(z) for z in zs] + [np.ones(x.size)])
    rx, ry = rankdata(x), rankdata(y)

    def resid(r):
        beta, *_ = np.linalg.lstsq(Z, r, rcond=None)
        return r - Z @ beta

    ex, ey = resid(rx), resid(ry)
    if ex.std() < 1e-12 or ey.std() < 1e-12:
        return float("nan")
    return float(((ex - ex.mean()) * (ey - ey.mean())).mean() / (ex.std() * ey.std()))


def entropy(c: np.ndarray) -> float:
    n = c.sum()
    if n <= 0:
        return 0.0
    p = c[c > 0] / n
    return float(-(p * np.log2(p)).sum())


def nmi(x: np.ndarray, y: np.ndarray, nbins: int) -> float:
    """正規化相互情報量 (0..1)。非単調な依存も拾う。Miller-Madow 補正つき。"""
    def qbin(v):
        q = np.unique(np.quantile(v[np.isfinite(v)], np.linspace(0, 1, nbins + 1)))
        if q.size < 2:
            return np.zeros(v.size, dtype=int), 1
        b = np.clip(np.digitize(v, q[1:-1], right=True), 0, q.size - 2)
        return b.astype(int), q.size - 1
    a, na = qbin(x)
    b, nb = qbin(y)
    joint = np.bincount(a * nb + b, minlength=na * nb).astype(float).reshape(na, nb)
    n = joint.sum()
    if n <= 0:
        return float("nan")
    mi = entropy(joint.sum(axis=1)) + entropy(joint.sum(axis=0)) - entropy(joint)
    occ = (joint > 0).sum()
    r = (joint.sum(axis=1) > 0).sum()
    c = (joint.sum(axis=0) > 0).sum()
    mi = max(mi - (occ - r - c + 1) / (2.0 * n * math.log(2)), 0.0)
    denom = max(min(entropy(joint.sum(axis=1)), entropy(joint.sum(axis=0))), 1e-9)
    return float(mi / denom)


def kendall(x: np.ndarray, y: np.ndarray, cap: int, rng) -> float:
    if _kendalltau is None:
        return float("nan")
    if x.size > cap:
        idx = rng.choice(x.size, cap, replace=False)
        x, y = x[idx], y[idx]
    t, _ = _kendalltau(x, y, variant="b")
    return float(t)


# =============================================================================
def plot(per_start, rows, label: str, out_png: Path, nbins: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pooled = {f: np.concatenate([per_start[s][f] for s in per_start]) for f in FNAMES}
    info = {(r["x"], r["z"]): r for r in rows}
    fig, axes = plt.subplots(2, 3, figsize=(16, 8.5))
    for ax, (fa, fb) in zip(axes.ravel(), itertools.combinations(FNAMES, 2)):
        x, y = pooled[fa], pooled[fb]
        m = np.isfinite(x) & np.isfinite(y)
        x, y = x[m], y[m]
        q = np.unique(np.quantile(x, np.linspace(0, 1, nbins + 1)))
        idx = np.clip(np.digitize(x, q[1:-1], right=True), 0, max(q.size - 2, 0))
        xs, med, lo, hi = [], [], [], []
        for b in range(max(q.size - 1, 1)):
            sel = idx == b
            if sel.sum() < 5:
                continue
            xs.append(np.median(x[sel])); med.append(np.median(y[sel]))
            lo.append(np.percentile(y[sel], 25)); hi.append(np.percentile(y[sel], 75))
        ax.fill_between(xs, lo, hi, alpha=0.2, color="#4c72b0", label="IQR")
        ax.plot(xs, med, marker="o", color="#4c72b0", label="median")
        if fa in ("deg", "past"):
            ax.set_xscale("log")
        if fb in ("deg", "past", "recency"):
            ax.set_yscale("log")
        r = info[(fa, fb)]
        ax.set_xlabel(LABEL_EN[fa]); ax.set_ylabel(LABEL_EN[fb])
        ax.set_title(f"{LABEL_EN[fa]} x {LABEL_EN[fb]}\n"
                     f"rho={r['rho']:+.3f} [{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]  "
                     f"NMI={r['nmi']:.3f}  {r['verdict_en']}", fontsize=9.5)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(f"Pairwise correlation between factors - {label}", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_png, dpi=150)
    import matplotlib.pyplot as _p; _p.close(fig)


# =============================================================================
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="4要素から2つ選んだ6ペアの相関の有無を調べる")
    ap.add_argument("--keys", required=True)
    ap.add_argument("--kind", choices=["all", "node", "edge"], default="all")
    ap.add_argument("--hop-metric", choices=["walk", "walk_min", "bfs"], default="walk")
    ap.add_argument("--bins", type=int, default=8)
    ap.add_argument("--rho-min", type=float, default=0.10,
                    help="これ未満の |ρ| は『実質相関なし』とみなす")
    ap.add_argument("--boot", type=int, default=2000, help="視点クラスタ・ブートストラップ回数")
    ap.add_argument("--kendall-cap", type=int, default=20000)
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
               else base / "figs" / "access_factors" / "03_pair_correlation")
    out_dir.mkdir(parents=True, exist_ok=True); fig_dir.mkdir(parents=True, exist_ok=True)
    label = a.label or keys.name.replace("accessfactors_", "").replace("_keys.csv", "")
    tag = f"{label}_{a.kind}_h{a.hop_metric}"

    per_start = load(keys, a.kind, a.hop_metric)
    starts = sorted(per_start)
    ns = {s: per_start[s]["deg"].size for s in starts}
    rng = np.random.default_rng(a.seed)

    print("=" * 96)
    print(f"keys={keys.name}  kind={a.kind}  hop={a.hop_metric}  "
          f"視点={len(starts)}  キー総数={sum(ns.values()):,}")
    print("=" * 96)

    rows = []
    for fa, fb in itertools.combinations(FNAMES, 2):
        # 視点ごとに計算 → 視点間で平均 (視点をまたいだプールは文脈が混ざる)
        per = []
        for s in starts:
            x, y = per_start[s][fa], per_start[s][fb]
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() >= 5:
                per.append(spearman(x[m], y[m]))
        per = np.asarray([v for v in per if np.isfinite(v)], dtype=float)
        rho = float(per.mean()) if per.size else float("nan")
        sd = float(per.std(ddof=1)) if per.size > 1 else 0.0
        # 視点をクラスタとしたブートストラップ CI
        if per.size > 1:
            bs = per[rng.integers(0, per.size, size=(a.boot, per.size))].mean(axis=1)
            ci_lo, ci_hi = np.percentile(bs, [2.5, 97.5])
        else:
            ci_lo = ci_hi = rho

        pooled_x = np.concatenate([per_start[s][fa] for s in starts])
        pooled_y = np.concatenate([per_start[s][fb] for s in starts])
        mm = np.isfinite(pooled_x) & np.isfinite(pooled_y)
        tau = kendall(pooled_x[mm], pooled_y[mm], a.kendall_cap, rng)
        nm = nmi(pooled_x[mm], pooled_y[mm], a.bins)
        others = [f for f in FNAMES if f not in (fa, fb)]
        zs = [np.concatenate([per_start[s][f] for s in starts])[mm] for f in others]
        pr = partial_spearman(pooled_x[mm], pooled_y[mm], zs)

        crosses0 = (ci_lo <= 0.0 <= ci_hi)
        if crosses0:
            verdict, ven = "判定不能 (CIが0をまたぐ)", "n.s."
        elif abs(rho) < a.rho_min:
            verdict, ven = f"実質なし (|ρ|<{a.rho_min})", "negligible"
        else:
            verdict = "相関あり (" + ("正" if rho > 0 else "負") + ")"
            ven = "correlated (+)" if rho > 0 else "correlated (-)"
        # 単調でない依存の検出
        if abs(rho) < a.rho_min and nm >= 0.05:
            verdict += " ※非単調な依存あり(NMI)"
            ven += " / nonmonotone"

        rows.append({"x": fa, "z": fb, "rho": rho, "sd": sd, "ci_lo": float(ci_lo),
                     "ci_hi": float(ci_hi), "tau": tau, "nmi": nm, "partial": pr,
                     "verdict": verdict, "verdict_en": ven})

    print(f"\n{'ペア':<26}{'Spearman ρ':>13}{'95%CI':>20}{'Kendall τ':>11}"
          f"{'NMI':>8}{'ρ|他2要素':>11}  判定")
    print("-" * 118)
    for r in rows:
        pair = f"{LABEL[r['x']]} × {LABEL[r['z']]}"
        ci = f"[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}]"
        print(f"{pair:<26}{r['rho']:>+9.3f}±{r['sd']:<4.2f}{ci:>20}"
              f"{r['tau']:>+11.3f}{r['nmi']:>8.3f}{r['partial']:>+11.3f}  {r['verdict']}")

    csv_p = out_dir / f"paircorr_{tag}.csv"
    with csv_p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\n[saved] {csv_p}")
    if not a.no_figs:
        png = fig_dir / f"paircorr_{tag}.png"
        plot(per_start, rows, tag, png, a.bins)
        print(f"[saved] {png}")


if __name__ == "__main__":
    main()
