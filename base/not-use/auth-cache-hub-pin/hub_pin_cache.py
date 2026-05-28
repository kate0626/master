"""
auth-cache-hub-pin: 新しいキャッシュポリシー 3 種を提供する。

`server.py` に import して、`--cache-policy` で次のいずれかを選べる:

  - lfu          : Least Frequently Used  (シンプルな freq-based eviction)
  - tinylfu      : Window-TinyLFU 風 (LRU window + LFU main, admit policy 付き)
  - hub-pin-lru  : 起動時に top-K nodes (degree or precomputed PageRank) を
                   pinned slot として固定し、残り (cap - K) を LRU で運用

`Auth cache hit/miss` のロギングは既存 LRU と同じ形式で出す。
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 基本インタフェース
# ---------------------------------------------------------------------------
class CacheBase:
    name: str = "base"

    def __init__(self, capacity: int):
        self.cap = capacity
        self.hits = 0
        self.misses = 0

    def get(self, key) -> Optional[object]:
        raise NotImplementedError

    def put(self, key, value) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        self.hits = 0
        self.misses = 0


# ---------------------------------------------------------------------------
# LFU
# ---------------------------------------------------------------------------
class LFUCache(CacheBase):
    """Least Frequently Used cache.
    同 freq 内は LRU で tie-break する。
    O(1) でない実装だが、cap=100 オーダなら十分。"""
    name = "lfu"

    def __init__(self, capacity: int):
        super().__init__(capacity)
        self.store: dict = {}
        self.freq: dict[object, int] = {}
        self.recency: OrderedDict = OrderedDict()  # tie-break 用

    def get(self, key):
        if key in self.store:
            self.freq[key] += 1
            self.recency.move_to_end(key)
            self.hits += 1
            return self.store[key]
        self.misses += 1
        return None

    def put(self, key, value):
        if key in self.store:
            self.store[key] = value
            self.freq[key] += 1
            self.recency.move_to_end(key)
            return
        if len(self.store) >= self.cap:
            self._evict()
        self.store[key] = value
        self.freq[key] = 1
        self.recency[key] = 1

    def _evict(self):
        # 最小 freq、tie で最古
        victim = None
        min_f = float("inf")
        for k in self.recency:
            if self.freq[k] < min_f:
                min_f = self.freq[k]
                victim = k
                if min_f == 1:
                    break
        if victim is None:
            return
        del self.store[victim]
        del self.freq[victim]
        del self.recency[victim]

    def stats(self) -> dict:
        return {
            "policy": self.name,
            "capacity": self.cap,
            "entries": len(self.store),
            "hits": self.hits,
            "misses": self.misses,
        }


# ---------------------------------------------------------------------------
# Window-TinyLFU (simplified)
# ---------------------------------------------------------------------------
class TinyLFUCache(CacheBase):
    """Window-TinyLFU 風: window LRU + main LFU。
       admit policy: window から main へ promote するときに、
       main の最弱 freq エントリとの比較で「higher freq だけ admit」する。
       公式 TinyLFU は count-min sketch を使うが、ここでは実カウンタで近似。"""
    name = "tinylfu"

    def __init__(self, capacity: int, window_ratio: float = 1/16):
        super().__init__(capacity)
        self.win_cap = max(1, int(round(capacity * window_ratio)))
        self.main_cap = capacity - self.win_cap
        self.win: OrderedDict = OrderedDict()           # key -> freq seen in window
        self.win_store: dict = {}
        self.main: dict = {}
        self.main_freq: dict = {}
        self.main_recency: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self.main:
            self.main_freq[key] += 1
            self.main_recency.move_to_end(key)
            self.hits += 1
            return self.main[key]
        if key in self.win_store:
            self.win[key] = self.win.get(key, 0) + 1
            self.win.move_to_end(key)
            self.hits += 1
            return self.win_store[key]
        self.misses += 1
        return None

    def put(self, key, value):
        if key in self.main:
            self.main[key] = value
            self.main_freq[key] += 1
            self.main_recency.move_to_end(key)
            return
        if key in self.win_store:
            self.win_store[key] = value
            self.win[key] = self.win.get(key, 0) + 1
            self.win.move_to_end(key)
            return
        # 新規 → window へ
        self.win_store[key] = value
        self.win[key] = 1
        if len(self.win) > self.win_cap:
            self._evict_from_window()

    def _evict_from_window(self):
        evicted_key, evicted_freq = self.win.popitem(last=False)
        evicted_val = self.win_store.pop(evicted_key)
        # main へ promote するか判定
        if len(self.main) < self.main_cap:
            self.main[evicted_key] = evicted_val
            self.main_freq[evicted_key] = evicted_freq
            self.main_recency[evicted_key] = 1
            return
        # main の最弱と freq 比較
        weakest = min(self.main_recency.keys(),
                      key=lambda k: self.main_freq[k])
        if evicted_freq > self.main_freq[weakest]:
            del self.main[weakest]
            del self.main_freq[weakest]
            del self.main_recency[weakest]
            self.main[evicted_key] = evicted_val
            self.main_freq[evicted_key] = evicted_freq
            self.main_recency[evicted_key] = 1
        # 負けたら window evict は捨てるだけ

    def stats(self) -> dict:
        return {
            "policy": self.name,
            "capacity": self.cap,
            "win_cap": self.win_cap,
            "main_cap": self.main_cap,
            "entries": len(self.main) + len(self.win),
            "hits": self.hits,
            "misses": self.misses,
        }


# ---------------------------------------------------------------------------
# hub-pin-lru
# ---------------------------------------------------------------------------
class HubPinLRUCache(CacheBase):
    """top-K hub nodes を pinned slot として固定。残り (cap - K) は LRU。
       pin_keys は起動時に与える (degree top-K / PageRank top-K など)。
       value も同時に与えるなら preload_values=True にする。"""
    name = "hub-pin-lru"

    def __init__(self, capacity: int, pin_keys: list,
                 preload_values: Optional[dict] = None):
        super().__init__(capacity)
        if len(pin_keys) > capacity:
            raise ValueError(f"pin_keys size {len(pin_keys)} > capacity {capacity}")
        self.pin: set = set(pin_keys)
        self.pin_store: dict = {k: (preload_values or {}).get(k) for k in pin_keys}
        self.lru_cap = capacity - len(self.pin)
        self.lru_store: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self.pin:
            self.hits += 1
            return self.pin_store.get(key)
        if key in self.lru_store:
            self.lru_store.move_to_end(key)
            self.hits += 1
            return self.lru_store[key]
        self.misses += 1
        return None

    def put(self, key, value):
        if key in self.pin:
            # pinned slot に値を埋める (初回 fetch 後)
            self.pin_store[key] = value
            return
        if key in self.lru_store:
            self.lru_store[key] = value
            self.lru_store.move_to_end(key)
            return
        if self.lru_cap == 0:
            return
        self.lru_store[key] = value
        if len(self.lru_store) > self.lru_cap:
            self.lru_store.popitem(last=False)

    def stats(self) -> dict:
        return {
            "policy": self.name,
            "capacity": self.cap,
            "pin_k": len(self.pin),
            "lru_cap": self.lru_cap,
            "pin_entries_filled": sum(1 for v in self.pin_store.values() if v is not None),
            "lru_entries": len(self.lru_store),
            "hits": self.hits,
            "misses": self.misses,
        }


# ---------------------------------------------------------------------------
# 起動時 hub 計算ヘルパ
# ---------------------------------------------------------------------------
def compute_top_k_by_degree(edge_file: Path, k: int) -> list:
    """`.gr` ファイル (1 行 1 辺 'u v' or 'u v w') を読み、
    全ノードの degree を数えて top-K を返す。"""
    deg: dict = {}
    with edge_file.open() as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            u, v = parts[0], parts[1]
            deg[u] = deg.get(u, 0) + 1
            deg[v] = deg.get(v, 0) + 1
    return [n for n, _ in sorted(deg.items(), key=lambda x: -x[1])[:k]]


def load_pin_keys_from_file(path: Path) -> list:
    """事前計算した pin set ファイル (JSON list, または 1行1ノード) を読む。"""
    text = path.read_text()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
    except json.JSONDecodeError:
        pass
    return [line.strip() for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def make_cache(policy: str, capacity: int, *,
               pin_keys: Optional[list] = None,
               window_ratio: float = 1/16):
    """サーバ側で `cache = make_cache(args.cache_policy, args.cache_capacity, ...)`
       のように使う。"""
    p = policy.lower()
    if p == "lfu":
        return LFUCache(capacity)
    if p == "tinylfu":
        return TinyLFUCache(capacity, window_ratio=window_ratio)
    if p == "hub-pin-lru":
        if not pin_keys:
            raise ValueError("hub-pin-lru requires --pin-keys-file or --hub-edge-file")
        return HubPinLRUCache(capacity, pin_keys)
    raise ValueError(f"unknown policy: {policy}")
