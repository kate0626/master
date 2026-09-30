#!/usr/bin/env python3
# =============================================================================
# analyze_score_form.py
#
#   「キャッシュの価値式は 掛け算か 足し算か」を実データで比べる。
#
#   現行:  V = O^θ · H^β · D^γ · R^ρ        ← 対数を取ると log V = Σ 係数×log(因子)
#          つまり **積形 = 対数スケールでの加法モデル**。
#   したがって本当の論点は 2 つ:
#     (a) どのスケールで足すか  … 対数(積) か 線形(和) か
#     (b) 交互作用項が要るか    … 冗長なペアは重みを下げ、相乗なペアは交差項を足す
#
#   ここでは候補式を実際に当てはめ、**交差適合**で 2 つの観点から比較する:
#     - D²        : Poisson 逸脱度の説明率 (予測精度)
#     - cov@C     : スコア上位 C 件が将来アクセスの何 % を覆うか (ランキング品質。
#                   キャッシュが実際に使うのはこちら)
#
#   比較する式:
#     M0_O        V = O のみ                        (単一因子の基準線)
#     M1_MUL      log V = Σ βi·zi                    ← **現行の積形**
#     M1_ADD      V     = Σ βi·xi (線形スケールの和) ← **足し算版**
#     M2_MUL_INT  log V = Σ βi·zi + Σ δij·zi·zj      ← 積形 + 交互作用
#     M3_MUL_noD  積形から D を抜いたもの            (次数の要否)
#     M4_MUL_noR  積形から R を抜いたもの            (直近性の要否)
#
#   zi は各因子を現行と同じ [1,2) 正規化してから対数を取ったもの、
#   xi はその正規化値そのもの。係数はデータから当てはめる (＝各式の最良ケース)。
#
#   python3 analyze_score_form.py --keys results/accessfactors_vldb_a0.1_..._keys.csv --kind node
# =============================================================================
from __future__ import annotations

import argparse
import csv
import itertools
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import statsmodels.api as sm

HOP_METRICS = {"walk": "hop_walk", "walk_min": "hop_walk_min", "bfs": "hop"}
FNAMES = ["deg", "hop", "past", "recency"]
JP = {"deg": "D 次数", "hop": "H hop", "past": "O 回数", "recency": "R 直近性"}


def load(keys_csv: Path, kind: str, hop_metric: str):
    hop_col = HOP_METRICS.get(hop_metric, "hop_walk")
    cols = {c: [] for c in ("is_edge", "deg", hop_col, "past", "future", "age")}
    with keys_csv.open() as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            if float(r["past"]) <= 0:
                continue
            ie = float(r["is_edge"])
            if (kind == "node" and ie > 0.5) or (kind == "edge" and ie < 0.5):
                continue
            for c in cols:
                cols[c].append(float(r[c]))
    a = {c: np.asarray(v, dtype=float) for c, v in cols.items()}
    return ({"deg": a["deg"], "hop": a[hop_col], "past": a["past"],
             "age": np.where(a["age"] < 0, np.nan, a["age"])}, a["future"])


def normalize(raw: Dict[str, np.ndarray], cf: float, cd: float, cr: float
              ) -> Dict[str, np.ndarray]:
    """現行実装と同じ [1,2) 正規化。各因子とも大きいほど『残すべき』向き。"""
    hop = np.maximum(raw["hop"], 0.0)
    age = np.nan_to_num(raw["age"], nan=np.nanmax(raw["age"]) if
                        np.isfinite(raw["age"]).any() else 1.0)
    return {
        "deg": 1.0 + raw["deg"] / (raw["deg"] + cd),
        "hop": 1.0 + 1.0 / (1.0 + hop),          # 近いほど大 (単調変換)
        "past": 1.0 + raw["past"] / (raw["past"] + cf),
        "recency": 1.0 + cr / (age + cr),
    }


def poisson_dev(y: np.ndarray, mu: np.ndarray) -> float:
    mu = np.maximum(mu, 1e-9)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(2.0 * (t - (y - mu)).sum())


def cov_at(score: np.ndarray, y: np.ndarray, cap: int) -> float:
    tot = y.sum()
    if tot <= 0:
        return float("nan")
    idx = np.argsort(-score, kind="mergesort")[:min(cap, score.size)]
    return float(100.0 * y[idx].sum() / tot)


def fit_eval(X: np.ndarray, y: np.ndarray, folds: np.ndarray, link_log: bool,
             cap: int) -> Tuple[float, float]:
    """交差適合で D² と cov@C を返す。link_log=True なら log リンク (=積形)、
    False なら恒等リンク (=線形スケールの和)。"""
    mu = np.empty(y.size, dtype=float)
    Xc = sm.add_constant(X, has_constant="add")
    for k in (0, 1):
        tr, ev = folds != k, folds == k
        fam = sm.families.Poisson(sm.families.links.Log() if link_log
                                  else sm.families.links.Identity())
        try:
            start = None
            if not link_log:            # 恒等リンクは初期値が要る
                start = np.zeros(Xc.shape[1]); start[0] = max(y[tr].mean(), 1e-3)
            res = sm.GLM(y[tr], Xc[tr], family=fam).fit(start_params=start,
                                                        maxiter=200)
            pred = res.predict(Xc[ev])
        except Exception:
            pred = np.full(ev.sum(), y[tr].mean())
        mu[ev] = np.maximum(np.nan_to_num(pred, nan=y[tr].mean()), 1e-9)
    dev_null = poisson_dev(y, np.full(y.size, y.mean()))
    d2 = 1.0 - poisson_dev(y, mu) / dev_null if dev_null > 0 else 0.0
    return float(d2), cov_at(mu, y, cap)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="価値式の形 (積 vs 和 vs 交互作用) を比べる")
    ap.add_argument("--keys", required=True)
    ap.add_argument("--kind", choices=["all", "node", "edge"], default="node")
    ap.add_argument("--hop-metric", choices=["walk", "walk_min", "bfs"], default="walk")
    ap.add_argument("--cap", type=int, default=100)
    ap.add_argument("--cf", type=float, default=5.0)
    ap.add_argument("--cd", type=float, default=10.0)
    ap.add_argument("--cr", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)

    keys = Path(a.keys)
    base = Path(__file__).resolve().parent.parent
    out_dir = Path(a.out_dir) if a.out_dir else base / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = (keys.name.replace("accessfactors_", "").replace("_keys.csv", "")
           + f"_{a.kind}")

    raw, y = load(keys, a.kind, a.hop_metric)
    f = normalize(raw, a.cf, a.cd, a.cr)
    n = y.size
    X_lin = np.column_stack([f[k] for k in FNAMES])          # 線形スケール
    X_log = np.log(X_lin)                                     # 対数スケール (=積形)
    pairs = list(itertools.combinations(range(4), 2))
    X_log_int = np.column_stack([X_log] +
                                [X_log[:, i] * X_log[:, j] for i, j in pairs])
    rng = np.random.default_rng(a.seed)
    folds = rng.integers(0, 2, size=n)

    models = [
        ("M0_O        O のみ", np.log(f["past"])[:, None], True),
        ("M1_MUL      積形 (現行)", X_log, True),
        ("M1_ADD      和形 (線形)", X_lin, False),
        ("M2_MUL_INT  積形+交互作用", X_log_int, True),
        ("M3_MUL_noD  積形 − D", X_log[:, [1, 2, 3]], True),
        ("M4_MUL_noR  積形 − R", X_log[:, [0, 1, 2]], True),
    ]

    print("=" * 84)
    print(f"{keys.name}  kind={a.kind}  hop={a.hop_metric}  n={n:,}  "
          f"将来アクセス合計={y.sum():,.0f}")
    print("=" * 84)
    print(f"{'model':<28}{'D² (予測精度)':>16}{'cov@' + str(a.cap) + ' % (順位品質)':>22}")
    print("-" * 84)
    rows = []
    for name, X, log_link in models:
        d2, cov = fit_eval(X, y, folds, log_link, a.cap)
        print(f"{name:<28}{d2:>16.4f}{cov:>22.1f}")
        rows.append({"model": name.split()[0], "desc": name.split(maxsplit=1)[1].strip(),
                     "d2": d2, "cov": cov})

    base_d2 = dict((r["model"], r["d2"]) for r in rows)
    base_cov = dict((r["model"], r["cov"]) for r in rows)
    print("\n読み取り:")
    print(f"  積 vs 和          : D² {base_d2['M1_MUL']:+.4f} vs {base_d2['M1_ADD']:+.4f}"
          f"   cov {base_cov['M1_MUL']:.1f} vs {base_cov['M1_ADD']:.1f}")
    print(f"  交互作用の価値    : D² {base_d2['M2_MUL_INT'] - base_d2['M1_MUL']:+.4f}"
          f"   cov {base_cov['M2_MUL_INT'] - base_cov['M1_MUL']:+.1f} pt")
    print(f"  D を抜くと        : D² {base_d2['M3_MUL_noD'] - base_d2['M1_MUL']:+.4f}"
          f"   cov {base_cov['M3_MUL_noD'] - base_cov['M1_MUL']:+.1f} pt")
    print(f"  R を抜くと        : D² {base_d2['M4_MUL_noR'] - base_d2['M1_MUL']:+.4f}"
          f"   cov {base_cov['M4_MUL_noR'] - base_cov['M1_MUL']:+.1f} pt")

    p = out_dir / f"scoreform_{tag}.csv"
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["model", "desc", "d2", "cov"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\n[saved] {p}")


if __name__ == "__main__":
    main()
