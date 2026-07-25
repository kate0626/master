#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_best_vs_baseline.py
  提案手法 (reuse_score / ppr_demand) の「ベスト構成」を選び、
  baseline (lru / arc / memo) と hit_rate・実行時間で比較する棒グラフを出力する。

  - hit_rate パネル : cap 有界の競合 (lru/arc) と提案ベストを比較。
                      memo は無制限キャッシュ = 到達可能上限 (ceiling) として併記。
  - time パネル     : walk_time_per_start と auth_time_per_start を policy 別に比較。
  - 同一ワークロード検証 : 全 policy で total_access (=hit+miss) が一致するか確認し、
                          食い違う場合は図タイトルに [!] 警告を出す (公平比較の前提)。

集約 CSV (all_<graph>_nobt_{settings,results}.csv) を入力に使うので、先に
  python3 base/proposed_cache/generate_results_csv.py --graph <graph>
を実行しておくこと。

使い方 (analyze_param_sweep_oat.py と同じフォルダ解決):
  # 全 reuse_score 構成からベスト (最大 hit_rate) を自動選択
  python3 base/proposed_cache/plot_best_vs_baseline.py \
      --graph vldb --alpha 0.05 --cap 100

  # ベストを「特定の操作点」に絞りたい場合 (指定した軸だけ一致を要求)
  #   例: θ=2, λ=7, lw=50 帯の中でのベストを選ぶ
  python3 base/proposed_cache/plot_best_vs_baseline.py \
      --graph vldb --alpha 0.05 --cap 100 \
      --anchor-theta 2.0 --anchor-lambda 7.0 --anchor-lw 50

  # フォルダ直接指定
  python3 base/proposed_cache/plot_best_vs_baseline.py \
      --folder base/proposed_cache/results/alpha0.05_walks_100_capa_100/vldb_nobt
"""
import argparse
import csv
import pathlib
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS_DIR = pathlib.Path(__file__).parent / "results"
BASELINES = ["lru", "arc", "memo"]
PROPOSED = ["reuse_score", "ppr_demand"]
# memo は「無制限キャッシュ = 上限リファレンス」。cap 有界の競合ではない。
CEILING = "memo"
# 操作点フィルタに使える連続パラメータ
ANCHOR_KEYS = ["theta", "lambda", "beta", "gamma", "rho"]


def fv(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def approx_eq(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def load_merged(folder: pathlib.Path, graph: str):
    nobt = f"{graph}_nobt"
    s_path = folder / f"all_{nobt}_settings.csv"
    r_path = folder / f"all_{nobt}_results.csv"
    if not s_path.exists() or not r_path.exists():
        raise FileNotFoundError(
            f"集約 CSV が見つかりません: {s_path}\n"
            f"先に `python3 base/proposed_cache/generate_results_csv.py "
            f"--graph {graph}` を実行してください。"
        )
    settings = {r["run_id"]: r for r in csv.DictReader(open(s_path))}
    results = {r["run_id"]: r for r in csv.DictReader(open(r_path))}
    merged = []
    for rid, s in settings.items():
        if rid in results:
            merged.append({**s, **results[rid]})
    return merged


def total_access(r) -> Optional[int]:
    h, m = fv(r.get("total_cache_hit")), fv(r.get("total_cache_miss"))
    if h is None or m is None:
        return None
    return int(h + m)


def pick_baselines(merged):
    """policy -> その policy の最大 hit_rate 行。"""
    best = {}
    for r in merged:
        pol = r["policy"]
        if pol not in BASELINES:
            continue
        hr = fv(r["hit_rate"])
        if hr is None:
            continue
        if pol not in best or fv(best[pol]["hit_rate"]) < hr:
            best[pol] = r
    return best


def pick_best_proposed(merged, anchor, anchor_lw):
    """提案 policy から、anchor フィルタに一致する行の中で hit_rate 最大を返す。
    anchor が空 (全 None) なら全提案構成が対象。"""
    cand = []
    for r in merged:
        if r["policy"] not in PROPOSED:
            continue
        hr = fv(r["hit_rate"])
        if hr is None:
            continue
        # 指定された軸だけ一致を要求
        ok = True
        for k, v in anchor.items():
            if v is None:
                continue
            if not approx_eq(fv(r.get(k)), v):
                ok = False
                break
        if ok and anchor_lw is not None:
            if int(fv(r.get("lw")) or 0) != int(anchor_lw):
                ok = False
        if ok:
            cand.append(r)
    if not cand:
        return None
    return max(cand, key=lambda r: fv(r["hit_rate"]))


def config_label(r) -> str:
    keys = ["theta", "lambda", "beta", "gamma", "rho", "lw"]
    short = {"theta": "θ", "lambda": "λ", "beta": "β", "gamma": "γ",
             "rho": "ρ", "lw": "lw"}
    parts = []
    for k in keys:
        v = fv(r.get(k))
        if v is not None:
            parts.append(f"{short[k]}={v:g}")
    return " ".join(parts)


def plot(out_png, folder_name, rows, best_prop, consistent):
    """rows: [(label, hit_rate, walk_t, auth_t, is_ceiling), ...] 表示順。"""
    labels = [x[0] for x in rows]
    hits = [x[1] for x in rows]
    walks = [x[2] for x in rows]
    auths = [x[3] for x in rows]
    ceil = [x[4] for x in rows]
    n = len(rows)
    xs = list(range(n))

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13, 5.2))

    # ---- 左: hit_rate ----
    colors = []
    for lab, is_c in zip(labels, ceil):
        if is_c:
            colors.append("#9aa7d0")          # memo (天井) は淡色
        elif lab.startswith("proposed"):
            colors.append("#d1495b")          # 提案は赤
        else:
            colors.append("#6c757d")          # 競合は灰
    barsL = axL.bar(xs, hits, color=colors, edgecolor="black", linewidth=0.6)
    for b, is_c in zip(barsL, ceil):
        if is_c:
            b.set_hatch("//")
    axL.set_xticks(xs)
    axL.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axL.set_ylabel("cache hit rate")
    axL.set_title("Hit rate  (memo = unlimited / ceiling)")
    axL.grid(axis="y", alpha=0.3)
    for x, h in zip(xs, hits):
        axL.text(x, h + max(hits) * 0.01, f"{h:.3f}", ha="center",
                 va="bottom", fontsize=9)
    # 提案 vs 最良の cap 有界競合 の改善率を注記
    comp = [(lab, h) for lab, h, _, _, is_c in rows
            if (not is_c) and (not lab.startswith("proposed"))]
    if comp and best_prop is not None:
        bc_lab, bc_h = max(comp, key=lambda t: t[1])
        bp = fv(best_prop["hit_rate"])
        if bc_h > 0:
            axL.text(0.02, 0.97,
                     f"proposed vs {bc_lab}: {bp - bc_h:+.4f} "
                     f"({(bp / bc_h - 1) * 100:+.1f}%)",
                     transform=axL.transAxes, va="top", fontsize=9,
                     bbox=dict(boxstyle="round", fc="#fff3cd", ec="#e0a800"))

    # ---- 右: 時間 (walk / auth per start) ----
    w = 0.38
    axR.bar([x - w / 2 for x in xs], walks, width=w, label="walk_time / start",
            color="#4c72b0", edgecolor="black", linewidth=0.6)
    axR.bar([x + w / 2 for x in xs], auths, width=w, label="auth_time / start",
            color="#dd8452", edgecolor="black", linewidth=0.6)
    axR.set_xticks(xs)
    axR.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axR.set_ylabel("time per start [s]")
    axR.set_title("Runtime (lower = better)")
    axR.grid(axis="y", alpha=0.3)
    axR.legend(fontsize=9)
    for x, wt, at in zip(xs, walks, auths):
        if wt is not None:
            axR.text(x - w / 2, wt, f"{wt:.2f}", ha="center", va="bottom",
                     fontsize=8)
        if at is not None:
            axR.text(x + w / 2, at, f"{at:.2f}", ha="center", va="bottom",
                     fontsize=8)

    warn = "" if consistent else "  [!] total_access mismatch — NOT same workload"
    fig.suptitle(f"Best proposed vs baselines — {folder_name}{warn}",
                 fontsize=12, color=("black" if consistent else "red"))
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="vldb")
    ap.add_argument("--alpha", default=None, help="例: 0.05")
    ap.add_argument("--cap", default=None, help="例: 100")
    ap.add_argument("--folder", default=None,
                    help="集約 CSV のあるフォルダを直接指定 (alpha/cap より優先)")
    ap.add_argument("--anchor-theta", type=float, default=None)
    ap.add_argument("--anchor-lambda", type=float, default=None)
    ap.add_argument("--anchor-beta", type=float, default=None)
    ap.add_argument("--anchor-gamma", type=float, default=None)
    ap.add_argument("--anchor-rho", type=float, default=None)
    ap.add_argument("--anchor-lw", type=int, default=None,
                    help="指定すると lw をこの値に固定した中でベストを選ぶ")
    ap.add_argument("--out", default=None,
                    help="出力 PNG パス (既定: <folder>/best_vs_baseline.png)")
    args = ap.parse_args()

    # フォルダ解決 (analyze_param_sweep_oat.py と同じ)
    if args.folder:
        folder = pathlib.Path(args.folder)
    else:
        if args.alpha is None or args.cap is None:
            ap.error("--folder を省略する場合は --alpha と --cap が必要です。")
        cand = sorted(RESULTS_DIR.glob(
            f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt"))
        if not cand:
            ap.error(f"該当フォルダなし: "
                     f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt")
        folder = cand[0]
    folder_name = f"{folder.parent.name}/{folder.name}"
    print(f"[INFO] 対象フォルダ: {folder}")

    merged = load_merged(folder, args.graph)

    anchor = {
        "theta": args.anchor_theta, "lambda": args.anchor_lambda,
        "beta": args.anchor_beta, "gamma": args.anchor_gamma,
        "rho": args.anchor_rho,
    }
    anchor_active = {k: v for k, v in anchor.items() if v is not None}
    if anchor_active or args.anchor_lw is not None:
        desc = ", ".join(f"{k}={v:g}" for k, v in anchor_active.items())
        if args.anchor_lw is not None:
            desc += (", " if desc else "") + f"lw={args.anchor_lw}"
        print(f"[INFO] ベスト選択を操作点に限定: {desc}")

    baselines = pick_baselines(merged)
    best = pick_best_proposed(merged, anchor, args.anchor_lw)
    if best is None:
        raise SystemExit("[ERROR] 条件に合う提案構成が見つかりません。"
                         "--anchor-* / --anchor-lw を緩めてください。")
    if not baselines:
        raise SystemExit("[ERROR] baseline (lru/arc/memo) が見つかりません。")

    # ---- 同一ワークロード検証 ----
    tas = {}
    for pol, r in baselines.items():
        tas[pol] = total_access(r)
    tas["proposed"] = total_access(best)
    uniq = set(v for v in tas.values() if v)
    consistent = len(uniq) == 1
    print("[INFO] total_access (hit+miss):")
    for k, v in tas.items():
        print(f"        {k:9s} = {v}")
    if consistent:
        print(f"[OK] 全 policy で total_access = {uniq.pop()} → 公平比較 (同一ワークロード)")
    else:
        print(f"[!] total_access がバラついています {sorted(uniq)} → 図に警告を表示します。"
              " 同一 seed/walks/サーバで回し直してください。")

    # ---- 表示順: lru, arc, (memo=天井は末尾), proposed ----
    def row_for(pol, r, is_prop=False):
        return (
            ("proposed\n" + config_label(r)) if is_prop else pol,
            fv(r["hit_rate"]),
            fv(r.get("walk_time_per_start")),
            fv(r.get("auth_time_per_start")),
            pol == CEILING,
        )
    rows = []
    for pol in ["lru", "arc"]:
        if pol in baselines:
            rows.append(row_for(pol, baselines[pol]))
    rows.append(row_for("proposed", best, is_prop=True))
    if CEILING in baselines:
        rows.append(row_for(CEILING, baselines[CEILING]))

    out_png = pathlib.Path(args.out) if args.out else folder / "best_vs_baseline.png"
    out_csv = out_png.with_suffix(".csv")
    plot(out_png, folder_name, rows, best, consistent)

    # ---- CSV も出力 ----
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["method", "role", "hit_rate", "walk_time_per_start",
                    "auth_time_per_start", "total_access", "config"])
        for pol in ["lru", "arc"]:
            if pol in baselines:
                r = baselines[pol]
                w.writerow([pol, "competitor(cap)", r["hit_rate"],
                            r.get("walk_time_per_start"),
                            r.get("auth_time_per_start"),
                            total_access(r), ""])
        w.writerow(["proposed(" + best["policy"] + ")", "proposed(best)",
                    best["hit_rate"], best.get("walk_time_per_start"),
                    best.get("auth_time_per_start"), total_access(best),
                    config_label(best)])
        if CEILING in baselines:
            r = baselines[CEILING]
            w.writerow([CEILING, "ceiling(unlimited)", r["hit_rate"],
                        r.get("walk_time_per_start"),
                        r.get("auth_time_per_start"), total_access(r), ""])

    print(f"[SAVED] {out_png}")
    print(f"[SAVED] {out_csv}")
    bp = fv(best["hit_rate"])
    print(f"[BEST] proposed hit_rate={bp:.4f}  [{config_label(best)}]")


if __name__ == "__main__":
    main()
