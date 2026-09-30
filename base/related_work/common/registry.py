"""Install method factories for BOTH engines; RW and RPC logic stay upstream."""
from .upstream import server, register
from methods.generic import FIFO, LFU, RandomCache, LRUK, GClock
from methods.degree import StaticDegree
from methods.lac import LAC

ONLINE = ("none", "random", "fifo", "lru", "lfu", "lru_k", "gclock",
          "degree_static", "soop_admission", "lac_layout", "proposed")


def install(metadata, starts=(0,), seed=42, k=2):
    register("fifo", FIFO)
    register("lfu", LFU)
    register("random", lambda c, s: RandomCache(c, s, seed))
    register("lru_k", lambda c, s: LRUK(c, s, k))
    register("gclock", lambda c, s: GClock(c, s, k))
    register("degree_static", lambda c, s: StaticDegree(c, s, metadata['degree'], starts))
    register("soop_admission", lambda c, s: StaticDegree(c, s, metadata['degree'], starts, True))
    register("lac_layout", lambda c, s: LAC(c, s, metadata['layout'], starts, k))


def unit_sizer():
    return server.DecisionCacheSizer(node_weight=1, edge_weight=1, start_weight=0)
