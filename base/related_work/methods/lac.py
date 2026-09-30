"""LAC Algorithms 1–4, adapted from pages to authorization entries.

Spatial distance is in the preprocessing layout, NOT network RTT. Each start
has a separate copy of the layout, so different authorization keys never have
zero spatial distance. Reference-counter cap K and circular victim scan are
shared with GCLOCK. SET is refreshed from current residents at each operation.
"""
from bisect import bisect_left, insort
from methods.generic import GClock


class LAC(GClock):
    policy = "lac_layout"

    def __init__(self, capacity, sizer, layout, starts=(0,), k=2):
        super().__init__(capacity, sizer, k)
        self.layout = layout
        self.start_rank = {s: i for i, s in enumerate(sorted(set(starts)))}
        self.universe = len(layout) * len(self.start_rank)
        self.positions = []

    def position(self, key):
        start, entity = key
        if start not in self.start_rank or entity not in self.layout:
            raise ValueError("LAC key absent from declared starts/graph metadata")
        return self.start_rank[start] * len(self.layout) + self.layout[entity]

    def set_value(self, key):
        pos = self.position(key)
        i = bisect_left(self.positions, pos)
        gaps = []
        if i:
            gaps.append(pos - self.positions[i - 1])
        right = i + 1 if i < len(self.positions) and self.positions[i] == pos else i
        if right < len(self.positions):
            gaps.append(self.positions[right] - pos)
        maxdist = max(1, self.universe - len(self.positions))
        return min(1.0, min(gaps, default=maxdist) / maxdist)

    def touch(self, key):
        increment = 1 - self.set_value(key)
        old = self.refs.get(key, 0)
        self.refs[key] = old + increment if old + increment <= self.k else old

    def inserted(self, key):
        insort(self.positions, self.position(key))
        super().inserted(key)

    def removed(self, key):
        self.positions.pop(bisect_left(self.positions, self.position(key)))
        super().removed(key)

    def decrement(self, key):
        return self.set_value(key)
