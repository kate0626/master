#!/usr/bin/env python3
"""
plot_param_sweep.py
  固定パラメータを指定して1変数をX軸に、ヒット率・認可時間をY軸にプロットする。

使い方:
  cd /Users/maiko/Documents/GitHub/master-progrem

  # lw を X 軸に (theta=2, delta=1, lambda=1, beta=1, gamma=0 を固定)
  python3 base/proposed_cache/plot_param_sweep.py \\
    --x-param lw \\
    --theta 2 --delta 1 --lambda 1.0 --beta 1 --gamma 0 \\
    --alpha 0.05 0.1 \\
    --cap 100 200

  # theta を X 軸に (lw=20 を固定)
  python3 base/proposed_cache/plot_param_sweep.py \\
    --x-param theta \\
    --lw 20 --delta 1 --lambda 1.0 --beta 1 --gamma 0 \\
    --alpha 0.05 0.1 --cap 100

  # 指定しないパラメータは全値を平均して使用
  # --out-dir で出力先を指定 (省略時は results/plots/ )
"""

from __future__ import annotations
import argparse
import csv
import pathlib
from collections import defaultdict
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

# ─────────────────────────────────────────────
RESULTS_DIR = pathlib.Path(__file__).parent / "results"
# CSV パスは main() で --graph に応じて差し替える
SETTINGS_CSV = RESULTS_DIR / "all_vldb_nobt_settings.csv"
RESULTS_CSV = RESULTS_DIR / "all_vldb_nobt_results.csv"

PARAM_COLS = ["theta", "delta", "lambda", "beta", "gamma", "lw"]
PARAM_LABELS = {
    "theta": "theta (structure prior strength)",
    "delta": "delta (first-hit discount)",
    "lambda": "lambda (learning rate)",
    "beta": "beta (hop exponent)",
    "gamma": "gamma (degree exponent)",
    "lw": "lw (learning walks)",
}


# ─────────────────────────────────────────────
def fv(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_data():
    settings = list(csv.DictReader(open(SETTINGS_CSV)))
    results = list(csv.DictReader(open(RESULTS_CSV)))
    res_map = {r["run_id"]: r for r in results}
    merged = [{**s, **res_map[s["run_id"]]} for s in settings if s["run_id"] in res_map]
    return merged


def approx_eq(a, b, tol=1e-6) -> bool:
    """どちらかが None のときは False。"""
    if a is None or b is None:
        return False
    return abs(a - b) < tol


def filter_rows(merged, x_param, fixed: dict, alpha_list, cap_list):
    """条件に合う提案手法 (ppr_demand / reuse_score) 行を返す。固定パラメータは近似一致でフィルタ。"""
    out = []
    for r in merged:
        if r["policy"] not in ("ppr_demand", "reuse_score"):
            continue
        # alpha / cap フィルタ
        if alpha_list and fv(r["alpha"]) not in [fv(a) for a in alpha_list]:
            continue
        if cap_list and int(r["exp_cap"]) not in [int(c) for c in cap_list]:
            continue
        # 固定パラメータのフィルタ
        skip = False
        for param, val in fixed.items():
            if param == x_param:
                continue  # X 軸パラメータは固定しない
            row_val = fv(r[param]) if param != "lw" else int(r["lw"] or 0)
            fix_val = fv(val) if param != "lw" else int(float(val))
            if not approx_eq(row_val, fix_val):
                skip = True
                break
        if skip:
            continue
        # X 軸の値が存在するか
        xv = fv(r[x_param]) if x_param != "lw" else int(r["lw"] or 0)
        if xv is None:
            continue
        out.append(r)
    return out


def get_baselines(merged, alpha_list, cap_list):
    """(alpha, cap, policy) → best hit_rate を返す。"""
    bl: dict = {}
    for r in merged:
        if r["policy"] not in ("lru", "arc", "memo"):
            continue
        alpha = fv(r["alpha"])
        cap = int(r["exp_cap"])
        if alpha_list and alpha not in [fv(a) for a in alpha_list]:
            continue
        if cap_list and cap not in [int(c) for c in cap_list]:
            continue
        key = (alpha, cap, r["policy"])
        hr = fv(r["hit_rate"])
        if hr and (key not in bl or bl[key] < hr):
            bl[key] = hr
    return bl


def build_series(rows, x_param, alpha_list, cap_list):
    """(alpha, cap) ごとに x_val → [hit_rate, auth_time] のリストを返す。"""
    series: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        alpha = fv(r["alpha"])
        cap = int(r["exp_cap"])
        if alpha_list and alpha not in [fv(a) for a in alpha_list]:
            continue
        if cap_list and cap not in [int(c) for c in cap_list]:
            continue
        xv = fv(r[x_param]) if x_param != "lw" else int(r["lw"] or 0)
        hr = fv(r["hit_rate"])
        wt = fv(r["walk_time_per_start"])
        key = (alpha, cap)
        if hr is not None:
            series[key][xv].append(("hit", hr))
        if wt is not None:
            series[key][xv].append(("walk", wt))
    return series


def plot(x_param, fixed, alpha_list, cap_list, out_dir: pathlib.Path, show_memo=False):
    merged = load_data()
    rows = filter_rows(merged, x_param, fixed, alpha_list, cap_list)

    if not rows:
        print(
            "[SKIP] 条件に一致するデータがありません。このプロットはスキップします。"
        )
        return  # エラー終了せず、次のプロットへ進めるよう正常リターン

    series = build_series(rows, x_param, alpha_list, cap_list)
    baselines = get_baselines(merged, alpha_list, cap_list)

    # ─── 描画 ───────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    colors = plt.cm.tab10.colors
    linestyles = ["-", "--", "-.", ":"]

    all_keys = sorted(series.keys())
    for i, key in enumerate(all_keys):
        alpha, cap = key
        color = colors[i % len(colors)]
        ls = linestyles[i % len(linestyles)]
        label = f"α={alpha}, cap={cap}"

        x_vals = sorted(series[key].keys())
        hit_means, hit_stds = [], []
        auth_means, auth_stds = [], []

        for xv in x_vals:
            hits  = [v for t, v in series[key][xv] if t == "hit"]
            walks = [v for t, v in series[key][xv] if t == "walk"]
            hit_means.append(np.mean(hits)  if hits  else np.nan)
            hit_stds.append(np.std(hits)    if len(hits)  > 1 else 0)
            auth_means.append(np.mean(walks) if walks else np.nan)
            auth_stds.append(np.std(walks)  if len(walks) > 1 else 0)

        xs = np.array(x_vals, dtype=float)
        hm = np.array(hit_means)
        hs = np.array(hit_stds)
        am = np.array(auth_means)
        as_ = np.array(auth_stds)

        axes[0].plot(xs, hm, marker="o", color=color, ls=ls, label=label)
        if np.any(hs > 0):
            axes[0].fill_between(xs, hm - hs, hm + hs, alpha=0.15, color=color)

        axes[1].plot(xs, am, marker="s", color=color, ls=ls, label=label)
        if np.any(as_ > 0):
            axes[1].fill_between(xs, am - as_, am + as_, alpha=0.15, color=color)

    # ベースライン（水平破線）
    bl_styles = {"lru": ("--", "gray"), "arc": (":", "brown"), "memo": ("-.", "navy")}
    drawn_bl = set()
    for (alpha, cap, pol), hr in baselines.items():
        if not show_memo and pol == "memo":
            continue
        ls2, col2 = bl_styles.get(pol, ("--", "black"))
        bl_label = f"{pol} α={alpha} cap={cap}"
        if bl_label not in drawn_bl:
            axes[0].axhline(hr, ls=ls2, color=col2, lw=1.2, alpha=0.7, label=bl_label)
            drawn_bl.add(bl_label)

    # 軸ラベル・凡例
    x_label = PARAM_LABELS.get(x_param, x_param)
    fixed_str = ", ".join(f"{k}={v}" for k, v in sorted(fixed.items()) if k != x_param)

    axes[0].set_xlabel(x_label)
    axes[0].set_ylabel("Hit Rate")
    axes[0].set_title(f"Hit Rate  [fixed: {fixed_str}]")
    axes[0].legend(fontsize=7, loc="best")
    axes[0].grid(True, alpha=0.4)
    axes[0].yaxis.set_major_formatter(ticker.FormatStrFormatter("%.3f"))

    axes[1].set_xlabel(x_label)
    axes[1].set_ylabel("Walk Time / start (s)")
    axes[1].set_title(f"Walk Time  [fixed: {fixed_str}]")
    axes[1].legend(fontsize=7, loc="best")
    axes[1].grid(True, alpha=0.4)

    fig.suptitle(f"X: {x_label}  |  fixed: {fixed_str}", fontsize=10, y=1.01)
    fig.tight_layout()

    out_dir.mkdir(parents=True, exist_ok=True)
    alpha_tag = "_".join(str(a) for a in (alpha_list or ["all"]))
    cap_tag = "_".join(str(c) for c in (cap_list or ["all"]))
    fixed_tag = "_".join(f"{k}{v}" for k, v in sorted(fixed.items()) if k != x_param)
    fname = f"sweep_{x_param}__a{alpha_tag}_c{cap_tag}.png"
    out_path = out_dir / fname
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[SAVED] {out_path}")
    print(f"  条件: {len(rows)} rows, {len(all_keys)} lines")


# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="固定パラメータ指定 → 1変数 X 軸プロット",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--x-param", required=True, choices=PARAM_COLS, help="X 軸にするパラメータ"
    )
    # 固定パラメータ (指定なし = そのパラメータでフィルタしない = 全値平均)
    ap.add_argument("--theta", type=str, default=None)
    ap.add_argument("--delta", type=str, default=None)
    ap.add_argument("--lambda", dest="lam", type=str, default=None)
    ap.add_argument("--beta", type=str, default=None)
    ap.add_argument("--gamma", type=str, default=None)
    ap.add_argument("--lw", type=str, default=None)
    # alpha / cap (複数可)
    ap.add_argument(
        "--alpha",
        nargs="+",
        default=None,
        help="対象 alpha 値 (例: 0.05 0.1)。省略時は全 alpha",
    )
    ap.add_argument(
        "--cap",
        nargs="+",
        default=None,
        help="対象 cap 値 (例: 100 200)。省略時は全 cap",
    )
    ap.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=RESULTS_DIR / "plots",
        help="出力ディレクトリ (デフォルト: results/plots/)",
    )
    ap.add_argument(
        "--show-memo", action="store_true", help="memo (無制限) のベースラインも表示"
    )
    ap.add_argument(
        "--graph", default="vldb",
        help="グラフ名 (CSV パスの判別に使用、例: vldb, amazon0601)",
    )
    args = ap.parse_args()

    # CSV パスを graph に応じて差し替え
    global SETTINGS_CSV, RESULTS_CSV
    nobt = f"{args.graph}_nobt"
    SETTINGS_CSV = RESULTS_DIR / f"all_{nobt}_settings.csv"
    RESULTS_CSV  = RESULTS_DIR / f"all_{nobt}_results.csv"
    if not SETTINGS_CSV.exists():
        print(f"[SKIP] {SETTINGS_CSV} が見つかりません。"
              f"このグラフ ({args.graph}) のデータがないためスキップします。")
        return  # エラー終了せず、後続処理を継続できるよう正常リターン

    fixed = {}
    for k, v in [
        ("theta", args.theta),
        ("delta", args.delta),
        ("lambda", args.lam),
        ("beta", args.beta),
        ("gamma", args.gamma),
        ("lw", args.lw),
    ]:
        if v is not None:
            fixed[k] = v

    plot(
        x_param=args.x_param,
        fixed=fixed,
        alpha_list=args.alpha,
        cap_list=args.cap,
        out_dir=args.out_dir,
        show_memo=args.show_memo,
    )


if __name__ == "__main__":
    main()
