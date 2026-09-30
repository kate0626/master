#!/usr/bin/env python3
"""各サーバの /access_stats (1行1JSON) を標準入力から読み、
全サーバ合計の node / edge / overall ヒット率を 1 行にまとめて出す。

  for ep in ...; do curl -s "http://$ep/access_stats"; echo; done \
    | python3 aggregate_stats.py <policy名>

node / edge の内訳はサーバ本体が数えている (PeerWalker._authorize_candidate)。
どのキャッシュポリシーでも取れる。
"""
from __future__ import annotations

import json
import sys


def main() -> None:
    policy = sys.argv[1] if len(sys.argv) > 1 else "?"
    hn = mn = he = me = 0
    hit = miss = 0
    remote = local = 0
    servers = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        servers += 1
        # 種別内訳はサーバが /access_stats のトップレベルに出す (どのポリシーでも取れる)。
        # 旧形式の結果との互換のため、cache_stats 内の値も読む。
        cs = d.get("cache_stats") or {}
        for key in ("hit_node", "miss_node", "hit_edge", "miss_edge"):
            v = d.get(key)
            if v is None:
                v = cs.get(key, 0)
            if key == "hit_node":
                hn += v
            elif key == "miss_node":
                mn += v
            elif key == "hit_edge":
                he += v
            else:
                me += v
        hit += d.get("auth_cache_hit", 0)
        miss += d.get("auth_cache_miss", 0)
        remote += d.get("remote_auth_calls", 0)
        local += d.get("local_auth_calls", 0)

    def rate(a: int, b: int) -> str:
        return f"{100.0 * a / (a + b):.2f}" if (a + b) else "-"

    node = rate(hn, mn) if (hn + mn) else "-"
    edge = rate(he, me) if (he + me) else "-"
    print(
        f"RESULT\t{policy}\toverall={rate(hit, miss)}\tnode={node}\tedge={edge}\t"
        f"accesses={hit + miss}\tremote_auth={remote}\tlocal_auth={local}\tservers={servers}"
    )


if __name__ == "__main__":
    main()
