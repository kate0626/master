#!/usr/bin/env python3
"""Replace D pairs with 32-start results; retain 8-start non-D pairs."""
import argparse
import csv
import hashlib
import json
import sys
from itertools import combinations, product
from pathlib import Path

from plot_degree_matrix import draw_summary, GROUPS, common

HERE = Path(__file__).resolve().parent
GRAPHS = ['er_mid', 'ba_mid', 'amazon', 'vldb', 'slashdot', 'enron']
PAIRS = list(combinations(['D', 'H', 'O', 'R'], 2))
sys.path.insert(0, str(HERE / '08_pair_four_groups'))
from plot_grouped_bars import draw_panel


def draw_bars(rows, out):
    alphas = ['0.1', '0.01']
    lookups = {(g, a): {} for g, a in product(GRAPHS, alphas)}
    for row in rows:
        lookups[row['graph'], row['alpha']][
            row['kind'], row['x'], row['y'], row['x_group'], row['y_group']] = row
    with common.plt.rc_context({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42}):
        fig, axes = common.plt.subplots(6, 4, figsize=(28, 25), sharey=True)
        for i, pair in enumerate(PAIRS):
            for j, alpha in enumerate(alphas):
                for k, kind in enumerate(('node', 'edge')):
                    ax = axes[i, j * 2 + k]
                    draw_panel(ax, lookups, pair, alpha, kind, GRAPHS, language='en')
                    if i:
                        ax.set_title('')
                    if j * 2 + k:
                        ax.set_ylabel('')
                    else:
                        starts = 32 if 'D' in pair else 8
                        ax.text(-.21, .5, f'{pair[0]} x {pair[1]}\n({starts} starts)',
                                transform=ax.transAxes, rotation=90,
                                ha='center', va='center', fontsize=14, fontweight='bold')
        fig.suptitle('All factor pairs: four-group reuse rates | D pairs: 32 starts; other pairs: 8 starts', fontsize=21)
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, .975),
                   ncol=4, title='Left factor x right factor')
        fig.subplots_adjust(left=.075, right=.99, top=.945, bottom=.065, wspace=.10, hspace=.42)
        fig.text(.5, .022,
                 'Low: at or below the cut per start/type/pair. High: above the cut. High R means more recent access.\n'
                 'Each bar: reuse within the next 100 accesses; access counts are pooled across starts.',
                 ha='center', fontsize=11)
        for ext in ('png', 'pdf'):
            path = out / f'all_graphs_h100_grouped_bars_D32_other8.{ext}'
            fig.savefig(path, dpi=150)
            print(path)
        common.plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detail-dir', required=True, type=Path)
    parser.add_argument('--legacy-dir', type=Path, default=HERE / '08_pair_four_groups')
    parser.add_argument('--out-dir', type=Path)
    args = parser.parse_args()
    merged, sources = [], []
    for graph, alpha in product(GRAPHS, ['0.1', '0.01']):
        for new in (True, False):
            starts = 32 if new else 8
            directory = args.detail_dir if new else args.legacy_dir
            prefix = 'D_pairs_' if new else ''
            path = directory / f'{prefix}{graph}_a{alpha}_w1000_s{starts}_h100_summary.csv'
            with path.open(newline='') as f:
                rows = list(csv.DictReader(f))
            selected = []
            for row in rows:
                if ('D' in (row['x'], row['y'])) != new:
                    continue
                if row['graph'] != graph or float(row['alpha']) != float(alpha):
                    raise ValueError(f'Condition mismatch: {path}')
                if new and int(row['requested_starts']) != 32:
                    raise ValueError(f'Not a 32-start result: {path}')
                n, reuses = int(row['n']), int(row['reuses'])
                if not 0 <= reuses <= n or not 0 <= int(row['n_starts']) <= starts:
                    raise ValueError(f'Invalid counts: {path}')
                selected.append(dict(graph=graph, alpha=alpha, kind=row['kind'],
                                     x=row['x'], y=row['y'], x_group=row['x_group'],
                                     y_group=row['y_group'], n=n, reuses=reuses,
                                     rate=reuses/n if n else float('nan'),
                                     n_starts=int(row['n_starts']), requested_starts=starts,
                                     source_csv=str(path.resolve())))
            pairs = [p for p in PAIRS if ('D' in p) == new]
            expected = {(kind, x, y, a, b) for kind in ('node', 'edge')
                        for x, y in pairs for a, b in GROUPS}
            actual = {(r['kind'], r['x'], r['y'], r['x_group'], r['y_group']) for r in selected}
            if actual != expected or len(selected) != len(expected):
                raise ValueError(f'Missing or duplicate cells: {path}')
            merged.extend(selected)
            sources.append(dict(path=str(path.resolve()), starts=starts,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    out = args.out_dir or args.detail_dir.parent / 'summary'
    out.mkdir(parents=True, exist_ok=True)
    stem = 'all_graphs_h100_four_group_summary_D32_other8'
    with (out / f'{stem}.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(merged[0]))
        writer.writeheader()
        writer.writerows(merged)
    (out / f'{stem}_sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    draw_summary(merged, GRAPHS, ['0.1', '0.01'], 100, 32, out, pairs=PAIRS,
                 title='All factor pairs: best observed reuse group | D pairs: 32 starts; other pairs: 8 starts | horizon=100',
                 filename=stem,
                 pair_labels=[f'{x} x {y}\n({32 if "D" in (x, y) else 8} starts)' for x, y in PAIRS])
    draw_bars(merged, out)
    print(f'Validated {len(merged)} cells from {len(sources)} source CSVs. No RW executed.')


if __name__ == '__main__':
    main()
