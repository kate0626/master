"""Degree-based admission adapted to per-server authorization decisions.

PaGraph-inspired: fixed top-degree node keys, lazily filled after authorization.
SOOP-inspired: whole degree buckets above a budget-derived strict threshold.
Neither caches adjacency lists or fabricates allow=True decisions.
"""
from collections import defaultdict
from common.cache import WeightedCache


class StaticDegree(WeightedCache):
    policy = "degree_static"

    def __init__(self, capacity, sizer, degree, starts=(0,), threshold=False):
        super().__init__(capacity, sizer)
        self.eligible = set()
        self.threshold = None
        candidates = [(int(s), entity) for s in sorted(set(starts)) for entity in degree]
        candidates.sort(key=lambda key: (-degree[key[1]], key))
        budget = capacity
        if threshold:
            self.policy = "soop_admission"
            buckets = defaultdict(list)
            for key in candidates:
                buckets[degree[key[1]]].append(key)
            self.threshold = 0
            for d in sorted(buckets, reverse=True):
                cost = sum(self.cost(k) for k in buckets[d])
                if cost > budget:
                    self.threshold = d
                    break
                self.eligible.update(buckets[d])
                budget -= cost
        else:
            for key in candidates:
                cost = self.cost(key)
                if cost <= budget:
                    self.eligible.add(key)
                    budget -= cost

    def cost(self, key):
        return max(self.sizer.entry_weight(key, True),
                   self.sizer.entry_weight(key, False))

    def admit(self, key):
        return key in self.eligible

    def victim(self):
        raise RuntimeError("static selection exceeded its reserved budget")

    def stats(self):
        return dict(super().stats(), eligible_keys=len(self.eligible),
                    degree_threshold=self.threshold)
