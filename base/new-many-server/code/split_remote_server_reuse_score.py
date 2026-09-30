#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import random
import re
import resource
import time
from collections import Counter, defaultdict, OrderedDict
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
# Utilities
"""
python3 base/auth-baseline-cache/split_remote_server_volume_base.py \
  --server-id 0 \
  --server-count 2 \
  --edges dataset/Louvain/graph/vldb.gr \
  --host 10.58.60.6 \
  --port 3000 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --node-to-starts-file base/auth-many-server/data/splits/vldb/0.3/node_to_starts_server0.json \
  --owned-hints-only \
  --cache-policy none \
  --cache-capacity 100
#   --cache-policy arc
#   --cache-policy lru 
#   --cache-policy none
#   --cache-policy memo
  
python3 base/auth-baseline-cache/split_remote_server_volume_base.py \
  --server-id 1 \
  --server-count 2 \
  --edges dataset/Louvain/graph/vldb.gr \
  --host 10.58.60.11 \
  --port 3000 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --node-to-starts-file base/auth-many-server/data/splits/vldb/0.3/node_to_starts_server1.json \
  --owned-hints-only \
  --cache-policy none \
  --cache-capacity 100
#   --cache-policy arc
  
python3 base/auth-baseline-cache/split_controller.py \
  --servers 2 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --start-node 1 \
  --walks 10 \
  --alpha 0.1 \
  --seed 42
"""


# ---------------------------------------------------------------------------


class DecisionCacheSizer:
    """Abstract-unit sizer for authorization decision cache entries."""

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
    """Mapping-like interface for authorization decision caches."""

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
    def __contains__(self, key: Tuple[int, str]) -> bool:
        return False

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        raise KeyError(key)

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        return None

    def __len__(self) -> int:
        return 0

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update({"policy": "none"})
        return base


class UnlimitedDecisionCache(BaseDecisionCache):
    def __init__(self, sizer: Optional[DecisionCacheSizer] = None) -> None:
        self.data: Dict[Tuple[int, str], bool] = {}
        self.sizer = sizer or DecisionCacheSizer()

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.data

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        return self.data[key]

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        self.data[key] = bool(value)

    def __len__(self) -> int:
        return len(self.data)

    def current_weight(self) -> int:
        return sum(self.sizer.entry_weight(k, v) for k, v in self.data.items())

    def storage_objects(self) -> Dict[str, Any]:
        return {"data": self.data}

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "memo",
                "entries": len(self.data),
                "insertions": len(self.data),
                "updates": 0,
                "evictions": 0,
                "rejected_too_large": 0,
            }
        )
        return base


class LRUDecisionCache(BaseDecisionCache):
    def __init__(
        self, capacity: int, sizer: Optional[DecisionCacheSizer] = None
    ) -> None:
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

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.data

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        value = self.data[key]
        self.data.move_to_end(key)
        return value

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
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

    def __len__(self) -> int:
        return len(self.data)

    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def storage_objects(self) -> Dict[str, Any]:
        return {
            "data": self.data,
            "weights": self.weights,
            "total_weight": self.total_weight,
        }

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "lru",
                "entries": len(self.data),
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
            }
        )
        return base

    def _evict_until_fit(self) -> None:
        while self.total_weight > self.capacity and self.data:
            old_key, _ = self.data.popitem(last=False)
            self.total_weight -= self.weights.pop(old_key, 0)
            self.evictions += 1


class ARCDecisionCache(BaseDecisionCache):
    """Weighted ARC cache for authorization decisions.

    Capacity is managed in abstract weight units for real cached entries.
    T1/T2 store real cache entries, while B1/B2 are ghost lists storing only keys.
    Ghost lists are also bounded in abstract units.
    """

    def __init__(
        self, capacity: int, sizer: Optional[DecisionCacheSizer] = None
    ) -> None:
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

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.t1 or key in self.t2

    def __getitem__(self, key: Tuple[int, str]) -> bool:
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

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
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
            delta = (
                key_weight
                if self.b1_weight >= self.b2_weight
                else max(key_weight, self.b2_weight // max(1, self.b1_weight))
            )
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
            delta = (
                key_weight
                if self.b2_weight >= self.b1_weight
                else max(key_weight, self.b1_weight // max(1, self.b2_weight))
            )
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

    def __len__(self) -> int:
        return len(self.t1) + len(self.t2)

    def current_weight(self) -> int:
        return self.t1_weight + self.t2_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def storage_objects(self) -> Dict[str, Any]:
        return {
            "t1": self.t1,
            "t2": self.t2,
            "b1": self.b1,
            "b2": self.b2,
            "t1_weights": self.t1_weights,
            "t2_weights": self.t2_weights,
            "b1_weights": self.b1_weights,
            "b2_weights": self.b2_weights,
            "t1_weight": self.t1_weight,
            "t2_weight": self.t2_weight,
            "b1_weight": self.b1_weight,
            "b2_weight": self.b2_weight,
            "p": self.p,
        }

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "arc",
                "entries": len(self),
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
                "b1_hits": self.b1_hits,
                "b2_hits": self.b2_hits,
                "p": self.p,
                "t1_entries": len(self.t1),
                "t2_entries": len(self.t2),
                "b1_entries": len(self.b1),
                "b2_entries": len(self.b2),
                "t1_weight": self.t1_weight,
                "t2_weight": self.t2_weight,
                "b1_weight": self.b1_weight,
                "b2_weight": self.b2_weight,
            }
        )
        return base

    def _replace(self, incoming_key: Tuple[int, str]) -> None:
        incoming_in_b2 = incoming_key in self.b2
        if self.t1 and (
            (incoming_in_b2 and self.t1_weight <= self.p) or (self.t1_weight > self.p)
        ):
            self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
        elif self.t2:
            self._move_real_lru_to_ghost(self.t2, self.t2_weights, "t2")
        elif self.t1:
            self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
        self._trim_ghosts()

    def _ensure_space_for_new_entry(
        self, incoming_weight: int, incoming_key: Tuple[int, str]
    ) -> None:
        while self.current_weight() + incoming_weight > self.capacity and (
            self.t1 or self.t2
        ):
            self._replace(incoming_key)
        self._trim_ghosts()

    def _evict_real_until_fit(self, prefer_t1: bool) -> None:
        while self.current_weight() > self.capacity and (self.t1 or self.t2):
            if prefer_t1 and self.t1:
                self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
            elif self.t2:
                self._move_real_lru_to_ghost(self.t2, self.t2_weights, "t2")
            elif self.t1:
                self._move_real_lru_to_ghost(self.t1, self.t1_weights, "t1")
            self._trim_ghosts()

    def _move_real_lru_to_ghost(
        self, store: OrderedDict, weights: Dict[Tuple[int, str], int], source: str
    ) -> None:
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

    def _ghost_pop(
        self,
        store: OrderedDict,
        weights: Dict[Tuple[int, str], int],
        key: Tuple[int, str],
        which: str,
    ) -> None:
        store.pop(key, None)
        removed = weights.pop(key, 0)
        if which == "b1":
            self.b1_weight -= removed
        else:
            self.b2_weight -= removed

    def _trim_ghosts(self) -> None:
        while self.b1_weight > self.capacity and self.b1:
            key, _ = self.b1.popitem(last=False)
            self.b1_weight -= self.b1_weights.pop(key, 0)
        while self.b2_weight > self.capacity and self.b2:
            key, _ = self.b2.popitem(last=False)
            self.b2_weight -= self.b2_weights.pop(key, 0)
        while (
            self.current_weight() + self.b1_weight + self.b2_weight > 2 * self.capacity
        ):
            if self.b2:
                key, _ = self.b2.popitem(last=False)
                self.b2_weight -= self.b2_weights.pop(key, 0)
            elif self.b1:
                key, _ = self.b1.popitem(last=False)
                self.b1_weight -= self.b1_weights.pop(key, 0)
            else:
                break


# ===========================================================================
# 提案手法用 frozen cache
# ===========================================================================
class FrozenPrefetchCache(BaseDecisionCache):
    """
    BFS-prefetch および BFS-score 用のキャッシュ。
    - prefetch フェーズ (controller からの /cache/prefetch コール) で初期エントリを投入
    - walk フェーズでは初期エントリへの hit を最大化しつつ、
      miss 時のライブ判定結果も保持して以降の同一 (start, entity) の再 hit を確保。

    ※ 当初は walk 中の書き込みを完全禁止していたが、prefetch でカバーできなかった
       エントリの ライブ判定結果まで捨ててしまうと auth_calls が削減されない
       (むしろ memo より悪化する) ため、walk 中も memo 同様に追加可能とした。
       提案手法の効果は「prefetch 由来の hit」 (`auth_cache_hit_prefetched`) で測る。
    """

    def __init__(self, sizer: Optional[DecisionCacheSizer] = None) -> None:
        self._sizer = sizer or DecisionCacheSizer()
        self.store: Dict[Tuple[int, str], bool] = {}
        self._weight = 0
        self._frozen = False  # ステータス参照用 (書き込みは抑制しない)

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.store

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        return self.store[key]

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        if key not in self.store:
            self._weight += self._sizer.entry_weight(key, value)
        self.store[key] = value

    def __len__(self) -> int:
        return len(self.store)

    def current_weight(self) -> int:
        return self._weight

    def max_weight(self) -> Optional[int]:
        return None

    def storage_objects(self) -> Dict[str, Any]:
        return {"store": self.store, "weight": self._weight}

    def stats(self) -> Dict[str, Any]:
        return {
            "policy": "frozen_prefetch",
            "capacity": None,
            "size": len(self.store),
            "weight": self._weight,
            "frozen": self._frozen,
        }

    def freeze(self) -> None:
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    def is_frozen(self) -> bool:
        return self._frozen


class PPRBoundedCache(BaseDecisionCache):
    """
    提案手法本命: 構造prior × 頻度の加法スコアによる容量有界キャッシュ。

    退去優先度 (大きいほど残す):
        H(e) = L + V(e) / size(e)
      - combine="add" (本命 ppr_demand):
            V(e) = max(0, freq(e) - delta) + theta * w_prior(e)
          ・実際に来た回数 (freq) が貯まれば prior を上書きする加法形 (rich-get-richer)。
          ・delta : 初回ヒットの割引 (= 「2回目で本物」, 1-hit-wonder 抑制)。
          ・theta : 構造prior の強さ (疑似カウント)。
      - combine="mul" (レガシー ppr_gdsf): V(e) = freq(e) × w_prior(e)
      - w_prior(e) : (1−α)^dist × deg(e)。
          ・use_hop_prior=True (ppr_demand): dist = walker の hops_done (= 歩いた歩数)。
            初回ミス挿入時に server が set_prior() で固定。BFS/prefetch 不要。
          ・use_hop_prior=False (ppr_gdsf): prior_fn(BFS距離) 経由で毎回算出。
      - L     : aging クロック (退去エントリの H に更新 = LRU 的 recency)
      - size  : DecisionCacheSizer の重み (node=1, edge=2)
    容量超過時は argmin H を退去し L←H_evicted。entries は容量 N で有界。

    prior 未設定時は w_prior=1.0 (= サイズ加重 LFU/LRU に縮退)。
    """

    def __init__(
        self,
        capacity: int,
        sizer: Optional[DecisionCacheSizer] = None,
        prior_fn: Optional[Any] = None,
        theta: float = 1.0,
        delta: float = 0.0,
        combine: str = "mul",
        use_hop_prior: bool = False,
        lambda_learn: float = 1.0,
        # ↓ reuse_score (multiplicative reuse-value) 用の飽和定数 ↓
        cf: float = 5.0,    # C_F: freq の飽和点 (中央値想定)
        cl: float = 5.0,    # C_L: learn の飽和点
        cd: float = 10.0,   # C_D: degree の飽和点 (graph 中央次数想定)
        beta: float = 1.0,  # β: H 項 (hop) の指数
        gamma: float = 1.0, # γ: D 項 (degree) の指数
        rho: float = 0.0,   # ρ: R 項 (recency) の指数。0=recency無効(=現行と一致)
        cr: float = 10.0,   # C_R: recency 飽和点 (再アクセス距離の中央値を想定)
    ) -> None:
        if capacity <= 0:
            raise ValueError("PPRBoundedCache capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer or DecisionCacheSizer()
        self.prior_fn = prior_fn
        self.theta = float(theta)
        self.delta = float(delta)
        self.lambda_learn = float(lambda_learn)  # 学習係数 λ の強さ
        # combine モードに "reuse_score" を追加
        self.combine = combine if combine in ("add", "mul", "reuse_score") else "mul"
        self.use_hop_prior = bool(use_hop_prior)
        # reuse_score: 飽和定数 (各特徴量を [1,2) に正規化するためのスケール係数)
        self.cf = float(cf)
        self.cl = float(cl)
        self.cd = float(cd)
        self.beta = float(beta)
        self.gamma = float(gamma)
        # recency (LRU 的な最近性) を第5因子として乗算合成する。
        #   R = 1 + C_R/(age+C_R) ∈ (1,2] , age = clock − last_access(key)
        #   V ← V × R^ρ   (ρ=0 のとき R^0=1 で現行スコアに完全一致)
        # R は退去比較時に「現在の clock」で動的評価する (静的保存しない)。
        # → 挿入時に焼き込むと全キー age=0→R≈2 となり recency が死ぬため。
        # 乗算かつ R∈(1,2] で有界なので、GDSF 加法クロックのような
        # 値のドリフト (青天井→桁落ち/価値項の埋没) は原理的に起きない。
        self.rho = float(rho)
        self.cr = float(cr)
        self.clock = 0  # 論理アクセス時計 (決定的。壁時計は再現性のため不使用)
        self.last_access: Dict[Tuple[int, str], int] = {}
        self.store: Dict[Tuple[int, str], bool] = {}
        self.freq: Dict[Tuple[int, str], int] = {}
        self.H: Dict[Tuple[int, str], float] = {}
        self.weights: Dict[Tuple[int, str], int] = {}
        self.prior_val: Dict[Tuple[int, str], float] = {}  # 挿入時固定の hop prior
        # learn(e): Phase1(学習RW) で観測したアクセス回数。退去しても消さない
        # (ghost係数として残し、再挿入時に head start を与える)。
        self.learn: Dict[Tuple[int, str], float] = {}
        self.total_weight = 0
        self.L = 0.0
        self.insertions = 0
        self.updates = 0
        self.evictions = 0
        self.rejected_too_large = 0
        self.seeded = 0  # prefetch で投入した件数

    def _prior(self, key: Tuple[int, str]) -> float:
        # use_hop_prior: 挿入時に固定した hop prior を優先
        if self.use_hop_prior:
            p = self.prior_val.get(key)
            if p is not None:
                return p if p > 0 else 1e-12
        if self.prior_fn is None:
            return 1.0
        try:
            p = float(self.prior_fn(key))
        except Exception:
            p = 1.0
        return p if p > 0 else 1e-12

    def set_prior(self, key: Tuple[int, str], prior: float) -> None:
        """挿入時に hop ベースの w_prior を固定保存する (ppr_demand 用)。"""
        self.prior_val[key] = prior if prior > 0 else 1e-12

    def bump_prior(self, key: Tuple[int, str], prior: float) -> None:
        """再遭遇時、より小さい hops(=より大きい prior) を観測したら更新 (最短hop追跡)。"""
        prior = prior if prior > 0 else 1e-12
        cur = self.prior_val.get(key)
        if cur is None or prior > cur:
            self.prior_val[key] = prior

    def _h_value(self, key: Tuple[int, str], size: int) -> float:
        """退去優先度 H を計算する。
        - reuse_score モード: H = V/size (L=0 相当、LRU aging を無視)
        - それ以外        : H = L + V/size (従来 GDSF 形式)
        """
        v_over_s = self._score(key) / size
        if self.combine == "reuse_score":
            # LRU 的 aging (L) を無視し、純粋に V ベースで退去する
            return v_over_s
        return self.L + v_over_s

    def set_learn(self, key: Tuple[int, str], value: float) -> None:
        """学習係数 learn(e) を固定保存する。Phase1 の観測アクセス回数を加法項として注入。
        store にあれば H を即再計算 (退去順を更新)。なければ次回挿入時に有効化される。
        """
        self.learn[key] = float(value) if value > 0 else 0.0
        if key in self.store:
            size = self.weights.get(key) or 1
            self.H[key] = self._h_value(key, size)

    def _score(self, key: Tuple[int, str]) -> float:
        """V(e):
          - combine="add"         : V = max(0, freq−δ) + θ·w_prior + λ·learn   (旧 ppr_demand)
          - combine="mul"         : V = freq × w_prior                          (旧 ppr_gdsf)
          - combine="reuse_score" : V = O^θ × L^λ × H^β × D^γ                   (新提案)
                                    (recency 因子 R^ρ は _score には含めず、退去比較時に
                                     _effective_h で現在時刻から動的に乗じる。ρ=0 で無効)

        reuse_score (multiplicative reuse-value score):
            各特徴量を飽和関数で [1, 2) に正規化し、すべて掛け算で合成。
            観測 (O), 学習 (L), 距離 (H), 次数 (D) は対等な寄与を持つ。

            O = 1 + freq  / (freq  + C_F)        観測項 (オンライン頻度)
            L = 1 + learn / (learn + C_L)        学習項 (Phase1 観測)
            H = 1 + (1−α)^hop                    距離項 (PPR の自然形)
            D = 1 + deg   / (deg   + C_D)        次数項
            (各因子 ∈ [1, 2))

            θ, λ, β, γ : 各項の重要度 (= 指数による重み付け)。実験で最適化する対象。
            C_F, C_L, C_D : 飽和点 (= スケール正規化定数)。データ統計から自動決定。
        """
        f = self.freq.get(key, 1)
        p = self._prior(key)
        if self.combine == "reuse_score":
            l = self.learn.get(key, 0.0)
            # self._prior(key) は _hop_prior で H_base × D_base を返す (後述)
            # ここで指数 β, γ を適用したものは _prior 内で計算済みとする実装も可能だが、
            # 解釈性のため O, L だけここで指数化する。
            o_term = 1.0 + f / (f + self.cf) if (f + self.cf) > 0 else 1.0
            l_term = 1.0 + l / (l + self.cl) if (l + self.cl) > 0 else 1.0
            # p は既に H^β × D^γ になっている (_hop_prior 参照)
            return (o_term ** self.theta) * (l_term ** self.lambda_learn) * p
        if self.combine == "add":
            l = self.learn.get(key, 0.0)
            return max(0.0, f - self.delta) + self.theta * p + self.lambda_learn * l
        return f * p

    def _recency_factor(self, key: Tuple[int, str]) -> float:
        """recency 因子 R^ρ を「現在の clock」で動的に評価する。
            R = 1 + C_R/(age + C_R) ∈ (1, 2] ,  age = clock − last_access(key)
            (age=0 → R=2 最近 / age→∞ → R→1 古い)
        ρ=0 または reuse_score 以外では 1.0 を返し、現行挙動に完全一致させる。
        static 保存しないのは、触っていないキーも clock 進行で古くなる (age 増加)
        ため。挿入時に焼き込むと全キー R≈2 となり recency が無意味化する。
        """
        if self.combine != "reuse_score" or self.rho <= 0.0:
            return 1.0
        age = self.clock - self.last_access.get(key, self.clock)
        r = 1.0 + self.cr / (age + self.cr)
        return r ** self.rho

    def _effective_h(self, key: Tuple[int, str]) -> float:
        """退去比較用の実効優先度 = H(recency 抜き) × R^ρ(現在時刻)。
        H = V_static/size なので (V_static × R^ρ)/size と等価。"""
        return self.H[key] * self._recency_factor(key)

    def __contains__(self, key: Tuple[int, str]) -> bool:
        return key in self.store

    def __getitem__(self, key: Tuple[int, str]) -> bool:
        # ヒット: freq を増やし H を再計算 (観測補正)
        self.freq[key] = self.freq.get(key, 1) + 1
        self.clock += 1
        self.last_access[key] = self.clock  # recency 更新 (ρ=0 なら退去で未使用)
        size = self.weights.get(key) or 1
        self.H[key] = self._h_value(key, size)
        return self.store[key]

    def __setitem__(self, key: Tuple[int, str], value: bool) -> None:
        value = bool(value)
        w = self.sizer.entry_weight(key, value)
        if w > self.capacity:
            self.rejected_too_large += 1
            return
        self.clock += 1
        self.last_access[key] = self.clock  # recency 更新 (挿入/更新とも最新化)
        if key in self.store:
            old_w = self.weights.get(key, 0)
            self.store[key] = value
            self.weights[key] = w
            self.total_weight += w - old_w
            self.H[key] = self._h_value(key, w)
            self.updates += 1
            self._evict_until_fit()
            return
        self.store[key] = value
        self.freq[key] = 1
        self.weights[key] = w
        self.total_weight += w
        self.H[key] = self._h_value(key, w)
        self.insertions += 1
        self._evict_until_fit()

    def __len__(self) -> int:
        return len(self.store)

    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def _evict_until_fit(self) -> None:
        # ρ>0 の reuse_score のみ、recency を現在時刻で動的評価して犠牲者を選ぶ。
        # それ以外は従来どおり静的 H で選ぶ (ρ=0 は既存挙動とビット一致)。
        use_recency = (self.combine == "reuse_score" and self.rho > 0.0)
        while self.total_weight > self.capacity and self.store:
            if use_recency:
                victim = min(self.store, key=self._effective_h)
            else:
                victim = min(self.H, key=self.H.__getitem__)
            h_v = self.H.pop(victim)
            # reuse_score モードは L を使わないので aging 更新もしない
            if self.combine != "reuse_score" and h_v > self.L:
                self.L = h_v  # GDSF aging (旧 ppr_demand / ppr_gdsf のみ)
            self.total_weight -= self.weights.pop(victim, 0)
            self.store.pop(victim, None)
            self.freq.pop(victim, None)
            self.prior_val.pop(victim, None)
            self.last_access.pop(victim, None)
            self.evictions += 1

    def storage_objects(self) -> Dict[str, Any]:
        return {
            "store": self.store,
            "freq": self.freq,
            "H": self.H,
            "weights": self.weights,
            "total_weight": self.total_weight,
        }

    def stats(self) -> Dict[str, Any]:
        base = super().stats()
        base.update(
            {
                "policy": "ppr_demand" if self.combine == "add" else "ppr_gdsf",
                "combine": self.combine,
                "theta": self.theta,
                "delta": self.delta,
                "lambda_learn": self.lambda_learn,
                "use_hop_prior": self.use_hop_prior,
                "entries": len(self.store),
                "learn_entries": len(self.learn),
                "insertions": self.insertions,
                "updates": self.updates,
                "evictions": self.evictions,
                "rejected_too_large": self.rejected_too_large,
                "seeded": self.seeded,
                "aging_L": self.L,
            }
        )
        return base


def build_authz_cache(
    policy: str,
    capacity: int,
    sizer: Optional[DecisionCacheSizer] = None,
    theta: float = 1.0,
    delta: float = 0.0,
    lambda_learn: float = 1.0,
    # ↓ reuse_score 用 (legacy ポリシーには無関係) ↓
    beta: float = 1.0,
    gamma: float = 1.0,
    cf: float = 5.0,
    cl: float = 5.0,
    cd: float = 10.0,
    rho: float = 0.0,   # reuse_score: recency 指数 ρ (0=無効=現行)
    cr: float = 10.0,   # reuse_score: recency 飽和点 C_R
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
    # 提案手法
    if policy_name in ("bfs_prefetch", "bfs_score"):
        return FrozenPrefetchCache(sizer=sizer)
    if policy_name == "ppr_gdsf":
        # レガシー: prefetch + BFS prior + 乗法
        return PPRBoundedCache(capacity, sizer=sizer, combine="mul")
    if policy_name == "ppr_demand":
        # 本命: prefetch なし + hop prior + 加法
        #   V = max(0, freq−δ) + θ·w_prior + λ·learn
        # learn(e) は Phase1 学習で注入する加法の学習係数 (リセットなしで持ち越し)。
        return PPRBoundedCache(
            capacity,
            sizer=sizer,
            theta=theta,
            delta=delta,
            combine="add",
            use_hop_prior=True,
            lambda_learn=lambda_learn,
        )
    if policy_name == "reuse_score":
        # 新提案: 多項式 multiplicative reuse-value score
        #   V = O^θ × L^λ × H^β × D^γ   (各因子 [1, 2) に正規化)
        # 観測 (O), 学習 (L), 距離 (H), 次数 (D) を対等な構造で合成。
        # θ, λ, β, γ は重要度パラメータ (実験対象)、
        # C_F, C_L, C_D は飽和定数 (データ統計から決定)。
        return PPRBoundedCache(
            capacity,
            sizer=sizer,
            theta=theta,
            delta=delta,            # reuse_score では未使用 (互換性のため受け取るだけ)
            lambda_learn=lambda_learn,
            combine="reuse_score",
            use_hop_prior=True,
            cf=cf, cl=cl, cd=cd,
            beta=beta, gamma=gamma,
            rho=rho, cr=cr,
        )
    raise ValueError(f"Unsupported cache policy: {policy}")


# ===========================================================================
# 提案手法用のヘルパ
# ===========================================================================
def _bfs_distances_from(
    shard: Any, start: int, max_hops: int = 30, node_limit: Optional[int] = None
) -> Dict[Any, int]:
    """
    shard の neighbor_map を用いて、start_node から各 entity までの bipartite BFS 距離を計算。
    node→edge→node と交互に進むので、距離は bipartite hop 数 (node は偶数, edge は奇数)。
    max_hops 以遠は到達しない。node_limit 件に達したら探索を打ち切る (prefetch コスト上限)。
    """
    from collections import deque
    dist: Dict[Any, int] = {start: 0}
    q = deque([start])
    while q:
        if node_limit is not None and len(dist) >= node_limit:
            break
        u = q.popleft()
        du = dist[u]
        if du >= max_hops:
            continue
        neighbors = shard.neighbor_map.get(u, [])
        for n in neighbors:
            v = getattr(n, "node_id", n)
            # int 化 (str ノードIDが混じる可能性)
            try:
                v_key = int(v) if not isinstance(v, str) or v.isdigit() else v
            except Exception:
                v_key = v
            if v_key not in dist:
                dist[v_key] = du + 1
                q.append(v_key)
    return dist


def _local_allowed_for_prefetch(server: Any, start_node: int, target: Any) -> bool:
    """サーバ内部の auth_table または node_to_starts を見て allow/deny を判定。
    _is_locally_allowed と同じロジック (副作用なし)。
    """
    # node_to_starts 方式 (deny list)
    if hasattr(server, "node_to_starts") and server.node_to_starts:
        denied_starts = server.node_to_starts.get(target, None)
        if denied_starts is None:
            # 試しに int / str 両方で
            denied_starts = server.node_to_starts.get(
                int(target) if isinstance(target, str) and target.isdigit() else target,
                set(),
            )
        if not denied_starts:
            return True
        return int(start_node) not in denied_starts
    return True


def _check_remote_for_prefetch(
    server: Any, owner_sid: Optional[int], start_node: int, target: Any
) -> Optional[bool]:
    """prefetch 中の他サーバへの /authorize 問い合わせ。
    成功: True/False を返す
    失敗: None を返す (cache には入れない — walk 時に live で問い合わせる)
    """
    if owner_sid is None or owner_sid < 0 or owner_sid >= len(server.endpoints):
        return None
    if owner_sid == server.server_id:
        return _local_allowed_for_prefetch(server, start_node, target)
    url = f"{server.endpoints[owner_sid].rstrip('/')}/authorize"
    payload = {"entity": target, "start_node": int(start_node)}
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib_request.urlopen(req, timeout=server.request_timeout) as resp:
            body = json.loads(resp.read())
        return bool(body.get("allowed"))
    except Exception as e:
        # 失敗時はキャッシュしない (walk 時にライブで再度問い合わせる)
        if not hasattr(server, "_prefetch_remote_fail_count"):
            server._prefetch_remote_fail_count = 0
        server._prefetch_remote_fail_count += 1
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def cache_entity_key(entity: Any, node_only: bool = False) -> str:
    # node: "node:12" / edge: "edge_1_2"
    if isinstance(entity, int):
        return f"node:{entity}"
    if isinstance(entity, str):
        if entity.startswith("edge_"):
            if node_only:
                # edge_U_V → node:U (最小ノードIDに縮退)
                parts = entity.split("_", 2)
                return f"node:{parts[1]}" if len(parts) >= 2 else entity
            return entity
        if entity.isdigit():
            return f"node:{entity}"
        return entity
    return str(entity)


def entity_from_cache_key(entity_key: Any) -> NodeOrEdgeId:
    """cache_entity_key の逆変換: 'node:12'->12, 'edge_1_2'->'edge_1_2', '5'->5"""
    if isinstance(entity_key, str):
        if entity_key.startswith("node:"):
            rest = entity_key[5:]
            try:
                return int(rest)
            except Exception:
                return rest
        if entity_key.startswith("edge_"):
            return entity_key
        if entity_key.isdigit():
            return int(entity_key)
    return entity_key


def make_ppr_prior_fn(server: Any) -> Any:
    """
    PPRBoundedCache 用の prior_fn を生成。
        w_prior(e) = (1−α)^(β·bfs_dist(s,e)) × deg(e)^γ
    server に保持した _ppr_dist / _ppr_alpha / _ppr_max_dist と
    shard.neighbor_map (次数)、β=ppr_prior_hop_exp / γ=ppr_prior_deg_exp を参照する closure。
    """
    dist = getattr(server, "_ppr_dist", {}) or {}
    alpha = float(getattr(server, "_ppr_alpha", 0.0) or 0.0)
    one_minus_alpha = max(0.0, 1.0 - alpha)
    max_dist = int(getattr(server, "_ppr_max_dist", 0) or 0)
    beta = float(getattr(server, "ppr_prior_hop_exp", 1.0))
    gamma = float(getattr(server, "ppr_prior_deg_exp", 1.0))
    nm = server.shard.neighbor_map

    def prior_fn(ckey: Tuple[int, str]) -> float:
        _, ekey = ckey
        ent = entity_from_cache_key(ekey)
        d = dist.get(ent)
        if d is None:
            d = max_dist + 1  # BFS 範囲外は floor prior
        deg = len(nm.get(ent, ())) or 1
        return (one_minus_alpha ** (beta * d)) * (float(deg) ** gamma)

    return prior_fn


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
    """
    Linux: /proc/self/status の VmRSS(kB) を読む。
    取れなければ ru_maxrss を返す（LinuxではKB）。
    """
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
    """
    Pythonオブジェクトの概算サイズ(byte)を再帰的に推定する。
    比較目的なので厳密でなくてOK。
    """
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

    # cache（実際に使っているのは authz_cache）
    authz_cache = getattr(server, "authz_cache", None)
    cache_entries = len(authz_cache) if authz_cache is not None else 0

    # counters（bytes推定対象としてまとめる）
    counters_bundle = {}
    for name in [
        "access_counter",
        "authorized_counter",
        "authorization_attempt_counter",
        "authorization_denied_counter",
        "transition_counter",
    ]:
        c = getattr(server, name, None)
        if c is not None:
            counters_bundle[name] = c

    graph_entities = len(nm)
    graph_total_neighbors = sum(len(v) for v in nm.values())

    auth_entities = len(nts)
    auth_total_starts = sum(len(v) for v in nts.values())

    counters_total_keys = sum(
        len(c) for c in counters_bundle.values() if hasattr(c, "__len__")
    )
    access_total = sum(int(v) for v in getattr(server, "access_counter", {}).values())
    authorized_total = sum(
        int(v) for v in getattr(server, "authorized_counter", {}).values()
    )
    attempts_total = sum(
        int(v) for v in getattr(server, "authorization_attempt_counter", {}).values()
    )
    denied_total = sum(
        int(v) for v in getattr(server, "authorization_denied_counter", {}).values()
    )
    transition_total = sum(
        int(v) for v in getattr(server, "transition_counter", {}).values()
    )

    auth_table = getattr(server, "auth_table", {}) or {}
    auth_table_entries = len(auth_table)
    auth_table_nodes = 0
    auth_table_edges = 0
    for entry in auth_table.values():
        nodes = entry.get("n", set()) if isinstance(entry, dict) else set()
        edges = entry.get("e", set()) if isinstance(entry, dict) else set()
        auth_table_nodes += len(nodes) if hasattr(nodes, "__len__") else 0
        auth_table_edges += len(edges) if hasattr(edges, "__len__") else 0

    # 入力ファイルサイズ
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
        "cache_weight": (
            authz_cache.current_weight()
            if hasattr(authz_cache, "current_weight")
            else None
        ),
        "cache_capacity": (
            authz_cache.max_weight() if hasattr(authz_cache, "max_weight") else None
        ),
        "counters_total_keys": counters_total_keys,
        "counters_total": {
            "access": access_total,
            "authorized": authorized_total,
            "attempts": attempts_total,
            "denied": denied_total,
            "transition": transition_total,
        },
        # ★追加：推定bytes（比較用）
        "bytes_est": {
            "neighbor_map": deep_getsizeof(nm),
            "node_to_starts": deep_getsizeof(nts),
            "owner_map": deep_getsizeof(owner_map),
            "auth_table": deep_getsizeof(auth_table),
            "authz_cache": deep_getsizeof(
                authz_cache.storage_objects()
                if hasattr(authz_cache, "storage_objects")
                else authz_cache
            ),
            "counters": deep_getsizeof(counters_bundle),
        },
        # ★追加：参照ファイル（ディスク）
        "files": {
            "edges": {"path": edges_path, "bytes": safe_file_size(edges_path)},
            "node_to_starts": {"path": nts_path, "bytes": safe_file_size(nts_path)},
            "auth_file": {"path": auth_path, "bytes": safe_file_size(auth_path)},
        },
    }


# ---------------------------------------------------------------------------
# Partitioners
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
    def __init__(
        self,
        server_count: int,
        mapping: Dict[str, int],
        fallback: Optional[ModuloPartitioner] = None,
    ) -> None:
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
# Shard
# ---------------------------------------------------------------------------
class GraphShard:
    """
    Bipartite expansion: node <-> edge_entity(edge_u_v) <-> node

    目的:
      - 隣接(neighbor_map)は「全体グラフ(エッジリスト)」から必ず構築する
      - 所有(local_entities)は別に決める（owned_hints_only / owner_map）
      - get_neighbors は
          * グラフに存在しない entity -> None
          * 存在するが次数0 -> []
        を返して、原因切り分けができるようにする
    """

    def __init__(
        self,
        edges: Sequence[Tuple[int, int]],
        server_id: int,
        server_count: int,
        partitioner: Optional[Any] = None,
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

        neigh = list(self.neighbor_map.get(ent, []))
        return neigh


# ---------------------------------------------------------------------------
# Auth tables
# ---------------------------------------------------------------------------
def load_entity_auth_table(
    path: Optional[Union[str, Path]],
) -> Dict[int, Dict[str, Set[Any]]]:
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


def load_node_to_starts_table(
    path: Optional[Union[str, Path]],
) -> Dict[NodeOrEdgeId, Set[int]]:
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


def build_owner_map_from_sibling_node_to_starts_files(
    base_path: Path,
) -> Dict[str, int]:
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
                print(
                    f"[WARN] entity {key} appears in multiple files: {owner_map[key]} and {sid}"
                )
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
    raise FileNotFoundError(
        f"node_to_starts file not found. Tried: {[str(c) for c in candidates]}"
    )


def filter_auth_table_for_shard(
    auth_table: Dict[int, Dict[str, Set[Any]]],
    partitioner: Any,
    server_id: int,
) -> Dict[int, Dict[str, Set[Any]]]:
    filtered: Dict[int, Dict[str, Set[Any]]] = {}
    for start, entries in auth_table.items():
        local_nodes = {
            n
            for n in entries.get("n", set())
            if partitioner.assign_entity(n) == server_id
        }
        local_edges = {
            e
            for e in entries.get("e", set())
            if partitioner.assign_entity(e) == server_id
        }
        if local_nodes or local_edges:
            filtered[start] = {"n": local_nodes, "e": local_edges}
    return filtered


# ---------------------------------------------------------------------------
# RNG serialization
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
# PeerWalker
# ---------------------------------------------------------------------------
class PeerWalker:
    def __init__(
        self,
        shard: GraphShard,
        endpoints: Sequence[str],
        request_timeout: float = 5.0,
        max_hops: int = 100000,
        auth_table: Optional[Dict[int, Dict[str, Set[str]]]] = None,
        stats_collector: Optional[Any] = None,
        ppr_mode: bool = False,
        node_to_starts: Optional[Dict[NodeOrEdgeId, Set[int]]] = None,
        owner_map: Optional[Dict[str, int]] = None,
        server: Optional[Any] = None,
    ):
        self.shard = shard
        self.endpoints = [
            ep if ep.startswith(("http://", "https://")) else f"http://{ep}"
            for ep in endpoints
        ]
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

    # 小規模グラフ：正しいリスト：ここで認可処理を行う
    # def _is_locally_allowed(self, start_node: Optional[int], entity: Any) -> bool:
    #     if start_node is None:
    #         return False
    #     allowed_starts = self.node_to_starts.get(entity)
    #     return bool(allowed_starts and start_node in allowed_starts)

    # deny方式：NGリストに入っていなければOK
    def _is_locally_allowed(self, start_node: Optional[int], entity: Any) -> bool:
        if start_node is None:
            return False

        # entity_to_denied_starts:
        #   entity -> Set[start] (NGになっている start)
        denied_starts = self.node_to_starts.get(entity)

        # denied_starts が存在しない or 空 → 誰もNGにしていない → OK
        if not denied_starts:
            return True

        # start_node が NG に含まれていなければ OK
        return start_node not in denied_starts

    # こちらで遠方の処理を行う
    def _check_remote_authorization(
        self, target_server: int, start_node: Optional[int], entity: Any
    ) -> bool:
        if target_server < 0 or target_server >= len(self.endpoints):
            return False
        url = f"{self.endpoints[target_server].rstrip('/')}/authorize"
        payload = {"entity": entity, "start_node": start_node}
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
                body = json.loads(resp.read())
            return bool(body.get("allowed"))
        except Exception:
            return False

    # キャッシュなしの時のコピー
    # def _authorize_candidate(
    #     self, start_node: Optional[int], candidate: Dict[str, Any]
    # ) -> bool:
    #     target = candidate["node_id"]
    #     owner_sid: Optional[int] = None
    #     if self.owner_map:
    #         owner_sid = self.owner_map.get(str(target))
    #     if owner_sid is None:
    #         try:
    #             owner_sid = int(candidate.get("server_id"))
    #         except Exception:
    #             owner_sid = self.shard.partitioner.assign_entity(target)

    #     t0 = time.perf_counter()
    #     if owner_sid == self.shard.server_id:
    #         allowed = self._is_locally_allowed(start_node, target)
    #     else:
    #         allowed = self._check_remote_authorization(owner_sid, start_node, target)
    #     self._record_auth_cost(time.perf_counter() - t0)
    #     return allowed

    # ここを確認　キャッシュありの時
    def _hop_prior(self, target: Any, hops_done: int, alpha: float) -> float:
        """構造プリオール (= 挿入時に固定する H × D 部分)。

        cache_policy によって式が切り替わる:
          - ppr_demand / ppr_gdsf (legacy): w_prior = (1−α)^(β·hops) × deg^γ
          - reuse_score (multiplicative reuse-value): w_prior = H^β × D^γ
                ここで
                  H = 1 + (1−α)^hops          ∈ [1, 2]
                  D = 1 + deg / (deg + C_D)   ∈ [1, 2)

        β, γ は **重要度パラメータ** (実験で最適化対象)。
        C_D は **飽和定数** (データ統計から自動決定)。
        """
        nm = getattr(self.shard, "neighbor_map", None)
        if nm is not None:
            deg = len(nm.get(target, ())) or 1
        else:
            deg = len(self._get_local_neighbors(target)) or 1
        one_minus_alpha = max(0.0, 1.0 - float(alpha))
        beta = float(getattr(self.server, "ppr_prior_hop_exp", 1.0)) if self.server else 1.0
        gamma = float(getattr(self.server, "ppr_prior_deg_exp", 1.0)) if self.server else 1.0

        # サーバ側で reuse_score モードが指定されているか判定
        policy_mode = (
            getattr(self.server, "auth_cache_policy", "").lower() if self.server else ""
        )
        if policy_mode == "reuse_score":
            # 新スコア: H^β × D^γ (各因子 [1, 2) 正規化済み)
            cd = float(getattr(self.server, "reuse_cd", 10.0)) if self.server else 10.0
            h_base = 1.0 + one_minus_alpha ** max(0, int(hops_done))
            d_base = 1.0 + float(deg) / (float(deg) + cd) if (float(deg) + cd) > 0 else 1.0
            return (h_base ** beta) * (d_base ** gamma)

        # 旧スコア: (1−α)^(β·hops) × deg^γ
        decay = one_minus_alpha ** (beta * max(0, int(hops_done)))
        deg_term = float(deg) ** gamma
        return decay * deg_term

    def _authorize_candidate(
        self,
        start_node: Optional[int],
        candidate: Dict[str, Any],
        hops_done: int = 0,
        alpha: float = 0.0,
        walk_id: Optional[int] = None,
    ) -> bool:
        target = candidate["node_id"]
        candidate_server = candidate.get("server_id")

        if start_node is None:
            return False

        # owner_sid の決定（元と同じ）
        owner_sid: Optional[int] = None
        if self.owner_map:
            owner_sid = self.owner_map.get(str(target))
        if owner_sid is None:
            try:
                owner_sid = int(candidate.get("server_id"))
            except Exception:
                owner_sid = self.shard.partitioner.assign_entity(target)

        # ★追加：サーバ常駐キャッシュ参照
        _node_only = getattr(self.server, "cache_key_mode", "full") == "node_only"
        ekey = cache_entity_key(target, node_only=_node_only)
        ckey = (int(start_node), ekey)
        # print(ekey, ckey)

        # 仮説検証用イベント記録 (到着順のキャッシュアクセス列)
        if self.server is not None and self.server.access_events is not None:
            _hit = ckey in self.server.authz_cache
            self.server.access_events.append(
                (walk_id, str(target), int(hops_done), bool(_hit))
            )

        if self.server is not None and ckey in self.server.authz_cache:
            self.server.auth_cache_hit += 1
            cache = self.server.authz_cache
            # 提案手法: prefetch によるヒットかどうかを記録
            if isinstance(cache, (FrozenPrefetchCache, PPRBoundedCache)):
                self.server.auth_cache_hit_prefetched += 1
            # ppr_demand: 再遭遇でより短い hops を観測したら prior を最短側に更新
            if isinstance(cache, PPRBoundedCache) and cache.use_hop_prior:
                cache.bump_prior(ckey, self._hop_prior(target, hops_done, alpha))
            return bool(cache[ckey])

        if self.server is not None:
            # print("キャッシュなしの時")
            self.server.auth_cache_miss += 1
        # print(
        #     f"[Server {self.shard.server_id}] candidate_check "
        #     f"start={start_node} target={target} candidate_server={candidate_server} "
        #     f"owner={owner_sid}"
        # )

        # 認可判定（元と同じ）
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

        # ★追加：結果を保存（ALLOW/DENY両方）
        if self.server is not None:
            # --- admission control ---
            # (1) 距離閾値: hops_done > admit_max_hops はキャッシュしない
            admit_max = getattr(self.server, "admit_max_hops", 0)
            _is_edge = isinstance(ekey, str) and ekey.startswith("edge_")
            admit_edge_only_scope = getattr(self.server, "admit_edge_only_scope", False)
            # (2) ノード専用モード: エッジはキャッシュしない
            admit_node_only = getattr(self.server, "admit_node_only", False)
            over_admit_hops = admit_max > 0 and int(hops_done) > admit_max
            if admit_edge_only_scope:
                over_admit_hops = over_admit_hops and _is_edge

            if over_admit_hops:
                pass  # キャッシュ非挿入 (距離超過)
            elif admit_node_only and _is_edge:
                pass  # キャッシュ非挿入 (ノード専用モード: エッジを除外)
            else:
                cache = self.server.authz_cache
                # ppr_demand: 挿入時に hop ベースの w_prior=(1−α)^hops × deg を保存。
                # bump_prior を使い「より短いhop(=より大きいprior)」を優先。
                # これにより学習フェーズで注入された empirical prior も上書きされない
                # (注入済みempiricalが構造priorより大きければ保持される)。
                if isinstance(cache, PPRBoundedCache) and cache.use_hop_prior:
                    cache.bump_prior(ckey, self._hop_prior(target, hops_done, alpha))
                cache[ckey] = bool(allowed)
        # NOTE: ミス毎の print はログ肥大/速度低下を招くため無効化 (デバッグ時のみ復活)
        # print(
        #     f"[Server {self.shard.server_id}] candidate_result "
        #     f"start={start_node} target={target} allowed={allowed}"
        # )

        return bool(allowed)

    def _get_local_neighbors(self, entity_id: Any) -> List[Dict[str, Any]]:
        neighbors = self.shard.get_neighbors(entity_id)
        if not neighbors:
            return []
        return [asdict(n) for n in neighbors]

    def _post_continue(self, server_id: int, state: dict) -> dict:
        url = f"{self.endpoints[server_id].rstrip('/')}/continue_walk"
        data = json.dumps(state).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib_request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read())

    def _bump(self, attr: str, key: Any) -> None:
        if self.stats_collector is None:
            return
        counter = getattr(self.stats_collector, attr, None)
        if counter is None:
            return
        counter[key] += 1

    def _record_entity_visit(
        self, entity_id: Any, hops_done: Optional[int] = None
    ) -> None:
        self._bump("access_counter", entity_id)
        if hops_done is not None:
            self._record_access_distance(entity_id, hops_done)

    def _record_access_distance(self, entity_id: Any, hops_done: int) -> None:
        """hops_done(=bipartite hop 距離) ごとのアクセス回数を node/edge 別に計上。"""
        if self.stats_collector is None:
            return
        is_edge = isinstance(entity_id, str) and entity_id.startswith("edge_")
        attr = "edge_access_by_distance" if is_edge else "node_access_by_distance"
        counter = getattr(self.stats_collector, attr, None)
        if counter is not None:
            counter[int(hops_done)] += 1

    def _record_authorization_attempt(self, source: Any) -> None:
        self._bump("authorization_attempt_counter", source)

    def _record_authorization_success(self, current: Any, target: Any) -> None:
        self._bump("authorized_counter", target)
        self._bump("transition_counter", f"{current}->{target}")

    def _record_authorization_denial(self, source: Any) -> None:
        self._bump("authorization_denied_counter", source)

    def _resolve_start_node(
        self, state: Dict[str, Any], path: List[NodeId]
    ) -> Optional[int]:
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

    def _select_next_neighbor(
        self,
        rng: random.Random,
        neighbors: List[Dict[str, Any]],
        start_node: Optional[int],
        current_entity: NodeId,
        hops_done: int = 0,
        alpha: float = 0.0,
        walk_id: Optional[int] = None,
        try_last: Any = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if not neighbors:
            return None, None

        indices = list(range(len(neighbors)))
        rng.shuffle(indices)
        # try_last (= 非バックトラック時の来た側ノード) は試行順の最後に回す。
        # 反対端が認可されればそちらへ進み、ダメな時だけ来た側に戻る。
        if try_last is not None:
            indices.sort(key=lambda i: 1 if neighbors[i].get("node_id") == try_last else 0)
        for idx in indices:
            self._record_authorization_attempt(current_entity)
            cand = neighbors[idx]
            cid = cand["node_id"]
            if self._authorize_candidate(start_node, cand, hops_done, alpha, walk_id):
                self._record_authorization_success(current_entity, cid)
                return cand, None
            else:
                self._record_authorization_denial(current_entity)

        denial = {
            "denied": True,
            "denied_reason": f"no authorized neighbors from {current_entity}",
        }
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
        walk_id = state.get("walk_id")
        start_node = self._resolve_start_node(state, path)

        # 距離計上は前進ステップ (line: next_entity) で行う。ここでの初回 visit は
        # handoff 再入でも発火するため、hops_done==0 (=新規 walk の始点) のときだけ
        # 距離0として計上し、handoff 時の二重計上を避ける。
        self._record_entity_visit(
            current_entity, hops_done if hops_done == 0 else None
        )

        while hops_done < self.max_hops and rng.random() > alpha:
            hops_done += 1

            owner = self.shard.partitioner.assign_entity(current_entity)
            # print(
            #     f"[Server {current_sid}] continue_check hop={hops_done} "
            #     f"entity={current_entity} owner={owner} current_server={current_sid}"
            # )
            if owner != current_sid:
                # print(
                #     f"[Server {current_sid}] handoff_by_owner -> server {owner} "
                #     f"entity={current_entity} path_len={len(path)}"
                # )
                state_out = {
                    "start_node": start_node,
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                    "walk_id": walk_id,
                }
                return self._post_continue(owner, state_out)
            # print(
            #     f"[Server {current_sid}] stay_local_by_owner "
            #     f"entity={current_entity} owner={owner}"
            # )

            neighbors = self._get_local_neighbors(current_entity)
            # 非バックトラック: エッジ実体に居るとき、来た側ノード(path[-2])は
            # 「最後の手段」にする。反対端を先に試し、それが渡れない(denied)時だけ
            # 来た側に戻る (= ユーザモデル「認可あれば反対へ/ダメなら他にいく」)。
            try_last = None
            if (
                getattr(self.server, "no_backtrack", False)
                and isinstance(current_entity, str)
                and current_entity.startswith("edge_")
                and len(path) >= 2
            ):
                try_last = path[-2]
            next_choice, denial_payload = self._select_next_neighbor(
                rng, neighbors, start_node, current_entity, hops_done, alpha,
                walk_id, try_last,
            )
            if next_choice is None:
                result = {
                    "finished": True,
                    "path": path,
                    "servers": servers,
                    "hops_done": hops_done,
                }
                if denial_payload:
                    result.update(denial_payload)
                return result

            next_entity = next_choice["node_id"]
            next_server = int(next_choice["server_id"])
            # print(
            #     f"[Server {current_sid}] selected_next current={current_entity} "
            #     f"next={next_entity} next_server={next_server}"
            # )

            self._record_entity_visit(next_entity, hops_done)

            path.append(next_entity)
            servers.append(next_server)
            current_entity = next_entity

            if next_server != current_sid:
                # print(
                #     f"[Server {current_sid}] handoff_by_next -> server {next_server} "
                #     f"next_entity={current_entity} path_len={len(path)}"
                # )
                state_out = {
                    "start_node": start_node,
                    "current_node": current_entity,
                    "path": path,
                    "servers": servers,
                    "alpha": alpha,
                    "rng_state": serialize_rng_state(rng),
                    "hops_done": hops_done,
                    "walk_id": walk_id,
                }
                return self._post_continue(next_server, state_out)

        return {
            "finished": True,
            "path": path,
            "servers": servers,
            "hops_done": hops_done,
        }


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------
class EdgeAwareHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._write_json({
                "status": "ok",
                "server_id": self.server.server_id,
                "no_backtrack": getattr(self.server, "no_backtrack", False),
                "log_access_events": getattr(self.server, "access_events", None) is not None,
            })
            return

        if parsed.path == "/access_events":
            self._write_json({
                "server_id": self.server.server_id,
                "access_events": self.server.access_events or [],
            })
            return

        if parsed.path == "/access_stats":
            payload = {
                "access": dict(self.server.access_counter),
                "node_access_by_distance": dict(
                    self.server.node_access_by_distance
                ),
                "edge_access_by_distance": dict(
                    self.server.edge_access_by_distance
                ),
                "authorized": dict(self.server.authorized_counter),
                "authorization_attempts": dict(
                    self.server.authorization_attempt_counter
                ),
                "authorization_denied": dict(self.server.authorization_denied_counter),
                "transition": dict(self.server.transition_counter),
                "auth_time_total": self.server.auth_time_total,
                "auth_calls": self.server.auth_calls,
                "local_auth_calls": getattr(self.server, "local_auth_calls", 0),
                "remote_auth_calls": getattr(self.server, "remote_auth_calls", 0),
                "walk_time_total": self.server.walk_time_total,
                "walk_calls": self.server.walk_calls,
                "memory": summarize_tables(self.server),
                "cache_stats": (
                    self.server.authz_cache.stats()
                    if hasattr(self.server.authz_cache, "stats")
                    else {}
                ),
                "auth_cache_hit": self.server.auth_cache_hit,
                "auth_cache_miss": self.server.auth_cache_miss,
                "auth_cache_size": len(self.server.authz_cache),
                "auth_cache_weight": (
                    self.server.authz_cache.current_weight()
                    if hasattr(self.server.authz_cache, "current_weight")
                    else None
                ),
                "auth_cache_capacity": (
                    self.server.authz_cache.max_weight()
                    if hasattr(self.server.authz_cache, "max_weight")
                    else None
                ),
                "auth_cache_policy": getattr(self.server, "auth_cache_policy", "none"),
                "auth_cache_hit_rate": (
                    self.server.auth_cache_hit
                    / max(1, self.server.auth_cache_hit + self.server.auth_cache_miss)
                ),
                # 提案手法用の追加メトリクス
                "auth_cache_hit_prefetched": getattr(
                    self.server, "auth_cache_hit_prefetched", 0
                ),
                "prefetch_size": getattr(self.server, "prefetch_size", 0),
                "prefetch_build_time_sec": getattr(
                    self.server, "prefetch_build_time", 0.0
                ),
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
                self.send_error(
                    400, "'node' must be an integer id or an edge id (edge_u_v)"
                )
                return

        neighbors = self.server.shard.get_neighbors(entity)
        if neighbors is None:
            self.send_error(404, f"Entity {entity} not owned by this shard")
            return

        payload = {
            "node_id": entity,
            "server_id": self.server.server_id,
            "neighbors": [asdict(n) for n in neighbors],
        }
        self._write_json(payload)

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
        if parsed.path == "/cache/prefetch":
            self._handle_cache_prefetch()
            return
        if parsed.path == "/cache/freeze":
            self._handle_cache_freeze()
            return
        if parsed.path == "/cache/refresh_priors":
            self._handle_cache_refresh_priors()
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

        if (
            start_node is None
            or alpha is None
            or endpoints is None
            or server_count is None
        ):
            self.send_error(
                400,
                "Missing required parameters: start_node, alpha, endpoints, server_count",
            )
            return

        walker = PeerWalker(
            self.server.shard,
            endpoints=endpoints,
            request_timeout=getattr(self.server, "request_timeout", 5.0),
            auth_table=getattr(self.server, "auth_table", None),
            stats_collector=self.server,
            ppr_mode=getattr(self.server, "ppr_mode", False),
            node_to_starts=getattr(self.server, "node_to_starts", None),
            owner_map=getattr(self.server, "owner_map", None),
            server=self.server,  # ★追加
        )

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
                "walk_id": i,
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

        payload = {
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
        }
        self._write_json(payload)

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
            ppr_mode=getattr(self.server, "ppr_mode", False),
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

        # 以下3行が[正解方法]
        # allowed_starts = self.server.node_to_starts.get(entity, set())
        # allowed = bool(start_int in allowed_starts)
        # self._write_json(
        #     {"allowed": allowed, "entity": entity, "server_id": self.server.server_id}
        # )

        # [deny方式:]
        #   entity_to_denied_starts: { entity -> set(denied_start_nodes) }
        denied_starts = self.server.node_to_starts.get(entity, set())
        # denied 情報が無い / 空 → 誰もNGにしていない → 許可
        if not denied_starts:
            allowed = True
        else:
            allowed = start_int not in denied_starts
        self._write_json(
            {"allowed": allowed, "entity": entity, "server_id": self.server.server_id}
        )

    def _handle_cache_reset(self) -> None:
        s = self.server
        s.authz_cache = build_authz_cache(
            s.auth_cache_policy,
            s.auth_cache_capacity,
            sizer=s.auth_cache_sizer,
            theta=getattr(s, "ppr_theta", 1.0),
            delta=getattr(s, "ppr_delta", 0.0),
            lambda_learn=getattr(s, "ppr_lambda", 1.0),
            beta=getattr(s, "ppr_prior_hop_exp", 1.0),
            gamma=getattr(s, "ppr_prior_deg_exp", 1.0),
            cf=getattr(s, "reuse_cf", 5.0),
            cl=getattr(s, "reuse_cl", 5.0),
            cd=getattr(s, "reuse_cd", 10.0),
            rho=getattr(s, "reuse_rho", 0.0),
            cr=getattr(s, "reuse_cr", 10.0),
        )
        s.auth_cache_hit = 0
        s.auth_cache_miss = 0
        s.auth_cache_hit_prefetched = 0  # 提案手法: prefetch ヒットの内訳
        s.auth_time_total = 0.0
        s.auth_calls = 0
        s.local_auth_calls = 0
        s.remote_auth_calls = 0
        s.walk_time_total = 0.0
        s.walk_calls = 0
        s.prefetch_size = 0
        s.prefetch_build_time = 0.0
        # 提案手法 ppr_gdsf: prior 用の状態もクリア (prefetch で再設定)
        s._ppr_dist = {}
        s._ppr_alpha = 0.0
        s._ppr_max_dist = 0
        s._prefetch_remote_fail_count = 0
        s.access_counter.clear()
        s.node_access_by_distance.clear()
        s.edge_access_by_distance.clear()
        if s.access_events is not None:
            s.access_events.clear()
        s.authorized_counter.clear()
        s.authorization_attempt_counter.clear()
        s.authorization_denied_counter.clear()
        s.transition_counter.clear()
        self._write_json(
            {
                "status": "ok",
                "policy": s.auth_cache_policy,
                "capacity": s.auth_cache_capacity,
            }
        )

    def _handle_cache_prefetch(self) -> None:
        """
        提案手法用: BFS で近傍ノードの auth 判定を事前計算し、cache に投入する。

        リクエスト JSON:
          {
            "start_node": int,
            "mode": "bfs_prefetch" | "bfs_score",
            "K": int            # bfs_prefetch: BFS 距離上限
            "capacity": int,    # bfs_score:   上位 N を採用
            "decay": float,     # bfs_score:   score = freq × decay^dist
            "attempts": {entity: count}  # bfs_score 用 (controller から渡される頻度)
          }

        共通動作:
          1. start_node から BFS で 全 shard 範囲のグラフを探索 (各 hop)
          2. 採用ノード集合を決定
          3. 各 ノードに対し _is_locally_allowed / _check_remote_authorization を実行
             → 結果を authz_cache に投入
          4. 完了後、cache を freeze (walk 中の追加書き込みを禁止)
        """
        payload = self._read_json_body()
        if payload is None:
            return

        s = self.server
        policy = s.auth_cache_policy
        if policy not in ("bfs_prefetch", "bfs_score", "ppr_gdsf"):
            self._write_json({"status": "skipped", "reason": f"policy={policy}"})
            return

        try:
            start_node = int(payload["start_node"])
        except (KeyError, TypeError, ValueError):
            self.send_error(400, "start_node required")
            return
        mode = payload.get("mode", policy)
        K = int(payload.get("K", 10))
        capacity = int(payload.get("capacity", 100))
        decay = float(payload.get("decay", 0.7))
        alpha = float(payload.get("alpha", 0.0))
        attempts = payload.get("attempts", {})

        # ===== 提案本命: PPR×GDSF 容量有界キャッシュの seed =====
        if policy == "ppr_gdsf":
            self._prefetch_ppr_gdsf(s, start_node, alpha, capacity)
            return

        # ---- BFS for distances ----
        t0 = time.perf_counter()
        dist = _bfs_distances_from(s.shard, start_node, max_hops=K if mode == "bfs_prefetch" else 30)

        # 採用ノード集合
        if mode == "bfs_prefetch":
            selected = [n for n, d in dist.items() if d <= K]
        else:  # bfs_score
            scored = []
            for n, d in dist.items():
                freq = int(attempts.get(str(n), attempts.get(n, 1)))
                scored.append((freq * (decay ** d), n))
            scored.sort(key=lambda x: -x[0])
            selected = [n for _, n in scored[:capacity]]

        # ---- 各選定ノードの allow/deny を計算してキャッシュに投入 ----
        # _authorize_candidate を経由しないので、計測カウンタは更新しない
        # (フェアな比較のため: prefetch コスト = サーバ内 BFS + ローカル判定のみ)
        cache = s.authz_cache
        if not isinstance(cache, FrozenPrefetchCache):
            self._write_json({"status": "skipped", "reason": "cache is not Frozen"})
            return
        cache.unfreeze()
        _node_only = getattr(s, "cache_key_mode", "full") == "node_only"

        inserted = 0
        for target in selected:
            ekey = cache_entity_key(target, node_only=_node_only)
            ckey = (start_node, ekey)
            if ckey in cache:
                continue
            # 所有者は誰か
            owner_sid = None
            if s.owner_map:
                owner_sid = s.owner_map.get(str(target))
            if owner_sid is None:
                try:
                    owner_sid = s.shard.partitioner.assign_entity(target)
                except Exception:
                    owner_sid = None

            if owner_sid == s.server_id:
                # ローカル判定 (常に成功)
                allowed = _local_allowed_for_prefetch(s, start_node, target)
            else:
                # リモート問い合わせ (失敗時は None → cache に入れない)
                allowed = _check_remote_for_prefetch(s, owner_sid, start_node, target)
            if allowed is None:
                # リモート失敗時はスキップ (walk 時の cache miss → ライブ問合せに任せる)
                # ただし FrozenPrefetchCache は freeze 後に miss 用の追加もできないので、
                # 実質的には walk 時にも live auth が走らない問題が残る → 暫定で skip
                continue
            cache[ckey] = bool(allowed)
            inserted += 1

        # walk 開始前に freeze
        cache.freeze()
        s.prefetch_size = inserted
        s.prefetch_build_time = time.perf_counter() - t0
        s.prefetch_remote_fail_count = getattr(s, "_prefetch_remote_fail_count", 0)

        self._write_json({
            "status": "ok",
            "mode": mode,
            "start_node": start_node,
            "selected": len(selected),
            "inserted": inserted,
            "build_time_sec": s.prefetch_build_time,
            "policy": s.auth_cache_policy,
        })

    def _prefetch_ppr_gdsf(
        self, s: Any, start_node: int, alpha: float, capacity: int
    ) -> None:
        """
        PPRBoundedCache を w_prior 上位で seed する。
          w_prior(e) = (1−α)^bfs_dist(s,e) × deg(e)
        seed 後も freeze せず、walk 中は GDSF で自己維持 (miss 挿入・容量退去)。
        """
        cache = s.authz_cache
        if not isinstance(cache, PPRBoundedCache):
            self._write_json({"status": "skipped", "reason": "cache is not PPRBounded"})
            return

        t0 = time.perf_counter()
        # BFS (bipartite hop)。prefetch コスト上限: 容量の ~20 倍まで探索
        node_limit = max(capacity * 20, 1000)
        dist = _bfs_distances_from(
            s.shard, start_node, max_hops=10 ** 9, node_limit=node_limit
        )
        # prior_fn 用の状態を server に保持し、closure を注入
        s._ppr_dist = dist
        s._ppr_alpha = alpha
        s._ppr_max_dist = max(dist.values()) if dist else 0
        cache.prior_fn = make_ppr_prior_fn(s)

        one_minus_alpha = max(0.0, 1.0 - alpha)
        nm = s.shard.neighbor_map
        scored = []
        for ent, d in dist.items():
            deg = len(nm.get(ent, ())) or 1
            scored.append(((one_minus_alpha ** d) * deg, ent))
        scored.sort(key=lambda x: -x[0])
        # 上位だけを seed 候補に (リモート問い合わせ回数を抑制)
        selected = [ent for _, ent in scored[:capacity]]

        _node_only = getattr(s, "cache_key_mode", "full") == "node_only"
        inserted = 0
        for target in selected:
            ekey = cache_entity_key(target, node_only=_node_only)
            ckey = (start_node, ekey)
            if ckey in cache:
                continue
            owner_sid = None
            if s.owner_map:
                owner_sid = s.owner_map.get(str(target))
            if owner_sid is None:
                try:
                    owner_sid = s.shard.partitioner.assign_entity(target)
                except Exception:
                    owner_sid = None
            if owner_sid == s.server_id:
                allowed = _local_allowed_for_prefetch(s, start_node, target)
            else:
                allowed = _check_remote_for_prefetch(s, owner_sid, start_node, target)
            if allowed is None:
                continue  # リモート失敗 → walk 時の live auth に任せる
            cache[ckey] = bool(allowed)
            inserted += 1

        cache.seeded = inserted
        s.prefetch_size = inserted
        s.prefetch_build_time = time.perf_counter() - t0
        s.prefetch_remote_fail_count = getattr(s, "_prefetch_remote_fail_count", 0)
        self._write_json(
            {
                "status": "ok",
                "mode": "ppr_gdsf",
                "start_node": start_node,
                "alpha": alpha,
                "capacity": capacity,
                "reachable": len(dist),
                "selected": len(selected),
                "inserted": inserted,
                "cache_entries": len(cache),
                "cache_weight": cache.current_weight(),
                "build_time_sec": s.prefetch_build_time,
                "policy": s.auth_cache_policy,
            }
        )

    def _handle_cache_freeze(self) -> None:
        """明示的に cache を freeze する。bfs_prefetch/bfs_score 用。"""
        s = self.server
        cache = s.authz_cache
        if isinstance(cache, FrozenPrefetchCache):
            cache.freeze()
            self._write_json({"status": "frozen", "size": len(cache)})
        else:
            self._write_json({"status": "no-op", "policy": s.auth_cache_policy})

    def _handle_cache_refresh_priors(self) -> None:
        """
        学習フェーズ (Phase1) 終了後に controller から呼ばれ、実測アクセス頻度を
        加法の学習係数 learn(e) として PPRBoundedCache に注入する。
        ※ リセットはしない (Phase1 で温めたキャッシュ・freq・H をそのまま持ち越す)。

        スコア式: V(e) = max(0, freq−δ) + θ·w_prior + λ·learn(e)
          ここで注入するのは learn(e)。λ はサーバ起動時の --ppr-lambda で固定。

        リクエスト JSON:
          {
            "start_node": int,
            "access_counts": {"entity_str": count, ...},
            "scale": float   (省略時=1.0: count に乗じるスケール係数。基本 1.0 推奨)
          }

        動作:
          - PPRBoundedCache の set_learn(ckey, count*scale) を呼び出す
            (ckey = (start_node, cache_entity_key(entity_str)))
          - learn は退去しても消えない (ghost係数) ため、Phase2 で再挿入された
            エントリにも head start を与える。
          - store 中のエントリは set_learn 内で H を即再計算 (eviction 順を更新)。
          - ppr_demand 以外のキャッシュポリシーは no-op
        """
        payload = self._read_json_body()
        if payload is None:
            return

        s = self.server
        cache = s.authz_cache
        if not isinstance(cache, PPRBoundedCache):
            self._write_json({
                "status": "skipped",
                "reason": f"policy={s.auth_cache_policy} is not PPRBoundedCache",
            })
            return

        start_node = payload.get("start_node")
        access_counts = payload.get("access_counts", {})
        scale = float(payload.get("scale", 1.0))

        if start_node is None:
            self.send_error(400, "start_node required")
            return

        sn = int(start_node)
        updated = 0
        for entity_str, count in access_counts.items():
            cnt = int(count)
            if cnt <= 0:
                continue
            # access_counter は entity_id (int or "edge_X_Y") がキー。
            # JSON シリアライズで整数ノードも文字列になっているため cache_entity_key で正規化。
            ckey = (sn, cache_entity_key(str(entity_str)))
            new_learn = float(cnt) * scale
            cache.set_learn(ckey, new_learn)  # learn(e) 注入 (store内なら H も即再計算)
            updated += 1

        self._write_json({
            "status": "ok",
            "updated_priors": updated,         # 後方互換: 注入した learn 件数
            "injected_learn": updated,
            "lambda_learn": getattr(cache, "lambda_learn", None),
            "recalculated_scores": len(cache.store),
        })

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
        ppr_theta: float = 1.0,
        ppr_delta: float = 0.0,
        ppr_lambda: float = 1.0,
        ppr_prior_hop_exp: float = 1.0,
        ppr_prior_deg_exp: float = 1.0,
        # reuse_score: 飽和定数
        reuse_cf: float = 5.0,
        reuse_cl: float = 5.0,
        reuse_cd: float = 10.0,
        reuse_rho: float = 0.0,
        reuse_cr: float = 10.0,
        admit_max_hops: int = 0,
        admit_edge_only_scope: bool = False,
        admit_node_only: bool = False,
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
        # アクセス局所性: bipartite hop 距離 (hops_done) ごとのアクセス回数。
        # node は偶数 hop、edge は奇数 hop に出る (start node = hop 0)。
        # access_counter と違い handoff の二重計上を避け、1 前進ステップ=1 計上。
        self.node_access_by_distance = Counter()
        self.edge_access_by_distance = Counter()
        # 仮説検証用: キャッシュアクセス (=_authorize_candidate) を到着順に記録。
        # LOG_ACCESS_EVENTS=1 のときだけ有効 (通常実行はゼロ影響)。
        # 各要素 = (walk_id, target_entity, hops_done, was_cache_hit)
        self.access_events = [] if os.environ.get("LOG_ACCESS_EVENTS") == "1" else None
        # 非バックトラック: エッジ実体に居るとき来た側ノードを候補から除外し、
        # 認可があれば必ず反対端へ渡る (元グラフ RW モデル)。NO_BACKTRACK=1 で有効。
        self.no_backtrack = os.environ.get("NO_BACKTRACK") == "1"

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
        self.ppr_theta = float(ppr_theta)
        self.ppr_delta = float(ppr_delta)
        self.ppr_lambda = float(ppr_lambda)  # 学習係数 λ
        # w_prior = (1−α)^(β·hop) × deg^γ の距離/次数の指数
        self.ppr_prior_hop_exp = float(ppr_prior_hop_exp)  # β: 距離の効き (大=距離重視)
        self.ppr_prior_deg_exp = float(ppr_prior_deg_exp)  # γ: 次数の効き (小=次数軽視, 0=無視)
        # reuse_score: 飽和定数 (各特徴量の [1, 2) 正規化スケール)
        self.reuse_cf = float(reuse_cf)
        self.reuse_cl = float(reuse_cl)
        self.reuse_cd = float(reuse_cd)
        # reuse_score: recency (LRU的最近性) の指数 ρ と 飽和点 C_R
        # ρ=0 で recency 無効 (現行スコアと完全一致)
        self.reuse_rho = float(reuse_rho)
        self.reuse_cr = float(reuse_cr)
        # admit_max_hops: キャッシュ admission の距離閾値 (bipartite hop 単位)
        # 0 = 閾値なし (全 miss をキャッシュ, 従来動作)
        # N > 0: hops_done > N のノード/エッジは認可するがキャッシュしない
        self.admit_max_hops = int(admit_max_hops)
        self.admit_edge_only_scope = bool(admit_edge_only_scope)
        self.admit_node_only = bool(admit_node_only)
        self.authz_cache = build_authz_cache(
            cache_policy,
            cache_capacity,
            sizer=self.auth_cache_sizer,
            theta=self.ppr_theta,
            delta=self.ppr_delta,
            lambda_learn=self.ppr_lambda,
            beta=self.ppr_prior_hop_exp,
            gamma=self.ppr_prior_deg_exp,
            cf=self.reuse_cf,
            cl=self.reuse_cl,
            cd=self.reuse_cd,
            rho=self.reuse_rho,
            cr=self.reuse_cr,
        )
        self.auth_cache_hit = 0
        self.auth_cache_miss = 0
        # 提案手法用カウンタ
        self.auth_cache_hit_prefetched = 0  # frozen prefetch エントリでヒットした回数
        self.prefetch_size = 0
        self.prefetch_build_time = 0.0
        # 提案手法 ppr_gdsf: prior_fn 用の状態 (prefetch で設定)
        self._ppr_dist: Dict[Any, int] = {}
        self._ppr_alpha = 0.0
        self._ppr_max_dist = 0
        self._prefetch_remote_fail_count = 0

        # ---- 計測用：このサーバが参照している入力ファイル ----
        self.edges_path: Optional[str] = None
        self.node_to_starts_path: Optional[str] = None
        self.auth_file_path: Optional[str] = None

        # ---- 認可キャッシュ（キャッシュ実装がある場合に使う） ----
        # ないモデルでは空のままでOK（サイズ0として観測できる）
        self.auth_cache: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed random walk server (node + edge bipartite model)."
    )

    parser.add_argument(
        "--owned-hints-only",
        action="store_true",
        help="If set, shard owns ONLY entities listed in node_to_starts_serverX.json keys.",
    )
    parser.add_argument(
        "--edges",
        default="./../../dataset/Louvain/graph/test.gr",
        help="Path to the shared edge list file.",
    )
    parser.add_argument(
        "--server-id",
        type=int,
        required=True,
        help="Unique id of this server within the cluster (0-indexed).",
    )
    parser.add_argument(
        "--server-count",
        type=int,
        required=True,
        help="Total number of servers participating in the cluster.",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host/IP address to bind the shard server."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to expose the shard server."
    )
    parser.add_argument(
        "--server-endpoints",
        nargs="+",
        required=True,
        help="Endpoints for all servers in order (host:port).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=5.0,
        help="Timeout (sec) when this server queries other shards.",
    )
    parser.add_argument(
        "--cache-policy",
        choices=[
            "none", "memo", "lru", "arc",
            "bfs_prefetch", "bfs_score", "ppr_gdsf", "ppr_demand", "reuse_score",
        ],
        default="none",
        help="Authorization decision cache policy. "
        "'bfs_prefetch'/'bfs_score'/'ppr_gdsf' are prefetch 系 (要 /cache/prefetch). "
        "'ppr_demand' は prefetch 不要のオンデマンド提案手法 "
        "(加法スコア freq+theta*prior, hop距離 prior, 初回 delta 割引).",
    )
    parser.add_argument(
        "--ppr-theta",
        type=float,
        default=1.0,
        help="ppr_demand: 構造prior の強さ (疑似カウント)。大きいほど近さ/次数を優遇.",
    )
    parser.add_argument(
        "--ppr-delta",
        type=float,
        default=0.0,
        help="ppr_demand: 初回ヒットの割引 (0=割引なし, 1 に近いほど 1-hit-wonder を排除).",
    )
    parser.add_argument(
        "--ppr-lambda",
        type=float,
        default=1.0,
        help="ppr_demand: 学習係数 λ の強さ。V に +λ·learn(e) を加える。"
        "learn(e)=Phase1(学習RW)で観測した実アクセス回数。大きいほど"
        "「構造で拾えないが実際は来る」エンティティを残しやすくする.",
    )
    parser.add_argument(
        "--ppr-prior-hop-exp",
        type=float,
        default=1.0,
        help="w_prior=(1−α)^(β·hop)×deg^γ の β (距離指数)。"
        "1.0=従来。大きいほど距離の減衰が急=始点距離を重視 (次数より距離を効かせる).",
    )
    parser.add_argument(
        "--ppr-prior-deg-exp",
        type=float,
        default=1.0,
        help="w_prior=(1−α)^(β·hop)×deg^γ の γ (次数指数)。"
        "1.0=従来。小さいほど次数の効きを弱める (0.5=√deg, 0=次数無視=純距離).",
    )
    # ↓ reuse_score 用の飽和定数 ↓
    parser.add_argument(
        "--reuse-cf",
        type=float,
        default=5.0,
        help="reuse_score: O=1+freq/(freq+C_F) の C_F (freq 飽和点)。"
        "推奨 = freq の中央値. 大きいほど freq 因子が飽和しにくくなる.",
    )
    parser.add_argument(
        "--reuse-cl",
        type=float,
        default=5.0,
        help="reuse_score: L=1+learn/(learn+C_L) の C_L (learn 飽和点).",
    )
    parser.add_argument(
        "--reuse-cd",
        type=float,
        default=10.0,
        help="reuse_score: D=1+deg/(deg+C_D) の C_D (degree 飽和点)。"
        "推奨 = グラフの中央次数 (vldb≈5, amazon0601≈15).",
    )
    parser.add_argument(
        "--reuse-rho",
        type=float,
        default=0.0,
        help="reuse_score: recency 因子 R=1+C_R/(age+C_R) の指数 ρ。"
        "0=recency無効(=現行スコアと完全一致). 大きいほど最近性(LRU的)を重視.",
    )
    parser.add_argument(
        "--reuse-cr",
        type=float,
        default=10.0,
        help="reuse_score: recency R=1+C_R/(age+C_R) の C_R (age 飽和点)。"
        "推奨 = 再アクセス距離(論理時計単位)の中央値. ρ>0 のときのみ有効.",
    )
    parser.add_argument(
        "--admit-max-hops",
        type=int,
        default=0,
        help="キャッシュ admission 閾値 (bipartite hop)。"
        "0=無制限(デフォルト)。N>0: hops_done>N のエンティティは認可するがキャッシュしない。"
        "例: --admit-max-hops 4 → 元グラフで始点から2ホップ以内のみキャッシュ。",
    )
    parser.add_argument(
        "--admit-edge-only-scope",
        action="store_true",
        default=False,
        help="admit-max-hops を edge 実体にだけ適用する。"
        "node 実体は距離に関係なくキャッシュ対象のままにする。",
    )
    parser.add_argument(
        "--admit-node-only",
        action="store_true",
        default=False,
        help="ノード専用キャッシュモード。エッジ実体は認可するがキャッシュしない。"
        "ノードのみに容量を集中させることで、ヒット率向上を狙う。",
    )
    parser.add_argument(
        "--cache-capacity",
        type=int,
        default=0,
        help="Maximum abstract cache weight units for LRU/ARC. Ignored for 'none'.",
    )
    parser.add_argument(
        "--cache-node-weight",
        type=int,
        default=1,
        help="Weight units for a cached node authorization decision.",
    )
    parser.add_argument(
        "--cache-edge-weight",
        type=int,
        default=2,
        help="Weight units for a cached edge authorization decision.",
    )
    parser.add_argument(
        "--cache-start-weight",
        type=int,
        default=1,
        help="Weight units for the start-node part of a cache key.",
    )
    parser.add_argument(
        "--cache-allow-weight",
        type=int,
        default=0,
        help="Additional weight units for storing an ALLOW decision.",
    )
    parser.add_argument(
        "--cache-deny-weight",
        type=int,
        default=0,
        help="Additional weight units for storing a DENY decision.",
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default=None,
        help="Optional JSON file path mapping start_node -> allowed entities (n/e).",
    )
    parser.add_argument(
        "--node-to-starts-file",
        type=str,
        default=None,
        help="Optional JSON path mapping target_node/edge -> allowed start nodes.",
    )
    parser.add_argument(
        "--dump-auth",
        action="store_true",
        help="Dump filtered auth/node_to_starts to auth_dump_server{sid}.json for debugging.",
    )
    parser.add_argument(
        "--cache-key-mode",
        choices=["full", "node_only"],
        default="full",
        help="Cache key granularity. 'node_only' maps edge entities to their smaller endpoint node (原因2診断用).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.cache_policy in {"lru", "arc", "ppr_gdsf", "ppr_demand", "reuse_score"} and args.cache_capacity <= 0:
        raise ValueError(
            "--cache-capacity must be positive when --cache-policy is "
            "'lru', 'arc', 'ppr_gdsf', or 'ppr_demand'"
        )
    # 提案手法: capacity は score の上位 N に使う
    if args.cache_policy == "bfs_score" and args.cache_capacity <= 0:
        raise ValueError(
            "--cache-capacity must be positive when --cache-policy is 'bfs_score' "
            "(used as top-N selection)"
        )

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

        partitioner = StaticPartitioner(
            server_count=args.server_count,
            mapping=owner_map,
            fallback=base_partitioner,
        )

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
    print(
        f"[Server {args.server_id}] OWNED entity counts: nodes={len(owned_nodes)}, edges={len(owned_edges)}, total={len(shard.local_entities)}"
    )
    print(f"[Server {args.server_id}] OWNED nodes sample: {owned_nodes[:5]}")
    print(f"[Server {args.server_id}] OWNED edges sample: {owned_edges[:5]}")

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
        ppr_theta=args.ppr_theta,
        ppr_delta=args.ppr_delta,
        ppr_lambda=args.ppr_lambda,
        ppr_prior_hop_exp=args.ppr_prior_hop_exp,
        ppr_prior_deg_exp=args.ppr_prior_deg_exp,
        reuse_cf=args.reuse_cf,
        reuse_cl=args.reuse_cl,
        reuse_cd=args.reuse_cd,
        reuse_rho=args.reuse_rho,
        reuse_cr=args.reuse_cr,
        admit_max_hops=args.admit_max_hops,
        admit_edge_only_scope=args.admit_edge_only_scope,
        admit_node_only=args.admit_node_only,
    )
    server.edges_path = str(edge_path)

    if args.auth_file:
        auth_table = load_entity_auth_table(Path(args.auth_file))
        server.auth_table = filter_auth_table_for_shard(
            auth_table, shard.partitioner, shard.server_id
        )
        server.auth_file_path = str(Path(args.auth_file))

    if args.node_to_starts_file:
        server.node_to_starts = filtered_node_to_starts
        server.node_to_starts_path = str(nts_path)

    if owner_map:
        server.owner_map = owner_map

    if args.dump_auth:
        dump = {
            "server_id": server.server_id,
            "auth_table": {
                str(k): {"n": sorted(v.get("n", [])), "e": sorted(v.get("e", []))}
                for k, v in server.auth_table.items()
            },
            "node_to_starts": {
                str(k): sorted(list(v)) for k, v in server.node_to_starts.items()
            },
            "owner_map_size": len(server.owner_map),
        }
        dump_path = Path(f"auth_dump_server{server.server_id}.json")
        dump_path.write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def dump_access_stats() -> None:
        stats = {
            "access": dict(server.access_counter),
            "node_access_by_distance": dict(server.node_access_by_distance),
            "edge_access_by_distance": dict(server.edge_access_by_distance),
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
            "cache_stats": (
                server.authz_cache.stats()
                if hasattr(server.authz_cache, "stats")
                else {}
            ),
        }
        out_path = Path(f"access_stats_server{server.server_id}.json")
        out_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(
            f"[Server {server.server_id}] auth summary: {server.auth_calls} calls, total {server.auth_time_total:.6f}s"
        )
        print(f"[Server {server.server_id}] Access stats saved to {out_path}")

    atexit.register(dump_access_stats)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[Server {server.server_id}] Shutting down.")


if __name__ == "__main__":
    main()
