#!/usr/bin/env python3
"""Register related-work caches, then start the SAME proposed HTTP RW server."""
import argparse
import json
import os
import sys
from common.upstream import server
from common.registry import install
from common.preprocess import load_or_prepare


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument('--method-metadata')
    p.add_argument('--method-starts', type=int, nargs='+', default=None)
    p.add_argument('--method-seed', type=int, default=42)
    p.add_argument('--method-k', type=int, default=2)
    p.add_argument('--method-ordering', choices=['bfs', 'id'], default='bfs')
    a, remaining = p.parse_known_args()
    # Read engine options without consuming them. Full validation stays upstream.
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument('--edges')
    probe.add_argument('--server-id', type=int, default=0)
    probe.add_argument('--cache-key-mode', default='full')
    opts, _ = probe.parse_known_args(remaining)
    sys.argv[1:] = remaining
    if '-h' in remaining or '--help' in remaining:
        install({'degree': {}, 'layout': {}})
        print('Extension options: --method-metadata PATH --method-starts N [N ...] '
              '--method-seed N --method-k N --method-ordering bfs|id')
        server.main()
        return
    if not opts.edges:
        p.error('--edges is required')
    if opts.cache_key_mode != 'full':
        p.error('comparison requires full authorization keys (node_only can alias DENY decisions)')
    starts = a.method_starts or [int(os.environ.get('START', '0'))]
    metadata = load_or_prepare(opts.edges, a.method_metadata, a.method_ordering)
    install(metadata, starts, a.method_seed + opts.server_id, a.method_k)
    print(json.dumps(dict(event='related_work_preprocessing', starts=starts,
                          graph_sha256=metadata['graph_sha256'],
                          ordering=metadata['ordering'], method_seed=a.method_seed,
                          method_k=a.method_k,
                          preprocessing_seconds=metadata['preprocessing_seconds'])), flush=True)
    server.main()


if __name__ == '__main__':
    main()
