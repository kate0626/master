#!/usr/bin/env python3
"""Repeat the proposed virtual distributed RW, changing only cache policy."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from common.upstream import server, virtual, register
from common.registry import ONLINE, install, unit_sizer
from common.preprocess import load_or_prepare
from methods.opt import Belady


class RecordingCache(server.NoDecisionCache):
    def __init__(self, trace):
        self.trace = trace

    def __contains__(self, key):
        if self.trace is not None:
            self.trace.append(key)
        return False


class TraceDigest:
    def __init__(self, server_count):
        self.digests = [hashlib.sha256() for _ in range(server_count)]

    def observe(self, sid, key):
        self.digests[sid].update((json.dumps(key, separators=(',', ':')) + '\n').encode())

    def hexdigest(self):
        return hashlib.sha256(''.join(d.hexdigest() for d in self.digests).encode()).hexdigest()


def run(args):
    if args.server_count < 1 or args.cache_capacity < 1 or args.walks < 1 or args.max_hops < 1:
        raise ValueError('server-count, capacity, walks and max-hops must be positive')
    if not 0 <= args.alpha <= 1:
        raise ValueError('alpha must be in [0,1]')
    if 'opt' in args.policies and args.capacity_mode != 'entries':
        raise ValueError('OPT requires --capacity-mode entries for ALL compared policies; '
                         'weighted-size Belady is not a theoretical optimum')
    if not args.policies or len(set(args.policies)) != len(args.policies):
        raise ValueError('provide distinct policies')
    if args.k < 1 or len(set(args.start_nodes)) != len(args.start_nodes):
        raise ValueError('K must be positive and start nodes must be distinct')
    if args.partition_dir:
        for sid in range(args.server_count):
            if not (Path(args.partition_dir) / f'node_to_starts_server{sid}.json').is_file():
                raise ValueError(f'missing authorization shard for server {sid}')
    metadata = load_or_prepare(args.edges, args.metadata, args.ordering)
    install(metadata, args.start_nodes, args.cache_seed, args.k)
    cluster = virtual.VirtualCluster(server.load_edge_list(Path(args.edges)), args.server_count,
                                     Path(args.partition_dir) if args.partition_dir else None)
    if any(s not in cluster.nodes for s in args.start_nodes):
        raise ValueError('start node not present in graph')
    sizer = unit_sizer() if args.capacity_mode == 'entries' else server.DecisionCacheSizer()
    common = dict(vars(args), cache_key_mode='full', cache_sizer=sizer,
                  theta=1.0, delta=0.0, lambda_learn=0.0, beta=1.0, gamma=1.0,
                  cf=5.0, cl=5.0, cd=10.0, rho=0.0, cr=10.0,
                  reuse_w=args.w, hop_exp=1.0, deg_exp=1.0,
                  size_neutral=True, reuse_edge_no_deg=True)
    rows = []
    for seed in args.seeds:
        a = SimpleNamespace(**dict(common, seed=seed))
        # A dedicated no-cache pass fixes future requests for offline OPT.
        traces = [[] if 'opt' in args.policies else None for _ in range(args.server_count)]
        digest = TraceDigest(args.server_count)
        a.access_observer = digest.observe
        factory_index = iter(range(args.server_count))
        register('_record', lambda c, s: RecordingCache(traces[next(factory_index)]))
        record_start = perf_counter()
        baseline = virtual.run_policy(cluster, '_record', a)
        trace_seconds = perf_counter() - record_start
        trace_hash = digest.hexdigest()
        for policy in args.policies:
            # Per-server independent RNGs reset for every run, outside RW RNGs.
            install(metadata, args.start_nodes, args.cache_seed, args.k)
            if policy == 'random':
                from methods.generic import RandomCache
                ids = iter(range(args.server_count))
                register('random', lambda c, s: RandomCache(c, s, args.cache_seed + next(ids)))
            if policy == 'opt':
                ids = iter(range(args.server_count))
                register('opt', lambda c, s: Belady(c, s, traces[next(ids)]))
            digest = TraceDigest(args.server_count)
            a.access_observer = digest.observe
            begin = perf_counter()
            result = virtual.run_policy(cluster, 'reuse_score' if policy == 'proposed' else policy, a)
            elapsed = perf_counter() - begin
            if result['accesses'] != baseline['accesses'] or result['total_hops'] != baseline['total_hops']:
                raise AssertionError('cache policy changed the RW workload')
            if digest.hexdigest() != trace_hash:
                raise AssertionError('cache policy changed a per-server authorization access sequence')
            if policy == 'opt':
                assert all(s.cache.index == len(traces[s.sid]) for s in cluster.servers)
            result.update(policy=policy, seed=seed, cache_seed=args.cache_seed,
                          server_count=args.server_count, capacity=args.cache_capacity,
                          capacity_mode=args.capacity_mode, alpha=args.alpha, walks=args.walks,
                          no_backtrack=args.no_backtrack, start_nodes=args.start_nodes,
                          trace_sha256=trace_hash, trace_generation_seconds=trace_seconds,
                          simulator_seconds=elapsed, preprocessing_seconds=(
                              metadata['preprocessing_seconds'] if policy in
                              ('degree_static', 'soop_admission', 'lac_layout') else 0),
                          remote_reduction=1 - result['remote_auth_calls'] / baseline['remote_auth_calls']
                          if baseline['remote_auth_calls'] else 0,
                          cache_stats=[s.cache.stats() for s in cluster.servers])
            rows.append(result)
            print(f"seed={seed} {policy:16} hit={result['hit_rate']:.4f} "
                  f"access={result['accesses']} remote={result['remote_auth_calls']}")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(schema=1, arguments=vars(args), graph_sha256=metadata['graph_sha256'],
                    ordering=metadata['ordering'], runtime='virtual',
                    timing_note='simulator_seconds is Python runtime, not distributed latency',
                    opt_scope='equal-size, unit-cost miss optimum with bypass; not RTT optimum')
    if args.partition_dir:
        manifest['partition_sha256'] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(args.partition_dir).glob('node_to_starts_server*.json'))}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    fields = [k for k in rows[0] if k not in ('per_server', 'cache_stats', 'start_nodes')]
    with (out / 'results.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return rows


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--edges', required=True)
    p.add_argument('--server-count', type=int, default=2)
    p.add_argument('--partition-dir')
    p.add_argument('--alpha', type=float, default=0.1)
    p.add_argument('--walks', type=int, default=100)
    p.add_argument('--start-nodes', type=int, nargs='+', default=[0])
    p.add_argument('--seeds', type=int, nargs='+', default=[42])
    p.add_argument('--cache-seed', type=int, default=42)
    p.add_argument('--max-hops', type=int, default=100000)
    p.add_argument('--no-backtrack', action='store_true')
    p.add_argument('--cache-capacity', type=int, default=100)
    p.add_argument('--capacity-mode', choices=['weighted', 'entries'], default='weighted')
    p.add_argument('--policies', nargs='+', choices=[*ONLINE, 'opt', 'arc', 'memo'], default=list(ONLINE))
    p.add_argument('--metadata')
    p.add_argument('--ordering', choices=['bfs', 'id'], default='bfs')
    p.add_argument('--k', type=int, default=2)
    p.add_argument('--w', type=float, default=100)
    p.add_argument('--out-dir', default='base/related_work/results/comparison')
    return p.parse_args(argv)


if __name__ == '__main__':
    run(parse_args())
