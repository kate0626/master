#!/usr/bin/env python3
"""Replay saved 32-start settings once, then compare reuse horizons.

All horizons share the same eligible events and Low/High cuts. No old outputs
are overwritten. Completed runs produce both bar and matrix summaries.
"""
import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

import run_degree_pairs as run


def compare_plot(rows, graph, horizons, alphas, out):
    plt = run.common.plt
    fig, axes = plt.subplots(3, 2 * len(alphas), squeeze=False,
                             figsize=(14 * len(alphas), 13), sharey=True)
    groups = [('Low', 'Low'), ('Low', 'High'), ('High', 'Low'), ('High', 'High')]
    lookup = {(r['alpha'], r['kind'], r['x'], r['y'], r['horizon'], r['x_group'], r['y_group']): r
              for r in rows if r['graph'] == graph}
    width = .8 / len(horizons)
    for i, (x, y) in enumerate(run.PAIRS):
        for j, alpha in enumerate(alphas):
            for k, kind in enumerate(('node', 'edge')):
                ax = axes[i, j*2+k]
                for hidx, h in enumerate(horizons):
                    rates = [100 * lookup[alpha, kind, x, y, h, a, b]['rate'] for a, b in groups]
                    positions = run.common.np.arange(4) + (hidx - (len(horizons)-1)/2)*width
                    ax.bar(positions, rates, width=width, label=f'Next {h} accesses')
                ax.set_xticks(range(4), ['Low/Low', 'Low/High', 'High/Low', 'High/High'])
                ax.set_title(f'{x} x {y} / alpha={alpha} / {kind}')
                ax.set_ylim(0, 100)
                ax.set_ylabel('Reuse rate [%]')
                ax.grid(axis='y', alpha=.2)
                ax.set_axisbelow(True)
    fig.suptitle(f'{graph}: reuse horizon comparison (32 starts, fixed groups)', fontsize=19)
    fig.legend(*axes[0, 0].get_legend_handles_labels(), loc='upper center',
               bbox_to_anchor=(.5, .955), ncol=len(horizons))
    fig.text(.5, .02, f'All horizons use identical events and cuts; final {max(horizons)} accesses per start excluded.\n'
             'Rates pool counts across starts. Time is measured in accesses across the concatenated walks, not seconds.',
             ha='center')
    fig.tight_layout(rect=(0, .07, 1, .91))
    for ext in ('png', 'pdf'):
        fig.savefig(out / f'{graph}_D_pairs_s32_horizon_comparison.{ext}', dpi=150)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--detail-dir', required=True, type=Path)
    p.add_argument('--graph-dir', type=Path, default=run.common.ROOT / 'dataset/Louvain/graph')
    p.add_argument('--horizons', nargs='+', type=int, default=[10, 100, 1000])
    p.add_argument('--out-dir', type=Path)
    a = p.parse_args()
    horizons = sorted(set(a.horizons))
    if not horizons or horizons[0] < 1:
        p.error('horizons must be positive')
    graphs = list(run.bars.GRAPHS)
    alphas = ['0.1', '0.01']
    metas = {}
    for graph in graphs:
        if not (a.graph_dir / run.common.GRAPHS[graph]).is_file():
            p.error(f'graph missing: {graph}')
        for alpha in alphas:
            path = a.detail_dir / f'D_pairs_{graph}_a{alpha}_w1000_s32_h100_meta.json'
            meta = json.loads(path.read_text())
            cfg = meta['original_config']
            if (len(meta['starts']) != 32 or len(set(meta['starts'])) != 32
                    or float(cfg['alpha']) != float(alpha) or cfg['walks'] != 1000
                    or cfg['partition_dir'] is not None or cfg['owner_filter'] is not None):
                p.error(f'incompatible source settings: {path}')
            metas[graph, alpha] = meta
    out = a.out_dir or a.detail_dir.parent / ('D_horizons_s32_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir(parents=True, exist_ok=False)
    detail_dir, summary_dir = out / 'detail', out / 'summary'
    detail_dir.mkdir(); summary_dir.mkdir()
    (out / 'manifest.json').write_text(json.dumps(dict(horizons=horizons,
        eligibility_horizon=max(horizons), sources={f'{g}_a{al}': m for (g, al), m in metas.items()},
        note='Same eligible events and cutoffs for every horizon; not identical to old h100-only eligibility.'), indent=2))
    all_rows = []
    for graph in graphs:
        graph_path = a.graph_dir / run.common.GRAPHS[graph]
        digest = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        edges = run.source.srv.load_edge_list(graph_path)
        for alpha in alphas:
            meta = metas[graph, alpha]
            if meta['graph_sha256'] != digest:
                raise ValueError(f'graph changed since saved experiment: {graph}')
            cfg = meta['original_config']
            cluster = run.source.sim.VirtualCluster(edges, cfg['server_count'], None)
            cluster.build_servers(lambda: run.source.srv.build_authz_cache('none', 0), 1., 1.)
            degree = run.source.build_degree_map(cluster)
            details = {h: [] for h in horizons}
            for i, start in enumerate(meta['starts']):
                seq = run.source.run_walks(cluster, [start], cfg['walks'], float(alpha),
                       cfg['seed'], cfg['max_hops'], cfg['no_backtrack'])[start]
                events, _, _, _ = run.common.records(seq, degree)
                previous = None
                for h in horizons:
                    rows = run.pair.tally(events, start, h, pairs=run.PAIRS, eligibility_horizon=max(horizons))
                    if previous is not None:
                        assert len(rows) == len(previous)
                        for before, after in zip(previous, rows):
                            assert all(before[k] == after[k] for k in ('kind', 'x', 'y', 'x_group', 'y_group', 'n', 'x_cut', 'y_cut'))
                            assert before['reuses'] <= after['reuses']
                    previous = rows
                    details[h].extend(rows)
                del seq, events
                print(f'{graph} alpha={alpha}: start {i+1}/32', flush=True)
            for h in horizons:
                tag = f'{graph}_a{alpha}_s32_h{h}'
                if details[h]:
                    run.pair.write_csv(detail_dir / f'{tag}_by_start.csv', details[h])
                rows = run.aggregate(details[h], graph, alpha, 32)
                for row in rows:
                    row.update(horizon=h, eligibility_horizon=max(horizons))
                run.pair.write_csv(detail_dir / f'{tag}_summary.csv', rows)
                all_rows.extend(rows)
            del cluster, degree
    run.pair.write_csv(summary_dir / 'D_pairs_s32_all_horizons.csv', all_rows)
    for h in horizons:
        rows = [r for r in all_rows if r['horizon'] == h]
        run.overview(rows, graphs, alphas, h, 32, summary_dir)
        run.matrix_summary(rows, graphs, alphas, h, 32, summary_dir,
            title=f'D pairs: 32 starts, horizon={h}, shared eligibility={max(horizons)} accesses')
    for graph in graphs:
        compare_plot(all_rows, graph, horizons, alphas, summary_dir)
    print(f'Complete: {summary_dir}', flush=True)


if __name__ == '__main__':
    main()
