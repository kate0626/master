"""Shared weighted decision storage. Policy subclasses select victims only."""
from collections import OrderedDict
from .upstream import server


class WeightedCache(server.BaseDecisionCache):
    policy = "abstract"

    def __init__(self, capacity, sizer=None):
        if capacity <= 0:
            raise ValueError("cache capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer or server.DecisionCacheSizer()
        self.data = OrderedDict()
        self.weights = {}
        self.total_weight = 0
        self.insertions = self.updates = self.evictions = 0
        self.rejected_too_large = self.rejected_admission = 0

    def __contains__(self, key):
        return key in self.data

    def __getitem__(self, key):
        value = self.data[key]
        self.touch(key)
        return value

    def __setitem__(self, key, value):
        value = bool(value)
        weight = self.sizer.entry_weight(key, value)
        if weight > self.capacity:
            self.rejected_too_large += 1
            return
        if not self.admit(key):
            self.rejected_admission += 1
            return
        if key in self.data:
            self.total_weight -= self.weights[key]
            self.data[key] = value
            self.weights[key] = weight
            self.total_weight += weight
            self.updates += 1
            self.touch(key)
        else:
            # Evict residents BEFORE insertion (mandatory admission).
            while self.data and self.total_weight + weight > self.capacity:
                self.remove(self.victim())
            self.data[key] = value
            self.weights[key] = weight
            self.total_weight += weight
            self.insertions += 1
            self.inserted(key)
        while self.total_weight > self.capacity:
            self.remove(self.victim())

    def remove(self, key):
        del self.data[key]
        self.total_weight -= self.weights.pop(key)
        self.evictions += 1
        self.removed(key)

    def admit(self, key):
        return True

    def touch(self, key):
        pass

    def inserted(self, key):
        self.touch(key)

    def removed(self, key):
        pass

    def victim(self):
        raise NotImplementedError

    def __len__(self):
        return len(self.data)

    def current_weight(self):
        return self.total_weight

    def max_weight(self):
        return self.capacity

    def storage_objects(self):
        return vars(self)

    def stats(self):
        return dict(super().stats(), policy=self.policy,
                    insertions=self.insertions, updates=self.updates,
                    evictions=self.evictions,
                    rejected_too_large=self.rejected_too_large,
                    rejected_admission=self.rejected_admission)
