#!/usr/bin/env python3
"""
policy_compare_summary.csv (compare_policy_results.py 出力) を参照し、
RTT を考慮した「全体の時間」を policy 別に計算する。

ユーザ確定モデル:
    全体の時間 = 基準時間(RTT未考慮) + 移動回数 × RTT          (×1: RTT は往復済)
    移動回数   = キャッシュ MISS 数 (node/edge 別にカウント)
    節約時間   = ヒット数 × RTT

基準時間(RTT未考慮) の取り方 (--base-mode):
    walk (既定): walk_time_total。compare の total=walk+auth が「walk(計算) + auth(RTT)」
                 の加算構成なので、auth を合成 RTT に置き換えるなら基準=walk が自然。
    walk_minus_auth : walk_time_total − auth_time_total。
    zero : 0 (RTT コスト分だけを見る)。

RTT (ms):
    --rtt-node-ms / --rtt-edge-ms : node/edge 別 RTT。既定は両方 --rtt-ms と同値 (uniform)。
    --rtt-sweep-ms                : この各値 (uniform) で total を出し PNG にする。
    node/edge 比較は --rtt-edge-ms を --rtt-node-ms と変えれば regime(i) 不均一になる。

入力 (どちらでも可):
    --input <policy_compare dir>  … policy_compare_summary.csv を含むディレクトリ
    --summary <csv>               … policy_compare_summary.csv を直接指定
出力 (入力ディレクトリと同じ場所、compare と同じ命名規則):
    policy_rtt_time_summary.csv
    policy_rtt_time.png       (policy 別 total_time バー, 指定 RTT)
    policy_rtt_time_sweep.png (横軸 RTT, 縦軸 total, policy 別の線)

使い方:
  # uniform RTT=1ms
  python3 rtt_time_compare.py \
      --input results/alpha0.01_walks_100_capa_100/vldb_nobt/policy_compare --rtt-ms 1
  # node 1ms / edge 5ms (regime i 不均一)
  python3 rtt_time_compare.py --input .../policy_compare --rtt-node-ms 1 --rtt-edge-ms 5
"""
from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path


def read_summary(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "policy": r["policy"],
                "node_hit": int(r["node_hit"]),
                "node_miss": int(r["node_miss"]),
                "edge_hit": int(r["edge_hit"]),
                "edge_miss": int(r["edge_miss"]),
                "walk": float(r.get("walk_time_total") or r.get("walk_time_sum", 0)),
                "auth": float(r.get("auth_time_total") or r.get("auth_time_sum", 0)),
                "comb_hit_rate": float(r["combined_hit_rate"]),
            })
    return rows


def base_time(r: dict, mode: str) -> float:
    if mode == "walk":
        return r["walk"]
    if mode == "walk_minus_auth":
        return max(0.0, r["walk"] - r["auth"])
    if mode == "zero":
        return 0.0
    raise ValueError(mode)


def total_time(r: dict, base: float, rtt_node_ms: float, rtt_edge_ms: float) -> float:
    return (base
            + r["node_miss"] * (rtt_node_ms / 1000.0)
            + r["edge_miss"] * (rtt_edge_ms / 1000.0))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", help="policy_compare ディレクトリ")
    g.add_argument("--summary", help="policy_compare_summary.csv のパス")
    ap.add_argument("--base-mode", default="walk",
                    choices=["walk", "walk_minus_auth", "zero"])
    ap.add_argument("--rtt-ms", type=float, default=1.0, help="uniform RTT(ms)。node/edge 個別指定が優先")
    ap.add_argument("--rtt-node-ms", type=float, default=None)
    ap.add_argument("--rtt-edge-ms", type=float, default=None)
    ap.add_argument("--rtt-sweep-ms", default="0,0.5,1,2,5,10",
                    help="uniform スイープ (PNG 用)")
    ap.add_argument("--out-dir", default=None, help="既定: 入力と同じ場所")
    args = ap.parse_args()

    if args.summary:
        csv_path = Path(args.summary)
        out_dir = Path(args.out_dir) if args.out_dir else csv_path.parent
    else:
        in_dir = Path(args.input)
        csv_path = in_dir / "policy_compare_summary.csv"
        out_dir = Path(args.out_dir) if args.out_dir else in_dir
    if not csv_path.exists():
        raise SystemExit(f"[ERROR] not found: {csv_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    rtt_node = args.rtt_node_ms if args.rtt_node_ms is not None else args.rtt_ms
    rtt_edge = args.rtt_edge_ms if args.rtt_edge_ms is not None else args.rtt_ms
    uniform = (rtt_node == rtt_edge)
    sweep = [float(x) for x in args.rtt_sweep_ms.split(",")]

    rows = read_summary(csv_path)
    if not rows:
        raise SystemExit(f"[ERROR] empty summary: {csv_path}")

    # ---- コンソール表 ----
    label = f"RTT_node={rtt_node}ms RTT_edge={rtt_edge}ms" + (" (uniform)" if uniform else " (hetero)")
    print(f"\n=== {csv_path.parent.name}: RTT-aware total time  [{label}]  base={args.base_mode} ===")
    print(f"{'policy':<36}{'node_miss':>10}{'edge_miss':>10}{'hit%':>7}"
          f"{'base_s':>9}{'+RTT_s':>9}{'total_s':>9}")
    out_rows = []
    for r in rows:
        base = base_time(r, args.base_mode)
        rtt_cost = r["node_miss"] * (rtt_node / 1000.0) + r["edge_miss"] * (rtt_edge / 1000.0)
        tot = base + rtt_cost
        print(f"{r['policy']:<36}{r['node_miss']:>10}{r['edge_miss']:>10}"
              f"{100*r['comb_hit_rate']:>6.1f}{base:>9.2f}{rtt_cost:>9.2f}{tot:>9.2f}")
        out_rows.append((r, base, rtt_cost, tot))

    # ---- CSV ----
    summary_csv = out_dir / "policy_rtt_time_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        head = ["policy", "node_miss", "edge_miss", "combined_hit_rate",
                "base_s", f"rtt_node_ms", "rtt_edge_ms", "rtt_cost_s", "total_s"]
        head += [f"total_s@uniform{m}ms" for m in sweep]
        w.writerow(head)
        for r, base, rtt_cost, tot in out_rows:
            line = [r["policy"], r["node_miss"], r["edge_miss"], f'{r["comb_hit_rate"]:.6f}',
                    f"{base:.6f}", rtt_node, rtt_edge, f"{rtt_cost:.6f}", f"{tot:.6f}"]
            for m in sweep:
                line.append(f"{total_time(r, base, m, m):.6f}")
            w.writerow(line)
    print(f"\n[OUT] {summary_csv}")

    # ---- 図 (compare と同じスタイル) ----
    try:
        mpl_dir = Path(tempfile.gettempdir()) / "mplconfig_codex"
        mpl_dir.mkdir(exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pols = [r["policy"] for r in rows]
        x = range(len(pols))

        # (1) policy 別 total_time バー (積み上げ: base / node-RTT / edge-RTT)
        fig, ax = plt.subplots(figsize=(1.7 * len(pols) + 3, 5))
        bases = [base_time(r, args.base_mode) for r in rows]
        node_c = [r["node_miss"] * (rtt_node / 1000.0) for r in rows]
        edge_c = [r["edge_miss"] * (rtt_edge / 1000.0) for r in rows]
        ax.bar(list(x), bases, 0.6, label="base (walk)", color="#9e9e9e")
        ax.bar(list(x), node_c, 0.6, bottom=bases, label=f"node RTT ({rtt_node}ms)", color="#2e7d32")
        ax.bar(list(x), edge_c, 0.6, bottom=[b + n for b, n in zip(bases, node_c)],
               label=f"edge RTT ({rtt_edge}ms)", color="#ef6c00")
        for i, (b, n, e) in enumerate(zip(bases, node_c, edge_c)):
            ax.text(i, b + n + e, f"{b+n+e:.1f}", ha="center", va="bottom", fontsize=8)
        ax.set_xticks(list(x)); ax.set_xticklabels(pols, rotation=10, ha="right")
        ax.set_ylabel("total time [s]")
        ax.set_title(f"RTT-aware total time — {csv_path.parent.name}  [{label}]")
        ax.legend(); ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        out = out_dir / "policy_rtt_time.png"
        fig.savefig(out, dpi=130); plt.close(fig)
        print(f"[OUT] {out}")

        # (2) uniform スイープ線図
        fig, ax = plt.subplots(figsize=(7, 5))
        for r in rows:
            base = base_time(r, args.base_mode)
            ys = [total_time(r, base, m, m) for m in sweep]
            ax.plot(sweep, ys, marker="o", label=f'{r["policy"]} (hit {100*r["comb_hit_rate"]:.0f}%)')
        ax.set_xlabel("RTT per move (ms, uniform)")
        ax.set_ylabel("total time [s]")
        ax.set_title(f"RTT sweep — {csv_path.parent.name} ({args.base_mode})")
        ax.grid(True, alpha=0.3); ax.legend(fontsize=8)
        fig.tight_layout()
        out = out_dir / "policy_rtt_time_sweep.png"
        fig.savefig(out, dpi=120); plt.close(fig)
        print(f"[OUT] {out}")
    except Exception as e:
        print(f"[WARN] plot skip: {e}")


if __name__ == "__main__":
    main()
