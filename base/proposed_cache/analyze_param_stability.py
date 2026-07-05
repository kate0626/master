#!/usr/bin/env python3
"""
analyze_param_stability.py
  パラメータ (t/d/l/b/g/lw) を動かしたときの「実行時間」と「始点間の揺れ」を集計する。

  目的:
    - 始点数で割って per-start 平均にする (始点数が多い組ほど時間が大きく見える偏りを除去)
    - 始点間の揺れ (std / CV) を測り、揺れが小さく安定したパラメータを見つける
    - パラメータごとに値を変えたときの「時間」と「揺れ」の推移を表示

  使い方 (base ディレクトリから):
    python3 base/proposed_cache/analyze_param_stability.py \
      --input base/proposed_cache/results/alpha0.01_walks_100_capa_100/vldb_nobt

    # 認可時間だけで見る / 特定パラメータの推移にしぼる
    python3 ... --metric auth --vary lambda

    # 揺れを公平比較するため、全組で共通の始点だけを使う
    python3 ... --common-starts

  注意:
    始点間の揺れ(CV)は「どの始点を含むか」に強く依存する。組ごとに始点セットが
    違うと揺れの比較は不公平になるので、--common-starts で共通始点に揃えるか、
    全組を同じ START_NODES_LIST で回すこと。
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import statistics as st
from pathlib import Path

START_RE = re.compile(r"start=(\d+)_")

# tag からパラメータを抽出する正規表現 (無いものは None)
PARAM_RES = {
    "theta":  re.compile(r"_t([0-9.]+)"),
    "delta":  re.compile(r"_d([0-9.]+)"),
    "lambda": re.compile(r"_l([0-9.]+)"),   # _lw とは別 (_l の次は数字)
    "beta":   re.compile(r"_b([0-9.]+)"),
    "gamma":  re.compile(r"_g([0-9.]+)"),
    "lw":     re.compile(r"_lw([0-9]+)"),
}
PARAM_ORDER = ["theta", "delta", "lambda", "beta", "gamma", "lw"]


def parse_params(name: str) -> dict:
    # tag に現れないパラメータはコード既定値とみなす:
    #   _b 無し -> beta=1.0, _g 無し -> gamma=1.0, _lw 無し -> lw=0
    # (theta/delta/lambda は ppr_demand タグに通常含まれる)
    DEFAULTS = {"beta": "1.0", "gamma": "1.0", "lw": "0"}
    out = {}
    for k, rgx in PARAM_RES.items():
        m = rgx.search(name)
        out[k] = m.group(1) if m else DEFAULTS.get(k)
    return out


def metric_of(j: dict, metric: str) -> float:
    a = float(j.get("auth_time_total", 0.0))
    w = float(j.get("walk_time_total", 0.0))
    if metric == "auth":
        return a
    if metric == "walk":
        return w
    return a + w  # total


def collect_combo(d: Path, metric: str):
    """tag ディレクトリ -> {start: {time, hit, miss, calls}} を始点別に返す。"""
    per_start = {}
    for f in sorted(d.glob("start=*_global_transition.json")):
        m = START_RE.search(f.name)
        if not m:
            continue
        s = int(m.group(1))
        j = json.loads(f.read_text())
        per_start[s] = {
            "time": metric_of(j, metric),
            "hit": int(j.get("cache hit", 0)),
            "miss": int(j.get("cache miss", 0)),
            "calls": int(j.get("auth_calls", 0)),
            "wcalls": int(j.get("walk_calls", 0)),  # 実際に走った walk 数 (RW100補正用)
        }
    return per_start


def stats_of(times: list[float]) -> dict:
    n = len(times)
    mean = sum(times) / n if n else 0.0
    sd = st.pstdev(times) if n > 1 else 0.0
    cv = sd / mean if mean else 0.0
    return {
        "n": n,
        "mean": mean,           # per-start 平均 (= sum/n, 始点数で割った正規化)
        "std": sd,              # 揺れ (絶対)
        "cv": cv,               # 揺れ (相対 = std/mean)。揺れの公平比較用
        "min": min(times) if times else 0.0,
        "max": max(times) if times else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="results/.../<graph>_<tag> ディレクトリ")
    ap.add_argument("--policy-prefix", default="ppr_demand", help="対象タグの接頭辞")
    ap.add_argument("--metric", default="total", choices=["total", "auth", "walk"],
                    help="時間指標 (total=auth+walk)")
    ap.add_argument("--rw100", action="store_true",
                    help="学習walkで増えた分を補正し RW=N 相当に正規化 (time*N/walk_calls)。"
                         "lw>0 の時間増を取り除いて公平に比較する")
    ap.add_argument("--rw-target", type=int, default=100,
                    help="--rw100 の基準walk数 (既定100=本走行RW数)")
    ap.add_argument("--vary", default=None, choices=PARAM_ORDER,
                    help="このパラメータの値ごとの推移だけ詳しく出す")
    ap.add_argument("--fix", nargs="*", default=None,
                    help="他パラメータを固定して交絡を除く。例: --fix t=0 d=1 b=1 g=0 lw=10 "
                         "(別名 t/d/l/b/g/lw も可。値は float 一致で比較)")
    ap.add_argument("--objective", default="hit", choices=["hit", "time"],
                    help="best を選ぶ基準 (hit=ヒット率最大 / time=時間最小)")
    ap.add_argument("--start", type=int, default=None,
                    help="この始点1本だけで比較 (固定始点でパラメータ比較。揺れは測らない)")
    ap.add_argument("--common-starts", action="store_true",
                    help="全組に共通する始点だけに揃えて揺れを公平比較する")
    ap.add_argument("--min-starts", type=int, default=1,
                    help="始点数がこれ未満の組は除外 (揺れを測るなら 2 以上推奨)")
    ap.add_argument("--out", default=None, help="CSV 出力先 (省略時 input 配下に自動)")
    args = ap.parse_args()

    base = Path(args.input)
    if not base.is_dir():
        raise SystemExit(f"[ERROR] not a directory: {base}")

    dirs = [d for d in sorted(base.iterdir())
            if d.is_dir() and d.name.startswith(args.policy_prefix)]
    if not dirs:
        raise SystemExit(f"[ERROR] no '{args.policy_prefix}*' dirs under {base}")

    # まず各組の始点別データを集める ({start: {time,hit,miss,calls}})
    raw = {}
    for d in dirs:
        pst = collect_combo(d, args.metric)
        if pst:
            raw[d.name] = pst

    # 使う始点の決定: --start 指定が最優先、次に --common-starts、無ければ各組の全始点
    common = None
    if args.start is not None:
        common = {args.start}
        print(f"[固定始点] start={args.start} だけでパラメータを比較します")
    elif args.common_starts:
        sets = [set(v.keys()) for v in raw.values()]
        common = set.intersection(*sets) if sets else set()
        print(f"[共通始点] {sorted(common)} ({len(common)}本) に揃えて比較")

    rows = []
    start_counts = set()
    for name, pst in raw.items():
        starts = sorted(pst.keys())
        if common is not None:
            starts = [s for s in starts if s in common]
        if len(starts) < args.min_starts:
            continue
        if args.rw100:
            # 学習walkで増えた分を補正: time * rw_target / walk_calls (RW=target 相当)
            times = [pst[s]["time"] * args.rw_target / pst[s]["wcalls"]
                     for s in starts if pst[s]["wcalls"] > 0]
        else:
            times = [pst[s]["time"] for s in starts]
        if not times:
            continue
        hit = sum(pst[s]["hit"] for s in starts)
        miss = sum(pst[s]["miss"] for s in starts)
        calls = sum(pst[s]["calls"] for s in starts)
        ss = stats_of(times)
        start_counts.add(ss["n"])
        hr = hit / (hit + miss) if (hit + miss) else 0.0
        p = parse_params(name)
        rows.append({"tag": name, **p, **ss, "hit_rate": hr,
                     "calls_per_start": calls / ss["n"] if ss["n"] else 0.0})

    if not rows:
        raise SystemExit("[ERROR] 集計対象なし (--start/--min-starts 条件を確認)")

    # 始点数がバラついていたら警告 (時間比較が始点数で偏る)
    if args.start is None and not args.common_starts and len(start_counts) > 1:
        print(f"[警告] 始点数が組ごとに異なります: {sorted(start_counts)}")
        print("       時間/揺れの比較が不公平になります。--start N で始点を固定するか、")
        print("       全組を同じ START_NODES_LIST で回すことを推奨します。\n")

    # ---- --fix: 他パラメータを固定して交絡を除く ----
    ALIAS = {"t": "theta", "d": "delta", "l": "lambda", "b": "beta", "g": "gamma", "lw": "lw"}

    def fnum(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    if args.fix:
        fixes = {}
        for kv in args.fix:
            if "=" not in kv:
                continue
            k, val = kv.split("=", 1)
            key = ALIAS.get(k.strip(), k.strip())
            if key in PARAM_ORDER:
                fixes[key] = fnum(val)
        before = len(rows)
        rows = [r for r in rows
                if all(fnum(r.get(k)) == v for k, v in fixes.items())]
        fixstr = " ".join(f"{k}={v}" for k, v in fixes.items())
        print(f"[固定] {fixstr} -> {len(rows)}/{before} 組に絞り込み\n")
        if not rows:
            raise SystemExit("[ERROR] 固定条件に一致する組がありません")

    # ---- 1) per-combo 表 (per-start 時間が小さい順) ----
    print(f"=== {base.name}: per-combo (metric={args.metric}, 時間は per-start 平均) ===")
    hdr = f"{'tag':<48}{'#st':>4}{'hit':>7}{'t_mean':>9}{'t_std':>8}{'t_cv':>7}{'calls/st':>10}"
    print(hdr)
    for r in sorted(rows, key=lambda r: r["mean"]):
        print(f"{r['tag'][:48]:<48}{r['n']:>4}{r['hit_rate']:>7.3f}"
              f"{r['mean']:>9.2f}{r['std']:>8.2f}{r['cv']:>7.3f}{r['calls_per_start']:>10.0f}")

    # ---- 2) パラメータごとの推移 (値 -> 平均 t_mean / hit。"1" と "1.0" は float で統合) ----
    def marginal(param: str):
        groups = {}   # float値 -> rows
        for r in rows:
            fv = fnum(r.get(param))
            if fv is None:
                continue
            groups.setdefault(fv, []).append(r)
        if not groups:
            return
        note = " (他は固定済み)" if args.fix else " (値ごとに他組を平均=交絡注意)"
        print(f"\n--- {param} の推移{note} ---")
        print(f"{param:>8}{'#combo':>8}{'t_mean':>9}{'hit':>8}")
        summ = []
        for fv in sorted(groups):
            g = groups[fv]
            tm = sum(x["mean"] for x in g) / len(g)
            hr = sum(x["hit_rate"] for x in g) / len(g)
            summ.append((fv, len(g), tm, hr))
        # best を選ぶ (objective: hit 最大 / time 最小)
        best = (max(summ, key=lambda s: s[3]) if args.objective == "hit"
                else min(summ, key=lambda s: s[2]))
        for fv, n, tm, hr in summ:
            mark = "  <- best" if (fv, n, tm, hr) == best else ""
            print(f"{fv:>8g}{n:>8}{tm:>9.2f}{hr:>8.3f}{mark}")
        obj = "hit最大" if args.objective == "hit" else "time最小"
        print(f"  => {param} の best ({obj}) = {best[0]:g}")

    if args.vary:
        marginal(args.vary)
    else:
        for p in PARAM_ORDER:
            marginal(p)

    # ---- 3) CSV 出力 ----
    out = Path(args.out) if args.out else (base / f"param_stability_{args.metric}.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tag", *PARAM_ORDER, "n_starts", "hit_rate",
                    "time_mean_per_start", "time_std", "time_cv",
                    "time_min", "time_max", "calls_per_start"])
        for r in sorted(rows, key=lambda r: r["mean"]):
            w.writerow([r["tag"], *[r.get(p, "") for p in PARAM_ORDER],
                        r["n"], f"{r['hit_rate']:.6f}",
                        f"{r['mean']:.6f}", f"{r['std']:.6f}", f"{r['cv']:.6f}",
                        f"{r['min']:.6f}", f"{r['max']:.6f}", f"{r['calls_per_start']:.3f}"])
    print(f"\n[OUT] {out}")


if __name__ == "__main__":
    main()
