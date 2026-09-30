#!/usr/bin/env python3
# =============================================================================
# analyze_onoff_maineffects.py
#   run_onoff_ablation.sh が出した 2^4 factorial の集約 CSV から
#   各因子 (O, L, H, D) の「効き」を主効果として集計する。
#
#   主効果(F) = mean(hit_rate | F=ON) - mean(hit_rate | F=OFF)
#              （F 以外の全組合せで平均 → 交絡が消える）
#   これが指数スイープより信頼できる「どの因子が最も効くか」の答え。
#
#   併せて、参考として:
#     - 単独ON  (その因子だけ ON, 他 OFF)
#     - Leave-one-out (その因子だけ OFF, 他 ON)
#     - 2因子交互作用 (どの組合せが相乗/打消しか)
#     - ベースライン (lru/arc/memo) との比較
#
#   使い方:
#     python3 analyze_onoff_maineffects.py --graph amazon0601
#     python3 analyze_onoff_maineffects.py --graph vldb
# =============================================================================
import argparse
import csv
import os
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))

# 因子名 -> settings CSV の列名
FACTOR_COL = {"O": "theta", "L": "lambda", "H": "beta", "D": "gamma"}
FACTOR_LABEL = {"O": "O 頻度", "L": "L 学習", "H": "H 距離", "D": "D 次数"}
FACTORS = ["O", "L", "H", "D"]


def _on(v):
    """settings の値 (文字列 '1.0'/'0.0' 等) を ON/OFF へ。"""
    try:
        return float(v) >= 0.5
    except (TypeError, ValueError):
        return False


def load(graph):
    base = os.path.join(HERE, "results")
    # nobt 付き / 無しの両方を許容
    cands = [
        (f"all_{graph}_nobt_settings.csv", f"all_{graph}_nobt_results.csv"),
        (f"all_{graph}_settings.csv", f"all_{graph}_results.csv"),
    ]
    sett = res = None
    for s, r in cands:
        # results/ 直下 か results/<graph>_nobt/ の下を探す
        for prefix in ("", f"{graph}_nobt", f"{graph}"):
            sp = os.path.join(base, prefix, s)
            rp = os.path.join(base, prefix, r)
            if os.path.exists(sp) and os.path.exists(rp):
                sett, res = sp, rp
                break
        if sett:
            break
    if not sett:
        raise SystemExit(f"[ERROR] 集約 CSV が見つかりません (graph={graph})。先に generate_results_csv.py を実行。")

    S = {row["run_id"]: row for row in csv.DictReader(open(sett))}
    R = {row["run_id"]: row for row in csv.DictReader(open(res))}
    rows = []
    for rid, s in S.items():
        if rid not in R:
            continue
        r = R[rid]
        try:
            hr = float(r["hit_rate"])
        except (TypeError, ValueError):
            continue
        rows.append(dict(policy=s["policy"], s=s, hit=hr,
                         nstarts=r.get("n_starts", "?"),
                         walk=r.get("walk_time_per_start", "?")))
    return rows, sett


def factorial_cells(rows):
    """reuse_score の 16 combo を {(O,L,H,D): hit_rate} に。lw 付きは除外。"""
    cells = {}
    for row in rows:
        if row["policy"] != "reuse_score":
            continue
        s = row["s"]
        # 学習walk (lw) を明示指定したバリアントは factorial 対象外
        lw = s.get("lw", "0") or "0"
        if str(lw) not in ("0", "0.0", ""):
            continue
        key = tuple(_on(s[FACTOR_COL[f]]) for f in FACTORS)
        # 同一 key が複数あれば平均
        cells.setdefault(key, []).append(row["hit"])
    return {k: sum(v) / len(v) for k, v in cells.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    args = ap.parse_args()

    rows, sett = load(args.graph)
    cells = factorial_cells(rows)

    print("=" * 62)
    print(f"ON/OFF factorial 主効果集計   graph={args.graph}")
    print(f"  settings: {os.path.relpath(sett, HERE)}")
    print(f"  reuse_score combo 数: {len(cells)} / 16")
    print("=" * 62)

    # ---- ベースライン ----
    print("\n[ベースライン]")
    for pol in ("lru", "arc", "memo", "none"):
        b = [r for r in rows if r["policy"] == pol]
        if b:
            r = b[0]
            print(f"  {pol:5s} hit={r['hit']*100:6.2f}%  (starts={r['nstarts']}, walk={r['walk']}s)")

    if not cells:
        print("\n[WARN] reuse_score の combo が無いので factorial 集計をスキップ。")
        return

    idx = {f: i for i, f in enumerate(FACTORS)}

    def mean_where(f, on):
        vals = [hr for k, hr in cells.items() if k[idx[f]] == on]
        return sum(vals) / len(vals) if vals else float("nan")

    # ---- 主効果 ----
    print("\n[主効果]  ON平均 − OFF平均  (他因子の全組合せで平均, %ポイント)")
    eff = {}
    for f in FACTORS:
        on, off = mean_where(f, True), mean_where(f, False)
        eff[f] = (on - off) * 100
        print(f"  {FACTOR_LABEL[f]:8s}: {eff[f]:+6.2f} pt   (ON={on*100:5.2f}%  OFF={off*100:5.2f}%)")

    order = sorted(FACTORS, key=lambda f: -eff[f])
    print("\n  → 効き順 (大きいほど重要): " + "  >  ".join(
        f"{FACTOR_LABEL[f]}({eff[f]:+.2f})" for f in order))

    # ---- 単独ON / Leave-one-out ----
    all_off = tuple(False for _ in FACTORS)
    all_on = tuple(True for _ in FACTORS)
    print("\n[参考] 単独ON (その因子だけ) / Leave-one-out (その因子だけOFF)")
    for f in FACTORS:
        solo = list(all_off); solo[idx[f]] = True; solo = tuple(solo)
        loo = list(all_on); loo[idx[f]] = False; loo = tuple(loo)
        s = f"{cells[solo]*100:5.2f}%" if solo in cells else "  n/a"
        l = f"{cells[loo]*100:5.2f}%" if loo in cells else "  n/a"
        print(f"  {FACTOR_LABEL[f]:8s}: 単独ON={s}   抜くと={l}")
    if all_on in cells:
        print(f"  {'全部ON':8s}: {cells[all_on]*100:5.2f}%")
    if all_off in cells:
        print(f"  {'全部OFF':8s}: {cells[all_off]*100:5.2f}%  (V=const, aging+sizeのみ)")

    # ---- 2因子交互作用 ----
    print("\n[2因子 交互作用]  (両ON効果) − (単独効果の和); + は相乗, − は打消し")
    base = cells.get(all_off)
    if base is not None:
        for a, b in combinations(FACTORS, 2):
            ka = list(all_off); ka[idx[a]] = True; ka = tuple(ka)
            kb = list(all_off); kb[idx[b]] = True; kb = tuple(kb)
            kab = list(all_off); kab[idx[a]] = True; kab[idx[b]] = True; kab = tuple(kab)
            if ka in cells and kb in cells and kab in cells:
                inter = (cells[kab] - base) - ((cells[ka] - base) + (cells[kb] - base))
                tag = "相乗" if inter > 0 else "打消"
                print(f"  {FACTOR_LABEL[a]} × {FACTOR_LABEL[b]}: {inter*100:+6.2f} pt  ({tag})")
    else:
        print("  (全部OFF セルが無いため基準が取れずスキップ)")

    # ---- 最良 combo ----
    best = max(cells.items(), key=lambda kv: kv[1])
    onlist = "+".join(FACTOR_LABEL[f].split()[0] for f, b in zip(FACTORS, best[0]) if b) or "(なし)"
    print(f"\n[最良 combo] {best[1]*100:.2f}%  ON factors = {onlist}")
    print("=" * 62)


if __name__ == "__main__":
    main()
