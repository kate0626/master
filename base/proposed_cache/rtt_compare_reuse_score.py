#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rtt_compare_reuse_score.py
  plot_best_vs_baseline.py と「対 (セット)」になる RTT 版。
  同じ集約 CSV・同じベスト構成・同じ手法の並び (lru / arc / proposed(best) / memo)
  を使い、指標だけを「RTT を考慮した全体時間」に置き換えて比較する。

  ★ plot_best_vs_baseline.py と選択ロジックを共有 (import) しているため、
    同じ引数で両者を回せば "proposed" は必ず同一構成を指す。図が対になる。

確定モデル (rtt_time_compare.py / analyze_rtt_time.py と同一):
    全体の時間 = 基準時間(RTT未考慮) + 移動回数 × RTT
    移動回数   = キャッシュ MISS 数 (= サーバへ実際に行った回数)
    RTT は往復時間そのもの。1 MISS = 1 往復 = 1×RTT (×2 しない)。

基準時間(RTT未考慮) の取り方 (--base-mode):
    walk (既定)     : walk_time_per_start。実測 total=walk+auth のうち walk(純計算)を基準にし、
                      auth(実LAN RTT) を合成 RTT (moves×RTT) に置き換える構成。
    walk_minus_auth : walk_time_per_start − auth_time_per_start。
    zero            : 0 (RTT コスト分 moves×RTT だけを見る)。

手法の選び方 (plot_best_vs_baseline.py と同一):
    lru / arc / memo(天井)         : 各 policy の hit_rate 最大行。
    proposed(best)                 : reuse_score / ppr_demand から hit_rate 最大の単一構成。
                                     --anchor-theta / --anchor-lambda / --anchor-lw などで
                                     操作点を固定した中でのベストに絞れる。

入力 (どちらでも可):
    --graph vldb --alpha 0.05 --cap 100     (results/alpha*_walks_*_capa_*/<graph>_nobt を解決)
    --folder <graph>_nobt フォルダを直接指定

出力 (フォルダ配下 rtt_compare/):
    rtt_time_summary.csv        手法別 base/moves/total (指定 RTT)
    rtt_time_bar.png            手法別 total_time バー (base 部分 + RTT 部分を積み上げ)
    rtt_time_sweep.png          横軸 RTT, 縦軸 total, 手法別の折れ線 (--rtt-sweep-ms 指定時)
    rtt_time_reduction.png      基準(既定 memo)に対する超過時間% バー

使い方 (リポジトリルート / base から):
    # plot_best_vs_baseline.py と同じ引数で回せば "対の図" になる
    python3 base/proposed_cache/plot_best_vs_baseline.py --graph vldb --alpha 0.05 --cap 100
    python3 base/proposed_cache/rtt_compare_reuse_score.py --graph vldb --alpha 0.05 --cap 100 --rtt-ms 1

    # RTT を掃引して線グラフも出す
    python3 base/proposed_cache/rtt_compare_reuse_score.py \
        --graph vldb --alpha 0.05 --cap 100 --rtt-ms 1 \
        --rtt-sweep-ms 0.1 0.5 1 2 5 10

    # proposed を特定の操作点に固定 (plot_best と同じ --anchor-*)
    python3 base/proposed_cache/rtt_compare_reuse_score.py \
        --graph vldb --alpha 0.05 --cap 100 --rtt-ms 1 \
        --anchor-theta 2.0 --anchor-lambda 7.0 --anchor-lw 50
"""
from __future__ import annotations
import argparse
import pathlib
import csv
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm

# 日本語グリフ欠落 (UserWarning: Glyph ... missing from font) を防ぐため、
# 利用可能な日本語対応フォントを優先設定する。見つからなければ既定のまま。
_JP_FONT_CANDIDATES = [
    "Hiragino Sans", "Hiragino Maru Gothic Pro", "Hiragino Kaku Gothic Pro",
    "YuGothic", "Yu Gothic", "Noto Sans CJK JP", "Noto Sans JP",
    "IPAexGothic", "Arial Unicode MS", "AppleGothic",
]
_available = {f.name for f in _fm.fontManager.ttflist}
for _cand in _JP_FONT_CANDIDATES:
    if _cand in _available:
        plt.rcParams["font.family"] = _cand
        break
plt.rcParams["axes.unicode_minus"] = False  # マイナス記号の文字化け防止

# --- 手法の選択ロジックは plot_best_vs_baseline.py と共有し、ズレを構造的に防ぐ ---
from plot_best_vs_baseline import (  # noqa: E402
    load_merged, pick_baselines, pick_best_proposed,
    config_label, total_access, fv, CEILING,
)

RESULTS_DIR = pathlib.Path(__file__).parent / "results"

# plot_best_vs_baseline.py と同じ配色で「対の図」に見せる
COLOR_COMPETITOR = "#6c757d"   # lru / arc = 灰 (cap 有界の競合)
COLOR_PROPOSED = "#d1495b"     # proposed  = 赤
COLOR_CEILING = "#9aa7d0"      # memo      = 天井 (無制限, 淡色)


def base_time(row, mode) -> Optional[float]:
    """基準時間 (RTT 未考慮, per-start 秒)。"""
    w = fv(row.get("walk_time_per_start"))
    a = fv(row.get("auth_time_per_start"))
    if mode == "zero":
        return 0.0
    if mode == "walk":
        return w
    if mode == "walk_minus_auth":
        if w is None or a is None:
            return w
        return max(0.0, w - a)
    return w


def moves_per_start(row) -> Optional[float]:
    """移動回数 (= MISS 数) を per-start に換算。"""
    miss = fv(row.get("total_cache_miss"))
    n = fv(row.get("n_starts")) or 1.0
    if miss is None:
        return None
    return miss / n if n > 0 else miss


def role_color(role: str) -> str:
    return {"competitor": COLOR_COMPETITOR,
            "proposed": COLOR_PROPOSED,
            "ceiling": COLOR_CEILING}.get(role, "tab:blue")


def method_label(role: str, key: str, row) -> str:
    """plot_best_vs_baseline.py と揃えたラベル。"""
    if role == "proposed":
        return "proposed\n" + config_label(row)
    if role == "ceiling":
        return "memo\n(ceiling)"
    return key  # lru / arc


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default="vldb")
    ap.add_argument("--alpha", default=None)
    ap.add_argument("--cap", default=None)
    ap.add_argument("--folder", default=None,
                    help="<graph>_nobt フォルダを直接指定 (alpha/cap より優先)")
    ap.add_argument("--rtt-ms", type=float, default=1.0,
                    help="1 往復あたりの RTT [ms] (既定 1.0)")
    ap.add_argument("--rtt-sweep-ms", type=float, nargs="*", default=None,
                    help="この各 RTT[ms] で total を出し折れ線 PNG にする")
    ap.add_argument("--base-mode", default="walk",
                    choices=["walk", "walk_minus_auth", "zero"])
    ap.add_argument("--base-policy", default="lru",
                    help="土台の walk_time をどの policy に統一するか (既定 lru)。"
                         "walk(純計算)は policy 非依存のはずなので基準を固定し、"
                         "差を RTT コスト(MISS×RTT)だけに出す。"
                         "'self' で各手法自身の walk_time を使う (旧挙動)。")
    ap.add_argument("--reference", default="memo",
                    help="時間差%% の基準 (既定 memo)。0%% になる。他は +で遅い/−で速い。"
                         "指定できるのは lru/arc/proposed/memo。")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="比較から除外する手法 (lru/arc/proposed/memo)。例: --exclude arc")
    ap.add_argument("--out-subdir", default="rtt_compare")

    # --- proposed のベスト選択 (plot_best_vs_baseline.py と同一の操作点フィルタ) ---
    ap.add_argument("--anchor-theta", type=float, default=None)
    ap.add_argument("--anchor-lambda", type=float, default=None)
    ap.add_argument("--anchor-beta", type=float, default=None)
    ap.add_argument("--anchor-gamma", type=float, default=None)
    ap.add_argument("--anchor-rho", type=float, default=None)
    ap.add_argument("--anchor-lw", type=int, default=None,
                    help="指定すると lw をこの値に固定した中でベストを選ぶ")
    # 後方互換: 旧 --theta/--lambda/... は --anchor-* が無いときのエイリアス
    ap.add_argument("--theta", type=float, default=None)
    ap.add_argument("--lambda", type=float, default=None, dest="lambda_")
    ap.add_argument("--beta", type=float, default=None)
    ap.add_argument("--gamma", type=float, default=None)
    ap.add_argument("--rho", type=float, default=None)
    ap.add_argument("--lw", type=int, default=None)
    ap.add_argument("--reuse-tag", default=None,
                    help="proposed を policy_tag 部分文字列で絞ってからベストを選ぶ")
    args = ap.parse_args()

    # anchor (優先) / 旧フラグ (フォールバック)
    anchor = {
        "theta": args.anchor_theta if args.anchor_theta is not None else args.theta,
        "lambda": args.anchor_lambda if args.anchor_lambda is not None else args.lambda_,
        "beta": args.anchor_beta if args.anchor_beta is not None else args.beta,
        "gamma": args.anchor_gamma if args.anchor_gamma is not None else args.gamma,
        "rho": args.anchor_rho if args.anchor_rho is not None else args.rho,
    }
    anchor_lw = args.anchor_lw if args.anchor_lw is not None else args.lw

    # フォルダ解決 (plot_best_vs_baseline.py と同一)
    if args.folder:
        folder = pathlib.Path(args.folder)
    else:
        if args.alpha is None or args.cap is None:
            ap.error("--folder を省略する場合は --alpha と --cap が必要です。")
        cand = sorted(RESULTS_DIR.glob(
            f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt"))
        if not cand:
            ap.error(f"該当フォルダが見つかりません: "
                     f"alpha{args.alpha}_walks_*_capa_{args.cap}/{args.graph}_nobt")
        folder = cand[0]
    condition = f"{folder.parent.name}/{folder.name}"
    print(f"[INFO] 対象フォルダ: {folder}")

    merged = load_merged(folder, args.graph)

    # 操作点フィルタの表示
    anchor_active = {k: v for k, v in anchor.items() if v is not None}
    if anchor_active or anchor_lw is not None:
        desc = ", ".join(f"{k}={v:g}" for k, v in anchor_active.items())
        if anchor_lw is not None:
            desc += (", " if desc else "") + f"lw={anchor_lw}"
        print(f"[INFO] proposed のベスト選択を操作点に限定: {desc}")

    # --- 手法選択 (plot_best_vs_baseline.py と共有) ---
    baselines = pick_baselines(merged)  # {lru, arc, memo}
    prop_pool = merged
    if args.reuse_tag:
        prop_pool = [r for r in merged
                     if r["policy"] not in ("reuse_score", "ppr_demand")
                     or args.reuse_tag in (r.get("policy_tag") or "")]
    best = pick_best_proposed(prop_pool, anchor, anchor_lw)
    if best is None:
        raise SystemExit("[ERROR] 条件に合う提案構成が見つかりません。"
                         "--anchor-* / --anchor-lw を緩めてください。")
    if not baselines:
        raise SystemExit("[ERROR] baseline (lru/arc/memo) が見つかりません。")
    print(f"[INFO] proposed 採用構成: {best['policy']}  [{config_label(best)}]  "
          f"hit={fv(best.get('hit_rate')):.4f}")

    # --- 表示順: lru, arc, proposed(best), memo(天井=末尾) ---
    methods = []  # (role, key, row)
    for pol in ["lru", "arc"]:
        if pol in baselines:
            methods.append(("competitor", pol, baselines[pol]))
    methods.append(("proposed", "proposed", best))
    if CEILING in baselines:
        methods.append(("ceiling", CEILING, baselines[CEILING]))
    # 除外指定
    if args.exclude:
        ex = set(args.exclude)
        methods = [m for m in methods if m[1] not in ex]
    if not methods:
        ap.error("除外の結果、比較対象が 0 件になりました。")

    # --- 同一ワークロード検証 (plot_best_vs_baseline.py と同じ観点) ---
    tas = {key: total_access(row) for _, key, row in methods}
    uniq = set(v for v in tas.values() if v)
    consistent = len(uniq) == 1
    print("[INFO] total_access (hit+miss):")
    for k, v in tas.items():
        print(f"        {k:9s} = {v}")
    if consistent:
        print(f"[OK] 全手法で total_access = {uniq.pop()} → 公平比較 (同一ワークロード)")
    else:
        print(f"[!] total_access がバラついています {sorted(uniq)} → 図に警告を表示します。")

    rtt_s = args.rtt_ms / 1000.0

    # --- 土台 walk_time を1つの policy に統一する (既定 lru) ---
    # walk(純計算)時間は cache policy に依存しないはずなので、測定ノイズを除くため
    # 基準を固定し、手法間の差は RTT コスト(MISS×RTT)だけに出るようにする。
    base_common = None
    base_src = args.base_policy.lower()
    if base_src != "self":
        base_row = next((r for _, key, r in methods if key == base_src), None)
        if base_row is None:
            base_row = baselines.get(base_src)
        if base_row is not None:
            base_common = base_time(base_row, args.base_mode)
            print(f"[INFO] 土台 walk_time を {base_src.upper()} に統一: "
                  f"{base_common:.4f}s (全手法共通、差は RTT のみ)")
        else:
            print(f"[WARN] base-policy={base_src} が見つからず、各手法自身の "
                  f"walk_time を使用します。")

    # --- RTT モデルで各手法を計算 ---
    rows_out = []
    for role, key, r in methods:
        b = base_common if base_common is not None else base_time(r, args.base_mode)
        mv = moves_per_start(r)
        if b is None or mv is None:
            continue
        rtt_cost = mv * rtt_s
        rows_out.append({
            "method": key,
            "role": role,
            "label": method_label(role, key, r).replace("\n", " "),
            "hit_rate": round(fv(r.get("hit_rate")), 6),
            "moves_per_start": round(mv, 2),
            "base_time_s": round(b, 4),
            "rtt_ms": args.rtt_ms,
            "rtt_cost_s": round(rtt_cost, 4),
            "total_time_s": round(b + rtt_cost, 4),
            "policy_tag": r.get("policy_tag") or key,
        })

    # --- 基準 (--reference) に対する時間差% ----
    by_key = {r["method"]: r for r in rows_out}
    ref = by_key.get(args.reference)
    ref_total = ref["total_time_s"] if ref else min(r["total_time_s"] for r in rows_out)
    ref_name = args.reference.upper() if ref else "best"
    for r in rows_out:
        r["pct_vs_reference"] = round(
            (r["total_time_s"] - ref_total) / ref_total * 100.0, 3)

    # --- 最大削減量 (LRU→memo) を 100% とした達成率 ---
    worst = by_key.get("lru")
    best_ref = by_key.get(CEILING)
    worst_total = worst["total_time_s"] if worst else max(r["total_time_s"] for r in rows_out)
    best_total = best_ref["total_time_s"] if best_ref else min(r["total_time_s"] for r in rows_out)
    max_red = worst_total - best_total
    for r in rows_out:
        r["reduction_vs_lru_pct"] = round(
            (worst_total - r["total_time_s"]) / worst_total * 100.0, 3) if worst_total else None
        r["pct_of_max_reduction"] = (
            round((worst_total - r["total_time_s"]) / max_red * 100.0, 1)
            if max_red > 0 else None)

    out_dir = folder / args.out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    warn = "" if consistent else "  [!] total_access mismatch — NOT same workload"
    title_color = "black" if consistent else "red"

    # ---- CSV ----
    csv_path = out_dir / "rtt_time_summary.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "method", "role", "label", "hit_rate", "moves_per_start", "base_time_s",
            "rtt_ms", "rtt_cost_s", "total_time_s",
            "pct_vs_reference", "reduction_vs_lru_pct", "pct_of_max_reduction",
            "policy_tag"])
        w.writeheader()
        w.writerows(rows_out)
    print(f"[SAVED] {csv_path}")

    labels = [method_label(r["role"], r["method"], best if r["role"] == "proposed"
                           else baselines[r["method"]]) for r in rows_out]
    bases = [r["base_time_s"] for r in rows_out]
    rtts = [r["rtt_cost_s"] for r in rows_out]
    totals = [r["total_time_s"] for r in rows_out]
    colors = [role_color(r["role"]) for r in rows_out]

    # ---- バーグラフ (base + RTT 積み上げ) ----
    fig, ax = plt.subplots(figsize=(1.7 * len(rows_out) + 3, 5.5))
    x = range(len(rows_out))
    ax.bar(x, bases, color=colors, alpha=0.55, label="base time (compute)")
    for i, r in enumerate(rows_out):
        hatch = "//" if r["role"] == "ceiling" else None
        ax.bar(i, r["rtt_cost_s"], bottom=r["base_time_s"], color=colors[i],
               alpha=1.0, hatch=hatch, edgecolor="white")
    # 凡例用ダミー
    ax.bar([], [], color="gray", alpha=1.0, hatch="//",
           label=f"RTT cost (moves × {args.rtt_ms}ms)")
    for i, r in enumerate(rows_out):
        d = r["pct_vs_reference"]
        tag = "" if r["method"] == args.reference else (
            f"\n(+{d:.1f}% vs {ref_name})" if d >= 0 else f"\n({d:.1f}% vs {ref_name})")
        ax.text(i, r["total_time_s"], f"{r['total_time_s']:.2f}s{tag}",
                ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Total time / start (s)")
    base_note = (f", base_walk={base_src.upper()}統一" if base_common is not None
                 else ", base_walk=self")
    ax.set_title(f"RTT-aware total time  (RTT={args.rtt_ms}ms, base={args.base_mode}"
                 f"{base_note})\n{condition}{warn}", color=title_color)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=8, loc="best")
    ax.set_ylim(0, max(totals) * 1.18)
    fig.tight_layout()
    bar_path = out_dir / "rtt_time_bar.png"
    fig.savefig(bar_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[SAVED] {bar_path}")

    # ---- RTT スイープ折れ線 ----
    if args.rtt_sweep_ms:
        fig, ax = plt.subplots(figsize=(7, 5))
        for i, r in enumerate(rows_out):
            b = r["base_time_s"]
            mv = r["moves_per_start"]
            ys = [b + mv * (rm / 1000.0) for rm in args.rtt_sweep_ms]
            ax.plot(args.rtt_sweep_ms, ys, marker="o", color=colors[i],
                    label=r["label"])
        ax.set_xlabel("RTT per round trip (ms)")
        ax.set_ylabel("Total time / start (s)")
        ax.set_title(f"RTT-aware total time vs RTT (base={args.base_mode})\n{condition}")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        sweep_path = out_dir / "rtt_time_sweep.png"
        fig.savefig(sweep_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[SAVED] {sweep_path}")

    # ---- 基準比バー (--reference, 既定 memo=0%) ----
    diff_rows = [r for r in rows_out if r["method"] != args.reference]
    if diff_rows and ref:
        fig, ax = plt.subplots(figsize=(1.6 * len(diff_rows) + 3, 5.5))
        x = range(len(diff_rows))
        diffs = [r["pct_vs_reference"] for r in diff_rows]
        dcolors = [role_color(r["role"]) for r in diff_rows]
        ax.bar(x, diffs, color=dcolors, alpha=0.9)
        ax.axhline(0, ls="--", color="tab:green", lw=1.5,
                   label=f"{ref_name} = 基準 (0%)")
        for i, r in enumerate(diff_rows):
            d = r["pct_vs_reference"]
            pm = r["pct_of_max_reduction"]
            extra = f"\n最大削減の{pm:.0f}%" if pm is not None else ""
            ax.text(i, d, (f"+{d:.1f}%" if d >= 0 else f"{d:.1f}%") + extra,
                    ha="center", va="bottom" if d >= 0 else "top", fontsize=8)
        ax.set_xticks(list(x))
        ax.set_xticklabels([r["label"] for r in diff_rows], fontsize=8)
        ax.set_ylabel(f"Extra time vs {ref_name} (%)   ← 小さいほど良い")
        ax.set_title(f"vs {ref_name} 時間差  (RTT={args.rtt_ms}ms, base={args.base_mode})\n"
                     f"{ref_name} を理想(0%)とした各手法の超過時間\n{condition}")
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        red_path = out_dir / "rtt_time_reduction.png"
        fig.savefig(red_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[SAVED] {red_path}")

    # ---- コンソール要約 ----
    print(f"\n[RESULT] RTT={args.rtt_ms}ms, base={args.base_mode}  (per start)  "
          f"基準={ref_name}(0%), 最大削減量(LRU→memo)={max_red:.3f}s=100%")
    print(f"  {'method':<12} {'hit':>6} {'moves':>7} {'total(s)':>9} "
          f"{'vsLRU削減%':>10} {'最大削減比%':>10}")
    for r in sorted(rows_out, key=lambda r: r["total_time_s"]):
        rl = r["reduction_vs_lru_pct"]
        pm = r["pct_of_max_reduction"]
        print(f"  {r['method']:<12} {r['hit_rate']:>6.3f} {r['moves_per_start']:>7.0f} "
              f"{r['total_time_s']:>9.3f} {('-' if rl is None else f'{rl:.2f}'):>10} "
              f"{('-' if pm is None else f'{pm:.1f}'):>10}")


if __name__ == "__main__":
    main()
