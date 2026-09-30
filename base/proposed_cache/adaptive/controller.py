#!/usr/bin/env python3
# =============================================================================
# controller.py — walk をバッチに分けて投げ、学習の進み具合（学習曲線）を記録する
#
#   既存 core/split_controller_reuse_score.py は /walk を1回で全本数投げるので、
#   途中経過（walk を重ねるほどヒット率が上がるか）が見えない。こちらは:
#
#     1. 最初に全サーバの /cache/reset（キャッシュと重みを初期化）
#     2. walk を --batch-walks 本ずつ /walk で投げる
#        seed = seed + (バッチ番号 × バッチ本数) にするので、サーバ側の
#        「i 本目は seed+i」の規則と合わせて **一括で投げた場合と同じ walk 集合**になる
#     3. バッチごとに全サーバの GET /learner_stats を取り、
#        バッチ内ヒット率（差分）・累積ヒット率・学習した重みを記録する
#     4. 最後に重みの履歴ごと JSON、学習曲線を CSV に保存する
#
#   /cache/reset はバッチ間では呼ばない（＝学習した重みが引き継がれる）。
#   学習キャッシュ以外（lru / arc / reuse_score）でも同じ形式で曲線が取れるので、
#   同じ walk 列に対する比較曲線になる。
#
#   使い方: run_local.sh / run_cluster.sh から呼ぶ。単体なら
#     python3 base/proposed_cache/adaptive/controller.py --servers 2 \
#       --server-endpoints 127.0.0.1:4100 127.0.0.1:4101 --start-node 0 \
#       --walks 1000 --batch-walks 100 --alpha 0.1 --seed 1 --tag learned \
#       --out-dir base/proposed_cache/adaptive/results/local
# =============================================================================
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.join(os.path.dirname(_THIS_DIR), "core")
for _p in (_CORE, os.path.join(_CORE, "multi")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import split_controller_reuse_score as ctrl   # noqa: E402  (HTTP ヘルパと始点サーバ選択を流用)
import partition as part                      # noqa: E402  (core/multi: ノード/エッジ分離配置)
sys.path.insert(0, _THIS_DIR)
import belady as bel                          # noqa: E402

COUNTERS = ("auth_cache_hit", "auth_cache_miss", "hit_node", "miss_node",
            "hit_edge", "miss_edge", "local_auth_calls", "remote_auth_calls")
FEATURES = ("b", "H", "O", "D", "R")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="学習曲線を取るコントローラ")
    p.add_argument("--servers", type=int, required=True)
    p.add_argument("--server-endpoints", nargs="+", required=True)
    p.add_argument("--start-node", type=int, default=0)
    p.add_argument("--walks", type=int, default=1000,
                   help="総 walk 本数（--until-converged のときは最小本数）")
    p.add_argument("--until-converged", action="store_true",
                   help="--walks を超えたら、学習が飽和するまで（最大 --max-walks まで）バッチを足し続ける")
    p.add_argument("--max-walks", type=int, default=None, help="--until-converged の上限（既定 --walks の 10 倍）")
    p.add_argument("--conv-window", type=int, default=5,
                   help="飽和の判定に使うバッチ数 C（C バッチ前との比較・直近 C と その前 C の比較）")
    p.add_argument("--conv-cos", type=float, default=0.995,
                   help="重みの向き（b を除く重みベクトル）の C バッチ前とのコサイン類似度の下限（全サーバ）")
    p.add_argument("--conv-norm-tol", type=float, default=0.05,
                   help="実効的な重みベクトルの大きさの C バッチ前からの相対変化の上限（全サーバ）")
    p.add_argument("--conv-hit-tol", type=float, default=1.0,
                   help="直近 C バッチと その前 C バッチの区間ヒット率の差の上限 [pt]")
    p.add_argument("--converged-file", default=None,
                   help="飽和した walk 数を書き出すファイル（run_local.sh / run_cluster.sh が残りの方策に使う）")
    p.add_argument("--batch-walks", type=int, default=100, help="1バッチの walk 本数")
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--request-timeout", type=float, default=30000)
    p.add_argument("--node-to-starts-file", default=None)
    p.add_argument("--start-server-mode", choices=["owner", "zero"], default="owner")
    p.add_argument("--no-reset", action="store_true",
                   help="開始前の /cache/reset を省く（前の run の学習を引き継ぐ）")
    p.add_argument("--node-servers", type=int, default=None,
                   help="ノード/エッジ分離配置のときのノード担当台数 Kn（サーバと同じ値）")
    p.add_argument("--edge-servers", type=int, default=None,
                   help="ノード/エッジ分離配置のときのエッジ担当台数 Ke")
    p.add_argument("--belady", action="store_true",
                   help="終了後に各サーバの /access_events（サーバを LOG_ACCESS_EVENTS=1 で起動）から "
                        "Belady（最適解）のヒット率を計算する。サーバ側の方策は何でもよい（アクセス列は同じ）")
    p.add_argument("--cache-capacity", type=int, default=100, help="--belady の容量（1台あたり、サーバと同じ値）")
    p.add_argument("--tag", default="run", help="出力ファイル名に付ける方策名")
    p.add_argument("--out-dir", required=True)
    return p.parse_args()


def collect(endpoints: List[str], timeout: float, history: bool = False) -> List[Dict[str, Any]]:
    q = "/learner_stats?history=1" if history else "/learner_stats"
    return [ctrl.get_json(ep, q, timeout=timeout) for ep in endpoints]


def totals(stats: List[Dict[str, Any]]) -> Dict[str, int]:
    return {c: sum(int(s.get(c, 0)) for s in stats) for c in COUNTERS}


DECISION = ("decisions", "good", "regret", "pairs", "pairs_correct")


def decision_totals(stats: List[Dict[str, Any]]) -> Dict[str, int]:
    """学習キャッシュ系（learned / structured*）の追い出し判断の質のカウンタを全サーバで合計。
    LRU / ARC などは記録しないので 0 のまま（LRU の値は structured_frozen で取れる）。"""
    out = {k: 0 for k in DECISION}
    for s in stats:
        d = (s.get("learner") or {}).get("decision") or {}
        for k in DECISION:
            out[k] += int(d.get(k, 0))
    return out


def ratio(a: int, b: int) -> float:
    return 100.0 * a / b if b else float("nan")


def rate(h: int, m: int) -> float:
    return 100.0 * h / (h + m) if (h + m) else 0.0


def run_belady(a, out_dir: Path) -> None:
    """各サーバのアクセス列から Belady を計算し、RESULT 行（dist/aggregate_stats.py と同じ形）を出す。"""
    per = []
    for sid, ep in enumerate(a.server_endpoints):
        ev = ctrl.get_json(ep, "/access_events", timeout=a.request_timeout).get("access_events") or []
        if not ev:
            raise SystemExit(f"[FATAL] server {sid} の /access_events が空です（LOG_ACCESS_EVENTS=1 で起動したか）")
        r = bel.belady_from_events(ev, a.cache_capacity)
        r["server_id"] = sid
        per.append(r)
        print(f"    [belady] server {sid}: hit%={rate(r['hits'], r['misses']):6.2f} "
              f"(hit {r['hits']} / miss {r['misses']})")
    tot = {k: sum(r[k] for r in per) for k in ("hits", "misses", "hit_node", "miss_node", "hit_edge", "miss_edge")}
    stem = f"belady_start{a.start_node}_a{a.alpha}_walks{a.walks}_seed{a.seed}"
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=list(per[0].keys()))
        wtr.writeheader()
        wtr.writerows(per)
    print(f"RESULT\tbelady\toverall={rate(tot['hits'], tot['misses']):.2f}"
          f"\tnode={rate(tot['hit_node'], tot['miss_node']):.2f}"
          f"\tedge={rate(tot['hit_edge'], tot['miss_edge']):.2f}"
          f"\taccesses={tot['hits'] + tot['misses']}\tremote_auth={tot['misses']}\tlocal_auth=0"
          f"\tservers={len(per)}")


def main() -> None:
    a = parse_args()
    if len(a.server_endpoints) != a.servers:
        raise ValueError("--server-endpoints の数と --servers が一致しません")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.node_servers is not None or a.edge_servers is not None:
        if a.node_servers is None or a.edge_servers is None:
            raise SystemExit("--node-servers と --edge-servers は両方指定してください")
        if a.node_servers + a.edge_servers != a.servers:
            raise SystemExit(f"--servers ({a.servers}) と Kn+Ke が一致しません")
        # 始点はノードなので、それを持つノード担当サーバ（0..Kn-1）に投げる
        sid = part.NodeEdgeSplitPartitioner(a.node_servers, a.edge_servers).assign_node(a.start_node)
        print(f"[adaptive-ctrl] 分離配置 Kn={a.node_servers} Ke={a.edge_servers}: "
              f"start_node={a.start_node} → server {sid}（ノード担当）")
    else:
        sid = ctrl.pick_start_server(a.start_node, a.servers, a.server_endpoints,
                                     a.node_to_starts_file, None, mode=a.start_server_mode)
    endpoint = a.server_endpoints[sid]

    if not a.no_reset:
        for ep in a.server_endpoints:
            if not ctrl.reset_cache(ep, timeout=a.request_timeout):
                raise SystemExit(f"[FATAL] reset に失敗: {ep}")

    first = collect(a.server_endpoints, a.request_timeout)
    policy = first[0].get("policy")
    placements = {s.get("placement") for s in first}
    print(f"[adaptive-ctrl] サーバ側の配置: {sorted(map(str, placements))}")
    if a.node_servers is not None and not all(str(x).startswith("split") for x in placements):
        raise SystemExit("[FATAL] コントローラは分離配置なのに、サーバが分離配置で起動していません")
    print(f"[adaptive-ctrl] policy={policy} start={a.start_node} (server {sid}) "
          f"walks={a.walks} batch={a.batch_walks} alpha={a.alpha} seed={a.seed}")
    print(f"{'batch':>5} {'walks':>6} {'batch_hit%':>10} {'cum_hit%':>9} "
          f"{'node%':>7} {'edge%':>7} {'remote':>7} {'good%':>6} {'regret%':>7} {'pair%':>6} {'sec':>6}   重み(server0)")

    st0 = collect(a.server_endpoints, a.request_timeout)
    prev = totals(st0)
    prev_d = decision_totals(st0)
    rows: List[Dict[str, Any]] = []
    done, b = 0, 0
    t_all = time.perf_counter()
    limit = (a.max_walks or 10 * a.walks) if a.until_converged else a.walks
    w_hist: List[Dict[str, List[float]]] = []   # バッチごとの {サーバ×型: b を除く重み}
    converged_at = None
    while done < limit:
        n = min(a.batch_walks, limit - done)
        payload = {"start_node": a.start_node, "alpha": a.alpha, "walks": n,
                   "seed": a.seed + done, "endpoints": a.server_endpoints,
                   "server_count": a.servers}
        t0 = time.perf_counter()
        ctrl.start_walk_on_server(endpoint, payload, timeout=a.request_timeout)
        dt = time.perf_counter() - t0
        done += n
        b += 1

        stats = collect(a.server_endpoints, a.request_timeout)
        cur = totals(stats)
        d = {c: cur[c] - prev[c] for c in COUNTERS}
        prev = cur
        cur_d = decision_totals(stats)
        dd = {k: cur_d[k] - prev_d[k] for k in DECISION}
        prev_d = cur_d
        row: Dict[str, Any] = {
            "tag": a.tag, "policy": policy, "batch": b, "walks_done": done,
            "batch_accesses": d["auth_cache_hit"] + d["auth_cache_miss"],
            "batch_hit": rate(d["auth_cache_hit"], d["auth_cache_miss"]),
            "batch_node_hit": rate(d["hit_node"], d["miss_node"]),
            "batch_edge_hit": rate(d["hit_edge"], d["miss_edge"]),
            "cum_hit": rate(cur["auth_cache_hit"], cur["auth_cache_miss"]),
            "cum_node_hit": rate(cur["hit_node"], cur["miss_node"]),
            "cum_edge_hit": rate(cur["hit_edge"], cur["miss_edge"]),
            "batch_remote_auth": d["remote_auth_calls"],
            # 追い出しの判断の質（ラベルが確定した判断だけ。W だけ遅れて確定する）
            #   good  = 追い出したキーの次回アクセスが境界 h より先だった割合（LRB の good decision）
            #   regret= 追い出したキーが h 以内に戻ってきた割合（LeCaR / CACHEUS の後悔）
            #   pair  = 追い出したキーが、残したキーより本当に後で使われた割合（順位の正しさ）
            "batch_decisions": dd["decisions"],
            "batch_good": ratio(dd["good"], dd["decisions"]),
            "batch_regret": ratio(dd["regret"], dd["decisions"]),
            "batch_pair_acc": ratio(dd["pairs_correct"], dd["pairs"]),
            "cum_good": ratio(cur_d["good"], cur_d["decisions"]),
            "cum_regret": ratio(cur_d["regret"], cur_d["decisions"]),
            "cum_pair_acc": ratio(cur_d["pairs_correct"], cur_d["pairs"]),
            "batch_sec": dt,
            "node_servers": a.node_servers if a.node_servers is not None else "",
        }
        # 学習した重み（サーバ × 型）を列に展開
        for s in stats:
            lr = s.get("learner") or {}
            by_type = lr.get("term_names_by_type") or {}
            for typ, m in (lr.get("models") or {}).items():
                names = ["b"] + list(by_type.get("node" if typ == "all" else typ)
                                     or lr.get("term_names") or FEATURES[1:])
                for f in names:
                    row[f"w_s{s['server_id']}_{typ}_{f}"] = m.get(f)
                    if f"eff_{f}" in m:   # 実効的な重み（重み × 特徴量の標準偏差）
                        row[f"e_s{s['server_id']}_{typ}_{f}"] = m[f"eff_{f}"]
                row[f"upd_s{s['server_id']}_{typ}"] = m.get("updates")
        # --- 飽和の判定（重みの向きが止まり、区間ヒット率が横ばい）---------------------
        wv = {}
        for s in stats:
            for typ, m in ((s.get("learner") or {}).get("models") or {}).items():
                if m.get("updates"):
                    # 実効的な重み（重み × 特徴量の標準偏差）で判定する。ばらつきの小さい項の重みが
                    # 伸び続けても、判断への影響が小さければ飽和とみなせるように
                    eff = [v for k, v in m.items() if k.startswith("eff_")]
                    wv[f"{s['server_id']}_{typ}"] = eff or [v for k, v in m.items()
                                                          if k not in ("b", "updates", "resets", "mae")]
        w_hist.append(wv)
        C = a.conv_window
        cos_min, hit_diff, norm_chg = float("nan"), float("nan"), float("nan")
        if len(w_hist) > C:
            old = w_hist[-1 - C]
            cs, ns = [], []
            for k, v in wv.items():
                o = old.get(k)
                if not o or len(o) != len(v):
                    continue
                nv, no = sum(x * x for x in v) ** 0.5, sum(x * x for x in o) ** 0.5
                if nv > 0 and no > 0:
                    cs.append(sum(x * y for x, y in zip(v, o)) / (nv * no))
                    ns.append(abs(nv / no - 1.0))
            cos_min = min(cs) if cs else 1.0      # 学習しない方策は重みの条件を満たすとみなす
            norm_chg = max(ns) if ns else 0.0
        if len(rows) + 1 >= 2 * C:
            hs = [r["batch_hit"] for r in rows[-(2 * C - 1):]] + [row["batch_hit"]]
            hit_diff = abs(sum(hs[C:]) / C - sum(hs[:C]) / C)
        row["conv_cos_min"] = cos_min
        row["conv_norm_chg"] = norm_chg
        row["conv_hit_diff"] = hit_diff
        rows.append(row)
        if (a.until_converged and converged_at is None and done >= a.walks
                and cos_min == cos_min and hit_diff == hit_diff and norm_chg == norm_chg
                and cos_min >= a.conv_cos and norm_chg <= a.conv_norm_tol
                and hit_diff <= a.conv_hit_tol):
            converged_at = done
        w0cols = [k for k in row if k.startswith("w_s0_")]
        w0 = " ".join(f"{k.split('_', 3)[3]}={row[k]:.2f}" for k in w0cols
                      if row[k] is not None) if w0cols else "-"
        print(f"{b:>5} {done:>6} {row['batch_hit']:>10.2f} {row['cum_hit']:>9.2f} "
              f"{row['batch_node_hit']:>7.2f} {row['batch_edge_hit']:>7.2f} "
              f"{row['batch_remote_auth']:>7} {row['batch_good']:>6.1f} {row['batch_regret']:>7.1f} "
              f"{row['batch_pair_acc']:>6.1f} {dt:>6.1f}   {w0}")
        if converged_at is not None:
            print(f"[adaptive-ctrl] 飽和: walks={converged_at}（実効的な重みの向きの cos={cos_min:.4f} ≥ {a.conv_cos}、"
                  f"大きさの変化={norm_chg:.3f} ≤ {a.conv_norm_tol}、"
                  f"区間ヒット率の差={hit_diff:.2f} ≤ {a.conv_hit_tol} pt、{C} バッチで判定）")
            break

    final = collect(a.server_endpoints, a.request_timeout, history=True)
    stem = f"curve_{a.tag}_start{a.start_node}_a{a.alpha}_walks{a.walks}_seed{a.seed}"
    keys: List[str] = []
    for r in rows:
        keys.extend(k for k in r if k not in keys)
    with (out_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=keys)
        wtr.writeheader()
        wtr.writerows(rows)
    (out_dir / f"{stem}.json").write_text(json.dumps(
        {"config": vars(a), "policy": policy, "curve": rows, "final_servers": final},
        ensure_ascii=False, indent=1))
    # サーバごとの最終結果（台数が多いときにサーバ別・役割別に見るため）
    srows = []
    for s in final:
        sid = int(s["server_id"])
        if a.node_servers is not None:
            role = "node" if sid < a.node_servers else "edge"
            cached = "edge" if role == "node" else "node"
        else:
            role, cached = "mixed", "mixed"
        hn, mn, he, me = (int(s.get(k, 0)) for k in ("hit_node", "miss_node", "hit_edge", "miss_edge"))
        lr = s.get("learner") or {}
        d = lr.get("decision") or {}
        r = {"tag": a.tag, "policy": policy, "server_id": sid, "role": role, "cached_type": cached,
             "hit": hn + he, "miss": mn + me, "hit_rate": rate(hn + he, mn + me),
             "remote_auth": s.get("remote_auth_calls", 0), "local_auth": s.get("local_auth_calls", 0),
             "cache_entries": s.get("cache_entries", ""),
             "decisions": d.get("decisions", ""),
             "good": ratio(d.get("good", 0), d.get("decisions", 0)) if d else "",
             "regret": ratio(d.get("regret", 0), d.get("decisions", 0)) if d else "",
             "pair_acc": ratio(d.get("pairs_correct", 0), d.get("pairs", 0)) if d else ""}
        for typ, m in (lr.get("models") or {}).items():
            r[f"updates_{typ}"] = m.get("updates")
            terms = {k: v for k, v in m.items() if k not in ("updates", "resets", "mae")}
            for k, v in terms.items():
                r[f"w_{typ}_{k}"] = v
            # 重みの比: |w_O| を 1 とした値（目的や尺度が違っても並べられる。追い出しの順番は比だけで決まる）
            # 比は実効的な重み（重み × 特徴量の標準偏差）で出す。無ければ素の重み
            base = {k[4:]: v for k, v in terms.items() if k.startswith("eff_")} or \
                {k: v for k, v in terms.items() if k != "b" and not k.startswith("eff_")}
            o = abs(base.get("O") or 0.0)
            for k, v in base.items():
                r[f"ratio_{typ}_{k}"] = (v / o) if o > 1e-12 else ""
        srows.append(r)
    skeys: List[str] = []
    for r in srows:
        skeys.extend(k for k in r if k not in skeys)
    sfile = out_dir / f"servers_{a.tag}_start{a.start_node}_a{a.alpha}_walks{a.walks}_seed{a.seed}.csv"
    with sfile.open("w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=skeys)
        wtr.writeheader()
        wtr.writerows(srows)
    print("[adaptive-ctrl] サーバ別:")
    for r in srows:
        print(f"    server {r['server_id']:>2} 役割={r['role']:<5} キャッシュ={r['cached_type']:<5} "
              f"hit%={r['hit_rate']:6.2f} (hit {r['hit']} / miss {r['miss']})  remote={r['remote_auth']}"
              + (f"  pair%={r['pair_acc']:.1f}" if isinstance(r['pair_acc'], float) else ""))

    if a.until_converged:
        if converged_at is None:
            print(f"NOT_CONVERGED\t{done}")
            print(f"[adaptive-ctrl] 上限 {limit} walks まで回しても飽和しませんでした（--max-walks を増やす）")
        else:
            print(f"CONVERGED\t{converged_at}")
        if a.converged_file:
            Path(a.converged_file).write_text(f"{done}\t{'converged' if converged_at else 'not_converged'}\n")
    if a.belady:
        run_belady(a, out_dir)

    last = rows[-1]
    print(f"[adaptive-ctrl] done {time.perf_counter() - t_all:.1f}s  "
          f"cum_hit={last['cum_hit']:.2f}% (node {last['cum_node_hit']:.2f} / "
          f"edge {last['cum_edge_hit']:.2f})")
    print(f"[saved] {out_dir / (stem + '.csv')}")
    print(f"[saved] {sfile}")
    # run スクリプトが集計できるよう1行サマリを出す
    print(f"SUMMARY\t{a.tag}\t{policy}\t{last['cum_hit']:.4f}\t{last['cum_node_hit']:.4f}"
          f"\t{last['cum_edge_hit']:.4f}\t{rows[0]['batch_hit']:.4f}\t{last['batch_hit']:.4f}"
          f"\t{last['cum_good']:.2f}\t{last['cum_regret']:.2f}\t{last['cum_pair_acc']:.2f}")


if __name__ == "__main__":
    main()
