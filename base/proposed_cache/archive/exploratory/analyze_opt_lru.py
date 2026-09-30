#!/usr/bin/env python3
"""
walk イベント (到着順キャッシュアクセス列) から、容量別の
  Memo(無制限) / LRU / OPT(Belady) ヒット率
を node/edge 別・start プールで比較する。

「余地(OPT-LRU)」= 賢い IN/OUT で取り戻せる分。
「取れない分(Memo-OPT)」= その容量では原理的に不可。

使い方:
  python3 analyze_opt_lru.py --input results/walk_events/vldb --caps 50,100,200
"""
from __future__ import annotations
import argparse, json, re
from collections import defaultdict
from pathlib import Path

START_RE = re.compile(r"start=(\d+)_server")


def stream(out_dir: Path, start: int, sid: int):
    p = out_dir / f"start={start}_server{sid}_events.json"
    if not p.exists():
        return []
    return [e[1] for e in json.load(p.open())["access_events"]]


def lru_hits(st, C):
    recent = []
    hits = 0
    for e in st:
        if e in recent:
            hits += 1
            recent.remove(e)
        elif len(recent) >= C:
            recent.pop()
        recent.insert(0, e)
    return hits


def opt_hits(st, C):
    pos = defaultdict(list)
    for i, e in enumerate(st):
        pos[e].append(i)
    ptr = defaultdict(int)
    cache = {}  # key -> next-use index
    hits = 0
    for e in st:
        ptr[e] += 1
        nxt = pos[e][ptr[e]] if ptr[e] < len(pos[e]) else 10**9
        if e in cache:
            hits += 1
            cache[e] = nxt
        else:
            if len(cache) >= C:
                victim = max(cache, key=lambda k: cache[k])
                del cache[victim]
            cache[e] = nxt
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--caps", default="50,100,200")
    args = ap.parse_args()
    out_dir = Path(args.input)
    caps = [int(x) for x in args.caps.split(",")]

    starts = sorted({int(START_RE.search(p.name).group(1))
                     for p in out_dir.glob("start=*_server*_events.json")})
    print(f"[INFO] {out_dir.name}  starts={starts}")

    comb = {"acc": 0, "memo": 0}
    comb_lru = defaultdict(int)
    comb_opt = defaultdict(int)
    # node = server1 の node-target, edge = server0 の edge-target
    for kind, sid in [("node", 1), ("edge", 0)]:
        streams = []
        for s in starts:
            st = [e for e in stream(out_dir, s, sid)
                  if (e.startswith("edge_")) == (kind == "edge")]
            if st:
                streams.append(st)
        tot = sum(len(s) for s in streams)
        memo = sum(len(s) - len(set(s)) for s in streams)
        comb["acc"] += tot
        comb["memo"] += memo
        print(f"\n### {kind}  全アクセス={tot}  Memo(無制限)={100*memo/tot:.1f}%")
        for C in caps:
            hl = sum(lru_hits(s, C) for s in streams)
            ho = sum(opt_hits(s, C) for s in streams)
            comb_lru[C] += hl
            comb_opt[C] += ho
            print(f"   C={C:>4}: LRU={100*hl/tot:5.1f}%  OPT={100*ho/tot:5.1f}%  "
                  f"→ 余地(OPT-LRU)={100*(ho-hl)/tot:4.1f}pt  "
                  f"取れない分(Memo-OPT)={100*(memo-ho)/tot:4.1f}pt")

    print(f"\n### 合算  全アクセス={comb['acc']}  Memo={100*comb['memo']/comb['acc']:.1f}%")
    for C in caps:
        hl, ho = comb_lru[C], comb_opt[C]
        a = comb["acc"]
        print(f"   C={C:>4}: LRU={100*hl/a:5.1f}%  OPT={100*ho/a:5.1f}%  "
              f"→ 余地={100*(ho-hl)/a:4.1f}pt  取れない分={100*(comb['memo']-ho)/a:4.1f}pt")


if __name__ == "__main__":
    main()
