#!/usr/bin/env python3
"""
RTT を考慮した「全体の時間」を splits.sh の出力から後付けで計算する。

目的 (ユーザ確定モデル):
    全体の時間 = 基準時間(RTT未考慮) + 移動回数 × RTT
    移動回数   = キャッシュ MISS 数 (= サーバへ実際に行った回数)
    節約時間   = ヒット数 × RTT       (ヒット 1 回 = 往復 1 回を回避 = RTT を 1 回節約)

データ源 = splits.sh が policy×容量×start ごとに出す
    .../<POLICY_TAG>/start=*_..._global_transition.json
から、各 start の以下を読む:
    walk_time_total   : 実測の総 walk 時間 (秒)
    auth_time_total   : 実測の認可時間 (秒, LAN 上の実 RTT 分)
    auth_calls        : 認可実行回数 = MISS = 移動回数 (scalar)
    authorization_attempts : {entity: lookups}  (= 認可 lookup 数, hit+miss。node/edge 別に分解可)

基準時間(RTT未考慮)の取り方 (--base-mode):
    walk_minus_auth (既定): walk_time_total − auth_time_total
        = 実測時間から「LAN 上の実認可時間」を抜いた純計算時間。これに合成 RTT を載せ直す。
    walk : walk_time_total をそのまま基準にする (実 RTT が無視できる前提)。
    zero : 基準 0。RTT コスト分 (moves×RTT) だけを見る。

注意:
  - RTT は往復時間そのもの。1 MISS = 1 往復 = 1×RTT。×2 しない。
  - node/edge 別 RTT (--rtt-node ≠ --rtt-edge) には MISS の node/edge 分解が要るが、
    集計 (global_transition) には MISS の node/edge 内訳が無い (auth_calls は合算 scalar)。
    現状は uniform RTT (--rtt) のみ厳密。node/edge 別は順序付きアクセス列 (walk_events)
    からの policy 再現が必要 → analyze_opt_lru 系で別途。ここでは uniform を担当。

使い方:
  # 1 容量ぶん (vldb capa=100 の全 policy) を RTT スイープして時間比較
  python3 analyze_rtt_time.py \
      --root results/alpha0.01_walks_100_capa_100 --graph vldb \
      --rtt-sweep-ms 0,0.1,0.5,1,2,5,10 \
      --out-dir results/rtt_time/vldb_capa100

  # 複数容量をまとめて (root を results/ にして capa_* を自動探索)
  python3 analyze_rtt_time.py --root results --graph vldb --auto-caps \
      --rtt-ms 1 --out-dir results/rtt_time/vldb_allcaps
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

START_RE = re.compile(r"start=(\d+)_")
CAPA_RE = re.compile(r"capa_(\d+)")


def is_edge(key: str) -> bool:
    return str(key).startswith("edge_")


def load_policy(policy_dir: Path) -> Optional[dict]:
    """policy ディレクトリ配下の per-start global_transition を集計して返す。"""
    files = sorted(policy_dir.glob("start=*_global_transition.json"))
    if not files:
        return None
    agg = {
        "n_starts": 0,
        "walk_time": 0.0,
        "auth_time": 0.0,
        "moves": 0,        # auth_calls (MISS)
        "lookups": 0,      # sum authorization_attempts
        "node_lookups": 0,
        "edge_lookups": 0,
        "starts": [],
    }
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            print(f"[WARN] skip {f.name}: {e}")
            continue
        att = d.get("authorization_attempts", {}) or {}
        n_look = sum(att.values())
        n_node = sum(v for k, v in att.items() if not is_edge(k))
        n_edge = sum(v for k, v in att.items() if is_edge(k))
        wt = float(d.get("walk_time_total", 0.0) or 0.0)
        at = float(d.get("auth_time_total", 0.0) or 0.0)
        mv = int(d.get("auth_calls", 0) or 0)
        # length=1 などの不正 start は moves≈0 で実質無害だが記録だけする
        m = START_RE.search(f.name)
        agg["starts"].append(int(m.group(1)) if m else -1)
        agg["n_starts"] += 1
        agg["walk_time"] += wt
        agg["auth_time"] += at
        agg["moves"] += mv
        agg["lookups"] += n_look
        agg["node_lookups"] += n_node
        agg["edge_lookups"] += n_edge
    agg["hits"] = agg["lookups"] - agg["moves"]
    agg["hit_rate"] = agg["hits"] / agg["lookups"] if agg["lookups"] else float("nan")
    return agg


def base_time(agg: dict, mode: str) -> float:
    if mode == "walk_minus_auth":
        return max(0.0, agg["walk_time"] - agg["auth_time"])
    if mode == "walk":
        return agg["walk_time"]
    if mode == "zero":
        return 0.0
    raise ValueError(mode)


def discover(root: Path, graph: str, auto_caps: bool) -> List[tuple]:
    """(capacity:int|None, policy_tag:str, policy_dir:Path) を列挙。"""
    out = []
    if auto_caps:
        graph_dirs = sorted(root.glob(f"*capa_*/{graph}"))
    else:
        gd = root / graph
        graph_dirs = [gd] if gd.is_dir() else sorted(root.glob(graph))
        if not graph_dirs and (root.name == graph):
            graph_dirs = [root]
    for gd in graph_dirs:
        cm = CAPA_RE.search(str(gd))
        cap = int(cm.group(1)) if cm else None
        for pd in sorted(p for p in gd.iterdir() if p.is_dir() and p.name != "policy_compare"):
            out.append((cap, pd.name, pd))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="results dir (capa_* dir, graph dir, or results/ root)")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--auto-caps", action="store_true", help="root 配下の *capa_*/<graph> を全て探索")
    ap.add_argument("--base-mode", default="walk_minus_auth",
                    choices=["walk_minus_auth", "walk", "zero"])
    ap.add_argument("--rtt-sweep-ms", default="0,0.1,0.5,1,2,5,10",
                    help="RTT(ms) スイープ。total time をこの各値で出す")
    ap.add_argument("--rtt-ms", type=float, default=None,
                    help="単一 RTT(ms)。指定時はこの値での時間を主表に出す")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sweep = [float(x) for x in args.rtt_sweep_ms.split(",")]
    if args.rtt_ms is not None and args.rtt_ms not in sweep:
        sweep.append(args.rtt_ms)
        sweep.sort()

    rows = []
    for cap, tag, pd in discover(root, args.graph, args.auto_caps):
        agg = load_policy(pd)
        if not agg:
            continue
        bt = base_time(agg, args.base_mode)
        rows.append({"cap": cap, "policy": tag, "agg": agg, "base": bt})

    if not rows:
        print(f"[ERROR] no global_transition under {root} for graph={args.graph}")
        return

    # ---- 主表 (CSV) ----
    summary_csv = out_dir / f"{args.graph}_rtt_time_summary.csv"
    with summary_csv.open("w", newline="") as fp:
        w = csv.writer(fp)
        header = ["graph", "capacity", "policy", "n_starts",
                  "base_free_s", "walk_meas_s", "auth_meas_s",
                  "moves", "lookups", "node_lookups", "edge_lookups",
                  "hits", "hit_rate"]
        header += [f"time_s@rtt{m}ms" for m in sweep]
        w.writerow(header)
        for r in sorted(rows, key=lambda x: (x["cap"] or 0, x["policy"])):
            a = r["agg"]
            line = [args.graph, r["cap"], r["policy"], a["n_starts"],
                    f'{r["base"]:.3f}', f'{a["walk_time"]:.3f}', f'{a["auth_time"]:.3f}',
                    a["moves"], a["lookups"], a["node_lookups"], a["edge_lookups"],
                    a["hits"], f'{a["hit_rate"]:.4f}']
            for m in sweep:
                t = r["base"] + a["moves"] * (m / 1000.0)
                line.append(f"{t:.3f}")
            w.writerow(line)
    print(f"[OK] {summary_csv}")

    # ---- コンソール表示 (単一 RTT) ----
    rtt = args.rtt_ms if args.rtt_ms is not None else 1.0
    print(f"\n=== {args.graph}  base-mode={args.base_mode}  RTT={rtt}ms ===")
    print(f"{'cap':>5} {'policy':<34} {'moves':>7} {'hit%':>6} {'base_s':>8} {'+RTT_s':>8} {'total_s':>8}")
    for r in sorted(rows, key=lambda x: (x["cap"] or 0, x["policy"])):
        a = r["agg"]
        rtt_cost = a["moves"] * (rtt / 1000.0)
        total = r["base"] + rtt_cost
        print(f"{str(r['cap']):>5} {r['policy']:<34} {a['moves']:>7} "
              f"{100*a['hit_rate']:>5.1f} {r['base']:>8.2f} {rtt_cost:>8.2f} {total:>8.2f}")

    # ---- PNG (RTT スイープ) ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # capacity ごとに 1 枚 (policy を線で)
        caps = sorted({r["cap"] for r in rows}, key=lambda x: x or 0)
        for cap in caps:
            sub = [r for r in rows if r["cap"] == cap]
            if not sub:
                continue
            fig, ax = plt.subplots(figsize=(7, 5))
            for r in sorted(sub, key=lambda x: x["policy"]):
                a = r["agg"]
                ys = [r["base"] + a["moves"] * (m / 1000.0) for m in sweep]
                ax.plot(sweep, ys, marker="o", label=f'{r["policy"]} (hit {100*a["hit_rate"]:.0f}%)')
            ax.set_xlabel("RTT per move (ms)")
            ax.set_ylabel("total time (s) = base + moves×RTT")
            ax.set_title(f"{args.graph} capa={cap}: RTT-aware total time ({args.base_mode})")
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
            png = out_dir / f"{args.graph}_capa{cap}_rtt_time.png"
            fig.tight_layout()
            fig.savefig(png, dpi=120)
            plt.close(fig)
            print(f"[OK] {png}")
    except Exception as e:
        print(f"[WARN] plot skipped: {e}")


if __name__ == "__main__":
    main()
