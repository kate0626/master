"""Offline Belady: exact only for equal-size entries and unit miss costs."""
from collections import defaultdict, deque
from common.cache import WeightedCache


class Belady(WeightedCache):
    policy = "opt"

    def __init__(self, capacity, sizer, trace):
        super().__init__(capacity, sizer)
        self.trace = trace
        self.index = 0
        self.future = defaultdict(deque)
        for i, key in enumerate(trace):
            if any(self.sizer.entry_weight(key, value) != 1 for value in (False, True)):
                raise ValueError('OPT requires unit-size entries, including DENY decisions')
            self.future[key].append(i)

    def __contains__(self, key):
        if self.index >= len(self.trace) or self.trace[self.index] != key:
            raise ValueError("OPT replay diverged from the recorded per-server trace")
        self.future[key].popleft()
        self.index += 1
        return super().__contains__(key)

    def victim(self):
        return max(self.data, key=self.next_use)

    def next_use(self, key):
        return self.future[key][0] if self.future[key] else float('inf')

    def admit(self, key):
        # Include the incoming item in the farthest-future choice. Bypass is
        # allowed by the proposed admission policy too, so mandatory admission
        # would not be a valid upper bound for this comparison.
        return (key in self.data or self.total_weight < self.capacity
                or self.next_use(key) < self.next_use(self.victim()))
