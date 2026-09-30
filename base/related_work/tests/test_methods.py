import itertools
import json
import random
import sys
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.upstream import server, virtual
from common.registry import unit_sizer, install, ONLINE
from common.preprocess import prepare, load_or_prepare
from methods.generic import FIFO, LFU, LRUK, GClock, RandomCache
from methods.degree import StaticDegree
from methods.lac import LAC
from methods.opt import Belady
from methods.grasorw import LoadingModel
from compare import RecordingCache


def key(x):
    return (0, f'node:{x}')


def access(cache, k, value=True):
    hit = k in cache
    if hit:
        assert cache[k] == value
    else:
        cache[k] = value
    return hit


class MethodTests(unittest.TestCase):
    def test_fifo_hit_does_not_reorder(self):
        c = FIFO(2, unit_sizer())
        for x in [1, 2, 1, 3]:
            access(c, key(x))
        self.assertEqual(set(c.data), {key(2), key(3)})

    def test_lfu_frequency_and_lru_tie(self):
        c = LFU(2, unit_sizer())
        for x in [1, 2, 1, 3]:
            access(c, key(x))
        self.assertEqual(set(c.data), {key(1), key(3)})
        self.assertNotIn(key(2), c.frequency)

    def test_lruk_protects_second_reference(self):
        c = LRUK(2, unit_sizer(), k=2)
        for x in [1, 1, 2, 3]:
            access(c, key(x))
        self.assertEqual(set(c.data), {key(1), key(3)})

    def test_gclock_circular_hand(self):
        c = GClock(2, unit_sizer(), k=2)
        for x in [1, 2, 1, 3]:
            access(c, key(x))
        self.assertEqual(set(c.data), {key(1), key(3)})
        self.assertTrue(all(0 <= r <= 2 for r in c.refs.values()))

    def test_weighted_capacity_and_false_decisions(self):
        sizer = server.DecisionCacheSizer(deny_weight=2)
        rng = random.Random(10)
        factories = [FIFO, LFU, LRUK, GClock, RandomCache]
        for factory in factories:
            c = factory(7, sizer)
            for _ in range(100):
                k = (rng.randrange(2), rng.choice(['node:1', 'edge_1_2', 'node:2']))
                value = k[0] == 0
                access(c, k, value)
                self.assertLessEqual(c.current_weight(), 7)
                self.assertEqual(c.current_weight(), sum(sizer.entry_weight(k, v) for k, v in c.data.items()))
            c[(0, 'edge_4_5')] = False  # too large under smaller budget
        c = FIFO(1, sizer)
        c[key(1)] = False
        self.assertEqual(len(c), 0)

    def test_weight_update(self):
        c = FIFO(5, server.DecisionCacheSizer(deny_weight=2))
        c[key(1)] = True
        c[key(2)] = True
        c[key(2)] = False
        self.assertEqual(c.current_weight(), 4)
        self.assertFalse(c[key(2)])

    def test_random_is_independent(self):
        random.seed(10)
        before = random.getstate()
        caches = [RandomCache(2, unit_sizer(), 8) for _ in range(2)]
        for x in range(30):
            for c in caches:
                access(c, key(x % 5))
        self.assertEqual(list(caches[0].data), list(caches[1].data))
        self.assertEqual(before, random.getstate())

    def test_static_no_fabricated_allow_and_budget(self):
        c = StaticDegree(2, unit_sizer(), {'node:1': 4, 'node:2': 2}, [0, 1])
        self.assertEqual(c.eligible, {(0, 'node:1'), (1, 'node:1')})
        self.assertNotIn(key(1), c)
        c[key(1)] = False
        self.assertFalse(c[key(1)])
        c[key(2)] = True
        self.assertNotIn(key(2), c)

    def test_soop_whole_bucket_threshold(self):
        c = StaticDegree(2, unit_sizer(), {'node:1': 5, 'node:2': 3, 'node:3': 3}, threshold=True)
        self.assertEqual(c.threshold, 3)
        self.assertEqual(c.eligible, {key(1)})

    def test_lac_locality_and_termination(self):
        layout = {f'node:{i}': i for i in range(100)}
        c = LAC(3, unit_sizer(), layout, [0, 1])
        for x in [1, 2, 90]:
            access(c, key(x))
        self.assertLess(c.set_value(key(1)), c.set_value(key(90)))
        for i in range(400):
            access(c, (i % 2, f'node:{i % 100}'))
            self.assertLessEqual(c.current_weight(), 3)
            self.assertEqual(len(c.positions), len(c))
        one = LAC(1, unit_sizer(), {'node:1': 0, 'node:2': 1})
        for x in [1, 2, 1]:
            access(one, key(x))
        self.assertEqual(len(one), 1)

    def test_opt_matches_exhaustive_optimum_with_bypass(self):
        for capacity in (1, 2):
            for raw in itertools.product(range(3), repeat=5):
                trace = [key(x) for x in raw]
                c = Belady(capacity, unit_sizer(), trace)
                misses = sum(not access(c, k) for k in trace)

                @lru_cache(None)
                def best(i, residents):
                    if i == len(raw):
                        return 0
                    resident = set(residents)
                    x = raw[i]
                    if x in resident:
                        return best(i + 1, residents)
                    choices = [resident]  # bypass
                    if len(resident) < capacity:
                        choices.append(resident | {x})
                    else:
                        choices += [(resident - {old}) | {x} for old in resident]
                    return 1 + min(best(i + 1, tuple(sorted(s))) for s in choices)
                self.assertEqual(misses, best(0, ()))

    def test_opt_rejects_weighted_or_divergent_trace(self):
        with self.assertRaises(ValueError):
            Belady(3, server.DecisionCacheSizer(), [key(1)])
        c = Belady(1, unit_sizer(), [key(1)])
        with self.assertRaises(ValueError):
            key(2) in c

    def test_grasorw_regression_and_threshold(self):
        model = LoadingModel.fit([(0, 2), (1, 3), (2, 4)], [(1, 5), (2, 10)])
        self.assertAlmostEqual(model.threshold, 0.5)
        self.assertEqual(model.choose(1, 10), 'on_demand')
        self.assertEqual(model.choose(9, 10), 'full')
        with self.assertRaises(ValueError):
            LoadingModel.fit([(1, 1), (1, 2)], [(1, 3)])

    def test_preprocess_preserves_graph_and_validates_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'graph.gr'
            p.write_text('0 1\n1 2\n4 5\n')
            before = p.read_bytes()
            data = prepare(p)
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(data['degree']['node:1'], 2)
            self.assertEqual(len(data['layout']), 8)
            meta = Path(tmp) / 'metadata.json'
            meta.write_text(json.dumps(data))
            self.assertEqual(load_or_prepare(p, meta), data)
            p.write_text('0 2\n')
            with self.assertRaises(ValueError):
                load_or_prepare(p, meta)

    def test_shared_walk_paths_unchanged_with_denies_multiple_starts(self):
        # Capture actual authorization keys, not just aggregate counts.
        edges = [(0, 1), (1, 2), (2, 0), (2, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'graph.gr'
            p.write_text(''.join(f'{u} {v}\n' for u, v in edges))
            metadata = prepare(p)
            install(metadata, [0, 1])
            reference = None
            for policy in ONLINE:
                if policy == 'proposed':
                    continue  # dedicated real/virtual integration check below
                cluster = virtual.VirtualCluster(edges, 3, None)
                cluster._denied_by_sid[cluster.owner(2)] = {2: {0}}
                cluster.build_servers(lambda: server.build_authz_cache(policy, 7), 1, 1)
                histories = [[] for _ in cluster.servers]
                # Delegate every mapping operation, while recording lookup keys.
                for s in cluster.servers:
                    inner = s.cache
                    class Recorder:
                        def __init__(self, cache, hist):
                            self.cache, self.hist = cache, hist
                        def __contains__(self, k):
                            self.hist.append(k)
                            return k in self.cache
                        def __getitem__(self, k):
                            return self.cache[k]
                        def __setitem__(self, k, v):
                            self.cache[k] = v
                    s.cache = Recorder(inner, histories[s.sid])
                for start in (0, 1):
                    for seed in range(15):
                        virtual.run_walk(cluster, start, .1, random.Random(seed), 100, False, True)
                if reference is None:
                    reference = histories
                self.assertEqual(reference, histories, policy)


if __name__ == '__main__':
    unittest.main()
