"""
直近の controller 出力(global_transition.json)から RTT 内訳を集計し、
methods_summary.csv に1行追記する。run_multi_local.sh が各実行の最後に呼ぶ。
  列: graph, walks, alpha, method, rtt_move(A), rtt_auth(B), rtt_total, hit_rate
  method = 認可なし / 認可あり・cacheなし / 認可あり・<policy>
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os


def method_label(authtag: str, policy: str) -> str:
    if authtag == "noauth":
        return "①認可なし"
    if policy == "none":
        return "②認可あり/cacheなし"
    return f"③認可あり/{policy}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--graph", required=True)
    ap.add_argument("--walks", required=True)
    ap.add_argument("--alpha", required=True)
    ap.add_argument("--cache-policy", required=True)
    ap.add_argument("--authtag", required=True)
    a = ap.parse_args()

    pat = (f"{a.results}/start=*_walks={a.walks}_alpha={a.alpha}_*"
           f"_cache={a.cache_policy}_*_global_transition.json")
    fs = sorted(glob.glob(pat), key=os.path.getmtime)
    if not fs:
        print(f"[append_summary] no json for {pat}")
        return
    d = json.load(open(fs[-1]))                      # 直近(=この実行)のファイル

    mv = au = hit = miss = 0.0
    for e in d.get("per_server_access_stats", []):
        s = e["stats"]
        mv += s.get("rtt_move_total", 0.0)
        au += s.get("rtt_auth_total", 0.0)
        hit += s.get("auth_cache_hit", 0)
        miss += s.get("auth_cache_miss", 0)

    row = {
        "graph": a.graph, "walks": int(a.walks), "alpha": float(a.alpha),
        "method": method_label(a.authtag, a.cache_policy),
        "rtt_move": round(mv, 1), "rtt_auth": round(au, 1),
        "rtt_total": round(mv + au, 1),
        "hit_rate": round(hit / (hit + miss), 3) if (hit + miss) else 0.0,
    }
    csvp = f"{a.results}/methods_summary.csv"
    is_new = not os.path.exists(csvp)
    with open(csvp, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if is_new:
            w.writeheader()
        w.writerow(row)
    print(f"[append_summary] + {row}")


if __name__ == "__main__":
    main()
