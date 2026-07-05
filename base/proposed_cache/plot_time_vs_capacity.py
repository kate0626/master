#!/usr/bin/env python3
"""
LRU vs 提案手法(ppr_demand) の実時間を「容量(cap=100/200/300)ごと」に比較する。

ユーザの問題意識:
  - 既存の plot_proposed_vs_baseline.py は 1 容量ずつの棒グラフ。
  - 知りたいのは「同じ容量ごとに、提案が LRU に対して時間をどれだけ削減できるか」
    を容量を横軸にした折れ線 + 削減率で見ること。

主指標: total = walk_time + auth_time (+ prefetch; ppr_demand は prefetch≒0)
  ※ per-start 平均、Length=1 (walk_time<1s) のラン除外 は plot_proposed_vs_baseline と同じ。

ディレクトリ命名が容量で不統一 (capa_100 / capa200 等) なため、
ディレクトリ名に依存せず **JSON の controller.cache_policy / cache_capacity** で判定する。
graph 名は <root>/<graph>/<policy_dir>/<json> の <graph> 階層から取る。

入力 (複数指定可。LRU と ppr_demand が別ツリーにあるので両方渡す):
  --roots base/auth-baseline-cache/results base/proposed_cache/results
出力:
  out_dir/time_vs_capacity_<graph>.png   左: total時間 vs 容量, 右: 削減率(%)
  out_dir/time_vs_capacity_summary.csv

実行例:
  cd /Users/maiko/Documents/GitHub/master-progrem
  python3 base/proposed_cache/plot_time_vs_capacity.py \
    --roots base/auth-baseline-cache/results base/proposed_cache/results \
    --graphs amazon0601 vldb --caps 100 200 300 \
    --out-dir base/proposed_cache/output_compare
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

_JP_FONTS = ["Hiragino Sans", "Hiragino Maru Gothic Pro", "AppleGothic",
             "Noto Sans CJK JP", "IPAGothic", "IPAPGothic", "TakaoGothic"]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# plot_proposed_vs_baseline の集計ロジックを再利用
_here = Path(__file__).parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))
from plot_proposed_vs_baseline import parse_one_json, aggregate  # noqa: E402

POLICY_STYLE = {
    "lru":        {"label": "LRU",                 "color": "#ff7f0e", "marker": "o"},
    "ppr_demand": {"label": "提案 (ppr_demand)",   "color": "#2ca02c", "marker": "s"},
}
METRIC_LABEL = {"total": "総実時間 walk+auth", "auth": "認可時間 auth",
                "walk": "ウォーク時間 walk", "hit_rate": "キャッシュヒット率"}
# 「高いほど良い」指標。時間系は低いほど良いが hit_rate は高いほど良いので、
# 右パネルの符号(改善の向き)と左パネルの単位(% か s)を分岐させる。
HIGHER_IS_BETTER = {"hit_rate"}


def discover(roots: list[Path], policies: list[str],
             graphs: list[str], caps: list[int],
             alpha: float, walks: int) -> dict:
    """全 root を再帰走査し、controller から (graph, policy, cap) を判定して
    records を (graph, policy, cap) -> [rec...] に集める。
    実験条件を揃えるため alpha / walks も厳密一致でフィルタする
    (これを怠ると別条件の同容量ランが混入し不公平な比較になる)。"""
    recs: dict = defaultdict(list)
    cap_set = set(caps)
    pol_set = set(policies)
    for root in roots:
        if not root.is_dir():
            print(f"[warn] root が無い: {root}")
            continue
        for jf in root.rglob("*_global_transition.json"):
            try:
                ctrl = json.loads(jf.read_text(encoding="utf-8")).get("controller", {})
            except Exception:
                continue
            pol = ctrl.get("cache_policy")
            cap = ctrl.get("cache_capacity")
            if pol not in pol_set or cap not in cap_set:
                continue
            # 条件一致: alpha / walks
            try:
                if abs(float(ctrl.get("alpha", -1)) - alpha) > 1e-9:
                    continue
                if int(ctrl.get("walks", -1)) != walks:
                    continue
            except (TypeError, ValueError):
                continue
            # graph 名: json の親(policy_dir)の親
            try:
                graph = jf.parent.parent.name
            except Exception:
                continue
            if graph not in graphs:
                continue
            rec = parse_one_json(jf)
            if rec is None:
                continue
            recs[(graph, pol, int(cap))].append(rec)

    # 重複排除: seed 固定(=42)で walk は決定的なため、baseline/proposed 両ツリーに
    # 同条件・同 start_node のランが重複しうる。start_node 単位で1件に揃え、
    # LRU と ppr_demand の標本数(=start数)を対称にする。
    deduped: dict = defaultdict(list)
    for key, rs in recs.items():
        seen = set()
        for r in rs:
            s = r.get("start_node")
            if s in seen:
                continue
            seen.add(s)
            deduped[key].append(r)
    return deduped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", type=Path, required=True)
    ap.add_argument("--graphs", nargs="+", default=["amazon0601", "vldb"])
    ap.add_argument("--caps", nargs="+", type=int, default=[100, 200, 300])
    ap.add_argument("--policies", nargs="+", default=["lru", "ppr_demand"])
    ap.add_argument("--metric", choices=["total", "auth", "walk", "hit_rate"],
                    default="total")
    ap.add_argument("--alpha", type=float, default=0.01,
                    help="比較する実験条件 alpha (controller.alpha と一致必須)")
    ap.add_argument("--walks", type=int, default=100,
                    help="比較する実験条件 walks (controller.walks と一致必須)")
    ap.add_argument("--baseline-policy", default="lru",
                    help="削減率の分母にする基準ポリシー")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    caps = sorted(args.caps)

    print(f"[条件] alpha={args.alpha}  walks={args.walks}  "
          f"caps={caps}  policies={args.policies}")
    recs = discover(args.roots, args.policies, args.graphs, caps,
                    args.alpha, args.walks)

    # (graph, policy, cap) -> aggregate
    agg: dict = {}
    for key, rs in recs.items():
        agg[key] = aggregate(rs, exclude_short=True)

    base_pol = args.baseline_policy
    other_pols = [p for p in args.policies if p != base_pol]

    # 指標ごとの表示分岐 (時間=低いほど良い/秒, hit_rate=高いほど良い/%)
    is_rate = args.metric in HIGHER_IS_BETTER
    yscale = 100.0 if is_rate else 1.0           # 描画スケール (率は%表示)
    red_unit = "pt" if is_rate else "%"          # 右パネルの改善量の単位
    right_label = "改善差" if is_rate else "削減率"

    def fmt_val(v):
        if v is None:
            return "—"
        return f"{v * 100:.1f}%" if is_rate else f"{v:.2f}s"

    def improvement(base, tgt):
        """正 = 提案が良い方向。時間→削減率[%], hit_rate→ポイント差[pt]。"""
        if base is None or tgt is None:
            return None
        if is_rate:
            return 100.0 * (tgt - base)          # percentage-point gain
        if not base:
            return None
        return 100.0 * (base - tgt) / base       # % reduction

    # ---- テキスト + CSV ----
    csv_path = args.out_dir / "time_vs_capacity_summary.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph", "cap", "policy", "n_valid", "walk", "auth",
                    "prefetch", "total", "hit_rate", "metric_value",
                    f"reduction_%_vs_{base_pol}"])
        for graph in args.graphs:
            print(f"\n=== {graph}  指標={METRIC_LABEL[args.metric]} ===")
            print(f"{'cap':>5} | " + " | ".join(
                f"{POLICY_STYLE.get(p,{}).get('label',p):>16}" for p in args.policies)
                + f" | {right_label:>8}")
            print("-" * 70)
            for cap in caps:
                cells = []
                base_val = None
                for p in args.policies:
                    a = agg.get((graph, p, cap))
                    val = a[args.metric] if (a and a["n"] > 0) else None
                    if p == base_pol:
                        base_val = val
                    cells.append((p, a, val))
                red = None
                # 改善量は other (最初の非baseline) を対象に表示
                tgt = next((c for c in cells if c[0] in other_pols), None)
                if tgt:
                    red = improvement(base_val, tgt[2])
                line = f"{cap:>5} | "
                for p, a, val in cells:
                    line += f"{fmt_val(val):>16} | "
                    if a:
                        w.writerow([graph, cap, p, a["n"],
                                    f"{a['walk']:.4f}", f"{a['auth']:.4f}",
                                    f"{a['prefetch']:.4f}", f"{a['total']:.4f}",
                                    f"{a['hit_rate']:.4f}", f"{val:.4f}" if val is not None else "",
                                    f"{red:.2f}" if (red is not None and p in other_pols) else ""])
                line += f"{(f'{red:+.1f}{red_unit}' if red is not None else '—'):>8}"
                print(line)
    print(f"\n[csv] {csv_path}")

    # ---- 描画: graph ごとに 左=時間 vs 容量, 右=削減率 ----
    for graph in args.graphs:
        fig, axes = plt.subplots(1, 2, figsize=(13, 5.0))

        # 左: 指標 vs 容量 (policy 別折れ線)
        ax = axes[0]
        any_pt = False
        for p in args.policies:
            xs, ys = [], []
            for cap in caps:
                a = agg.get((graph, p, cap))
                if a and a["n"] > 0:
                    xs.append(cap)
                    ys.append(a[args.metric] * yscale)
            if not xs:
                continue
            any_pt = True
            st = POLICY_STYLE.get(p, {"label": p, "color": "#333", "marker": "o"})
            ax.plot(xs, ys, marker=st["marker"], color=st["color"],
                    linewidth=2.0, markersize=8, label=st["label"])
            for xi, yi in zip(xs, ys):
                lbl = f" {yi:.1f}%" if is_rate else f" {yi:.1f}s"
                ax.text(xi, yi, lbl, fontsize=8, va="bottom", color=st["color"])
        ax.set_xticks(caps)
        ax.set_xlabel("キャッシュ容量 (entries/server)", fontsize=11)
        ax.set_ylabel(f"{METRIC_LABEL[args.metric]} [{'%' if is_rate else 's'}] "
                      f"(per-start平均)", fontsize=11)
        ax.set_title(f"{graph} — 容量別 {METRIC_LABEL[args.metric]} (LRU vs 提案)",
                     fontsize=12, fontweight="bold")
        ax.grid(True, linestyle="--", alpha=0.4)
        if any_pt:
            ax.legend(fontsize=10)
        ax.set_ylim(bottom=0)

        # 右: 改善量 vs 容量 (時間=削減率%, hit_rate=ポイント差pt)
        ax = axes[1]
        for p in other_pols:
            xs, ys = [], []
            for cap in caps:
                a = agg.get((graph, p, cap))
                b = agg.get((graph, base_pol, cap))
                if a and b and a["n"] > 0 and b["n"] > 0:
                    imp = improvement(b[args.metric], a[args.metric])
                    if imp is not None:
                        xs.append(cap)
                        ys.append(imp)
            if not xs:
                continue
            st = POLICY_STYLE.get(p, {"label": p, "color": "#2ca02c", "marker": "s"})
            ax.plot(xs, ys, marker=st["marker"], color=st["color"],
                    linewidth=2.0, markersize=8,
                    label=f"{st['label']} vs {POLICY_STYLE.get(base_pol,{}).get('label',base_pol)}")
            for xi, yi in zip(xs, ys):
                ax.text(xi, yi, f" {yi:+.1f}{red_unit}", fontsize=8, va="bottom",
                        color=st["color"])
        ax.axhline(0, color="#999", linewidth=1)
        ax.set_xticks(caps)
        ax.set_xlabel("キャッシュ容量 (entries/server)", fontsize=11)
        if is_rate:
            ax.set_ylabel(f"{base_pol} 比のヒット率改善 [pt]", fontsize=11)
            ax.set_title(f"{graph} — 提案によるヒット率改善 (+が改善)",
                         fontsize=12, fontweight="bold")
        else:
            ax.set_ylabel(f"{base_pol} 比の時間削減率 [%]", fontsize=11)
            ax.set_title(f"{graph} — 提案による時間削減率 (+が削減)",
                         fontsize=12, fontweight="bold")
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(fontsize=10)

        fig.suptitle(
            f"{graph}: LRU vs 提案(ppr_demand) — 指標={METRIC_LABEL[args.metric]}",
            fontsize=13, fontweight="bold")
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        out = args.out_dir / f"time_vs_capacity_{graph}_{args.metric}.png"
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {out}")


if __name__ == "__main__":
    main()
