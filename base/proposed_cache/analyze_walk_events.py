#!/usr/bin/env python3
"""
walk イベント (到着順キャッシュアクセス列) の仮説検証。

入力: run_walk_events_local.sh が出す start=*_server{0,1}_events.json。
各イベント = (walk_id, target_entity, hops_done, was_cache_hit)。cache=none なので
全アクセスが記録され、(start,target) の真の再利用回数が分かる。walk_id は到着順
(walk は逐次実行: walk i が完了してから i+1)。

検証する問い:
  Q1 正しい node/edge one-hit/multi 割合 (前回の source-keyed proxy の検証)。
  Q2 昇格タイミング: multi-hit entity の「2回目アクセスが何本目の walker で来るか」(t2)。
      短い観測窓で reuse-worthy を捕まえられるか = probation 窓長の根拠。
  Q3 アンサンブル駆動か: multi-hit の再利用は別walker(inter)か同walker再訪(intra)か。
  Q4 early 予測力: 最初の K walker での被アクセス数が最終 multi-hit を予測する AUC。
  Q5 probation admission の取りこぼし: 「2回目で昇格」だと exactly-2 を取りこぼす量。

使い方:
  python3 analyze_walk_events.py --input results/walk_events/vldb --graph-label vldb
"""
from __future__ import annotations
import argparse, csv, glob, json, re
from collections import defaultdict, Counter
from pathlib import Path

START_RE = re.compile(r"start=(\d+)_")


def is_edge(e: str) -> bool:
    return str(e).startswith("edge_")


def auc(scores_pos, scores_neg) -> float:
    """score 大ほど pos と予測する AUC (Mann-Whitney)。0.5=無情報。"""
    if not scores_pos or not scores_neg:
        return float("nan")
    comb = sorted([(s, 1) for s in scores_pos] + [(s, 0) for s in scores_neg])
    ranks = [0.0] * len(comb)
    i = 0
    while i < len(comb):
        j = i
        while j + 1 < len(comb) and comb[j + 1][0] == comb[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    npos, nneg = len(scores_pos), len(scores_neg)
    sr = sum(r for r, (_, lab) in zip(ranks, comb) if lab == 1)
    return (sr - npos * (npos + 1) / 2.0) / (npos * nneg)


def load_start(out_dir: Path, start: int):
    """両サーバの events を結合し entity -> sorted [(walk_id,hop)] を返す。"""
    ev = []
    for sid in (0, 1):
        p = out_dir / f"start={start}_server{sid}_events.json"
        if p.exists():
            ev.extend(json.load(p.open())["access_events"])
    acc = defaultdict(list)
    for walk_id, ent, hop, hit in ev:
        acc[ent].append((walk_id if walk_id is not None else -1, hop))
    for ent in acc:
        acc[ent].sort()
    return acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="results/walk_events/<graph> ディレクトリ")
    ap.add_argument("--graph-label", default="")
    ap.add_argument("--ks", default="1,2,3,5,10,20", help="early-window の walker 数 K")
    args = ap.parse_args()
    out_dir = Path(args.input)
    Ks = [int(x) for x in args.ks.split(",")]

    starts = sorted({int(START_RE.search(p.name).group(1))
                     for p in out_dir.glob("start=*_server*_events.json")})
    print(f"[INFO] starts={starts}")

    # start ごとに entity アクセス列を作り、全 start でプールして集計
    # (entity は (start,entity) として扱う: キャッシュキー粒度)
    key_acc = {}   # (start, ent) -> [(walk_id,hop)]
    for s in starts:
        for ent, lst in load_start(out_dir, s).items():
            key_acc[(s, ent)] = lst

    # ---------- Q1 node/edge one/multi ----------
    pooled = {"node": Counter(), "edge": Counter()}
    for (s, ent), lst in key_acc.items():
        kind = "edge" if is_edge(ent) else "node"
        c = len(lst)
        pooled[kind]["entries"] += 1
        pooled[kind]["one" if c == 1 else "multi"] += 1
        pooled[kind]["access"] += c
        pooled[kind]["reuse"] += c - 1
    print("\n=== Q1 true cache-access stream: one/multi (pooled (start,entity)) ===")
    for kind in ("node", "edge"):
        c = pooled[kind]
        tot = c["entries"]
        if tot:
            print(f"  {kind:5s}: entries={tot}  one={c['one']} ({100*c['one']/tot:.1f}%)  "
                  f"multi={c['multi']} ({100*c['multi']/tot:.1f}%)  "
                  f"max_hitrate(reuse/acc)={c['reuse']/c['access']:.3f}")

    # ---------- Q2 昇格タイミング t2 + Q3 ensemble + Q4 early AUC ----------
    # node と edge を分けて (戦略はノード優先)
    for kind in ("node", "edge"):
        items = {k: v for k, v in key_acc.items() if (is_edge(k[1]) == (kind == "edge"))}
        multi = {k: v for k, v in items.items() if len(v) >= 2}
        if not multi:
            continue
        print(f"\n========== {kind.upper()} ==========")

        # Q2: 2回目アクセスの walker (t2)
        t2 = [v[1][0] for v in multi.values()]  # 2番目の (walk_id,hop) の walk_id
        t2.sort()
        print("  [Q2] multi-hit が2回目アクセスを受ける walker (t2) 分布:")
        for K in Ks:
            frac = sum(1 for x in t2 if x < K) / len(t2)
            print(f"        2回目が最初の {K:>3} walker 以内: {100*frac:5.1f}%")
        print(f"        t2 中央値={t2[len(t2)//2]}  平均={sum(t2)/len(t2):.1f}")

        # Q3: ensemble 駆動か (distinct walkers / accesses)
        distinct_frac = []
        inter_share = []
        for v in multi.values():
            wids = [w for w, _ in v]
            distinct_frac.append(len(set(wids)) / len(wids))
            first_w = wids[0]
            inter_share.append(sum(1 for w in wids[1:] if w != first_w) / max(1, len(wids) - 1))
        print(f"  [Q3] multi-hit の再利用構造:")
        print(f"        distinct walkers / accesses 平均={sum(distinct_frac)/len(distinct_frac):.2f} "
              f"(1.0=毎回別walker=完全アンサンブル駆動)")
        print(f"        2回目以降が別walker(inter)の割合 平均={100*sum(inter_share)/len(inter_share):.1f}%")

        # Q4: early-K の被アクセス数 が最終 multi を予測する AUC
        print(f"  [Q4] early-window 予測力 (score=最初のK walkerでの被アクセス数, label=最終multi):")
        for K in Ks:
            pos, neg = [], []
            for k, v in items.items():
                early = sum(1 for w, _ in v if w < K)
                (pos if len(v) >= 2 else neg).append(early)
            a = auc(pos, neg)
            # 「K walker以内に2回以上」を admission ルールにした時の捕捉率/適合率
            promoted = [k for k, v in items.items() if sum(1 for w, _ in v if w < K) >= 2]
            tp = sum(1 for k in promoted if len(items[k]) >= 2)
            recall = tp / len(multi) if multi else 0
            prec = tp / len(promoted) if promoted else 0
            print(f"        K={K:>3}: AUC={a:.3f}  | 『K内に2回』admission → "
                  f"recall(multi捕捉)={100*recall:4.1f}% precision={100*prec:4.1f}% "
                  f"(promoted={len(promoted)})")

        # Q5: 2回目昇格で取りこぼす exactly-2
        cnts = Counter(len(v) for v in multi.values())
        exactly2 = cnts.get(2, 0)
        print(f"  [Q5] multi {len(multi)} 中 exactly-2 = {exactly2} ({100*exactly2/len(multi):.1f}%) "
              f"→ 純フィルタ昇格(値保持なし)だと この分の hit を取りこぼす")


if __name__ == "__main__":
    main()
