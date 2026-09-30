#!/usr/bin/env python3
"""English best-group matrix for the new D-pair experiments; reads CSVs only."""
import argparse
import csv
from pathlib import Path

import analyze as common
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

PAIRS = [('D', 'H'), ('D', 'O'), ('D', 'R')]
GROUPS = [('Low', 'Low'), ('Low', 'High'), ('High', 'Low'), ('High', 'High')]
LABELS = ['Low x Low', 'Low x High', 'High x Low', 'High x High']
COLORS = ['#e0e0e0', '#7db9d3', '#e3ae79', '#8ec99c']


def draw_summary(rows, graphs, alphas, horizon, starts, out, *, pairs=None,
                 title=None, filename=None, pair_labels=None):
    pairs = PAIRS if pairs is None else pairs
    lookup = {}
    for row in rows:
        key = (row['graph'], str(row['alpha']), row['kind'], row['x'], row['y'],
               row['x_group'], row['y_group'])
        if key in lookup:
            raise ValueError(f'Duplicate summary cell: {key}')
        lookup[key] = row
    with common.plt.rc_context({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42}):
        fig, axes = common.plt.subplots(len(alphas), 2, squeeze=False,
                                       figsize=(max(16, 4.4 * len(pairs)), 2.3 + len(alphas) * max(2.8, len(graphs) * .8)))
        for i, alpha in enumerate(alphas):
            for j, kind in enumerate(('node', 'edge')):
                ax = axes[i, j]
                best = common.np.full((len(graphs), len(pairs)), common.np.nan)
                for g, graph in enumerate(graphs):
                    for p, (x, y) in enumerate(pairs):
                        rates = []
                        for a, b in GROUPS:
                            row = lookup.get((graph, alpha, kind, x, y, a, b))
                            n = int(row['n']) if row else 0
                            rates.append(100 * int(row['reuses']) / n if n else float('nan'))
                        valid = [k for k, rate in enumerate(rates) if common.np.isfinite(rate)]
                        if len(valid) < 2:
                            label = 'Insufficient data'
                        else:
                            ordered = sorted(valid, key=lambda k: rates[k], reverse=True)
                            first, second = ordered[:2]
                            best[g, p] = first
                            label = (f'{LABELS[first]}  {rates[first]:.1f}%\n'
                                     f'Gap: {rates[first] - rates[second]:.1f} pp')
                            if len(valid) < 4:
                                label += f'\n({len(valid)}/4 groups available)'
                        ax.text(p, g, label, ha='center', va='center', fontsize=10)
                cmap = ListedColormap(COLORS)
                cmap.set_bad('white')
                ax.imshow(best, cmap=cmap, vmin=-.5, vmax=3.5, aspect='auto')
                ax.set_xticks(range(len(pairs)), pair_labels or [f'{x} x {y}' for x, y in pairs])
                ax.set_yticks(range(len(graphs)), graphs)
                ax.set_title(f'alpha={alpha} / {kind}')
                ax.set_xticks(common.np.arange(-.5, len(pairs), 1), minor=True)
                ax.set_yticks(common.np.arange(-.5, len(graphs), 1), minor=True)
                ax.grid(which='minor', color='white', linewidth=2)
                ax.tick_params(which='minor', bottom=False, left=False)
        fig.suptitle(title or f'D factor pairs: best observed reuse group ({starts} starts, horizon={horizon})', fontsize=17)
        fig.legend(handles=[Patch(facecolor=c, label=l) for c, l in zip(COLORS, LABELS)],
                   loc='lower center', bbox_to_anchor=(.5, .065), ncol=4,
                   title='Color = best observed group')
        fig.text(.5, .015,
                 'Cell: best group, reuse rate, and gap to the runner-up (percentage points). High R = more recent.\n'
                 'Rates pool access counts across starts. Ties use LL, LH, HL, HH order. Colors do not imply statistical significance.',
                 ha='center', fontsize=9)
        fig.tight_layout(rect=(0, .16, 1, .94))
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        for ext in ('png', 'pdf'):
            stem = filename or f'D_pairs_s{starts}_h{horizon}_all_graphs_four_group_summary'
            path = out / f'{stem}.{ext}'
            fig.savefig(path, dpi=150)
            print(path)
        common.plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir', required=True, type=Path)
    p.add_argument('--num-starts', type=int, default=32)
    p.add_argument('--horizon', type=int, default=100)
    p.add_argument('--out-dir', type=Path)
    a = p.parse_args()
    rows = []
    for path in sorted(a.input_dir.glob(f'D_pairs_*_a*_w*_s{a.num_starts}_h{a.horizon}_summary.csv')):
        with path.open(newline='') as f:
            for row in csv.DictReader(f):
                if int(row['requested_starts']) != a.num_starts:
                    raise ValueError(f'Start count does not match: {path}')
                rows.append(row)
    if not rows:
        p.error('No matching D-pair summary CSVs found')
    graphs = [g for g in ('er_mid', 'ba_mid', 'amazon', 'vldb', 'slashdot', 'enron')
              if any(r['graph'] == g for r in rows)]
    alphas = sorted({r['alpha'] for r in rows}, key=float, reverse=True)
    print(f'Available graphs: {graphs}; alphas: {alphas}. Missing cells are marked as insufficient data.')
    draw_summary(rows, graphs, alphas, a.horizon, a.num_starts,
                 a.out_dir or a.input_dir / 'summary_matrix')


if __name__ == '__main__':
    main()
