#!/usr/bin/env python3
"""Rerun D×H/D×O/D×R with more starts, retaining saved experiment settings.

Only this entry point starts experiments. Outputs go to a NEW directory;
an existing output directory is rejected, so previous figures cannot be lost.
"""
import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import plot_pair_four_groups as pair

common = pair.common
source = pair.source
PAIRS = [('D', 'H'), ('D', 'O'), ('D', 'R')]
sys.path.insert(0, str(common.HERE / '08_pair_four_groups'))
import plot_grouped_bars as bars
from plot_degree_matrix import draw_summary as matrix_summary

# analyze.py selects a system Japanese font at import time. Override it with
# Matplotlib's bundled font: non-ASCII system font names can fail PDF encoding.
common.plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42})


def aggregate(detail, graph, alpha, starts):
    groups = defaultdict(lambda: [0, 0, 0])
    for row in detail:
        key = row['kind'], row['x'], row['y'], row['x_group'], row['y_group']
        groups[key][0] += row['n']
        groups[key][1] += row['reuses']
        groups[key][2] += row['n'] > 0
    rows = []
    for kind in ('node', 'edge'):
        for x, y in PAIRS:
            for a, b in pair.GROUPS:
                xg, yg = ('High' if a else 'Low'), ('High' if b else 'Low')
                n, reuses, covered = groups[kind, x, y, xg, yg]
                rows.append(dict(graph=graph, alpha=alpha, kind=kind, x=x, y=y,
                                 x_group=xg, y_group=yg, n=n, n_starts=covered,
                                 requested_starts=starts, reuses=reuses,
                                 rate=reuses / n if n else float('nan')))
    return rows


def overview(rows, graphs, alphas, horizon, starts, out):
    lookup = {(g, a): {(r['kind'], r['x'], r['y'], r['x_group'], r['y_group']): r
                      for r in rows if r['graph'] == g and r['alpha'] == a}
              for g in graphs for a in alphas}
    fig, axes = common.plt.subplots(3, 2 * len(alphas), squeeze=False,
                                    figsize=(14 * len(alphas), 13), sharey=True)
    for i, xy in enumerate(PAIRS):
        for j, alpha in enumerate(alphas):
            for k, kind in enumerate(('node', 'edge')):
                ax = axes[i, 2 * j + k]
                bars.draw_panel(ax, lookup, xy, alpha, kind, graphs, language="en")
                ax.set_title(f'{xy[0]} × {xy[1]} / α={alpha} / {kind}')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=4, bbox_to_anchor=(.5, .965))
    fig.suptitle(f'D factor pairs: {starts} starts / reuse within the next {horizon} accesses', fontsize=19)
    fig.text(.5, .012, 'Low: at or below the cut per start/type/pair. High: above the cut. High R means more recent access.\n'
             'Rates pool access counts across starts. Cuts and group counts are saved in by_start.csv.',
             ha='center', fontsize=11)
    fig.tight_layout(rect=(0, .065, 1, .93))
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'D_pairs_s{starts}_h{horizon}_all_graphs.{ext}', dpi=150)
    common.plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--num-starts', type=int, default=32)
    p.add_argument('--graphs', nargs='+', choices=list(common.GRAPHS), default=list(bars.GRAPHS))
    p.add_argument('--alphas', nargs='+', choices=['0.1', '0.01'], default=['0.1', '0.01'])
    p.add_argument('--horizon', type=int, default=100)
    p.add_argument('--meta-dir', type=Path, default=common.PC / 'results/cap')
    p.add_argument('--graph-dir', type=Path, default=common.ROOT / 'dataset/Louvain/graph')
    p.add_argument('--out-dir', type=Path)
    a = p.parse_args()
    if a.num_starts < 8 or a.horizon < 1:
        p.error('num-starts must be >= 8; horizon must be positive')
    if len(set(a.graphs)) != len(a.graphs) or len(set(a.alphas)) != len(a.alphas):
        p.error('graphs and alphas must be distinct')
    # Validate every source before starting any expensive RW.
    metas = {}
    for graph in a.graphs:
        if not (a.graph_dir / common.GRAPHS[graph]).is_file():
            p.error(f'graph missing: {a.graph_dir / common.GRAPHS[graph]}')
        for alpha in a.alphas:
            path = a.meta_dir / f'accessfactors_{graph}_a{alpha}_w1000_s8_keys.meta.json'
            meta = json.loads(path.read_text())
            cfg = meta['config']
            if (cfg['partition_dir'] is not None or cfg['owner_filter'] is not None
                    or cfg['walks'] != 1000 or float(cfg['alpha']) != float(alpha)):
                p.error(f'unsupported source settings: {path}')
            if len(meta['starts']) != 8 or len(set(meta['starts'])) != 8:
                p.error(f'expected 8 distinct original starts: {path}')
            metas[graph, alpha] = (meta, path)
    out = a.out_dir or common.HERE / '08_pair_four_groups' / (
        f'D_pairs_s{a.num_starts}_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir(parents=True, exist_ok=False)
    print(f'Output: {out}', flush=True)
    all_rows = []
    for graph in a.graphs:
        graph_path = a.graph_dir / common.GRAPHS[graph]
        edges = source.srv.load_edge_list(graph_path)
        for alpha in a.alphas:
            meta, meta_path = metas[graph, alpha]
            cfg = meta['config']
            cluster = source.sim.VirtualCluster(edges, cfg['server_count'], None)
            cluster.build_servers(lambda: source.srv.build_authz_cache('none', 0), 1., 1.)
            deg_map = source.build_degree_map(cluster)
            pool = sorted(source.largest_component_nodes(cluster) if cfg['largest_component'] else cluster.nodes)
            starts = list(meta['starts'])
            if not set(starts).issubset(pool) or len(pool) < a.num_starts:
                raise ValueError(f'{graph}: invalid original starts or insufficient nodes')
            remaining = sorted(set(pool) - set(starts))
            starts += random.Random(cfg['start_seed']).sample(remaining, a.num_starts - len(starts))
            tag = f'D_pairs_{graph}_a{alpha}_w{cfg["walks"]}_s{len(starts)}_h{a.horizon}'
            manifest = dict(source_metadata=str(meta_path), original_config=cfg,
                            source_metadata_sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                            graph_sha256=hashlib.sha256(graph_path.read_bytes()).hexdigest(),
                            starts=starts, retained_starts=meta['starts'], pairs=PAIRS,
                            horizon=a.horizon, sampling='retain original starts, sample remaining uniformly')
            (out / f'{tag}_meta.json').write_text(json.dumps(manifest, indent=2) + '\n')
            detail = []
            for i, start in enumerate(starts):
                seq = source.run_walks(cluster, [start], cfg['walks'], float(alpha),
                                       cfg['seed'], cfg['max_hops'], cfg['no_backtrack'])[start]
                if start in meta['starts'] and len(seq) != meta['per_start'][str(start)]['n_access']:
                    raise RuntimeError(f'replay length differs: {tag}, start={start}')
                events, _, _, _ = common.records(seq, deg_map)
                detail.extend(pair.tally(events, start, a.horizon, pairs=PAIRS))
                print(f'{graph} α={alpha} start {i+1}/{len(starts)} ({start})', flush=True)
                del events, seq
            if detail:
                pair.write_csv(out / f'{tag}_by_start.csv', detail)
            summary = aggregate(detail, graph, alpha, len(starts))
            pair.write_csv(out / f'{tag}_summary.csv', summary)
            all_rows.extend(summary)
            for kind in ('node', 'edge'):
                for ext in ('png', 'pdf'):
                    pair.plot_condition(summary, f'{graph} ({len(starts)} starts)', alpha, kind,
                                        a.horizon, out / f'{tag}_{kind}.{ext}', pairs=PAIRS, language="en")
            del cluster, deg_map
    pair.write_csv(out / f'D_pairs_s{a.num_starts}_h{a.horizon}_all_conditions.csv', all_rows)
    overview(all_rows, a.graphs, a.alphas, a.horizon, a.num_starts, out)
    matrix_summary(all_rows, a.graphs, a.alphas, a.horizon, a.num_starts, out / 'summary_matrix')
    print(f'Complete: {out}', flush=True)


if __name__ == '__main__':
    main()
