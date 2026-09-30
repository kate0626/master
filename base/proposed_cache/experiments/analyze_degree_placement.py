#!/usr/bin/env python3
# =============================================================================
# analyze_degree_placement.py
#
#   「次数 D を式のどこに置けば効くか」を、置き場所ごとに実測で比べる。
#
#   これまでの結論: 価値項に乗せる形 (V = … · D^γ) は順位品質 cov@100 を上げない。
#   だが試していない置き場所が残っている。とくに重要なのは
#
#     **O(過去アクセス回数)=1 の層** — 挿入直後のエントリ。ここでは O は全員同じで
#     情報を持たないので、構造量 (D, H) しか手がかりが無い。実システムでも
#     挿入時に prior を与える口 (bump_prior) が既にある。
#
#   比べる置き場所:
#     A) 全体の乗算項        V = O^θ·H^β·D^γ            (既に否定済み。基準として再掲)
#     B) 挿入時の prior      O=1 の層でだけ D で順序付け
#     C) タイブレーク        (O,H) が同値のときだけ D で順序付け
#     D) コスト項            V / (1 + D/C_D)            (次数を占有コストとみなす)
#
#   評価は cov@100 (スコア上位 100 件が将来アクセスを覆う割合)。
# =============================================================================
from __future__ import annotations

import argparse, csv
from pathlib import Path
import numpy as np
import statsmodels.api as sm

HOP_METRICS = {"walk": "hop_walk", "walk_min": "hop_walk_min", "bfs": "hop"}


def load(keys_csv: Path, kind: str, hop_metric: str):
    hop_col = HOP_METRICS.get(hop_metric, "hop_walk")
    cols = {c: [] for c in ("is_edge", "deg", hop_col, "past", "future", "age")}
    with keys_csv.open() as fh:
        for r in csv.DictReader(fh):
            if float(r["past"]) <= 0:
                continue
            ie = float(r["is_edge"])
            if (kind == "node" and ie > 0.5) or (kind == "edge" and ie < 0.5):
                continue
            for c in cols:
                cols[c].append(float(r[c]))
    a = {c: np.asarray(v, dtype=float) for c, v in cols.items()}
    return a["deg"], a[hop_col], a["past"], a["future"]


def cov_at(score, y, cap):
    tot = y.sum()
    if tot <= 0:
        return float("nan")
    idx = np.argsort(-score, kind="mergesort")[:min(cap, score.size)]
    return float(100.0 * y[idx].sum() / tot)


def fit_score(X, y, folds):
    """交差適合した Poisson(log) の予測値をスコアとして返す。"""
    mu = np.empty(y.size)
    Xc = sm.add_constant(X, has_constant="add")
    for k in (0, 1):
        tr, ev = folds != k, folds == k
        try:
            res = sm.GLM(y[tr], Xc[tr], family=sm.families.Poisson()).fit(maxiter=200)
            mu[ev] = np.maximum(res.predict(Xc[ev]), 1e-9)
        except Exception:
            mu[ev] = y[tr].mean()
    return mu


def main(argv=None):
    ap = argparse.ArgumentParser(description="次数の置き場所を比べる")
    ap.add_argument("--keys", required=True)
    ap.add_argument("--kind", choices=["all", "node", "edge"], default="node")
    ap.add_argument("--hop-metric", default="walk")
    ap.add_argument("--cap", type=int, default=100)
    ap.add_argument("--caps", type=int, nargs="+", default=[100, 1000, 5000],
                    help="容量を変えて評価する (次数が効くのは境界がどこに来るかで決まる)")
    ap.add_argument("--cf", type=float, default=5.0)
    ap.add_argument("--cd", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)

    keys = Path(a.keys)
    base = Path(__file__).resolve().parent.parent
    out_dir = Path(a.out_dir) if a.out_dir else base / "results"
    tag = keys.name.replace("accessfactors_", "").replace("_keys.csv", "") + f"_{a.kind}"

    deg, hop, past, y = load(keys, a.kind, a.hop_metric)
    n = y.size
    rng = np.random.default_rng(a.seed)
    folds = rng.integers(0, 2, size=n)

    fO = np.log(1.0 + past / (past + a.cf))
    fH = np.log(1.0 + 1.0 / (1.0 + np.maximum(hop, 0.0)))
    fD = np.log(1.0 + deg / (deg + a.cd))

    base_score = fit_score(np.column_stack([fO, fH]), y, folds)          # A の土台
    with_deg = fit_score(np.column_stack([fO, fH, fD]), y, folds)        # A)

    # --- B) 挿入直後の層 (O=1) の中だけで D を使う -----------------------------
    new = past <= 1
    share_keys = 100.0 * new.mean()
    share_fut = 100.0 * y[new].sum() / y.sum() if y.sum() > 0 else float("nan")
    # 層内だけで並べ替え: 基本スコアの順位に、層内の D 順位を微小量で足す
    def with_prior_in_stratum(mask, prior, sign):
        s = base_score.astype(float).copy()
        if mask.sum() >= 2:
            r = np.zeros(n)
            pr = prior[mask]
            order = np.argsort(sign * pr, kind="mergesort")
            rank = np.empty(order.size); rank[order] = np.arange(order.size)
            r[mask] = rank / max(order.size - 1, 1)
            # 層内の基本スコアの幅より小さい摂動にする (層内だけの並べ替え)
            span = np.ptp(s[mask]) if mask.sum() else 0.0
            s[mask] = s[mask] + 0.999 * span * (r[mask] - 0.5) * 2 if span > 0 else r[mask]
        return s

    b_hi = with_prior_in_stratum(new, deg, +1.0)   # 高次数ほど残す
    b_lo = with_prior_in_stratum(new, deg, -1.0)   # 低次数ほど残す

    # --- C) (O,H) 同値のときだけ D でタイブレーク ------------------------------
    keyq = past.astype(np.int64) * 10_000 + np.round(hop).astype(np.int64)
    _, inv = np.unique(keyq, return_inverse=True)
    prim = np.zeros(n)
    for g in range(inv.max() + 1):
        m = inv == g
        prim[m] = base_score[m].mean()      # 同値グループは同じ主スコアに潰す
    eps = 1e-6 * (np.ptp(prim) if np.ptp(prim) > 0 else 1.0)
    c_hi = prim + eps * np.argsort(np.argsort(deg))
    c_lo = prim - eps * np.argsort(np.argsort(deg))

    # --- D) コスト項 (次数が大きいほど不利) ------------------------------------
    d_cost = base_score / (1.0 + deg / (deg + a.cd))

    rows = [
        ("基準  V=O^θ·H^β", cov_at(base_score, y, a.cap)),
        ("A) 乗算項 ·D^γ", cov_at(with_deg, y, a.cap)),
        ("B) 挿入prior 高次数優先", cov_at(b_hi, y, a.cap)),
        ("B) 挿入prior 低次数優先", cov_at(b_lo, y, a.cap)),
        ("C) タイブレーク 高次数", cov_at(c_hi, y, a.cap)),
        ("C) タイブレーク 低次数", cov_at(c_lo, y, a.cap)),
        ("D) コスト項 /D", cov_at(d_cost, y, a.cap)),
    ]
    b0 = rows[0][1]
    if not a.quiet:
        print("=" * 78)
        print(f"{keys.name}  kind={a.kind}  n={n:,}")
        print(f"O=1 の層: キーの {share_keys:.1f}%  / 将来アクセスの {share_fut:.1f}%")
        print("=" * 78)
        print(f"{'置き場所':<26}{'cov@'+str(a.cap):>10}{'基準との差':>12}")
        for name, v in rows:
            print(f"{name:<26}{v:>10.1f}{v-b0:>+12.1f}")

    # --- 容量を振ったときの寄与 ---------------------------------------------
    cap_rows = []
    for cap in a.caps:
        if cap >= n:
            continue
        b = cov_at(base_score, y, cap)
        cap_rows.append((cap, b, cov_at(with_deg, y, cap) - b,
                         cov_at(b_hi, y, cap) - b, cov_at(b_lo, y, cap) - b,
                         cov_at(d_cost, y, cap) - b))
    if not a.quiet and cap_rows:
        print(f"\n容量を振ったとき (基準との差 [pt])")
        print(f"{'cap':>7}{'基準cov':>10}{'A)乗算':>9}{'B)prior高':>11}"
              f"{'B)prior低':>11}{'D)コスト':>10}")
        for cap, b, da, dbh, dbl, dd in cap_rows:
            print(f"{cap:>7}{b:>10.1f}{da:>+9.1f}{dbh:>+11.1f}{dbl:>+11.1f}{dd:>+10.1f}")

    # --- O=1 層の中だけを見る (挿入直後の並べ替え余地) -------------------------
    strat = {}
    if new.sum() >= 50:
        yn, dn, hn = y[new], deg[new], hop[new]
        k = max(10, int(0.1 * yn.size))
        def rho(x, z):
            rx = np.argsort(np.argsort(x)).astype(float)
            rz = np.argsort(np.argsort(z)).astype(float)
            if rx.std() < 1e-12 or rz.std() < 1e-12:
                return float("nan")
            return float(((rx-rx.mean())*(rz-rz.mean())).mean()/(rx.std()*rz.std()))
        k = max(10, int(0.1 * yn.size))
        strat = {"n": yn.size, "rho_deg": rho(dn, yn), "rho_hop": rho(-hn, yn),
                 "cov_deg_hi": cov_at(dn, yn, k), "cov_deg_lo": cov_at(-dn, yn, k),
                 "cov_hop": cov_at(-hn, yn, k), "cov_rand": 100.0 * k / yn.size}
        if not a.quiet:
            print(f"\nO=1 層の中だけ (n={yn.size:,}, 将来アクセスの {share_fut:.1f}%)")
            print(f"  rho(D, 将来)={rho(dn, yn):+.3f}   rho(H, 将来)={rho(-hn, yn):+.3f}")
            print(f"  層内 top-{k} 捕捉率 [%]:  D高={cov_at(dn, yn, k):.1f}  "
                  f"D低={cov_at(-dn, yn, k):.1f}  H近={cov_at(-hn, yn, k):.1f}  "
                  f"ランダム={100.0*k/yn.size:.1f}")

    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"degplace_{tag}.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["placement", "cov", "delta", "new_key_share", "new_future_share"])
        for name, v in rows:
            w.writerow([name, f"{v:.4f}", f"{v-b0:.4f}",
                        f"{share_keys:.2f}", f"{share_fut:.2f}"])
        for k2, v2 in strat.items():
            w.writerow([f"stratum_{k2}", f"{v2:.4f}", "", "", ""])
    if not a.quiet:
        print(f"\n[saved] {p}")


if __name__ == "__main__":
    main()
