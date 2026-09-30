#!/usr/bin/env python3
# =============================================================================
# aggregate_pair_correlations.py
#   analyze_pair_correlations.py が吐いた paircorr_*.csv を横断して 1 枚の表にする。
#   ns = ブートストラップ 95%CI が 0 をまたぐ (相関ありと言えない)
#
#   python3 aggregate_pair_correlations.py [--kind all] [--hop walk]
# =============================================================================
from __future__ import annotations
import argparse, csv, glob, os
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "results"
PAIRS = [("deg", "hop"), ("deg", "past"), ("deg", "recency"),
         ("hop", "past"), ("hop", "recency"), ("past", "recency")]
HDR = ["D×H", "D×O", "D×R", "H×O", "H×R", "O×R"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="all", choices=["all", "node", "edge"])
    ap.add_argument("--hop", default="walk", choices=["walk", "walk_min", "bfs"])
    a = ap.parse_args()

    suffix = f"_{a.kind}_h{a.hop}.csv"
    files = sorted(glob.glob(str(RES / f"paircorr_*{suffix}")))
    if not files:
        raise SystemExit(f"{RES} に paircorr_*{suffix} が無い")

    print(f"# kind={a.kind}  hop={a.hop}   (ns = 95%CI が 0 をまたぐ)")
    print(f"{'config':<24}" + "".join(f"{h:>12}" for h in HDR))
    for f in files:
        tag = os.path.basename(f)[len("paircorr_"):-len(suffix)].replace("_w1000_s8", "")
        d = {(r["x"], r["z"]): r for r in csv.DictReader(open(f))}
        line = f"{tag:<24}"
        for p in PAIRS:
            r = d.get(p)
            if r is None:
                line += f"{'-':>12}"
                continue
            rho = float(r["rho"])
            ns = float(r["ci_lo"]) <= 0 <= float(r["ci_hi"])
            line += f"{rho:>+10.2f}{'ns' if ns else '  '}"
        print(line)


if __name__ == "__main__":
    main()
