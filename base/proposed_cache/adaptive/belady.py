#!/usr/bin/env python3
# =============================================================================
# belady.py — 記録したアクセス列から Belady（将来を知っている最適な追い出し）のヒット数を計算する
#
#   アクセス列は方策によらず同じ（キャッシュは walk の軌跡を変えない）なので、どの方策で回したときの
#   列を使ってもよい。実サーバは LOG_ACCESS_EVENTS=1 で /access_events に
#   (walk_id, target, hops_done, hit) を到着順に記録する（キャッシュ参照の直前）。
#
#   方式: ミスしたら必ず入れる（demand paging）。あふれたら、キャッシュ内で「次に使われるのが
#   最も遠い（または二度と使われない）」キーを追い出す（Belady の MIN）。ほかの方策も必ず入れるので、
#   同じ条件での上限になる。
#   キーの大きさ（重み）が揃っていれば厳密に最適。ノード/エッジ分離配置では1つのキャッシュに
#   1種類の型しか入らないので揃う。型が混ざる場合（モジュロ分割）は近似になる。
# =============================================================================
from __future__ import annotations

import heapq
from typing import Any, Dict, List, Sequence, Tuple


def entry_weight(target: Any, sizer=None) -> int:
    """DecisionCacheSizer の既定（start 1 + node 1 / edge 2、allow/deny 0）と同じ重み。"""
    if sizer is not None:
        ek = target if isinstance(target, str) and target.startswith("edge_") else f"node:{target}"
        return sizer.key_weight((0, ek))
    return 3 if isinstance(target, str) and target.startswith("edge_") else 2


def belady_hits(keys: Sequence[Any], weights: Sequence[int], capacity: int,
                flags: List[bool] = None) -> Tuple[int, int]:
    """(hits, misses) を返す。keys はアクセス順のキー列、weights は各アクセスのキーの重み。
    flags にリストを渡すと、各アクセスがヒットしたかを順に追記する。"""
    n = len(keys)
    INF = n + 1
    nxt = [INF] * n
    last: Dict[Any, int] = {}
    for i in range(n - 1, -1, -1):
        nxt[i] = last.get(keys[i], INF)
        last[keys[i]] = i

    in_cache: Dict[Any, int] = {}      # key -> そのキーの「次に使われる位置」
    wt: Dict[Any, int] = {}
    total = 0
    heap: List[Tuple[int, Any]] = []   # (−次に使われる位置, key)。古い記録は遅延削除
    hits = misses = 0
    for i, k in enumerate(keys):
        if k in in_cache:
            hits += 1
            if flags is not None:
                flags.append(True)
        else:
            misses += 1
            if flags is not None:
                flags.append(False)
            w = weights[i]
            if w > capacity:
                continue
            # 入れる前に、入る余地ができるまで「次に使われるのが最も遠い」キーを追い出す
            while total + w > capacity:
                neg, v = heapq.heappop(heap)
                if in_cache.get(v) != -neg:
                    continue                  # 古い記録（その後に再アクセスされた）
                del in_cache[v]
                total -= wt.pop(v)
            wt[k] = w
            total += w
        in_cache[k] = nxt[i]
        heapq.heappush(heap, (-nxt[i], k))
    return hits, misses


def belady_from_events(events: List[list], capacity: int, sizer=None) -> Dict[str, int]:
    """1台分の /access_events から計算する。型ごとの内訳も返す。"""
    keys = [str(e[1]) for e in events]
    weights = [entry_weight(e[1] if not str(e[1]).isdigit() else int(e[1]), sizer) for e in events]
    flags: List[bool] = []
    hits, misses = belady_hits(keys, weights, capacity, flags)
    is_edge = [k.startswith("edge_") for k in keys]
    he = sum(1 for f, e in zip(flags, is_edge) if f and e)
    ne = sum(is_edge)
    return {"hits": hits, "misses": misses, "accesses": len(keys),
            "hit_edge": he, "miss_edge": ne - he,
            "hit_node": hits - he, "miss_node": (len(keys) - ne) - (hits - he)}


def _selftest() -> None:
    # 教科書の例: 容量 3、列 7 0 1 2 0 3 0 4 2 3 0 3 2 → OPT のミスは 7
    seq = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2]
    h, m = belady_hits(seq, [1] * len(seq), 3)
    assert m == 7, (h, m)
    # LRU より悪くならない（ランダム列で確認）
    import random
    from collections import OrderedDict
    rng = random.Random(0)
    for _ in range(50):
        s = [rng.randint(0, 15) for _ in range(300)]
        cap = rng.randint(2, 8)
        oh, _ = belady_hits(s, [1] * len(s), cap)
        lru, lh = OrderedDict(), 0
        for x in s:
            if x in lru:
                lh += 1
                lru.move_to_end(x)
            else:
                lru[x] = 1
                if len(lru) > cap:
                    lru.popitem(last=False)
        assert oh >= lh, (oh, lh, cap)
    print("belady.py selftest OK")


if __name__ == "__main__":
    _selftest()
