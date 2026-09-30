#!/usr/bin/env python3
"""Plot existing paired-start degree evidence; does not execute random walks."""
import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/access-factor-distribution-mpl')
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

GRAPHS = ['er_mid', 'ba_mid', 'amazon', 'vldb', 'slashdot', 'enron']
LABELS = ['ER', 'BA', 'Amazon', 'VLDB', 'Slashdot', 'Enron']
COLORS = ['#0072B2', '#D55E00', '#009E73']


def read(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patterns-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.patterns_dir / 'evidence'
    out.mkdir(exist_ok=True)
    rows = read(args.patterns_dir / 'conditional_effects.csv')
    paired = read(args.patterns_dir / 'paired_starts.csv')
    index = {(r['alpha'], r['kind'], r['graph'], int(r['horizon']), r['factor'], r['level']): r for r in rows}
    if len(index) != 432 or len(rows) != 432:
        raise ValueError('Expected 432 unique conditions for the six-graph experiment')
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42, 'font.size': 10})

    def get(alpha, kind, graph, horizon, factor, level):
        r = index[alpha, kind, graph, horizon, factor, level]
        return np.array([float(r[k]) for k in ['mean_delta_pp', 'ci_low', 'ci_high']])

    def point(ax, v, y, color, label=None):
        ax.errorbar(v[0], y, xerr=[[v[0]-v[1]], [v[2]-v[0]]], fmt='o',
                    color=color, capsize=3, label=label, markersize=4)

    def save(fig, name, note):
        fig.text(.5, .012, note, ha='center', fontsize=9)
        fig.tight_layout(rect=(0, .045, 1, .95))
        for ext in ['png', 'pdf']:
            fig.savefig(out / f'{name}.{ext}', dpi=180)
        plt.close(fig)

    for alpha in ['0.01', '0.1']:
        suffix = 'a' + alpha.replace('.', 'p')
        fig, axes = plt.subplots(1, 3, figsize=(15, 6), sharey=True)
        for ax, factor in zip(axes, ['H', 'O', 'R']):
            for i, graph in enumerate(GRAPHS):
                for j, level in enumerate(['Low', 'High']):
                    point(ax, get(alpha, 'node', graph, 1000, factor, level), i + (j-.5)*.26,
                          COLORS[j], f'{factor} {level.lower()}' if i == 0 else None)
            ax.axvline(0, color='gray', linestyle='--', linewidth=1)
            ax.set_title({'H': 'Walk step H', 'O': 'Prior access count O', 'R': 'Recency R'}[factor])
            ax.set_xlabel('D-high minus D-low reuse rate [pp]')
            ax.legend(loc='best')
            ax.grid(axis='x', alpha=.2)
        axes[0].set_yticks(range(6), LABELS)
        axes[0].invert_yaxis()
        fig.suptitle(f'Conditional degree association: nodes | alpha={alpha} | horizon=1,000 accesses')
        save(fig, '01_node_conditions_' + suffix,
             'Dots: equal-weight mean across starts. Bars: 95% start-bootstrap intervals (exploratory; no multiplicity correction).')

        fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True, sharey=True)
        for ax, graph, label in zip(axes.flat, GRAPHS, LABELS):
            for color, factor, level in zip(COLORS, ['H', 'O', 'R'], ['High', 'Low', 'High']):
                values = np.array([get(alpha, 'node', graph, h, factor, level) for h in [10, 100, 1000]])
                ax.plot([10, 100, 1000], values[:, 0], marker='o', color=color, label=f'{factor} {level.lower()}')
                ax.fill_between([10, 100, 1000], values[:, 1], values[:, 2], color=color, alpha=.12)
            ax.axhline(0, color='gray', linestyle='--', linewidth=1)
            ax.set_xscale('log')
            ax.set_xticks([10, 100, 1000], ['10', '100', '1,000'])
            ax.set_title(label)
            ax.grid(alpha=.2)
            ax.set_xlabel('Reuse horizon [accesses]')
            ax.set_ylabel('D-high minus D-low [pp]')
        axes[0, 0].legend()
        fig.suptitle(f'Horizon sensitivity of candidate conditions: nodes | alpha={alpha}')
        save(fig, '02_horizon_sensitivity_' + suffix,
             'Same eligible events and cuts at all horizons. Shading: 95% start-bootstrap intervals. Conditions are separate, not joint.')

        fig, axes = plt.subplots(1, 3, figsize=(15, 6), sharey=True, sharex=True)
        for ax, factor, level in zip(axes, ['H', 'O', 'R'], ['High', 'Low', 'High']):
            for i, graph in enumerate(GRAPHS):
                for j, kind in enumerate(['node', 'edge']):
                    point(ax, get(alpha, kind, graph, 1000, factor, level), i+(j-.5)*.26,
                          COLORS[j], kind if i == 0 else None)
            ax.axvline(0, color='gray', linestyle='--', linewidth=1)
            ax.set_title(f'{factor} {level.lower()}')
            ax.set_xlabel('D-high minus D-low reuse rate [pp]')
            ax.legend()
            ax.grid(axis='x', alpha=.2)
        axes[0].set_yticks(range(6), LABELS)
        axes[0].invert_yaxis()
        fig.suptitle(f'Entity type matters | alpha={alpha} | horizon=1,000 accesses')
        save(fig, '03_node_edge_' + suffix,
             'D: node degree for nodes; mean endpoint degree for edges. Bars: exploratory 95% start-bootstrap intervals.')

    # Direct within-start comparisons of D associations between the two strata.
    grouped = {}
    for r in paired:
        key = (r['alpha'], r['kind'], r['graph'], int(r['horizon']), r['factor'])
        cell = grouped.setdefault(key, {}).setdefault(r['level'], {})
        if r['start'] in cell:
            raise ValueError('Duplicate start')
        cell[r['start']] = float(r['delta_pp'])
    rng = np.random.default_rng(20260928)
    interactions = []
    for key, levels in sorted(grouped.items()):
        alpha, kind, graph, horizon, factor = key
        first, second = ('Low', 'High') if factor == 'O' else ('High', 'Low')
        starts = sorted(set(levels[first]) & set(levels[second]))
        delta = np.array([levels[first][s]-levels[second][s] for s in starts])
        boot = delta[rng.integers(len(delta), size=(5000, len(delta)))].mean(axis=1)
        lo, hi = np.quantile(boot, [.025, .975])
        interactions.append(dict(alpha=alpha, kind=kind, graph=graph, horizon=horizon, factor=factor,
                                 contrast=f'{first}-{second}', n_starts=len(starts), mean_delta_pp=delta.mean(),
                                 ci_low=lo, ci_high=hi))
    with (out/'stratum_contrasts.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(interactions[0]))
        writer.writeheader()
        writer.writerows(interactions)
    for alpha in ['0.01', '0.1']:
        fig, axes = plt.subplots(1, 3, figsize=(15, 6), sharey=True)
        for ax, factor in zip(axes, ['H', 'O', 'R']):
            for i, graph in enumerate(GRAPHS):
                r = next(r for r in interactions if (r['alpha'], r['kind'], r['graph'], r['horizon'], r['factor']) ==
                         (alpha, 'node', graph, 1000, factor))
                point(ax, np.array([r[k] for k in ['mean_delta_pp', 'ci_low', 'ci_high']]), i, COLORS[0])
            ax.axvline(0, color='gray', linestyle='--', linewidth=1)
            ax.set_title(f"{factor}: " + ('low minus high' if factor == 'O' else 'high minus low'))
            ax.set_xlabel('Difference in D association [pp]')
            ax.grid(axis='x', alpha=.2)
        axes[0].set_yticks(range(6), LABELS)
        axes[0].invert_yaxis()
        fig.suptitle(f'Does the condition modify the D association? | nodes | alpha={alpha} | horizon=1,000')
        save(fig, '04_stratum_comparison_a' + alpha.replace('.', 'p'),
             'Positive: stronger D association in H-high / O-low / R-high. Paired differences across common starts; 95% bootstrap intervals.')
    print(out)
    for r in interactions:
        if r['alpha'] == '0.01' and r['kind'] == 'node' and r['horizon'] == 1000:
            print(r['graph'], r['factor'], *(round(r[k], 2) for k in ['mean_delta_pp', 'ci_low', 'ci_high']))


if __name__ == '__main__':
    main()
