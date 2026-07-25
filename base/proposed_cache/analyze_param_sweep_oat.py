#!/usr/bin/env python3
"""
analyze_param_sweep_oat.py
  提案手法 (reuse_score / ppr_demand) の OAT (One-At-a-Time) パラメータ比較。

  ある <graph>_nobt フォルダ (= 単一の alpha, cap 条件) の中で、
  基準点 (anchor) を決め、パラメータを 1 つずつ振ったときの
  hit_rate / walk_time の変化を「パラメータ軸ごと」に集計する。

  入力:
    <folder>/all_<graph>_nobt_settings.csv   (generate_results_csv.py が出力)
    <folder>/all_<graph>_nobt_results.csv

  出力 (<folder>/param_sweep/ 配下):
    sweep_<axis>.csv   軸ごとの value → hit_rate / walk_time / Δ
    sweep_<axis>.png   軸ごとの折れ線 (hit_rate 左 / walk_time 右, baseline 併記)
    best_summary.csv   各軸の最良値 + フォルダ内の総合ベスト構成

使い方 (base ディレクトリから / リポジトリルートから):
  python3 base/proposed_cache/analyze_param_sweep_oat.py \
    --graph vldb --alpha 0.05 --cap 100

  # フォルダを直接指定する場合
  python3 base/proposed_cache/analyze_param_sweep_oat.py \
    --folder base/proposed_cache/results/alpha0.05_walks_100_capa_100/vldb_nobt

  # 手法・基準点・lw を変える
  python3 base/proposed_cache/analyze_param_sweep_oat.py --graph vldb --alpha 0.05 --cap 100 \
    --policy reuse_score --anchor-lw 20 --baselines lru arc memo
"""

from __future__ import annotations
import argparse
import csv
import pathlib
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS_DIR = pathlib.Path(__file__).parent / "results"

# reuse_score / ppr_demand で共通に振りうる連続パラメータ
AXES = ["theta", "lambda", "beta", "gamma", "rho", "lw"]
AXIS_LABELS = {
    "theta":  "theta (structure prior strength)",
    "lambda": "lambda (learning rate)",
    "beta":   "beta (hop exponent)",
    "gamma":  "gamma (degree exponent)",
    "lw":     "lw (learning walks)",
    "rho":    "rho (restart probability)",
}
# anchor から外して axis 走査時に一致を要求しないパラメータ
# (lw は axis!=lw のとき anchor に固定する)
ANCHOR_DEFAULT = {
    "theta": 1.0,
    "lambda": 1.0,
    "beta": 1.0,
    "gamma": 1.0,
    "rho": 0.0,
}


def fv(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def approx_eq(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) < tol


def param_value(r, key) -> Optional[float]:
    """旧フォーマット互換: 空の rho は 0.0 とみなす。"""
    v = fv(r.get(key))
    if v is None and key == "rho":
        return 0.0
    return v


def load_merged(folder: pathlib.Path, graph: str):
    nobt = f"{graph}_nobt"
    s_path = folder / f"all_{nobt}_settings.csv"
    r_path = folder / f"all_{nobt}_results.csv"
    if not s_path.exists() or not r_path.exists():
        raise FileNotFoundError(
            f"集約 CSV が見つかりません: {s_path}\n"
            f"先に `python3 base/proposed_cache/generate_results_csv.py --graph {graph}` を実行してください。"
        )
    settings = {r["run_id"]: r for r in csv.DictReader(open(s_path))}
    results = {r["run_id"]: r for r in csv.DictReader(open(r_path))}
    merged = []
    for rid, s in settings.items():
        if rid not in results:
            continue
        merged.append({**s, **results[rid]})
    return merged


def get_baselines(merged, wanted):
    """baseline policy (lru/arc/memo) → hit_rate。同名複数なら最大を採用。"""
    bl = {}
    for r in merged:
        pol = r["policy"]
        if pol not in wanted:
            continue
        hr = fv(r["hit_rate"])
        if hr is None:
            continue
        if pol not in bl or bl[pol] < hr:
            bl[pol] = hr
    return bl


def get_consts(merged, policy):
    """policy の正規化飽和定数 (cf/cl/cd) を 1 件拾って返す (スイープ間で一定の想定)。"""
    for r in merged:
        if r["policy"] != policy:
            continue
        vals = {k: fv(r.get(k)) for k in ("cf", "cl", "cd")}
        if any(v is not None for v in vals.values()):
            return vals
    return {}


def matches_anchor(r, anchor, axis, anchor_lw):
    """axis 以外の anchor パラメータが一致するか。"""
    for p, val in anchor.items():
        if p == axis:
            continue
        if not approx_eq(param_value(r, p), val):
            return False
    # lw: axis が lw のときは固定しない。それ以外は anchor_lw に固定。
    if axis != "lw":
        if int(fv(r["lw"]) or 0) != int(anchor_lw):
            return False
    return True


def best_anchor_for_axis(merged, policy, axis):
    """axis を最も多くの値で振っている操作点 (他パラメータの固定値) を自動検出する。
    返り値: (anchor_dict, anchor_lw, n_values) / データ無しなら None。
    """
    other = [p for p in ("theta", "lambda", "beta", "gamma", "rho") if p != axis]
    groups = {}  # key(tuple) -> set(axis値)
    for r in merged:
        if r["policy"] != policy:
            continue
        lwv = int(fv(r["lw"]) or 0)
        xv = lwv if axis == "lw" else param_value(r, axis)
        if xv is None:
            continue
        key = tuple((p, param_value(r, p)) for p in other)
        if axis != "lw":
            key = key + (("lw", lwv),)
        groups.setdefault(key, set()).add(xv)
    if not groups:
        return None
    best_key = max(groups, key=lambda k: len(groups[k]))
    anc = dict(ANCHOR_DEFAULT)
    lw = 0
    for p, v in best_key:
        if p == "lw":
            lw = int(v or 0)
        elif v is not None:
            anc[p] = v
    return anc, lw, len(groups[best_key])


def collect_axis(merged, policy, axis, anchor, anchor_lw):
    """axis を振った (value, hit_rate, walk_time) のリストを value 昇順で返す。"""
    pts = {}
    for r in merged:
        if r["policy"] != policy:
            continue
        if not matches_anchor(r, anchor, axis, anchor_lw):
            continue
        xv = int(fv(r["lw"]) or 0) if axis == "lw" else param_value(r, axis)
        if xv is None:
            continue
        hr = fv(r["hit_rate"])
        wt = fv(r.get("walk_time_per_start"))
        # 同一 value が複数あれば hit_rate 最大を採用
        if xv not in pts or (hr is not None and pts[xv][0] is not None and hr > pts[xv][0]):
            pts[xv] = (hr, wt)
    return sorted((x, v[0], v[1]) for x, v in pts.items())


def anchor_hit(merged, policy, anchor, anchor_lw):
    """anchor 点 (全パラメータ = anchor, lw = anchor_lw) の hit_rate。"""
    for r in merged:
        if r["policy"] != policy:
            continue
        if int(fv(r["lw"]) or 0) != int(anchor_lw):
            continue
        if all(approx_eq(param_value(r, p), val) for p, val in anchor.items()):
            return fv(r["hit_rate"])
    return None


def write_axis_csv(path, axis, series, base_hit, baselines):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([axis, "hit_rate", "walk_time_per_start",
                    "delta_hit_vs_anchor"] + [f"baseline_{b}" for b in baselines])
        for xv, hr, wt in series:
            dhit = (hr - base_hit) if (hr is not None and base_hit is not None) else None
            w.writerow([xv,
                        "" if hr is None else round(hr, 6),
                        "" if wt is None else round(wt, 4),
                        "" if dhit is None else round(dhit, 6)]
                       + [round(baselines[b], 6) for b in baselines])


def plot_axis(path, axis, series, base_hit, baselines, anchor_lw, condition,
              const_str="", anchor=None):
    xs = [x for x, _, _ in series]
    hits = [h for _, h, _ in series]
    wts = [w for _, _, w in series]
    has_wt = any(w is not None for w in wts)

    ncol = 2 if has_wt else 1
    fig, axes = plt.subplots(1, ncol, figsize=(6.5 * ncol, 5), squeeze=False)
    axes = axes[0]

    ax0 = axes[0]
    ax0.plot(xs, hits, marker="o", color="tab:blue", label="reuse_score")
    if base_hit is not None:
        ax0.axhline(base_hit, ls=":", color="tab:blue", lw=1,
                    alpha=0.6, label=f"anchor hit={base_hit:.3f}")
    bl_styles = {"lru": ("--", "gray"), "arc": (":", "brown"), "memo": ("-.", "navy")}
    for b, hr in baselines.items():
        ls, col = bl_styles.get(b, ("--", "black"))
        ax0.axhline(hr, ls=ls, color=col, lw=1.2, alpha=0.7, label=f"{b}={hr:.3f}")
    ax0.set_xlabel(AXIS_LABELS.get(axis, axis))
    ax0.set_ylabel("Hit Rate")
    ax0.set_title(f"Hit Rate vs {axis}")
    ax0.grid(True, alpha=0.4)
    ax0.legend(fontsize=7, loc="best")

    if has_wt:
        ax1 = axes[1]
        ax1.plot(xs, wts, marker="s", color="tab:red", label="reuse_score")
        ax1.set_xlabel(AXIS_LABELS.get(axis, axis))
        ax1.set_ylabel("Walk Time / start (s)")
        ax1.set_title(f"Walk Time vs {axis}")
        ax1.grid(True, alpha=0.4)
        ax1.legend(fontsize=7, loc="best")

    anc = anchor if anchor is not None else ANCHOR_DEFAULT
    fixed_parts = [f"{p}={anc[p]:g}" for p in anc if p != axis]
    if axis != "lw":
        fixed_parts.append(f"lw={anchor_lw}")
    fixed = ", ".join(fixed_parts)
    fig.suptitle(f"{condition}  |  OAT sweep: {axis}",
                 fontsize=10, y=1.02)
    fig.tight_layout()
    # 図の下部に、振っていない (固定した) パラメータの実際の値を明示する
    bottom = f"fixed: {fixed}"
    if const_str:
        bottom += f"    |    norm. consts: {const_str}"
    fig.text(0.5, -0.04, bottom, ha="center", va="top",
             fontsize=8, color="dimgray")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def overall_best(merged, policy):
    best = None
    for r in merged:
        if r["policy"] != policy:
            continue
        hr = fv(r["hit_rate"])
        if hr is None:
            continue
        if best is None or hr > best["hit_rate"]:
            best = {
                "hit_rate": hr,
                "theta": r["theta"], "lambda": r["lambda"],
                "beta": r["beta"], "gamma": r["gamma"], "rho": param_value(r, "rho"), "lw": r["lw"],
                "cf": r.get("cf"), "cl": r.get("cl"), "cd": r.get("cd"),
                "policy_tag": r.get("policy_tag"),
                "walk_time_per_start": r.get("walk_time_per_start"),
            }
    return best


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default="vldb")
    ap.add_argument("--alpha", default=None, help="例: 0.05")
    ap.add_argument("--cap", default=None, help="例: 100")
    ap.add_argument("--folder", default=None,
                    help="<graph>_nobt フォルダを直接指定 (alpha/cap より優先)")
    ap.add_argument("--policy", default="reuse_score",
                    choices=["reuse_score", "ppr_demand"])
    ap.add_argument("--anchor-lw", type=int, default=20,
                    help="axis!=lw のとき固定する lw (デフォルト 20)")
    # 座標降下用: 基準点 (固定値) を軸ごとに上書きできる。
    # 例) θ=2.0, γ=3.0 で固定した状態で lw を振った結果を見たい:
    #     --anchor-theta 2.0 --anchor-gamma 3.0
    ap.add_argument("--anchor-theta", type=float, default=None)
    ap.add_argument("--anchor-lambda", type=float, default=None)
    ap.add_argument("--anchor-beta", type=float, default=None)
    ap.add_argument("--anchor-gamma", type=float, default=None)
    ap.add_argument("--anchor-rho", type=float, default=None)
    ap.add_argument("--auto-anchor", action="store_true",
                    help="各軸ごとに、その軸を最も多く振っている操作点を自動で anchor にする "
                         "(操作点が軸ごとに違っても全点が出る)。--anchor-* 手動指定より優先。")
    ap.add_argument("--baselines", nargs="*", default=["lru", "arc", "memo"])
    ap.add_argument("--out-subdir", default="param_sweep")
    args = ap.parse_args()

    # フォルダ解決
    if args.folder:
        folder = pathlib.Path(args.folder)
    else:
        if args.alpha is None or args.cap is None:
            ap.error("--folder を省略する場合は --alpha と --cap が必要です。")
        # walks は結果に依存しないので glob で拾う
        cand = sorted(RESULTS_DIR.glob(
            f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt"))
        if not cand:
            ap.error(f"該当フォルダが見つかりません: "
                     f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt")
        folder = cand[0]
    condition = f"{folder.parent.name}/{folder.name}"
    print(f"[INFO] 対象フォルダ: {folder}")

    # 基準点 (anchor): CLI で上書きされた軸だけ置き換える (座標降下の固定点)
    anchor = dict(ANCHOR_DEFAULT)
    for k, v in (("theta", args.anchor_theta), ("lambda", args.anchor_lambda),
                 ("beta", args.anchor_beta), ("gamma", args.anchor_gamma),
                 ("rho", args.anchor_rho)):
        if v is not None:
            anchor[k] = v
    anchor_str = ", ".join(f"{k}={anchor[k]:g}" for k in anchor)

    merged = load_merged(folder, args.graph)
    baselines = get_baselines(merged, set(args.baselines))
    consts = get_consts(merged, args.policy)
    const_str = ", ".join(f"{k}={v:g}" for k, v in consts.items() if v is not None)
    base_hit = anchor_hit(merged, args.policy, anchor, args.anchor_lw)
    print(f"[INFO] anchor({anchor_str}, lw={args.anchor_lw}) hit_rate = {base_hit}")
    print(f"[INFO] baselines = " +
          ", ".join(f"{k}={v:.4f}" for k, v in baselines.items()))

    out_dir = folder / args.out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    best_rows = []
    for axis in AXES:
        # auto-anchor: この軸を最も多く振っている操作点を自動採用
        ax_anchor, ax_lw = anchor, args.anchor_lw
        if args.auto_anchor:
            ba = best_anchor_for_axis(merged, args.policy, axis)
            if ba:
                ax_anchor, ax_lw, _n = ba
                fixed = ", ".join(f"{p}={ax_anchor[p]:g}" for p in ax_anchor if p != axis)
                print(f"[AUTO] axis={axis}: anchor=[{fixed}, lw={ax_lw}] ({_n} 値)")
        ax_base_hit = anchor_hit(merged, args.policy, ax_anchor, ax_lw)
        series = collect_axis(merged, args.policy, axis, ax_anchor, ax_lw)
        if not series:
            print(f"[SKIP] axis={axis}: 該当データなし")
            continue
        c_path = out_dir / f"sweep_{axis}.csv"
        p_path = out_dir / f"sweep_{axis}.png"
        write_axis_csv(c_path, axis, series, ax_base_hit, baselines)
        plot_axis(p_path, axis, series, ax_base_hit, baselines, ax_lw,
                  condition, const_str, ax_anchor)
        # 各軸のベスト
        valid = [(x, h) for x, h, _ in series if h is not None]
        bx, bh = max(valid, key=lambda t: t[1]) if valid else (None, None)
        best_rows.append({
            "axis": axis, "n_values": len(series),
            "best_value": bx, "best_hit_rate": None if bh is None else round(bh, 6),
            "anchor_hit_rate": None if ax_base_hit is None else round(ax_base_hit, 6),
            "gain_vs_anchor": None if (bh is None or ax_base_hit is None) else round(bh - ax_base_hit, 6),
        })
        print(f"[SAVED] {c_path.name}, {p_path.name}  "
              f"(n={len(series)}, best {axis}={bx} hit={bh})")

    # 総合ベスト構成
    best = overall_best(merged, args.policy)

    summary_path = out_dir / "best_summary.csv"
    with open(summary_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["# condition", condition])
        w.writerow(["# policy", args.policy])
        w.writerow(["# anchor", f"{anchor_str},lw={args.anchor_lw}"])
        for b, hr in baselines.items():
            w.writerow([f"# baseline_{b}", round(hr, 6)])
        w.writerow([])
        w.writerow(["axis", "n_values", "best_value", "best_hit_rate",
                    "anchor_hit_rate", "gain_vs_anchor"])
        for r in best_rows:
            w.writerow([r["axis"], r["n_values"], r["best_value"],
                        r["best_hit_rate"], r["anchor_hit_rate"], r["gain_vs_anchor"]])
        w.writerow([])
        if best:
            w.writerow(["# overall_best (フォルダ内 hit_rate 最大の reuse_score 構成)"])
            w.writerow(["hit_rate", "theta", "lambda", "beta", "gamma", "rho", "lw",
                        "cf", "cl", "cd", "walk_time_per_start", "policy_tag"])
            w.writerow([round(best["hit_rate"], 6), best["theta"], best["lambda"],
                        best["beta"], best["gamma"], best["rho"], best["lw"], best["cf"],
                        best["cl"], best["cd"], best["walk_time_per_start"],
                        best["policy_tag"]])
    print(f"[SAVED] {summary_path}")
    if best:
        print(f"[BEST]  hit_rate={best['hit_rate']:.4f}  "
              f"θ={best['theta']} λ={best['lambda']} β={best['beta']} "
              f"γ={best['gamma']} ρ={best['rho']} lw={best['lw']}  tag={best['policy_tag']}")


if __name__ == "__main__":
    main()
