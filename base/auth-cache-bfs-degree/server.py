#!/usr/bin/env python3
"""
auth-cache-strategies: Strategy A (bfs-lru) と Strategy C (degree-lru) の実装。

baseline の lru/arc/memo/none に加えて以下のポリシーを追加:
  bfs-lru    : Strategy A — BFS距離ベース。遠方エンティティを優先的に追い出す。
                             walk開始時にローカル近傍を先読みキャッシュ投入（プリフェッチ）。
  degree-lru : Strategy C — 次数ベース。low-low edge を先に追い出し、ハブノードを保護する。

使い方:
  python3 base/auth-cache-strategies/server.py \\
    --server-id 0 --server-count 2 \\
    --edges dataset/Louvain/graph/vldb.gr \\
    --host 127.0.0.1 --port 3000 \\
    --server-endpoints 127.0.0.1:3000 127.0.0.1:3001 \\
    --node-to-starts-file base/auth-many-server/data/splits/vldb/0.3/node_to_starts_server0.json \\
    --owned-hints-only \\
    --cache-policy bfs-lru \\
    --cache-capacity 100 \\
    --cache-bfs-far-threshold 6

  # degree-lru の場合
    --cache-policy degree-lru \\
    --cache-hub-threshold 10
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import random
import re
import resource
import time
from collections import Counter, defaultdict, OrderedDict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
import sys

NodeId = Union[int, str]
NodeOrEdgeId = Union[int, str]


# ---------------------------------------------------------------------------
# Utilities (baseline と同一)
# ---------------------------------------------------------------------------

class DecisionCacheSizer:
    def __init__(
        self,
        node_weight: int = 1,
        edge_weight: int = 2,
        allow_weight: int = 0,
        deny_weight: int = 0,
        start_weight: int = 1,
    ) -> None:
        for name, value in {
            "node_weight": node_weight,
            "edge_weight": edge_weight,
            "allow_weight": allow_weight,
            "deny_weight": deny_weight,
            "start_weight": start_weight,
        }.items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        self.node_weight = node_weight
        self.edge_weight = edge_weight
        self.allow_weight = allow_weight
        self.deny_weight = deny_weight
        self.start_weight = start_weight

    def entity_weight(self, entity_key: str) -> int:
        return self.edge_weight if entity_key.startswith("edge_") else self.node_weight

    def key_weight(self, key: Tuple[int, str]) -> int:
        _, entity_key = key
        return self.start_weight + self.entity_weight(entity_key)

    def entry_weight(self, key: Tuple[int, str], value: bool) -> int:
        return self.key_weight(key) + (self.allow_weight if value else self.deny_weight)


class BaseDecisionCache:
    def __contains__(self, key: Tuple[int, str]) -> bool:
        raise NotImplementedError

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        raise NotImplementedError

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError

    def current_weight(self) -> int:
        return 0

    def max_weight(self) -> Optional[int]:
        return None

    def storage_objects(self) -> Dict[str, Any]:
        return {}

    def stats(self) -> Dict[str, Any]:
        return {
            "entries": len(self),
            "current_weight": self.current_weight(),
            "max_weight": self.max_weight(),
        }


class NoDecisionCache(BaseDecisionCache):
    def __contains__(self, key):
        return False

    def __getitem__(self, key):
        raise KeyError(key)

    def __setitem__(self, key, value):
        return None

    def __len__(self):
        return 0

    def stats(self):
        base = super().stats()
        base.update({"policy": "none"})
        return base


class UnlimitedDecisionCache(BaseDecisionCache):
    def __init__(self, sizer=None):
        self.data: Dict[Tuple[int, str], bool] = {}
        self.sizer = sizer or DecisionCacheSizer()

    def __contains__(self, key):
        return key in self.data

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = bool(value)

    def __len__(self):
        return len(self.data)

    def current_weight(self):
        return sum(self.sizer.entry_weight(k, v) for k, v in self.data.items())

    def storage_objects(self):
        return {"data": self.data}

    def stats(self):
        base = super().stats()
        base.update({"policy": "memo", "entries": len(self.data), "insertions": len(self.data), "updates": 0, "evictions": 0, "rejected_too_large": 0})
        return base


class LRUDecisionCache(BaseDecisionCache):
    def __init__(self, capacity: int, sizer=None):
        if capacity <= 0:
            raise ValueError("LRU cache capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer or DecisionCacheSizer()
        self.data: OrderedDict[Tuple[int, str], bool] = OrderedDict()
        self.weights: Dict[Tuple[int, str], int] = {}
        self.total_weight = 0
        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0

    def __contains__(self, key):
        return key in self.data

    def __getitem__(self, key):
        value = self.data[key]
        self.data.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        value = bool(value)
        new_weight = self.sizer.entry_weight(key, value)
        if new_weight > self.capacity:
            self.rejected_too_large += 1
            return
        if key in self.data:
            old_weight = self.weights.get(key, 0)
            self.data[key] = value
            self.data.move_to_end(key)
            self.weights[key] = new_weight
            self.total_weight += new_weight - old_weight
            self.updates += 1
            self._evict_until_fit()
            return
        self.data[key] = value
        self.data.move_to_end(key)
        self.weights[key] = new_weight
        self.total_weight += new_weight
        self.insertions += 1
        self._evict_until_fit()

    def __len__(self):
        return len(self.data)

    def current_weight(self):
        return self.total_weight

    def max_weight(self):
        return self.capacity

    def storage_objects(self):
        return {"data": self.data, "weights": self.weights, "total_weight": self.total_weight}

    def stats(self):
        base = super().stats()
        base.update({"policy": "lru", "entries": len(self.data), "insertions": self.insertions, "updates": self.updates, "evictions": self.evictions, "rejected_too_large": self.rejected_too_large})
        return base

    def _evict_until_fit(self):
        while self.total_weight > self.capacity and self.data:
            old_key, _ = self.data.popitem(last=False)
            self.total_weight -= self.weights.pop(old_key, 0)
            self.evictions += 1


class ARCDecisionCache(BaseDecisionCache):
    def __init__(self, capacity: int, sizer=None):
        if capacity <= 0:
            raise ValueError("ARC cache capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer or DecisionCacheSizer()
        self.p = 0
        self.t1: OrderedDict[Tuple[int, str], bool] = OrderedDict()
        self.t2: OrderedDict[Tuple[int, str], bool] = OrderedDict()
        self.b1: OrderedDict[Tuple[int, str], None] = OrderedDict()
        self.b2: OrderedDict[Tuple[int, str], None] = OrderedDict()
        self.t1_weights: Dict[Tuple[int, str], int] = {}
        self.t2_weights: Dict[Tuple[int, str], int] = {}
        self.b1_weights: Dict[Tuple[int, str], int] = {}
        self.b2_weights: Dict[Tuple[int, str], int] = {}
        self.t1_weight = 0
        self.t2_weight = 0
        self.b1_weight = 0
        self.b2_weight = 0
        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0
        self.b1_hits = 0
        self.b2_hits = 0

    def __contains__(self, key):
        return key in self.t1 or key in self.t2

    def __getitem__(self, key):
        if key in self.t1:
            value = self.t1.pop(key)
            weight = self.t1_weights.pop(key)
            self.t1_weight -= weight
            self.t2[key] = value
            self.t2_weights[key] = weight
            self.t2_weight += weight
            return value
        value = self.t2.pop(key)
        weight = self.t2_weights.pop(key)
        self.t2[key] = value
        self.t2_weights[key] = weight
        return value

    def __setitem__(self, key, value):
        value = bool(value)
        entry_weight = self.sizer.entry_weight(key, value)
        key_weight = self.sizer.key_weight(key)
        if entry_weight > self.capacity:
            self.rejected_too_large += 1
            return
        if key in self.t1:
            old_weight = self.t1_weights.pop(key)
            self.t1.pop(key)
            self.t1_weight -= old_weight
            self.t2[key] = value
            self.t2_weights[key] = entry_weight
            self.t2_weight += entry_weight
            self.updates += 1
            self._evict_real_until_fit(prefer_t1=False)
            return
        if key in self.t2:
            old_weight = self.t2_weights.get(key, 0)
            self.t2.pop(key)
            self.t2[key] = value
            self.t2_weights[key] = entry_weight
            self.t2_weight += entry_weight - old_weight
            self.updates += 1
            self._evict_real_until_fit(prefer_t1=False)
            return
        if key in self.b1:
            self.b1_hits += 1
            delta = key_weight if self.b1_weight >= self.b2_weight else max(key_weight, self.b2_weight // max(1, self.b1_weight))
            self.p = min(self.capacity, self.p + delta)
            self._replace(key)
            self._ghost_pop(self.b1, self.b1_weights, key, "b1")
            self.t2[key] = value
            self.t2_weights[key] = entry_weight
            self.t2_weight += entry_weight
            self.insertions += 1
            self._evict_real_until_fit(prefer_t1=False)
            return
        if key in self.b2:
            self.b2_hits += 1
            delta = key_weight if self.b2_weight >= self.b1_weight else max(key_weight, self.b1_weight // max(1, self.b2_weight))
            self.p = max(0, self.p - delta)
            self._replace(key)
            self._ghost_pop(self.b2, self.b2_weights, key, "b2")
            self.t2[key] = value
            self.t2_weights[key] = entry_weight
            self.t2_weight += entry_weight
            self.insertions += 1
            self._evict_real_until_fit(prefer_t1=False)
            return
        self._ensure_space_for_new_entry(entry_weight, key)
        self.t1[key] = value
        self.t1_weights[key] = entry_weight
        self.t1_weight += entry_weight
        self.insertions += 1
        self._evict_real_until_fit(prefer_t1=True)

    def __len__(self):
        return len(self.t1) + len(self.t2)

    def current_weight(self):
        return self.t1_weight + self.t2_weight

    def max_weight(self):
        return self.capacity

    def storage_objects(self):
        return {"t1": self.t1, "t2": self.t2, "b1": self.b1, "b2": self.b2, "t1_weights": self.t1_weights, "t2_weights": self.t2_weights, "b1_weights": self.b1_weights, "b2_weights": self.b2_weights, "t1_weight": self.t1_weight, "t2_weight": self.t2_weight, "b1_weight": self.b1_weight, "b2_weight": self.b2_weight, "p": self.p}

    def stats(self):
        base = super().stats()
        base.update({"policy": "arc", "entries": len(self), "insertions": self.insertions, "updates": self.updates, "evictions": self.evictions, "rejected_too_large": self.rejected_too_large, "b1_hits": self.b1_hits, "b2_hits": self.b2_hits, "p": self.p, "t1_entries": len(self.t1), "t2_entries": len(self.t2), "b1_entries": len(self.b1), "b2_entries": len(self.b2), "t1_weight": self.t1_weight, "t2_weight": self.t2_weight, "b1_weight": self.b1_weight, "b2_weight": self.b2_weight})
        return base

    def _replace(self, incoming_key):
        incoming_in_b2 = incoming_key in self.b2
        if self.t1 and ((incoming_in_b2 and self.t1_weight <= self.p) or (self.t1_weight > self.p)):
            self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
        elif self.t2:
            self._move_real_lru_to_ghost(self.t2, self.t2_weights, "t2")
        elif self.t1:
            self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
        self._trim_ghosts()

    def _ensure_space_for_new_entry(self, incoming_weight, incoming_key):
        while self.current_weight() + incoming_weight > self.capacity and (self.t1 or self.t2):
            self._replace(incoming_key)
        self._trim_ghosts()

    def _evict_real_until_fit(self, prefer_t1):
        while self.current_weight() > self.capacity and (self.t1 or self.t2):
            if prefer_t1 and self.t1:
                self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
            elif self.t2:
                self._move_real_lru_to_ghost(self.t2, self.t2_weights, "t2")
            elif self.t1:
                self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
            self._trim_ghosts()

    def _move_real_lru_to_ghost(self, store, weights, source):
        old_key, _ = store.popitem(last=False)
        old_weight = weights.pop(old_key, 0)
        self.evictions += 1
        if source == "t1":
            self.t1_weight -= old_weight
            self.b1[old_key] = None
            ghost_weight = self.sizer.key_weight(old_key)
            self.b1_weights[old_key] = ghost_weight
            self.b1_weight += ghost_weight
        else:
            self.t2_weight -= old_weight
            self.b2[old_key] = None
            ghost_weight = self.sizer.key_weight(old_key)
            self.b2_weights[old_key] = ghost_weight
            self.b2_weight += ghost_weight

    def _ghost_pop(self, store, weights, key, which):
        store.pop(key, None)
        removed = weights.pop(key, 0)
        if which == "b1":
            self.b1_weight -= removed
        else:
            self.b2_weight -= removed

    def _trim_ghosts(self):
        while self.b1_weight > self.capacity and self.b1:
            key, _ = self.b1.popitem(last=False)
            self.b1_weight -= self.b1_weights.pop(key, 0)
        while self.b2_weight > self.capacity and self.b2:
            key, _ = self.b2.popitem(last=False)
            self.b2_weight -= self.b2_weights.pop(key, 0)
        while (self.current_weight() + self.b1_weight + self.b2_weight > 2 * self.capacity):
            if self.b2:
                key, _ = self.b2.popitem(last=False)
                self.b2_weight -= self.b2_weights.pop(key, 0)
            elif self.b1:
                key, _ = self.b1.popitem(last=False)
                self.b1_weight -= self.b1_weights.pop(key, 0)
            else:
                break


# ---------------------------------------------------------------------------
# Strategy A: BFS距離ベースLRUキャッシュ
# ---------------------------------------------------------------------------

class BFSDistanceLRUCache(BaseDecisionCache):
    """
    Strategy A: BFS距離ベースのLRUキャッシュ。

    Cache-in (2経路):
      1. reactive  : _authorize_candidate() でキャッシュミス後に自動投入。
                     BFS距離不明のため dist=0（near 扱い）で投入される。
      2. proactive : walk開始時に prefetch_bfs_neighbors() が呼ばれ、
                     ローカル所有エンティティをBFS depth以内で先読み投入する。
                     正確な dist 付きで set_with_dist() を呼ぶことで
                     far/near の振り分けが正確になる。

    Cache-out:
      - bfs_dist >= far_threshold のエントリ（far group）を優先的に追い出す。
        → 遠方の低アクセスエンティティが先に退去することで near を保護。
      - far group が空になったら near group (dist < far_threshold) からLRU順。
    """

    def __init__(
        self, capacity: int, far_threshold: int = 6, sizer: Optional[DecisionCacheSizer] = None
    ) -> None:
        if capacity <= 0:
            raise ValueError("BFSDistanceLRUCache capacity must be positive")
        self.capacity = capacity
        self.far_threshold = far_threshold
        self.sizer = sizer or DecisionCacheSizer()

        # near: bfs_dist < far_threshold  /  far: bfs_dist >= far_threshold
        self.near: OrderedDict[Tuple[int, str], bool] = OrderedDict()
        self.far: OrderedDict[Tuple[int, str], bool] = OrderedDict()
        self.weights: Dict[Tuple[int, str], int] = {}
        self.total_weight = 0

        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0
        self.prefetch_count = 0  # proactive cache-in の件数

    def _locate(self, key: Tuple[int, str]) -> Optional[OrderedDict]:
        if key in self.near:
            return self.near
        if key in self.far:
            return self.far
        return None

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.near or key in self.far

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        if key in self.near:
            value = self.near[key]
            self.near.move_to_end(key)
            return value
        value = self.far[key]
        self.far.move_to_end(key)
        return value

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        # reactive cache-in: dist 不明 → near 扱い (dist=0)
        self.set_with_dist(key, value, bfs_dist=0)

    def set_with_dist(self, key: Tuple[int, str], value: bool, bfs_dist: int = 0) -> None:
        """Cache-in: BFS距離付きで投入する（proactive・reactive 共通インタフェース）。"""
        value = bool(value)
        new_weight = self.sizer.entry_weight(key, value)
        if new_weight > self.capacity:
            self.rejected_too_large += 1
            return

        is_far = bfs_dist >= self.far_threshold
        target = self.far if is_far else self.near

        existing = self._locate(key)
        if existing is not None:
            old_weight = self.weights.get(key, 0)
            existing.pop(key)
            target[key] = value
            target.move_to_end(key)
            self.weights[key] = new_weight
            self.total_weight += new_weight - old_weight
            self.updates += 1
            self._evict_until_fit()
            return

        target[key] = value
        target.move_to_end(key)
        self.weights[key] = new_weight
        self.total_weight += new_weight
        self.insertions += 1
        if bfs_dist > 0:
            self.prefetch_count += 1
        self._evict_until_fit()

    def __len__(self) -> int:
        return len(self.near) + len(self.far)

    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def _evict_until_fit(self) -> None:
        # Cache-out: far（遠方）→ near（近傍）の順で追い出す
        while self.total_weight > self.capacity:
            if self.far:
                old_key, _ = self.far.popitem(last=False)
            elif self.near:
                old_key, _ = self.near.popitem(last=False)
            else:
                break
            self.total_weight -= self.weights.pop(old_key, 0)
            self.evictions += 1

    def storage_objects(self) -> Dict[str, Any]:
        return {"near": self.near, "far": self.far, "weights": self.weights}

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "bfs-lru",
                "entries": len(self),
                "near_entries": len(self.near),
                "far_entries": len(self.far),
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
                "prefetch_count": self.prefetch_count,
                "far_threshold": self.far_threshold,
            }
        )
        return base


# ---------------------------------------------------------------------------
# Strategy C: 次数ベースLRUキャッシュ
# ---------------------------------------------------------------------------

class DegreeAwareLRUCache(BaseDecisionCache):
    """
    Strategy C: 次数ベースのLRUキャッシュ。

    Cache-in:
      - reactive のみ: _authorize_candidate() でミス後に自動投入（追加変更なし）。
      - 投入時に hub_threshold を基にエンティティのTierを決定してグループに振り分ける。

    Cache-out (Tier順に追い出す):
      Tier 0: low-low エッジ（両端ノード次数がともに < hub_threshold）→ 最初に追い出す
      Tier 1: hub-low エッジ（片端だけ >= hub_threshold）
      Tier 2: 低次数ノード（degree < hub_threshold）
      Tier 3: hub-hub エッジ（両端とも >= hub_threshold）
      Tier 4: ハブノード（degree >= hub_threshold）→ 最後に追い出す
      ※ 各Tier内はLRU順
    """

    TIER_LOW_LOW_EDGE = 0
    TIER_HUB_LOW_EDGE = 1
    TIER_LOW_NODE = 2
    TIER_HUB_HUB_EDGE = 3
    TIER_HUB_NODE = 4
    NUM_TIERS = 5
    TIER_LABELS = ["low_low_edge", "hub_low_edge", "low_node", "hub_hub_edge", "hub_node"]

    def __init__(
        self,
        capacity: int,
        neighbor_map: Dict[Any, List[Any]],
        hub_threshold: int = 10,
        sizer: Optional[DecisionCacheSizer] = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("DegreeAwareLRUCache capacity must be positive")
        self.capacity = capacity
        self.neighbor_map = neighbor_map
        self.hub_threshold = hub_threshold
        self.sizer = sizer or DecisionCacheSizer()

        self.tiers: List[OrderedDict] = [OrderedDict() for _ in range(self.NUM_TIERS)]
        self.key_to_tier: Dict[Tuple[int, str], int] = {}
        self.weights: Dict[Tuple[int, str], int] = {}
        self.total_weight = 0

        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0

    def _node_degree(self, node_id: Any) -> Optional[int]:
        """neighbor_map から二部グラフ上の次数（= 元グラフの次数）を返す。"""
        nm = self.neighbor_map.get(node_id)
        if nm is None and isinstance(node_id, str) and node_id.isdigit():
            nm = self.neighbor_map.get(int(node_id))
        return len(nm) if nm is not None else None

    def _tier_of(self, key: Tuple[int, str]) -> int:
        """エンティティキーから eviction Tier を決定する。"""
        _, entity_key = key
        ht = self.hub_threshold

        if entity_key.startswith("edge_"):
            parts = entity_key.split("_")
            if len(parts) == 3:
                try:
                    du = self._node_degree(int(parts[1]))
                    dv = self._node_degree(int(parts[2]))
                    if du is not None and dv is not None:
                        mn, mx = min(du, dv), max(du, dv)
                        if mx < ht:
                            return self.TIER_LOW_LOW_EDGE
                        if mn >= ht:
                            return self.TIER_HUB_HUB_EDGE
                        return self.TIER_HUB_LOW_EDGE
                except (ValueError, TypeError):
                    pass
            return self.TIER_LOW_LOW_EDGE

        if entity_key.startswith("node:"):
            try:
                nid = int(entity_key[5:])
            except ValueError:
                return self.TIER_LOW_NODE
            deg = self._node_degree(nid)
            if deg is None:
                return self.TIER_LOW_NODE
            return self.TIER_HUB_NODE if deg >= ht else self.TIER_LOW_NODE

        return self.TIER_LOW_NODE

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.key_to_tier

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        tier = self.key_to_tier[key]
        value = self.tiers[tier][key]
        self.tiers[tier].move_to_end(key)
        return value

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        """Cache-in: Tierを計算して対応グループに投入する。"""
        value = bool(value)
        new_weight = self.sizer.entry_weight(key, value)
        if new_weight > self.capacity:
            self.rejected_too_large += 1
            return

        new_tier = self._tier_of(key)

        if key in self.key_to_tier:
            old_tier = self.key_to_tier[key]
            old_weight = self.weights.get(key, 0)
            self.tiers[old_tier].pop(key)
            self.tiers[new_tier][key] = value
            self.tiers[new_tier].move_to_end(key)
            self.key_to_tier[key] = new_tier
            self.weights[key] = new_weight
            self.total_weight += new_weight - old_weight
            self.updates += 1
            self._evict_until_fit()
            return

        self.tiers[new_tier][key] = value
        self.tiers[new_tier].move_to_end(key)
        self.key_to_tier[key] = new_tier
        self.weights[key] = new_weight
        self.total_weight += new_weight
        self.insertions += 1
        self._evict_until_fit()

    def __len__(self) -> int:
        return sum(len(t) for t in self.tiers)

    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def _evict_until_fit(self) -> None:
        # Cache-out: Tier 0（low-low edge）から順に追い出す
        for tier_idx in range(self.NUM_TIERS):
            while self.total_weight > self.capacity and self.tiers[tier_idx]:
                old_key, _ = self.tiers[tier_idx].popitem(last=False)
                self.total_weight -= self.weights.pop(old_key, 0)
                self.key_to_tier.pop(old_key, None)
                self.evictions += 1
            if self.total_weight <= self.capacity:
                break

    def storage_objects(self) -> Dict[str, Any]:
        return {"tiers": self.tiers, "key_to_tier": self.key_to_tier, "weights": self.weights}

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "degree-lru",
                "entries": len(self),
                "tier_entries": {
                    self.TIER_LABELS[i]: len(self.tiers[i]) for i in range(self.NUM_TIERS)
                },
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
                "hub_threshold": self.hub_threshold,
            }
        )
        return base


# ---------------------------------------------------------------------------
# Strategy H: BFS距離 × 次数Tier ハイブリッドLRUキャッシュ
# ---------------------------------------------------------------------------

class HybridLRUCache(BaseDecisionCache):
    """
    Strategy H: BFS距離（near/far）と次数Tier（0-4）を組み合わせた10段階優先キャッシュ。

    Cache-in (2経路):
      1. reactive  : __setitem__() でキャッシュミス後に自動投入（bfs_dist=0 = near扱い）。
      2. proactive : walk開始時に prefetch_bfs_neighbors() が set_with_dist() を呼び、
                     正確な bfs_dist 付きで投入する。

    Cache-out スロット優先順 (slot 0 = 最初に追い出す):
      Slot 0: far  + low-low edge   (遠くて両端低次数エッジ → 最優先追い出し)
      Slot 1: far  + hub-low edge
      Slot 2: far  + low node
      Slot 3: far  + hub-hub edge
      Slot 4: far  + hub node
      Slot 5: near + low-low edge
      Slot 6: near + hub-low edge
      Slot 7: near + low node
      Slot 8: near + hub-hub edge
      Slot 9: near + hub node       (近くてハブノード → 最後まで保護)
      ※ 各slot内はLRU順
    """

    NUM_TIERS = 5
    NUM_SLOTS = 10  # 2 (far/near) × 5 tiers
    TIER_LOW_LOW_EDGE = 0
    TIER_HUB_LOW_EDGE = 1
    TIER_LOW_NODE = 2
    TIER_HUB_HUB_EDGE = 3
    TIER_HUB_NODE = 4
    TIER_LABELS = ["low_low_edge", "hub_low_edge", "low_node", "hub_hub_edge", "hub_node"]

    def __init__(
        self,
        capacity: int,
        neighbor_map: Dict[Any, List[Any]],
        far_threshold: int = 6,
        hub_threshold: int = 10,
        sizer: Optional[DecisionCacheSizer] = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("HybridLRUCache capacity must be positive")
        self.capacity = capacity
        self.neighbor_map = neighbor_map
        self.far_threshold = far_threshold
        self.hub_threshold = hub_threshold
        self.sizer = sizer or DecisionCacheSizer()

        self.slots: List[OrderedDict] = [OrderedDict() for _ in range(self.NUM_SLOTS)]
        self.key_to_slot: Dict[Tuple[int, str], int] = {}
        self.key_to_dist: Dict[Tuple[int, str], int] = {}
        self.weights: Dict[Tuple[int, str], int] = {}
        self.total_weight = 0

        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0
        self.prefetch_count = 0

    def _node_degree(self, node_id: Any) -> Optional[int]:
        nm = self.neighbor_map.get(node_id)
        if nm is None and isinstance(node_id, str) and node_id.isdigit():
            nm = self.neighbor_map.get(int(node_id))
        return len(nm) if nm is not None else None

    def _tier_of(self, key: Tuple[int, str]) -> int:
        _, entity_key = key
        ht = self.hub_threshold
        if entity_key.startswith("edge_"):
            parts = entity_key.split("_")
            if len(parts) == 3:
                try:
                    du = self._node_degree(int(parts[1]))
                    dv = self._node_degree(int(parts[2]))
                    if du is not None and dv is not None:
                        mn, mx = min(du, dv), max(du, dv)
                        if mx < ht:
                            return self.TIER_LOW_LOW_EDGE
                        if mn >= ht:
                            return self.TIER_HUB_HUB_EDGE
                        return self.TIER_HUB_LOW_EDGE
                except (ValueError, TypeError):
                    pass
            return self.TIER_LOW_LOW_EDGE
        if entity_key.startswith("node:"):
            try:
                nid = int(entity_key[5:])
            except ValueError:
                return self.TIER_LOW_NODE
            deg = self._node_degree(nid)
            if deg is None:
                return self.TIER_LOW_NODE
            return self.TIER_HUB_NODE if deg >= ht else self.TIER_LOW_NODE
        return self.TIER_LOW_NODE

    def _slot_of(self, key: Tuple[int, str], bfs_dist: int) -> int:
        """far group: slots 0-4 / near group: slots 5-9。同じdistance内はTier昇順で追い出す。"""
        is_far = bfs_dist >= self.far_threshold
        tier = self._tier_of(key)
        return tier if is_far else (self.NUM_TIERS + tier)

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.key_to_slot

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        slot = self.key_to_slot[key]
        value = self.slots[slot][key]
        self.slots[slot].move_to_end(key)
        return value

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        self.set_with_dist(key, value, bfs_dist=0)

    def set_with_dist(self, key: Tuple[int, str], value: bool, bfs_dist: int = 0) -> None:
        value = bool(value)
        new_weight = self.sizer.entry_weight(key, value)
        if new_weight > self.capacity:
            self.rejected_too_large += 1
            return

        new_slot = self._slot_of(key, bfs_dist)

        if key in self.key_to_slot:
            old_slot = self.key_to_slot[key]
            old_weight = self.weights.get(key, 0)
            self.slots[old_slot].pop(key)
            self.slots[new_slot][key] = value
            self.slots[new_slot].move_to_end(key)
            self.key_to_slot[key] = new_slot
            self.key_to_dist[key] = bfs_dist
            self.weights[key] = new_weight
            self.total_weight += new_weight - old_weight
            self.updates += 1
            self._evict_until_fit()
            return

        self.slots[new_slot][key] = value
        self.slots[new_slot].move_to_end(key)
        self.key_to_slot[key] = new_slot
        self.key_to_dist[key] = bfs_dist
        self.weights[key] = new_weight
        self.total_weight += new_weight
        self.insertions += 1
        if bfs_dist > 0:
            self.prefetch_count += 1
        self._evict_until_fit()

    def __len__(self) -> int:
        return sum(len(s) for s in self.slots)

    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def _evict_until_fit(self) -> None:
        for slot_idx in range(self.NUM_SLOTS):
            while self.total_weight > self.capacity and self.slots[slot_idx]:
                old_key, _ = self.slots[slot_idx].popitem(last=False)
                self.total_weight -= self.weights.pop(old_key, 0)
                self.key_to_slot.pop(old_key, None)
                self.key_to_dist.pop(old_key, None)
                self.evictions += 1
            if self.total_weight <= self.capacity:
                break

    def storage_objects(self) -> Dict[str, Any]:
        return {"slots": self.slots, "key_to_slot": self.key_to_slot, "weights": self.weights}

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        slot_labels = (
            [f"far_{lbl}" for lbl in self.TIER_LABELS]
            + [f"near_{lbl}" for lbl in self.TIER_LABELS]
        )
        base.update(
            {
                "policy": "hybrid-lru",
                "entries": len(self),
                "slot_entries": {slot_labels[i]: len(self.slots[i]) for i in range(self.NUM_SLOTS)},
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
                "prefetch_count": self.prefetch_count,
                "far_threshold": self.far_threshold,
                "hub_threshold": self.hub_threshold,
            }
        )
        return base


# ---------------------------------------------------------------------------
# キャッシュファクトリ（全ポリシー対応）
# ---------------------------------------------------------------------------

def build_authz_cache(
    policy: str,
    capacity: int,
    sizer: Optional[DecisionCacheSizer] = None,
    neighbor_map: Optional[Dict] = None,
    hub_threshold: int = 10,
    bfs_far_threshold: int = 6,
) -> BaseDecisionCache:
    policy_name = (policy or "none").lower()
    if policy_name == "none":
        return NoDecisionCache()
    if policy_name == "memo":
        return UnlimitedDecisionCache(sizer=sizer)
    if policy_name == "lru":
        return LRUDecisionCache(capacity, sizer=sizer)
    if policy_name == "arc":
        return ARCDecisionCache(capacity, sizer=sizer)
    if policy_name == "bfs-lru":
        return BFSDistanceLRUCache(capacity, far_threshold=bfs_far_threshold, sizer=sizer)
    if policy_name == "degree-lru":
        if neighbor_map is None:
            raise ValueError("degree-lru requires neighbor_map. --edges でグラフを渡してください。")
        return DegreeAwareLRUCache(
            capacity, neighbor_map=neighbor_map, hub_threshold=hub_threshold, sizer=sizer
        )
    if policy_name == "hybrid-lru":
        if neighbor_map is None:
            raise ValueError("hybrid-lru requires neighbor_map. --edges でグラフを渡してください。")
        return HybridLRUCache(
            capacity,
            neighbor_map=neighbor_map,
            far_threshold=bfs_far_threshold,
            hub_threshold=hub_threshold,
            sizer=sizer,
        )
    raise ValueError(f"Unsupported cache policy: {policy}")


# ---------------------------------------------------------------------------
# Strategy A プリフェッチ関数
# ---------------------------------------------------------------------------

def prefetch_bfs_neighbors(
    server: Any,
    start_node: int,
    walker: "PeerWalker",
    depth: int = 2,
) -> Dict[str, int]:
    """
    Strategy A の proactive cache-in。

    walk 開始時に start_node から BFS depth 以内の近傍を探索し、
    ローカル所有エンティティのみ即時認可してキャッシュに投入する。
    リモート所有エンティティはwalk中に reactive で投入される。

    Returns:
        bfs_dist_map: str(entity_id) -> bfs_dist のマップ。
                      _authorize_candidate() が reactive cache-in 時に
                      正確な dist を付与するために参照する。
    """
    bfs_dist_map: Dict[str, int] = {}
    visited: Set[str] = {str(start_node)}
    q: deque = deque([(start_node, 0)])

    while q:
        entity, d = q.popleft()
        if d >= depth:
            continue

        neighbors = server.shard.get_neighbors(entity)
        if not neighbors:
            continue

        for nb in neighbors:
            nb_id = nb.node_id
            nb_str = str(nb_id)
            if nb_str in visited:
                continue
            visited.add(nb_str)
            bfs_dist_map[nb_str] = d + 1

            # ローカル所有エンティティのみ proactive cache-in
            if nb.server_id == server.shard.server_id:
                allowed = walker._is_locally_allowed(start_node, nb_id)
                _node_only = getattr(server, "cache_key_mode", "full") == "node_only"
                ekey = cache_entity_key(nb_id, node_only=_node_only)
                ckey = (int(start_node), ekey)
                if hasattr(server.authz_cache, "set_with_dist"):
                    server.authz_cache.set_with_dist(ckey, allowed, bfs_dist=d + 1)
                else:
                    server.authz_cache[ckey] = allowed

            q.append((nb_id, d + 1))

    return bfs_dist_map


# ---------------------------------------------------------------------------
# Utilities (baseline と同一)
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def cache_entity_key(entity: Any, node_only: bool = False) -> str:
    if isinstance(entity, int):
        return f"node:{entity}"
    if isinstance(entity, str):
        if entity.startswith("edge_"):
            if node_only:
                parts = entity.split("_", 2)
                return f"node:{parts[1]}" if len(parts) >= 2 else entity
            return entity
        if entity.isdigit():
            return f"node:{entity}"
        return entity
    return str(entity)


def parse_entity_id(raw: Any) -> NodeOrEdgeId:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.startswith("edge_"):
        return raw
    try:
        return int(raw)
    except Exception:
        return str(raw)


def make_edge_id(u: int, v: int) -> str:
    a, b = sorted((u, v))
    return f"edge_{a}_{b}"


def load_edge_list(edge_path: Path) -> List[Tuple[int, int]]:
    edges: List[Tuple[int, int]] = []
    with edge_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) != 2:
                raise ValueError(f"Edge list line is malformed: {line!r}")
            u, v = map(int, parts)
            edges.append((u, v))
    return edges


@dataclass(frozen=True)
class Neighbor:
    node_id: NodeId
    server_id: int


def split_owned_entities(local_entities: Set[NodeId]) -> Tuple[List[int], List[str]]:
    nodes: List[int] = []
    edges: List[str] = []
    for ent in local_entities:
        if isinstance(ent, int):
            nodes.append(ent)
        elif isinstance(ent, str) and ent.startswith("edge_"):
            edges.append(ent)
    nodes.sort()
    edges.sort()
    return nodes, edges


def get_process_rss_kb() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return int(parts[1])
    except Exception:
        pass
    return get_process_rss_kb_max()


def get_process_rss_kb_max() -> int:
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return -1


def deep_getsizeof(obj: Any, seen: Optional[Set[int]] = None) -> int:
    if obj is None:
        return 0
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            size += deep_getsizeof(k, seen)
            size += deep_getsizeof(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for x in obj:
            size += deep_getsizeof(x, seen)
    return size


def safe_file_size(path: Optional[Union[str, Path]]) -> Optional[int]:
    if not path:
        return None
    try:
        p = Path(path)
        return p.stat().st_size
    except Exception:
        return None


def summarize_tables(server: Any) -> Dict[str, Any]:
    shard = server.shard
    nm = getattr(shard, "neighbor_map", {}) or {}
    nts = getattr(server, "node_to_starts", {}) or {}
    owner_map = getattr(server, "owner_map", {}) or {}
    authz_cache = getattr(server, "authz_cache", None)
    cache_entries = len(authz_cache) if authz_cache is not None else 0
    counters_bundle = {}
    for name in ["access_counter", "authorized_counter", "authorization_attempt_counter", "authorization_denied_counter", "transition_counter"]:
        c = getattr(server, name, None)
        if c is not None:
            counters_bundle[name] = c
    graph_entities = len(nm)
    graph_total_neighbors = sum(len(v) for v in nm.values())
    auth_entities = len(nts)
    auth_total_starts = sum(len(v) for v in nts.values())
    access_total = sum(int(v) for v in getattr(server, "access_counter", {}).values())
    authorized_total = sum(int(v) for v in getattr(server, "authorized_counter", {}).values())
    attempts_total = sum(int(v) for v in getattr(server, "authorization_attempt_counter", {}).values())
    denied_total = sum(int(v) for v in getattr(server, "authorization_denied_counter", {}).values())
    transition_total = sum(int(v) for v in getattr(server, "transition_counter", {}).values())
    auth_table = getattr(server, "auth_table", {}) or {}
    auth_table_entries = len(auth_table)
    auth_table_nodes = 0
    auth_table_edges = 0
    for entry in auth_table.values():
        nodes = entry.get("n", set()) if isinstance(entry, dict) else set()
        edges = entry.get("e", set()) if isinstance(entry, dict) else set()
        auth_table_nodes += len(nodes) if hasattr(nodes, "__len__") else 0
        auth_table_edges += len(edges) if hasattr(edges, "__len__") else 0
    edges_path = getattr(server, "edges_path", None)
    nts_path = getattr(server, "node_to_starts_path", None)
    auth_path = getattr(server, "auth_file_path", None)
    return {
        "pid": os.getpid(),
        "rss_kb": get_process_rss_kb(),
        "rss_kb_max": get_process_rss_kb_max(),
        "graph_entities": graph_entities,
        "graph_total_neighbors": graph_total_neighbors,
        "local_entities": len(getattr(shard, "local_entities", set()) or set()),
        "auth_entities": auth_entities,
        "auth_total_starts": auth_total_starts,
        "auth_table_entries": auth_table_entries,
        "auth_table_nodes": auth_table_nodes,
        "auth_table_edges": auth_table_edges,
        "owner_map_size": len(owner_map),
        "cache_entries": cache_entries,
        "cache_weight": authz_cache.current_weight() if hasattr(authz_cache, "current_weight") else None,
        "cache_capacity": authz_cache.max_weight() if hasattr(authz_cache, "max_weight") else None,
        "counters_total": {"access": access_total, "authorized": authorized_total, "attempts": attempts_total, "denied": denied_total, "transition": transition_total},
        "bytes_est": {"neighbor_map": deep_getsizeof(nm), "node_to_starts": deep_getsizeof(nts), "owner_map": deep_getsizeof(owner_map), "auth_table": deep_getsizeof(auth_table), "authz_cache": deep_getsizeof(authz_cache.storage_objects() if hasattr(authz_cache, "storage_objects") else authz_cache), "counters": deep_getsizeof(counters_bundle)},
        "files": {"edges": {"path": edges_path, "bytes": safe_file_size(edges_path)}, "node_to_starts": {"path": nts_path, "bytes": safe_file_size(nts_path)}, "auth_file": {"path": auth_path, "bytes": safe_file_size(auth_path)}},
    }


# ---------------------------------------------------------------------------
# Partitioners (baseline と同一)
# ---------------------------------------------------------------------------

class ModuloPartitioner:
    def __init__(self, server_count: int) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count

    def assign_node(self, node_id: int) -> int:
        return node_id % self.server_count

    def assign_edge(self, u: int, v: int) -> int:
        a, b = sorted((u, v))
        return (a * 1_000_003 + b) % self.server_count

    def assign_entity(self, entity_id: NodeId) -> int:
        if isinstance(entity_id, int):
            return self.assign_node(entity_id)
        if isinstance(entity_id, str) and entity_id.startswith("edge_"):
            try:
                _, raw_u, raw_v = entity_id.split("_", 2)
                return self.assign_edge(int(raw_u), int(raw_v))
            except ValueError as exc:
                raise ValueError(f"Malformed edge id: {entity_id!r}") from exc
        raise TypeError(f"Unsupported entity id type: {entity_id!r}")


class StaticPartitioner:
    def __init__(self, server_count: int, mapping: Dict[str, int], fallback=None) -> None:
        if server_count <= 0:
            raise ValueError("server_count must be positive")
        self.server_count = server_count
        self.mapping = {str(k): int(v) % server_count for k, v in mapping.items()}
        self.fallback = fallback or ModuloPartitioner(server_count)

    def assign_entity(self, entity_id: NodeId) -> int:
        key = str(entity_id)
        if key in self.mapping:
            return self.mapping[key]
        return self.fallback.assign_entity(entity_id)


# ---------------------------------------------------------------------------
# GraphShard (baseline と同一)
# ---------------------------------------------------------------------------

class GraphShard:
    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_id: int,
        server_count: int,
        partitioner=None,
        owner_map: Optional[Dict[str, int]] = None,
        owned_hints_only: bool = False,
        owned_hints: Optional[Set[NodeOrEdgeId]] = None,
    ) -> None:
        if server_id < 0 or server_id >= server_count:
            raise ValueError("server_id must satisfy 0 <= server_id < server_count")
        self.server_id = server_id
        self.partitioner = partitioner or ModuloPartitioner(server_count)
        self.owner_map: Dict[str, int] = owner_map or {}
        self.owned_hints_only = owned_hints_only
        self.owned_hints: Set[NodeOrEdgeId] = owned_hints or set()
        self.neighbor_map: Dict[NodeId, List[Neighbor]] = {}
        self.local_entities: Set[NodeId] = set()

        def normalize_entity(ent: Any) -> NodeId:
            if isinstance(ent, int):
                return ent
            if isinstance(ent, str):
                if ent.startswith("edge_"):
                    return ent
                if ent.isdigit():
                    try:
                        return int(ent)
                    except Exception:
                        return ent
            return ent

        def owner_of(ent: Any) -> int:
            key = str(ent)
            if key in self.owner_map:
                return int(self.owner_map[key])
            return self.partitioner.assign_entity(ent)

        def ensure_key(ent: NodeId) -> None:
            if ent not in self.neighbor_map:
                self.neighbor_map[ent] = []

        for u, v in edges:
            u = normalize_entity(u)
            v = normalize_entity(v)
            edge_id = normalize_entity(make_edge_id(int(u), int(v)))
            ensure_key(u)
            ensure_key(v)
            ensure_key(edge_id)
            edge_owner = owner_of(edge_id)
            u_owner = owner_of(u)
            v_owner = owner_of(v)
            self.neighbor_map[u].append(Neighbor(edge_id, edge_owner))
            self.neighbor_map[v].append(Neighbor(edge_id, edge_owner))
            self.neighbor_map[edge_id].append(Neighbor(u, u_owner))
            self.neighbor_map[edge_id].append(Neighbor(v, v_owner))

        if self.owned_hints_only:
            hints_str = {str(x) for x in self.owned_hints}
            for x in self.owned_hints:
                nx = normalize_entity(x)
                ensure_key(nx)
            for ent in self.neighbor_map.keys():
                if str(ent) in hints_str:
                    self.local_entities.add(ent)
        else:
            for ent in self.neighbor_map.keys():
                if owner_of(ent) == self.server_id:
                    self.local_entities.add(ent)

    def get_neighbors(self, entity_id: Any) -> Optional[List[Neighbor]]:
        ent = entity_id
        if isinstance(ent, str) and (not ent.startswith("edge_")) and ent.isdigit():
            try:
                ent = int(ent)
            except Exception:
                pass
        if ent not in self.neighbor_map:
            print(f"[GraphShard] entity NOT IN GRAPH: {ent!r}")
            return None
        return list(self.neighbor_map.get(ent, []))


# ---------------------------------------------------------------------------
# Auth tables (baseline と同一)
# ---------------------------------------------------------------------------

def load_entity_auth_table(path: Optional[Union[str, Path]]) -> Dict[int, Dict[str, Set[Any]]]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Auth table not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[int, Dict[str, Set[Any]]] = {}
    for k, v in raw.items():
        try:
            start = int(k)
        except Exception:
            continue
        nodes = set(int(x) for x in v.get("n", []) if x is not None)
        edges = set(str(x) for x in v.get("e", []) if x is not None)
        out[start] = {"n": nodes, "e": edges}
    return out


def load_node_to_starts_table(path: Optional[Union[str, Path]]) -> Dict[NodeOrEdgeId, Set[int]]:
    if path is None:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"node_to_starts file not found: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: Dict[NodeOrEdgeId, Set[int]] = {}
    for k, values in raw.items():
        try:
            entity_key: NodeOrEdgeId = int(k)
        except Exception:
            entity_key = str(k)
        starts: Set[int] = set()
        for v in values:
            try:
                starts.add(int(v))
            except Exception:
                continue
        out[entity_key] = starts
    return out


def build_owner_map_from_sibling_node_to_starts_files(base_path: Path) -> Dict[str, int]:
    dir_path = base_path.parent
    owner_map: Dict[str, int] = {}
    pat = re.compile(r"node_to_starts_server(\d+)\.json$")
    for p in sorted(dir_path.glob("node_to_starts_server*.json")):
        m = pat.search(p.name)
        if not m:
            continue
        sid = int(m.group(1))
        tbl = load_node_to_starts_table(p)
        for ent in tbl.keys():
            key = str(ent)
            if key in owner_map and owner_map[key] != sid:
                print(f"[WARN] entity {key} appears in multiple files: {owner_map[key]} and {sid}")
            owner_map[key] = sid
    return owner_map


def resolve_node_to_starts_path(base_path: Path, server_id: int) -> Path:
    stem = base_path.stem
    suffix = base_path.suffix
    candidates = [
        base_path.with_name(f"{stem}_server{server_id}{suffix}"),
        base_path.with_name(f"{stem}{server_id}{suffix}"),
        base_path,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"node_to_starts file not found. Tried: {[str(c) for c in candidates]}")


def filter_auth_table_for_shard(auth_table, partitioner, server_id):
    filtered = {}
    for start, entries in auth_table.items():
        local_nodes = {n for n in entries.get("n", set()) if partitioner.assign_entity(n) == server_id}
        local_edges = {e for e in entries.get("e", set()) if partitioner.assign_entity(e) == server_id}
        if local_nodes or local_edges:
            filtered[start] = {"n": local_nodes, "e": local_edges}
    return filtered


# ---------------------------------------------------------------------------
# RNG serialization (baseline と同一)
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    return obj


def _from_jsonable(obj: Any) -> Any:
    if isinstance(obj, list):
        return tuple(_from_jsonable(x) for x in obj)
    return obj


def serialize_rng_state(rng: random.Random) -> Any:
    return _to_jsonable(rng.getstate())


def deserialize_rng_state(jsonable_state: Any) -> tuple:
    return _from_jsonable(jsonable_state)


# ---------------------------------------------------------------------------
# PeerWalker — _authorize_candidate のみ変更（BFS dist map 参照を追加）
# ---------------------------------------------------------------------------

class PeerWalker:
    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        auth_table=None,
        stats_collector=None,
        ppr_mode: bool = False,
        node_to_starts=None,
        owner_map=None,
        server=None,
    ):
        self.shard = shard
        self.endpoints = [ep if ep.startswith(("http://", "https://")) else f"http://{ep}" for ep in endpoints]
        self.request_timeout = request_timeout
        self.max_hops = max_hops
        self.auth_table = auth_table or {}
        self.stats_collector = stats_collector
        self.ppr_mode = ppr_mode
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = node_to_starts or {}
        self.owner_map: Dict[str, int] = owner_map or {}
        self.server = server

    def _record_auth_cost(self, duration: float) -> None:
        if self.stats_collector is None:
            return
        if hasattr(self.stats_collector, "auth_time_total"):
            self.stats_collector.auth_time_total += duration
        if hasattr(self.stats_collector, "auth_calls"):
            self.stats_collector.auth_calls += 1

    def _is_locally_allowed(self, start_node: Optional[int], entity: Any) -> bool:
        if start_node is None:
            return False
        denied_starts = self.node_to_starts.get(entity)
        if not denied_starts:
            return True
        return start_node not in denied_starts

    def _check_remote_authorization(self, target_server: int, start_node: Optional[int], entity: Any) -> bool:
        if target_server < 0 or target_server >= len(self.endpoints):
            return False
        url = f"{self.endpoints[target_server].rstrip('/')}/authorize"
        payload = {"entity": entity, "start_node": start_node}
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
                body = json.loads(resp.read())
            return bool(body.get("allowed"))
        except Exception:
            return False

    def _authorize_candidate(self, start_node: Optional[int], candidate: Dict[str, Any]) -> bool:
        target = candidate["node_id"]
        if start_node is None:
            return False

        owner_sid: Optional[int] = None
        if self.owner_map:
            owner_sid = self.owner_map.get(str(target))
        if owner_sid is None:
            try:
                owner_sid = int(candidate.get("server_id"))
            except Exception:
                owner_sid = self.shard.partitioner.assign_entity(target)

        _node_only = getattr(self.server, "cache_key_mode", "full") == "node_only"
        ekey = cache_entity_key(target, node_only=_node_only)
        ckey = (int(start_node), ekey)

        if self.server is not None and ckey in self.server.authz_cache:
            self.server.auth_cache_hit += 1
            return bool(self.server.authz_cache[ckey])

        if self.server is not None:
            self.server.auth_cache_miss += 1

        t0 = time.perf_counter()
        if owner_sid == self.shard.server_id:
            if self.server is not None:
                self.server.local_auth_calls += 1
            allowed = self._is_locally_allowed(start_node, target)
        else:
            if self.server is not None:
                self.server.remote_auth_calls += 1
            allowed = self._check_remote_authorization(owner_sid, start_node, target)
        self._record_auth_cost(time.perf_counter() - t0)

        # ★ Cache-in: bfs-lru の場合は dist 付きで投入する
        if self.server is not None:
            if hasattr(self.server.authz_cache, "set_with_dist"):
                bfs_dist_map = getattr(self.server, "_bfs_dist_map", {})
                dist = bfs_dist_map.get(str(target), 0)
                self.server.authz_cache.set_with_dist(ckey, bool(allowed), bfs_dist=dist)
            else:
                self.server.authz_cache[ckey] = bool(allowed)

        return bool(allowed)

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def _bump(self, attr: str, key: Any) -> None:
        if self.stats_collector is None:
            return
        counter = getattr(self.stats_collector, attr, None)
        if counter is None:
            return
        counter[key] += 1

    def _record_entity_visit(self, entity_id: Any) -> None:
        self._bump("access_counter", entity_id)

    def _record_authorization_attempt(self, source: Any) -> None:
        self._bump("authorization_attempt_counter", source)

    def _record_authorization_success(self, current: Any, target: Any) -> None:
        self._bump("authorized_counter", target)
        self._bump("transition_counter", f"{current}->{target}")

    def _record_authorization_denial(self, source: Any) -> None:
        self._bump("authorization_denied_counter", source)

    def _resolve_start_node(self, state: Dict[str, Any], path: List[NodeId]) -> Optional[int]:
        if "start_node" in state:
            try:
                return int(state["start_node"])
            except Exception:
                return None
        if path:
            try:
                return int(path[0])
            except Exception:
                return None
        return None

    def _select_next_neighbor(self, rng, neighbors, start_node, current_entity):
        if not neighbors:
            return None, None
        indices = list(range(len(neighbors)))
        rng.shuffle(indices)
        for idx in indices:
            self._record_authorization_attempt(current_entity)
            cand = neighbors[idx]
            cid = cand["node_id"]
            if self._authorize_candidate(start_node, cand):
                self._record_authorization_success(current_entity, cid)
                return cand, None
            else:
                self._record_authorization_denial(current_entity)
        denial = {"denied": True, "denied_reason": f"no authorized neighbors from {current_entity}"}
        return None, denial

    def continue_from_state(self, state: dict) -> dict:
        current_sid = self.shard.server_id
        rng_state_json = state.get("rng_state")
        rng = random.Random()
        if rng_state_json is not None:
            loaded = deserialize_rng_state(rng_state_json)
            if loaded:
                rng.setstate(loaded)
        else:
            rng = random.Random(state.get("seed"))

        current_entity: NodeId = state["current_node"]
        path = list(state["path"])
        servers = list(state["servers"])
        alpha = float(state["alpha"])
        hops_done = int(state.get("hops_done", 0))
        start_node = self._resolve_start_node(state, path)
        self._record_entity_visit(current_entity)

        while hops_done < self.max_hops and rng.random() > alpha:
            hops_done += 1
            owner = self.shard.partitioner.assign_entity(current_entity)
            if owner != current_sid:
                state_out = {"start_node": start_node, "current_node": current_entity, "path": path, "servers": servers, "alpha": alpha, "rng_state": serialize_rng_state(rng), "hops_done": hops_done}
                return self._post_continue(owner, state_out)

            neighbors = self._get_local_neighbors(current_entity)
            next_choice, denial_payload = self._select_next_neighbor(rng, neighbors, start_node, current_entity)
            if next_choice is None:
                result = {"finished": True, "path": path, "servers": servers, "hops_done": hops_done}
                if denial_payload:
                    result.update(denial_payload)
                return result

            next_entity = next_choice["node_id"]
            next_server = int(next_choice["server_id"])
            self._record_entity_visit(next_entity)
            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            if next_server != current_sid:
                state_out = {"start_node": start_node, "current_node": current_entity, "path": path, "servers": servers, "alpha": alpha, "rng_state": serialize_rng_state(rng), "hops_done": hops_done}
                return self._post_continue(next_server, state_out)

        return {"finished": True, "path": path, "servers": servers, "hops_done": hops_done}


# ---------------------------------------------------------------------------
# HTTP Server — _handle_walk_start と _handle_cache_reset を変更
# ---------------------------------------------------------------------------

class EdgeAwareHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json({"status": "ok", "server_id": self.server.server_id})
            return
        if parsed.path == "/access_stats":
            payload = {
                "access": dict(self.server.access_counter),
                "authorized": dict(self.server.authorized_counter),
                "authorization_attempts": dict(self.server.authorization_attempt_counter),
                "authorization_denied": dict(self.server.authorization_denied_counter),
                "transition": dict(self.server.transition_counter),
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
                "local_auth_calls": getattr(self.server, "local_auth_calls", 0),
                "remote_auth_calls": getattr(self.server, "remote_auth_calls", 0),
                "walk_time_total": self.server.walk_time_total,
                "walk_calls": self.server.walk_calls,
                "memory": summarize_tables(self.server),
                "cache_stats": self.server.authz_cache.stats() if hasattr(self.server.authz_cache, "stats") else {},
                "auth_cache_hit": self.server.auth_cache_hit,
                "auth_cache_miss": self.server.auth_cache_miss,
                "auth_cache_size": len(self.server.authz_cache),
                "auth_cache_weight": self.server.authz_cache.current_weight() if hasattr(self.server.authz_cache, "current_weight") else None,
                "auth_cache_capacity": self.server.authz_cache.max_weight() if hasattr(self.server.authz_cache, "max_weight") else None,
                "auth_cache_policy": getattr(self.server, "auth_cache_policy", "none"),
                "auth_cache_hit_rate": self.server.auth_cache_hit / max(1, self.server.auth_cache_hit + self.server.auth_cache_miss),
            }
            self._write_json(payload)
            return
        if parsed.path != "/neighbors":
            self.send_error(404, "Unknown path")
            return
        query = parse_qs(parsed.query)
        raw_entity = query.get("node", [None])[0]
        if raw_entity is None:
            self.send_error(400, "Missing 'node' query parameter")
            return
        if raw_entity.startswith("edge_"):
            entity: NodeId = raw_entity
        else:
            try:
                entity = int(raw_entity)
            except ValueError:
                self.send_error(400, "'node' must be an integer id or an edge id (edge_u_v)")
                return
        neighbors = self.server.shard.get_neighbors(entity)
        if neighbors is None:
            self.send_error(404, f"Entity {entity} not owned by this shard")
            return
        self._write_json({"node_id": entity, "server_id": self.server.server_id, "neighbors": [asdict(n) for n in neighbors]})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/walk":
            self._handle_walk_start()
            return
        if parsed.path == "/continue_walk":
            self._handle_continue_walk()
            return
        if parsed.path == "/authorize":
            self._handle_authorize()
            return
        if parsed.path == "/cache/reset":
            self._handle_cache_reset()
            return
        self.send_error(404, "Unknown path")

    def _handle_walk_start(self) -> None:
        params = self._read_json_body()
        if params is None:
            return

        start_node = params.get("start_node")
        alpha = params.get("alpha")
        walks = int(params.get("walks", 1))
        seed = params.get("seed", None)
        endpoints = params.get("endpoints")
        server_count = params.get("server_count")

        if start_node is None or alpha is None or endpoints is None or server_count is None:
            self.send_error(400, "Missing required parameters: start_node, alpha, endpoints, server_count")
            return

        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            node_to_starts=getattr(self.server, "node_to_starts", None),
            owner_map=getattr(self.server, "owner_map", None),
            server=self.server,
        )

        # ★ bfs-lru / hybrid-lru: walk開始時に近傍をプリフェッチ投入する
        if getattr(self.server, "auth_cache_policy", "none") in ("bfs-lru", "hybrid-lru"):
            bfs_depth = getattr(self.server, "auth_cache_bfs_prefetch_depth", 2)
            bfs_dist_map = prefetch_bfs_neighbors(self.server, int(start_node), walker, depth=bfs_depth)
            self.server._bfs_dist_map = bfs_dist_map
        else:
            self.server._bfs_dist_map = {}

        start_ts = time.perf_counter()
        wall_start_epoch = time.time()
        wall_start_iso = now_iso()

        results = []
        for i in range(walks):
            rng = random.Random(seed if seed is None else (seed + i))
            initial_state = {
                "start_node": int(start_node),
                "current_node": int(start_node),
                "path": [int(start_node)],
                "servers": [self.server.server_id],
                "alpha": float(alpha),
                "rng_state": serialize_rng_state(rng),
                "hops_done": 0,
            }
            t0 = time.perf_counter()
            res = walker.continue_from_state(initial_state)
            dt = time.perf_counter() - t0
            self.server.walk_time_total += dt
            self.server.walk_calls += 1
            results.append(res)

        wall_end_epoch = time.time()
        wall_end_iso = now_iso()
        duration = time.perf_counter() - start_ts

        self._write_json({
            "walks": results,
            "metrics": {
                "server_id": self.server.server_id,
                "duration_sec": duration,
                "wall_start_epoch": wall_start_epoch,
                "wall_end_epoch": wall_end_epoch,
                "wall_start_time": wall_start_iso,
                "wall_end_time": wall_end_iso,
                "walks_requested": walks,
                "walks_completed": len(results),
                "alpha": float(alpha),
            },
        })

    def _handle_continue_walk(self) -> None:
        state = self._read_json_body()
        if state is None:
            return
        walker = PeerWalker(
            self.server.shard,
            endpoints=self.server.endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            node_to_starts=getattr(self.server, "node_to_starts", None),
            owner_map=getattr(self.server, "owner_map", None),
            server=self.server,
        )
        try:
            t0 = time.perf_counter()
            res = walker.continue_from_state(state)
            _ = time.perf_counter() - t0
        except Exception as exc:
            self.send_error(500, f"Error during continue_walk: {exc}")
            return
        self._write_json(res)

    def _handle_authorize(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        raw_entity = payload.get("entity")
        start_node = payload.get("start_node")
        entity = parse_entity_id(raw_entity)
        try:
            start_int = int(start_node)
        except Exception:
            self.send_error(400, "'start_node' must be an integer")
            return
        denied_starts = self.server.node_to_starts.get(entity, set())
        if not denied_starts:
            allowed = True
        else:
            allowed = start_int not in denied_starts
        self._write_json({"allowed": allowed, "entity": entity, "server_id": self.server.server_id})

    def _handle_cache_reset(self) -> None:
        s = self.server
        neighbor_map = s.shard.neighbor_map if s.auth_cache_policy in ("degree-lru", "hybrid-lru") else None
        s.authz_cache = build_authz_cache(
            s.auth_cache_policy,
            s.auth_cache_capacity,
            sizer=s.auth_cache_sizer,
            neighbor_map=neighbor_map,
            hub_threshold=getattr(s, "auth_cache_hub_threshold", 10),
            bfs_far_threshold=getattr(s, "auth_cache_bfs_far_threshold", 6),
        )
        s._bfs_dist_map = {}
        s.auth_cache_hit = 0
        s.auth_cache_miss = 0
        s.auth_time_total = 0.0
        s.auth_calls = 0
        s.local_auth_calls = 0
        s.remote_auth_calls = 0
        s.walk_time_total = 0.0
        s.walk_calls = 0
        s.access_counter.clear()
        s.authorized_counter.clear()
        s.authorization_attempt_counter.clear()
        s.authorization_denied_counter.clear()
        s.transition_counter.clear()
        self._write_json({"status": "ok", "policy": s.auth_cache_policy, "capacity": s.auth_cache_capacity})

    def log_message(self, format: str, *args) -> None:
        return

    def _read_json_body(self) -> Optional[dict]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return None
        body = self.rfile.read(content_length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return None

    def _write_json(self, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class GraphShardServer(ThreadingHTTPServer):
    def __init__(
        self,
        host: str,
        port: int,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        cache_policy: str = "none",
        cache_capacity: int = 0,
        cache_sizer: Optional[DecisionCacheSizer] = None,
        cache_key_mode: str = "full",
        hub_threshold: int = 10,
        bfs_far_threshold: int = 6,
        bfs_prefetch_depth: int = 2,
    ) -> None:
        super().__init__((host, port), EdgeAwareHandler)
        self.shard = shard
        self.server_id = shard.server_id
        self.endpoints = endpoints
        self.request_timeout = request_timeout

        self.auth_table: Dict[int, Dict[str, Set[Any]]] = {}
        self.node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
        self.owner_map: Dict[str, int] = {}

        self.access_counter = Counter()
        self.authorized_counter = Counter()
        self.authorization_attempt_counter = Counter()
        self.authorization_denied_counter = Counter()
        self.transition_counter = Counter()

        self.auth_time_total = 0.0
        self.auth_calls = 0
        self.local_auth_calls = 0
        self.remote_auth_calls = 0
        self.walk_time_total = 0.0
        self.walk_calls = 0

        self.auth_cache_policy = cache_policy
        self.auth_cache_capacity = cache_capacity
        self.auth_cache_sizer = cache_sizer or DecisionCacheSizer()
        self.cache_key_mode = cache_key_mode
        self.auth_cache_hub_threshold = hub_threshold
        self.auth_cache_bfs_far_threshold = bfs_far_threshold
        self.auth_cache_bfs_prefetch_depth = bfs_prefetch_depth

        # degree-lru / hybrid-lru には neighbor_map が必要
        neighbor_map = shard.neighbor_map if cache_policy in ("degree-lru", "hybrid-lru") else None
        self.authz_cache = build_authz_cache(
            cache_policy,
            cache_capacity,
            sizer=self.auth_cache_sizer,
            neighbor_map=neighbor_map,
            hub_threshold=hub_threshold,
            bfs_far_threshold=bfs_far_threshold,
        )
        self.auth_cache_hit = 0
        self.auth_cache_miss = 0
        self._bfs_dist_map: Dict[str, int] = {}

        self.edges_path: Optional[str] = None
        self.node_to_starts_path: Optional[str] = None
        self.auth_file_path: Optional[str] = None
        self.auth_cache: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="auth-cache-strategies server: bfs-lru and degree-lru in addition to baseline policies."
    )
    parser.add_argument("--owned-hints-only", action="store_true")
    parser.add_argument("--edges", default="./../../dataset/Louvain/graph/test.gr")
    parser.add_argument("--server-id", type=int, required=True)
    parser.add_argument("--server-count", type=int, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--server-endpoints", nargs="+", required=True)
    parser.add_argument("--request-timeout", type=float, default=5.0)
    parser.add_argument(
        "--cache-policy",
        choices=["none", "memo", "lru", "arc", "bfs-lru", "degree-lru", "hybrid-lru"],
        default="none",
        help=(
            "bfs-lru: Strategy A — BFS距離ベース。"
            "degree-lru: Strategy C — 次数（ハブ性）ベース。"
            "hybrid-lru: Strategy H — BFS距離 × 次数Tier の10段階複合。"
        ),
    )
    parser.add_argument("--cache-capacity", type=int, default=0)
    parser.add_argument("--cache-node-weight", type=int, default=1)
    parser.add_argument("--cache-edge-weight", type=int, default=2)
    parser.add_argument("--cache-start-weight", type=int, default=1)
    parser.add_argument("--cache-allow-weight", type=int, default=0)
    parser.add_argument("--cache-deny-weight", type=int, default=0)
    parser.add_argument(
        "--cache-hub-threshold",
        type=int,
        default=10,
        help="degree-lru: ハブ判定の次数閾値。この値以上の次数をハブとして保護する。",
    )
    parser.add_argument(
        "--cache-bfs-far-threshold",
        type=int,
        default=6,
        help="bfs-lru: この距離以上のエンティティを far group として優先的に追い出す。",
    )
    parser.add_argument(
        "--cache-bfs-prefetch-depth",
        type=int,
        default=2,
        help="bfs-lru: walk開始時にプリフェッチするBFS深さ。",
    )
    parser.add_argument("--auth-file", type=str, default=None)
    parser.add_argument("--node-to-starts-file", type=str, default=None)
    parser.add_argument("--dump-auth", action="store_true")
    parser.add_argument("--cache-key-mode", choices=["full", "node_only"], default="full")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.cache_policy in {"lru", "arc", "bfs-lru", "degree-lru", "hybrid-lru"} and args.cache_capacity <= 0:
        raise ValueError("--cache-capacity must be positive for this policy")

    edge_path = Path(args.edges).expanduser()
    if not edge_path.exists():
        raise FileNotFoundError(f"Edge list not found: {edge_path}")

    edges = load_edge_list(edge_path)
    base_partitioner = ModuloPartitioner(args.server_count)
    partitioner: Any = base_partitioner

    filtered_node_to_starts: Dict[NodeOrEdgeId, Set[int]] = {}
    owned_hints: Set[NodeOrEdgeId] = set()
    owner_map: Dict[str, int] = {}

    if args.node_to_starts_file:
        base_nts_path = Path(args.node_to_starts_file)
        nts_path = resolve_node_to_starts_path(base_nts_path, args.server_id)
        loaded_nts = load_node_to_starts_table(nts_path)
        filtered_node_to_starts = loaded_nts
        owned_hints = set(loaded_nts.keys())
        owner_map = build_owner_map_from_sibling_node_to_starts_files(nts_path)
        partitioner = StaticPartitioner(server_count=args.server_count, mapping=owner_map, fallback=base_partitioner)

    shard = GraphShard(
        edges,
        server_id=args.server_id,
        server_count=args.server_count,
        partitioner=partitioner,
        owned_hints=owned_hints,
        owned_hints_only=bool(args.owned_hints_only),
        owner_map=owner_map,
    )

    owned_nodes, owned_edges = split_owned_entities(shard.local_entities)
    print(f"[Server {args.server_id}] OWNED: nodes={len(owned_nodes)}, edges={len(owned_edges)}, total={len(shard.local_entities)}")

    cache_sizer = DecisionCacheSizer(
        node_weight=args.cache_node_weight,
        edge_weight=args.cache_edge_weight,
        allow_weight=args.cache_allow_weight,
        deny_weight=args.cache_deny_weight,
        start_weight=args.cache_start_weight,
    )

    server = GraphShardServer(
        host=args.host,
        port=args.port,
        shard=shard,
        endpoints=args.server_endpoints,
        request_timeout=args.request_timeout,
        cache_policy=args.cache_policy,
        cache_capacity=args.cache_capacity,
        cache_sizer=cache_sizer,
        cache_key_mode=args.cache_key_mode,
        hub_threshold=args.cache_hub_threshold,
        bfs_far_threshold=args.cache_bfs_far_threshold,
        bfs_prefetch_depth=args.cache_bfs_prefetch_depth,
    )
    server.edges_path = str(edge_path)

    if args.auth_file:
        auth_table = load_entity_auth_table(Path(args.auth_file))
        server.auth_table = filter_auth_table_for_shard(auth_table, shard.partitioner, shard.server_id)
        server.auth_file_path = str(Path(args.auth_file))

    if args.node_to_starts_file:
        server.node_to_starts = filtered_node_to_starts
        server.node_to_starts_path = str(nts_path)

    if owner_map:
        server.owner_map = owner_map

    if args.dump_auth:
        dump = {
            "server_id": server.server_id,
            "auth_table": {str(k): {"n": sorted(v.get("n", [])), "e": sorted(v.get("e", []))} for k, v in server.auth_table.items()},
            "node_to_starts": {str(k): sorted(list(v)) for k, v in server.node_to_starts.items()},
            "owner_map_size": len(server.owner_map),
        }
        dump_path = Path(f"auth_dump_server{server.server_id}.json")
        dump_path.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")

    def dump_access_stats() -> None:
        stats = {
            "access": dict(server.access_counter),
            "authorized": dict(server.authorized_counter),
            "authorization_attempts": dict(server.authorization_attempt_counter),
            "authorization_denied": dict(server.authorization_denied_counter),
            "transition": dict(server.transition_counter),
            "auth_time_total": server.auth_time_total,
            "auth_calls": server.auth_calls,
            "local_auth_calls": getattr(server, "local_auth_calls", 0),
            "remote_auth_calls": getattr(server, "remote_auth_calls", 0),
            "walk_time_total": server.walk_time_total,
            "walk_calls": server.walk_calls,
            "memory": summarize_tables(server),
            "cache_stats": server.authz_cache.stats() if hasattr(server.authz_cache, "stats") else {},
        }
        out_path = Path(f"access_stats_server{server.server_id}.json")
        out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"[Server {server.server_id}] auth summary: {server.auth_calls} calls, total {server.auth_time_total:.6f}s")
        print(f"[Server {server.server_id}] Access stats saved to {out_path}")

    atexit.register(dump_access_stats)

    print(f"[Server {args.server_id}] policy={args.cache_policy} capacity={args.cache_capacity}")
    if args.cache_policy == "bfs-lru":
        print(f"[Server {args.server_id}]   far_threshold={args.cache_bfs_far_threshold} prefetch_depth={args.cache_bfs_prefetch_depth}")
    elif args.cache_policy == "degree-lru":
        print(f"[Server {args.server_id}]   hub_threshold={args.cache_hub_threshold}")
    elif args.cache_policy == "hybrid-lru":
        print(f"[Server {args.server_id}]   far_threshold={args.cache_bfs_far_threshold} hub_threshold={args.cache_hub_threshold} prefetch_depth={args.cache_bfs_prefetch_depth}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {args.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
