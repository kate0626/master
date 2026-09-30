#!/usr/bin/env python3
# =============================================================================
# baselines.py — 関連研究の比較手法（実サーバ・仮想サーバ共通のキャッシュクラス）
#
#   方策名（cache_extensions に登録。--cache-policy で選ぶ）:
#     lfu          回数が最少のものを追い出す（同数なら最終アクセスが古いもの）
#     lecar        LeCaR（Vietri+, HotStorage'18）: LRU と LFU の2つを「専門家」とし、
#                  追い出したキーの履歴（ghost）にヒットしたら、その専門家の重みを下げる
#                  （後悔最小化）。追い出しは重みに比例した確率で専門家を選ぶ
#     cacheus      CACHEUS（Rodriguez+, FAST'21）の簡略版: LeCaR の枠組みで
#                  専門家を LRU と CR-LFU（同数のとき最近のものを追い出す = チャーン耐性）にし、
#                  学習率をヒット率の変化から山登りで自動調整する
#                  ※ 原典の SR-LRU（スキャン耐性 LRU）は LRU で代用している（簡略版である点に注意）
#
#   いずれも論文の記述を記憶から実装したもので、原典の擬似コードとは細部が違う可能性がある。
#   Belady（最適解）はキャッシュではなく、記録したアクセス列から belady.py で計算する。
#
#   容量は重み単位（DecisionCacheSizer）。ノード/エッジ分離配置では1つのキャッシュに1種類の型しか
#   入らないので、実質は件数ベースになる。
# =============================================================================
from __future__ import annotations

import math
import random
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

Key = Tuple[int, str]


class _Base:
    """容量（重み）・保存・統計の共通部分。"""

    policy_name = "base"

    def __init__(self, capacity: int, sizer) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer
        self.store: Dict[Key, bool] = {}
        self.weights: Dict[Key, int] = {}
        self.total_weight = 0
        self.clock = 0
        self.last_access: Dict[Key, int] = {}
        self.freq: Dict[Key, int] = {}
        self.insertions = 0
        self.evictions = 0
        self.rejected_too_large = 0

    def __contains__(self, key: Key) -> bool:
        return key in self.store

    def __len__(self) -> int:
        return len(self.store)

    def __getitem__(self, key: Key) -> bool:
        self.clock += 1
        self.last_access[key] = self.clock
        self.freq[key] = self.freq.get(key, 0) + 1
        self._on_hit(key)
        return self.store[key]

    def __setitem__(self, key: Key, value: bool) -> None:
        value = bool(value)
        w = self.sizer.entry_weight(key, value)
        if w > self.capacity:
            self.rejected_too_large += 1
            return
        self.clock += 1
        if key in self.store:
            self.total_weight += w - self.weights.get(key, 0)
            self.store[key] = value
            self.weights[key] = w
            self.last_access[key] = self.clock
            return
        self._on_miss(key)
        self.store[key] = value
        self.weights[key] = w
        self.total_weight += w
        self.last_access[key] = self.clock
        self.freq[key] = self._initial_freq(key)
        self.insertions += 1
        while self.total_weight > self.capacity and len(self.store) > 1:
            victim = self._choose_victim(exclude=key)
            self._remove(victim)
            self.evictions += 1

    # --- 各手法で上書き ---------------------------------------------------------
    def _on_hit(self, key: Key) -> None:
        pass

    def _on_miss(self, key: Key) -> None:
        pass

    def _initial_freq(self, key: Key) -> int:
        return 1

    def _choose_victim(self, exclude: Key) -> Key:
        raise NotImplementedError

    def _remove(self, key: Key) -> None:
        self.total_weight -= self.weights.pop(key, 0)
        self.store.pop(key, None)
        self.last_access.pop(key, None)
        self.freq.pop(key, None)

    # --- サーバが呼ぶ集計 --------------------------------------------------------
    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def storage_objects(self) -> Dict[str, Any]:
        return {"store": self.store}

    def stats(self) -> Dict[str, Any]:
        return {"policy": self.policy_name, "entries": len(self.store),
                "current_weight": self.total_weight, "max_weight": self.capacity,
                "insertions": self.insertions, "evictions": self.evictions}


# =============================================================================
class LFUCache(_Base):
    policy_name = "lfu"

    def _choose_victim(self, exclude: Key) -> Key:
        la, fr = self.last_access, self.freq
        return min((k for k in self.store if k != exclude),
                   key=lambda k: (fr.get(k, 0), la.get(k, 0)))


# =============================================================================
class LeCaRCache(_Base):
    """LeCaR。専門家 = LRU / LFU。

    - 重み w = (w_LRU, w_LFU)、初期 0.5 / 0.5
    - 追い出し: 確率 w_LRU で LRU、残りで LFU を選び、その専門家の犠牲者を追い出して
      その専門家の履歴（ghost）に入れる。履歴の長さはキャッシュの件数と同じ
    - ミスしたキーが専門家 E の履歴にあったら「E の判断は誤り」:
        r = d^(経過時間)、d = 0.005^(1/N)   （最近追い出したものほど罰が大きい）
        もう一方の専門家の重みに exp(λ·r) を掛けて正規化（= E の重みが下がる）
    - 履歴から戻ったキーは、追い出し時の回数を引き継ぐ（LFU 側が再び判断できるように）
    """

    policy_name = "lecar"

    def __init__(self, capacity: int, sizer, learning_rate: float = 0.45,
                 discount: float = 0.005, seed: int = 0) -> None:
        super().__init__(capacity, sizer)
        self.lr = learning_rate
        self.discount = discount
        self.w = [0.5, 0.5]                 # [LRU, LFU]
        self.hist = [OrderedDict(), OrderedDict()]   # key -> (追い出し時刻, 回数)
        self.rng = random.Random(seed)
        self.ghost_hits = [0, 0]

    def _n(self) -> int:
        return max(1, len(self.store))

    def _lru_victim(self, exclude: Key) -> Key:
        la = self.last_access
        return min((k for k in self.store if k != exclude), key=lambda k: la.get(k, 0))

    def _lfu_victim(self, exclude: Key) -> Key:
        la, fr = self.last_access, self.freq
        return min((k for k in self.store if k != exclude),
                   key=lambda k: (fr.get(k, 0), la.get(k, 0)))

    def _initial_freq(self, key: Key) -> int:
        return getattr(self, "_restored_freq", None) or 1

    def _on_miss(self, key: Key) -> None:
        self._restored_freq = None
        for e in (0, 1):
            h = self.hist[e]
            if key in h:
                t_ev, f = h.pop(key)
                n = self._n()
                r = self.discount ** ((self.clock - t_ev) / n)
                other = 1 - e
                self.w[other] *= math.exp(self.lr * r)
                s = self.w[0] + self.w[1]
                self.w = [self.w[0] / s, self.w[1] / s]
                self.ghost_hits[e] += 1
                self._restored_freq = f + 1
                break

    def _pick_expert(self) -> int:
        return 0 if self.rng.random() < self.w[0] else 1

    def _expert_victim(self, e: int, exclude: Key) -> Key:
        return self._lru_victim(exclude) if e == 0 else self._lfu_victim(exclude)

    def _choose_victim(self, exclude: Key) -> Key:
        e = self._pick_expert()
        v = self._expert_victim(e, exclude)
        h = self.hist[e]
        h[v] = (self.clock, self.freq.get(v, 1))
        while len(h) > self._n():
            h.popitem(last=False)
        return v

    def stats(self) -> Dict[str, Any]:
        d = super().stats()
        d.update({"w_lru": self.w[0], "w_lfu": self.w[1], "learning_rate": self.lr,
                  "ghost_hits_lru": self.ghost_hits[0], "ghost_hits_lfu": self.ghost_hits[1]})
        return d


# =============================================================================
class CacheusLiteCache(LeCaRCache):
    """CACHEUS の簡略版。

    - 専門家 = LRU（原典は SR-LRU）と CR-LFU（回数が同じなら最近使われた方を追い出す）
    - 学習率の自動調整（原典の考え方）: N 回アクセスごとに区間ヒット率を測り、
      前回の学習率の変更と、ヒット率の変化の符号が同じなら同じ向きにもう一歩、違えば逆向き。
      ヒット率が 10 区間続けて改善しなければ学習率を無作為に選び直す
    """

    policy_name = "cacheus"

    def __init__(self, capacity: int, sizer, seed: int = 0) -> None:
        super().__init__(capacity, sizer, learning_rate=0.45, seed=seed)
        self.lr_prev = self.lr
        self.hr_prev: Optional[float] = None
        self.win_hits = 0
        self.win_acc = 0
        self.no_improve = 0
        self.lr_changes = 0

    def _lfu_victim(self, exclude: Key) -> Key:
        la, fr = self.last_access, self.freq
        # CR-LFU: 回数が同じなら「最近」使われた方を追い出す（MRU 同点処理 = チャーン耐性）
        return min((k for k in self.store if k != exclude),
                   key=lambda k: (fr.get(k, 0), -la.get(k, 0)))

    def _tick(self, hit: bool) -> None:
        self.win_acc += 1
        self.win_hits += int(hit)
        if self.win_acc < self._n():
            return
        hr = self.win_hits / self.win_acc
        self.win_acc = self.win_hits = 0
        if self.hr_prev is not None:
            d_hr = hr - self.hr_prev
            d_lr = self.lr - self.lr_prev
            self.lr_prev = self.lr
            if d_hr > 0:
                self.no_improve = 0
            else:
                self.no_improve += 1
            if self.no_improve >= 10:
                self.lr = self.rng.uniform(1e-3, 1.0)
                self.no_improve = 0
            elif d_hr != 0:
                direction = 1.0 if (d_lr >= 0) == (d_hr > 0) else -1.0
                step = max(abs(d_lr), 0.01 * self.lr)
                self.lr = min(1.0, max(1e-3, self.lr + direction * step))
            self.lr_changes += 1
        self.hr_prev = hr

    def __getitem__(self, key: Key) -> bool:
        self._tick(True)
        return super().__getitem__(key)

    def __setitem__(self, key: Key, value: bool) -> None:
        if key not in self.store:
            self._tick(False)
        super().__setitem__(key, value)

    def stats(self) -> Dict[str, Any]:
        d = super().stats()
        d.update({"lr_changes": self.lr_changes})
        return d


FACTORIES = {
    "lfu": lambda cap, sizer: LFUCache(cap, sizer),
    "lecar": lambda cap, sizer: LeCaRCache(cap, sizer),
    "cacheus": lambda cap, sizer: CacheusLiteCache(cap, sizer),
}


def _selftest() -> None:
    class _S:
        def entry_weight(self, k, v):
            return 1
    s = _S()
    for name, f in FACTORIES.items():
        c = f(5, s)
        rng = random.Random(0)
        hits = acc = 0
        for _ in range(5000):
            k = (0, f"node:{int(rng.paretovariate(1.1)) % 50}")
            acc += 1
            if k in c:
                c[k]
                hits += 1
            else:
                c[k] = True
            assert c.total_weight <= 5, (name, c.total_weight)
        print(f"  {name:<8} hit={100 * hits / acc:5.1f}%  {c.stats()}")
    print("baselines.py selftest OK")


if __name__ == "__main__":
    _selftest()
