#!/usr/bin/env python3
"""
policy_compare の結果 (run_policy_compare_local.sh / splits.sh 出力) を読み、
ポリシー横断で hit率を比較する。server0=エッジ, server1=ノード。

  python3 compare_policy_results.py --input results/policy_compare/vldb
  # ポリシー順を指定: --policies memo lru arc ppr_demand
  
  
  python3 base/proposed_cache/compare_policy_results.py \
  --input base/proposed_cache/results/alpha0.01_walks_100_capa_100/vldb_nobt
  --policies memo_100 none_100 lru_100 arc_100 ppr_demand_cap100_t1.0_d0.5 ppr_demand_cap100_t1.0_d1_l1.0_h-0 
"""

from __future__ import annotations
import argparse
import csv
import json
import os
import re
import tempfile
from pathlib import Path

START_RE = re.compile(r"start=(\d+)_")
SERVER_KIND = {0: "edge", 1: "node"}  # server0=エッジ認可, server1=ノード認可


def normalize_policy_args(raw_policies: list[str] | None) -> list[str] | None:
    """全角スペースや余分な空白が混じっても policy 名を復元する。"""
    if raw_policies is None:
        return None
    normalized: list[str] = []
    for raw in raw_policies:
        cleaned = raw.replace("\u3000", " ").strip()
        normalized.extend(tok for tok in cleaned.split() if tok)
    return normalized


def collect(policy_dir: Path):
    """policy_dir 配下の start=*_global_transition.json から
    per-start の (server_id -> (hit,miss)) と auth_time を集める。"""
    per_start = {}
    for f in sorted(policy_dir.glob("start=*_global_transition.json")):
        s = int(START_RE.search(f.name).group(1))
        d = json.loads(f.read_text())
        hm = {}
        for e in d.get("per_server_access_stats", []):
            sid = e.get("server_id")
            st = e.get("stats", e)
            hm[sid] = (
                int(st.get("auth_cache_hit", 0)),
                int(st.get("auth_cache_miss", 0)),
            )
        per_start[s] = {
            "hm": hm,
            "auth_time": float(d.get("auth_time_total", 0.0)),
            "walk_time": float(d.get("walk_time_total", 0.0)),
            "comb_hit": int(d.get("cache hit", 0)),
            "comb_miss": int(d.get("cache miss", 0)),
        }
    return per_start


def rate(h, m):
    return h / (h + m) if (h + m) else 0.0


def write_summary_csv(out_path: Path, rows: dict[str, dict]) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "policy",
                "node_hit_rate",
                "edge_hit_rate",
                "combined_hit_rate",
                "n_starts",
                "walk_time_per_start",
                "auth_time_per_start",
                "total_time_per_start",
                "walk_time_sum",
                "auth_time_sum",
                "node_hit",
                "node_miss",
                "edge_hit",
                "edge_miss",
            ]
        )
        for pol, r in rows.items():
            w.writerow(
                [
                    pol,
                    f"{r['node']:.6f}",
                    f"{r['edge']:.6f}",
                    f"{r['comb']:.6f}",
                    r.get("n_starts", 0),
                    f"{r['walk']:.6f}",
                    f"{r['auth']:.6f}",
                    f"{r['total']:.6f}",
                    f"{r.get('walk_sum', 0.0):.6f}",
                    f"{r.get('auth_sum', 0.0):.6f}",
                    r["nh"],
                    r["nm"],
                    r["eh"],
                    r["em"],
                ]
            )


def write_per_start_csv(
    out_path: Path, data: dict[str, dict], starts: list[int]
) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["policy", *[f"start_{s}" for s in starts]])
        for pol, ps in data.items():
            row = [pol]
            for s in starts:
                v = ps.get(s)
                row.append(
                    "" if v is None else f"{rate(v['comb_hit'], v['comb_miss']):.6f}"
                )
            w.writerow(row)


def canonical_policy(name: str) -> str:
    """ポリシー名を正規化して種別を返す (none/lru/arc/memo/ppr_demand)"""
    for prefix in ("ppr_demand", "arc", "memo", "lru", "none"):
        if name.startswith(prefix):
            return prefix
    return name


def load_compare_csv(csv_path: Path) -> dict[str, float]:
    """--compare-csv の summary CSV を読み、canonical → combined_hit_rate を返す"""
    result: dict[str, float] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = canonical_policy(row["policy"])
            result[key] = float(row["combined_hit_rate"])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="results/policy_compare/<graph>")
    ap.add_argument("--policies", nargs="*", default=None, help="比較するポリシー順")
    ap.add_argument("--compare-csv", default=None,
                    help="比較対象の policy_compare_summary.csv (別グラフ)")
    ap.add_argument("--compare-label", default="VLDB",
                    help="比較データのラベル (デフォルト: VLDB)")
    args = ap.parse_args()
    base = Path(args.input)
    if not base.exists():
        raise SystemExit(f"[ERROR] input dir not found: {base}")
    if not base.is_dir():
        raise SystemExit(f"[ERROR] input is not a directory: {base}")

    compare_data: dict[str, float] | None = None
    if args.compare_csv:
        compare_path = Path(args.compare_csv)
        if not compare_path.exists():
            print(f"[WARN] --compare-csv not found: {compare_path}")
        else:
            compare_data = load_compare_csv(compare_path)
            print(f"[INFO] compare data loaded from {compare_path}: {compare_data}")

    policies = normalize_policy_args(args.policies) or sorted(
        [p.name for p in base.iterdir() if p.is_dir()]
    )
    missing_policies = [pol for pol in policies if not (base / pol).is_dir()]
    for pol in missing_policies:
        print(f"[WARN] policy dir not found: {base / pol}")

    data = {pol: collect(base / pol) for pol in policies if (base / pol).is_dir()}
    data = {k: v for k, v in data.items() if v}
    if not data:
        raise SystemExit(
            f"[ERROR] no start=*_global_transition.json found under selected policies in {base}"
        )

    out_dir = base / "policy_compare"
    out_dir.mkdir(exist_ok=True)

    # ---- 集計表 (時間は per-start 平均) ----
    # 時間は start (始点) ごとの値を合算しているため、始点数で割って per-start 平均にする。
    # ポリシーによって有効 start 数が異なる (例: memo/lru=5本, ppr_demand=4本) ので、
    # 固定値ではなく各ポリシーの実 start 数 (len(ps)) で割る。
    print(
        f"\n=== {base.name}: policy 比較 (時間=per-start平均, server0=EDGE/server1=NODE) ==="
    )
    print(
        f"{'policy':<30}{'NODE hit':>10}{'EDGE hit':>10}{'合算hit':>10}"
        f"{'#st':>5}{'walk/st':>11}{'auth/st':>11}{'total/st':>11}"
    )
    rows = {}
    for pol, ps in data.items():
        nh = sum(v["hm"].get(1, (0, 0))[0] for v in ps.values())
        nm = sum(v["hm"].get(1, (0, 0))[1] for v in ps.values())
        eh = sum(v["hm"].get(0, (0, 0))[0] for v in ps.values())
        em = sum(v["hm"].get(0, (0, 0))[1] for v in ps.values())
        ch = sum(v["comb_hit"] for v in ps.values())
        cm = sum(v["comb_miss"] for v in ps.values())
        at = sum(v["auth_time"] for v in ps.values())
        wt = sum(v["walk_time"] for v in ps.values())
        n_starts = len(ps) or 1  # 始点数 (0 除算回避)
        walk_ps = wt / n_starts
        auth_ps = at / n_starts
        total_ps = (wt + at) / n_starts
        rows[pol] = dict(
            node=rate(nh, nm),
            edge=rate(eh, em),
            comb=rate(ch, cm),
            walk=walk_ps,  # per-start 平均
            auth=auth_ps,  # per-start 平均
            total=total_ps,  # per-start 平均
            n_starts=len(ps),
            walk_sum=wt,  # 参考: 合算値も保持
            auth_sum=at,
            nh=nh,
            nm=nm,
            eh=eh,
            em=em,
        )
        print(
            f"{pol:<30}{rate(nh,nm):>10.3f}{rate(eh,em):>10.3f}{rate(ch,cm):>10.3f}"
            f"{len(ps):>5}{walk_ps:>11.4f}{auth_ps:>11.4f}{total_ps:>11.4f}"
        )

    # ---- baseline=lru に対する改善 ----
    baseline_key = (
        "lru" if "lru" in rows else ("lru_100" if "lru_100" in rows else None)
    )
    if baseline_key is not None:
        b = rows[baseline_key]
        print(f"\n--- vs LRU (pt差, +が改善) ---")
        print(f"{'policy':<14}{'NODE':>8}{'EDGE':>8}{'合算':>8}")
        for pol, r in rows.items():
            if pol == baseline_key:
                continue
            print(
                f"{pol:<14}{100*(r['node']-b['node']):>+8.1f}{100*(r['edge']-b['edge']):>+8.1f}{100*(r['comb']-b['comb']):>+8.1f}"
            )

    # ---- per-start (合算hit率) ----
    starts = sorted({s for ps in data.values() for s in ps})
    print(f"\n=== per-start 合算hit率 ===")
    print(f"{'policy':<14}" + "".join(f"start{ s}".rjust(9) for s in starts))
    for pol, ps in data.items():
        cells = []
        for s in starts:
            v = ps.get(s)
            cells.append(
                f"{rate(v['comb_hit'], v['comb_miss']):.3f}".rjust(9)
                if v
                else "-".rjust(9)
            )
        print(f"{pol:<14}" + "".join(cells))

    summary_csv = out_dir / "policy_compare_summary.csv"
    per_start_csv = out_dir / "policy_compare_per_start.csv"
    write_summary_csv(summary_csv, rows)
    write_per_start_csv(per_start_csv, data, starts)

    # ---- 図 ----
    try:
        mpl_dir = Path(tempfile.gettempdir()) / "mplconfig_codex"
        mpl_dir.mkdir(exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pols = list(rows)
        x = range(len(pols))

        if compare_data:
            # グループ棒グラフ: primary (Amazon) + compare (VLDB)
            w = 0.38
            main_label = base.parent.name  # 例: amazon0601_nobt
            cmp_label  = args.compare_label
            fig, ax = plt.subplots(figsize=(1.8 * len(pols) + 3, 5))
            main_vals = [rows[p]["comb"] for p in pols]
            cmp_vals  = [compare_data.get(canonical_policy(p)) for p in pols]
            bars_main = ax.bar(
                [i - w / 2 for i in x], main_vals, width=w,
                color="#1565c0", edgecolor="white", linewidth=1.0,
                label=main_label,
            )
            bars_cmp = ax.bar(
                [i + w / 2 for i in x],
                [v if v is not None else 0 for v in cmp_vals], width=w,
                color="#e53935", edgecolor="white", linewidth=1.0,
                label=cmp_label,
            )
            all_vals = main_vals + [v for v in cmp_vals if v is not None]
            ax.set_ylim(0, max(all_vals) * 1.22 + 0.02)
            for bar, v in list(zip(bars_main, main_vals)) + list(zip(bars_cmp, cmp_vals)):
                if v is None:
                    continue
                ax.text(bar.get_x() + bar.get_width() / 2, v + 0.004,
                        f"{v:.1%}", ha="center", va="bottom",
                        fontsize=8, fontweight="bold")
            ax.legend(fontsize=9, framealpha=0.9)
        else:
            # 単色バーグラフ (compare なし)
            fig, ax = plt.subplots(figsize=(1.6 * len(pols) + 3, 5))
            bars = ax.bar(
                list(x),
                [rows[p]["comb"] for p in pols],
                width=0.55, color="#1565c0",
                edgecolor="white", linewidth=1.2,
            )
            ax.set_ylim(0, max(rows[p]["comb"] for p in pols) * 1.18 + 0.02)
            for bar, p in zip(bars, pols):
                v = rows[p]["comb"]
                ax.text(bar.get_x() + bar.get_width() / 2, v + 0.005,
                        f"{v:.1%}", ha="center", va="bottom",
                        fontsize=9, fontweight="bold")

        ax.set_xticks(list(x))
        ax.set_xticklabels(pols, rotation=10, ha="right")
        ax.set_ylabel("Cache hit rate (combined)")
        ax.set_title(f"Policy comparison — {base.name}  cache hit rate (combined)")
        ax.grid(True, axis="y", alpha=0.3)
        ax.set_axisbelow(True)
        out = out_dir / "policy_compare.png"
        fig.tight_layout()
        fig.savefig(out, dpi=130)
        plt.close(fig)
        print(f"\n[OUT] {out}")

        def save_time_plot(
            metric: str, title: str, ylabel: str, filename: str, color: str
        ):
            fig, ax = plt.subplots(figsize=(1.7 * len(pols) + 3, 5))
            vals = [rows[p][metric] for p in pols]
            bars = ax.bar(list(x), vals, color=color, width=0.6)
            ax.set_xticks(list(x))
            ax.set_xticklabels(pols, rotation=10, ha="right")
            ax.set_ylabel(ylabel)
            ax.set_title(title)
            ax.grid(True, axis="y", alpha=0.3)
            for b, v in zip(bars, vals):
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    v,
                    f"{v:.3f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
            fig.tight_layout()
            out_path = out_dir / filename
            fig.savefig(out_path, dpi=130)
            plt.close(fig)
            print(f"[OUT] {out_path}")

        save_time_plot(
            "walk",
            f"Policy comparison — walk time / start ({base.name})",
            "walk time per start [s]",
            "policy_walk_time.png",
            "#6a1b9a",
        )
        save_time_plot(
            "auth",
            f"Policy comparison — auth time / start ({base.name})",
            "auth time per start [s]",
            "policy_auth_time.png",
            "#00897b",
        )
        save_time_plot(
            "total",
            f"Policy comparison — total time / start ({base.name})",
            "(walk + auth) per start [s]",
            "policy_total_time.png",
            "#3949ab",
        )
    except Exception as e:
        print(f"[WARN] plot skip: {e}")

    print(f"[OUT] {summary_csv}")
    print(f"[OUT] {per_start_csv}")


if __name__ == "__main__":
    main()
