"""GraSorw Section 5.2: learned block-loading cost model, not a cache policy.

Fit full-load t_f = a_f * eta + b_f, on-demand t_o = a_o * eta,
eta = number of walks in the bucket / number of vertices in the block.
Call choose() with the CURRENT bucket, never with future evaluation accesses.
The existing authorization engine has no block-I/O/bucket execution API; this
component is deliberately not advertised as an end-to-end GraSorw baseline.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LoadingModel:
    full_slope: float
    full_intercept: float
    demand_slope: float

    @classmethod
    def fit(cls, full_samples, demand_samples):
        """Samples are (eta, seconds), from separate calibration runs."""
        full, demand = list(full_samples), list(demand_samples)
        if len(full) < 2 or not demand:
            raise ValueError('need >=2 full-load and >=1 on-demand measurements')
        if any(not math.isfinite(x) or not math.isfinite(y) or x < 0 or y < 0
               for x, y in full + demand):
            raise ValueError('eta/time must be finite and nonnegative')
        mx = sum(x for x, y in full) / len(full)
        my = sum(y for x, y in full) / len(full)
        variance = sum((x - mx) ** 2 for x, y in full)
        dxx = sum(x * x for x, y in demand)
        if variance == 0 or dxx == 0:
            raise ValueError('calibration needs distinct full-load eta and positive demand eta')
        af = sum((x - mx) * (y - my) for x, y in full) / variance
        bf = my - af * mx
        ao = sum(x * y for x, y in demand) / dxx
        return cls(af, bf, ao)

    @property
    def threshold(self):
        denominator = self.demand_slope - self.full_slope
        return self.full_intercept / denominator if denominator > 0 else None

    def choose(self, walks, vertices):
        if walks < 0 or vertices <= 0:
            raise ValueError('walks must be nonnegative; vertices must be positive')
        if walks == 0:
            return 'on_demand'
        eta = walks / vertices
        full = max(0, self.full_slope * eta + self.full_intercept)
        demand = max(0, self.demand_slope * eta)
        return 'full' if full < demand else 'on_demand'
