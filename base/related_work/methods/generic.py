"""Topology-blind policies. Random uses its own RNG, never the walk RNG."""
import random
import math
from collections import deque
from common.cache import WeightedCache


class FIFO(WeightedCache):
    policy = "fifo"

    def victim(self):
        return next(iter(self.data))


class RandomCache(WeightedCache):
    policy = "random"

    def __init__(self, capacity, sizer=None, seed=42):
        super().__init__(capacity, sizer)
        self.rng = random.Random(seed)

    def victim(self):
        return self.rng.choice(tuple(self.data))


class LFU(WeightedCache):
    """Resident frequency; LRU tie break; forgotten on eviction."""
    policy = "lfu"

    def __init__(self, capacity, sizer=None):
        super().__init__(capacity, sizer)
        self.frequency = {}
        self.last = {}
        self.clock = 0

    def touch(self, key):
        self.clock += 1
        self.frequency[key] = self.frequency.get(key, 0) + 1
        self.last[key] = self.clock

    def removed(self, key):
        self.frequency.pop(key)
        self.last.pop(key)

    def victim(self):
        return min(self.data, key=lambda k: (self.frequency[k], self.last[k]))


class LRUK(WeightedCache):
    """Resident-history LRU-K; immature entries first, oldest first-reference.

    History is bounded by K * resident entries. This explicitly does not retain
    the unbounded nonresident history of some LRU-K implementations.
    """
    policy = "lru_k"

    def __init__(self, capacity, sizer=None, k=2):
        super().__init__(capacity, sizer)
        if k < 1:
            raise ValueError("K must be positive")
        self.k = k
        self.history = {}
        self.clock = 0

    def touch(self, key):
        self.clock += 1
        self.history.setdefault(key, deque(maxlen=self.k)).append(self.clock)

    def removed(self, key):
        self.history.pop(key)

    def victim(self):
        return min(self.data, key=lambda k: (
            len(self.history[k]) >= self.k, self.history[k][0]))


class GClock(WeightedCache):
    """Circular GCLOCK with capped counters. Separate from LRU-K."""
    policy = "gclock"

    def __init__(self, capacity, sizer=None, k=2):
        super().__init__(capacity, sizer)
        if k < 1:
            raise ValueError("K must be positive")
        self.k = k
        self.ring = deque()
        self.refs = {}

    def touch(self, key):
        self.refs[key] = min(self.k, self.refs.get(key, 0) + 1)

    def inserted(self, key):
        self.ring.append(key)
        self.touch(key)

    def removed(self, key):
        self.ring.remove(key)
        self.refs.pop(key)

    def decrement(self, key):
        return 1

    def victim(self):
        # Skip complete revolutions in O(C), preserving circular tie order.
        steps = min(math.ceil(max(0, self.refs[k] - 1e-12)
                                           / self.decrement(k)) for k in self.ring)
        if steps:
            for k in self.ring:
                self.refs[k] = max(0, self.refs[k] - steps * self.decrement(k))
        while True:
            key = self.ring[0]
            if self.refs[key] <= 1e-12:
                return key
            self.refs[key] = max(0, self.refs[key] - self.decrement(key))
            self.ring.rotate(-1)
