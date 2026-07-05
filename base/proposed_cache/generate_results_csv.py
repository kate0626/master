#!/usr/bin/env python3
"""
generate_results_csv.py
  <graph>_nobt 配下の実験結果を 2 つの CSV に集約する。
  各 alpha フォルダの <graph>_nobt ごとに、その中身だけを集計し、
  当該 <graph>_nobt フォルダ配下に CSV を出力する。

出力 (フォルダごと):
  results/alpha*_walks_*_capa_*/<graph>_nobt/all_<graph>_nobt_settings.csv
  results/alpha*_walks_*_capa_*/<graph>_nobt/all_<graph>_nobt_results.csv

使い方:
  cd /Users/maiko/Documents/GitHub/master-progrem
  python3 base/proposed_cache/generate_results_csv.py                # vldb (デフォルト)
  python3 base/proposed_cache/generate_results_csv.py --graph amazon0601
"""

from __future__ import annotations
import argparse
import json
import csv
import re
import pathlib

BASE = pathlib.Path(__file__).parent / "results"


def parse_policy_tag(tag: str) -> dict:
    """ディレクトリ名からポリシー設定を辞書で返す。"""
    d = {
        "policy": None, "cache_cap": None,
        "theta": None, "delta": None, "lambda": None,
        "beta": None, "gamma": None, "lw": 0,
        "admit_hops": None,
        # reuse_score 用: 飽和定数
        "cf": None, "cl": None, "cd": None,
        # reuse_score 用: recency (LRU 要素) の指数 ρ と飽和点 C_R
        "rho": None, "cr": None,
    }
    tag = tag.strip("/")

    m = re.search(r"_h(-?\d+)", tag)
    d["admit_hops"] = int(m.group(1)) if m else None

    m = re.search(r"_lw(\d+)", tag)
    d["lw"] = int(m.group(1)) if m else 0

    if tag.startswith("reuse_score"):
        # 新提案: reuse_score_cap100_t1.0_l1.0_b1.0_g1.0_cf5.0_cl5.0_cd10.0_h-0
        d["policy"] = "reuse_score"
        m = re.search(r"cap(\d+)",  tag); d["cache_cap"] = int(m.group(1))   if m else None
        m = re.search(r"_t([0-9.]+)", tag); d["theta"]   = float(m.group(1)) if m else None
        m = re.search(r"_l([0-9.]+)_", tag); d["lambda"] = float(m.group(1)) if m else None
        m = re.search(r"_b([0-9.]+)", tag); d["beta"]    = float(m.group(1)) if m else None
        m = re.search(r"_g([0-9.]+)", tag); d["gamma"]   = float(m.group(1)) if m else None
        m = re.search(r"_cf([0-9.]+)", tag); d["cf"]     = float(m.group(1)) if m else None
        m = re.search(r"_cl([0-9.]+)", tag); d["cl"]     = float(m.group(1)) if m else None
        m = re.search(r"_cd([0-9.]+)", tag); d["cd"]     = float(m.group(1)) if m else None
        # recency (LRU 要素): reuse_score_..._rho1.0_cr8.0_h-0
        m = re.search(r"_rho([0-9.]+)", tag); d["rho"]   = float(m.group(1)) if m else None
        m = re.search(r"_cr([0-9.]+)", tag);  d["cr"]    = float(m.group(1)) if m else None
    elif tag.startswith("ppr_demand"):
        d["policy"] = "ppr_demand"
        m = re.search(r"cap(\d+)", tag);     d["cache_cap"] = int(m.group(1)) if m else None
        m = re.search(r"_t([0-9.]+)",  tag); d["theta"]  = float(m.group(1)) if m else None
        m = re.search(r"_d([0-9.]+)",  tag); d["delta"]  = float(m.group(1)) if m else None
        m = re.search(r"_l([0-9.]+)_", tag); d["lambda"] = float(m.group(1)) if m else None
        m = re.search(r"_b([0-9.]+)",  tag); d["beta"]   = float(m.group(1)) if m else None
        m = re.search(r"_g([0-9.]+)",  tag); d["gamma"]  = float(m.group(1)) if m else None
    elif tag.startswith("lru"):
        d["policy"] = "lru"
        m = re.search(r"lru_(\d+)", tag); d["cache_cap"] = int(m.group(1)) if m else None
    elif tag.startswith("arc"):
        d["policy"] = "arc"
        m = re.search(r"arc_(\d+)", tag); d["cache_cap"] = int(m.group(1)) if m else None
    elif tag.startswith("memo"):
        d["policy"] = "memo"
        m = re.search(r"memo_(\d+)", tag); d["cache_cap"] = int(m.group(1)) if m else None
    elif tag.startswith("none"):
        d["policy"] = "none"
        m = re.search(r"none_(\d+)", tag); d["cache_cap"] = int(m.group(1)) if m else None
    else:
        d["policy"] = tag
    return d


def aggregate_jsons(pol_dir: pathlib.Path):
    """ポリシーディレクトリ内の JSON を集計して per-start のリストを返す。"""
    files = sorted(pol_dir.glob("*_global_transition.json"))
    if not files:
        return None
    rows = []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        h = data.get("cache hit")
        m = data.get("cache miss")
        if h is None or m is None:
            nh, nm = data.get("node_hit"), data.get("node_miss")
            eh, em = data.get("edge_hit"), data.get("edge_miss")
            if None in (nh, nm, eh, em):
                continue
            h, m = nh + eh, nm + em
        sm = re.search(r"start=(\d+)", f.name)
        start = int(sm.group(1)) if sm else -1
        rows.append({
            "start": start,
            "cache_hit": h,
            "cache_miss": m,
            "hit_rate": h / (h + m) if (h + m) > 0 else 0.0,
            "walk_time": data.get("walk_time_total", None),
            "auth_time": data.get("auth_time_total", None),
            "auth_calls": data.get("auth_calls", None),
        })
    return rows if rows else None


S_FIELDS = ["run_id","alpha","exp_cap","policy","cache_cap",
            "theta","delta","lambda","beta","gamma","lw",
            "admit_hops","cf","cl","cd","rho","cr","policy_tag"]
R_FIELDS = ["run_id","n_starts","total_cache_hit","total_cache_miss",
            "hit_rate","walk_time_sum","auth_time_sum",
            "walk_time_per_start","auth_time_per_start"]


def write_csv(path: pathlib.Path, fields: list, rows: list):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def collect_nobt_dir(nobt_dir: pathlib.Path):
    """1 つの <graph>_nobt フォルダ分だけを集計し (settings_rows, results_rows) を返す。"""
    exp_dir = nobt_dir.parent.name
    m = re.match(r"alpha([0-9.]+)_walks_(\d+)_capa_(\d+)", exp_dir)
    if not m:
        return None, None
    alpha = float(m.group(1)); walks = int(m.group(2)); exp_cap = int(m.group(3))

    settings_rows = []
    results_rows  = []
    run_id = 0

    for pol_dir in sorted(nobt_dir.iterdir()):
        if not pol_dir.is_dir() or pol_dir.name == "policy_compare":
            continue
        tag = pol_dir.name
        parsed = parse_policy_tag(tag)
        rows = aggregate_jsons(pol_dir)
        if rows is None:
            continue

        run_id += 1
        total_h = sum(r["cache_hit"]  for r in rows)
        total_m = sum(r["cache_miss"] for r in rows)
        avg_hr  = total_h / (total_h + total_m) if (total_h + total_m) > 0 else 0.0
        wt_list = [r["walk_time"] for r in rows if r["walk_time"] is not None]
        at_list = [r["auth_time"] for r in rows if r["auth_time"] is not None]

        settings_rows.append({
            "run_id": run_id,
            "alpha": alpha,
            "exp_cap": exp_cap,
            "policy": parsed["policy"],
            "cache_cap": parsed["cache_cap"],
            "theta": parsed["theta"],
            "delta": parsed["delta"],
            "lambda": parsed["lambda"],
            "beta": parsed["beta"],
            "gamma": parsed["gamma"],
            "lw": parsed["lw"],
            "admit_hops": parsed["admit_hops"],
            "cf": parsed.get("cf"),
            "cl": parsed.get("cl"),
            "cd": parsed.get("cd"),
            "rho": parsed.get("rho"),
            "cr": parsed.get("cr"),
            "policy_tag": tag,
        })
        results_rows.append({
            "run_id": run_id,
            "n_starts": len(rows),
            "total_cache_hit": total_h,
            "total_cache_miss": total_m,
            "hit_rate": round(avg_hr, 6),
            "walk_time_sum": round(sum(wt_list), 4) if wt_list else None,
            "auth_time_sum": round(sum(at_list), 4) if at_list else None,
            "walk_time_per_start": round(sum(wt_list) / len(wt_list), 4) if wt_list else None,
            "auth_time_per_start": round(sum(at_list) / len(at_list), 4) if at_list else None,
        })

    return settings_rows, results_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="vldb", help="グラフ名 (例: vldb, amazon0601)")
    args = ap.parse_args()
    graph = args.graph
    nobt_name = f"{graph}_nobt"

    nobt_dirs = sorted(BASE.glob(f"alpha*_walks_*_capa_*/{nobt_name}"))
    print(f"[INFO] 対象 = {nobt_name}  走査ディレクトリ数: {len(nobt_dirs)}")

    n_saved = 0
    for nobt_dir in nobt_dirs:
        settings_rows, results_rows = collect_nobt_dir(nobt_dir)
        if not settings_rows:
            continue

        # 各 <graph>_nobt フォルダ配下に、そのフォルダ分だけの集計 CSV を出力
        s_path = nobt_dir / f"all_{nobt_name}_settings.csv"
        r_path = nobt_dir / f"all_{nobt_name}_results.csv"
        write_csv(s_path, S_FIELDS, settings_rows)
        write_csv(r_path, R_FIELDS, results_rows)
        n_saved += 1

        print(f"[SAVED] {s_path}  ({len(settings_rows)} rows)")
        print(f"[SAVED] {r_path}  ({len(results_rows)} rows)")

    print(f"[DONE] 出力フォルダ数: {n_saved}")


if __name__ == "__main__":
    main()
