#!/usr/bin/env python3
# =============================================================================
# aggregate_access_factors.py
#   analyze_access_factors.py が吐いた summary CSV を横断して比較表にする。
#   使い方: python3 aggregate_access_factors.py [--cap 100] [tag ...]
#   tag 省略時は results/accessfactors_*_summary.csv を全部拾う (karate は除外)。
# =============================================================================
from __future__ import annotations
import argparse, csv, glob, statistics as st
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "results"


def load(tag):
    return list(csv.DictReader((RES / f"accessfactors_{tag}_summary.csv").open()))


def ms(rows, kind, key):
    v = [float(r[key]) for r in rows
         if r["kind"] == kind and r["start"] != "POOLED"
         and r.get(key, "") not in ("", "nan")]
    if not v:
        return float("nan"), 0.0
    return st.mean(v), (st.stdev(v) if len(v) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="*")
    ap.add_argument("--cap", type=int, default=100)
    a = ap.parse_args()
    tags = a.tags or [Path(p).name[len("accessfactors_"):-len("_summary.csv")]
                      for p in sorted(glob.glob(str(RES / "accessfactors_*_summary.csv")))
                      if "karate" not in p]

    feats = [("rho_deg", "D 次数"), ("rho_hop", "H hop"),
             ("rho_past", "O 過去回数"), ("rho_recency", "R 直近性")]
    for kind in ("all", "node", "edge"):
        print(f"\n##### kind={kind}  Spearman ρ(要素, 将来アクセス回数)  mean±sd over starts")
        print(f"{'config':<26}" + "".join(f"{n:>18}" for _, n in feats))
        for t in tags:
            rows = load(t); line = f"{t:<26}"
            for k, _ in feats:
                m, s = ms(rows, kind, k); line += f"{m:>+11.3f}±{s:<6.3f}"
            print(line)

    pk = [("rho_deg|past", "D|O"), ("rho_deg|hop", "D|H"), ("rho_hop|past", "H|O"),
          ("rho_recency|past", "R|O"), ("rho_past|recency", "O|R")]
    print("\n##### 統制後 (partial rank corr)  kind=all")
    print(f"{'config':<26}" + "".join(f"{n:>14}" for _, n in pk))
    for t in tags:
        rows = load(t); line = f"{t:<26}"
        for k, _ in pk:
            m, _s = ms(rows, "all", k); line += f"{m:>+14.3f}"
        print(line)

    ck = [f"cov{a.cap}_{n}" for n in
          ("deg", "hop", "past", "recency", "oracle", "random")]
    print(f"\n##### top-{a.cap} 将来アクセス捕捉率 [%]  kind=all")
    print(f"{'config':<26}" + "".join(f"{k.split('_')[1]:>12}" for k in ck))
    for t in tags:
        rows = load(t); line = f"{t:<26}"
        for k in ck:
            m, _ = ms(rows, "all", k); line += f"{m:>12.1f}"
        print(line)


if __name__ == "__main__":
    main()
