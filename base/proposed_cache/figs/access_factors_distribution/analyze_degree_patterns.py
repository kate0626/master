#!/usr/bin/env python3
"""Paired start-level D-high minus D-low contrasts; no RW execution."""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--min-count', type=int, default=20)
    a = p.parse_args()
    rng = np.random.default_rng(20260928)
    results, paired = [], []
    for path in sorted((a.run_dir / 'detail').glob('*_by_start.csv')):
        match = re.fullmatch(r'(.+)_a([\d.]+)_s32_h(\d+)_by_start.csv', path.name)
        if not match:
            continue
        graph, alpha, horizon = match.groups()
        groups = defaultdict(dict)
        with path.open() as f:
            for r in csv.DictReader(f):
                assert r['x'] == 'D'
                key = r['kind'], r['y'], r['y_group'], int(r['start'])
                assert r['x_group'] not in groups[key]
                groups[key][r['x_group']] = r
        conditions = defaultdict(list)
        for (kind, factor, level, start), cells in groups.items():
            lo, hi = cells['Low'], cells['High']
            nl, nh = int(lo['n']), int(hi['n'])
            if min(nl, nh) < a.min_count:
                continue
            assert lo['x_cut'] == hi['x_cut'] and lo['y_cut'] == hi['y_cut']
            low, high = int(lo['reuses']) / nl, int(hi['reuses']) / nh
            item = dict(graph=graph, alpha=alpha, horizon=int(horizon), kind=kind,
                        factor=factor, level=level, start=start,
                        low_n=nl, high_n=nh, low_reuses=int(lo['reuses']), high_reuses=int(hi['reuses']),
                        low_rate=100*low, high_rate=100*high, delta_pp=100*(high-low))
            paired.append(item)
            conditions[kind, factor, level].append(item)
        for (kind, factor, level), items in conditions.items():
            delta = np.array([r['delta_pp'] for r in items])
            boot = delta[rng.integers(0, len(delta), size=(5000, len(delta)))].mean(axis=1)
            lower, upper = np.quantile(boot, [.025, .975])
            pooled = 100*(sum(r['high_reuses'] for r in items)/sum(r['high_n'] for r in items)
                          - sum(r['low_reuses'] for r in items)/sum(r['low_n'] for r in items))
            results.append(dict(graph=graph, alpha=alpha, horizon=int(horizon), kind=kind,
                                factor=factor, level=level, n_starts=len(items),
                                mean_delta_pp=float(delta.mean()), median_delta_pp=float(np.median(delta)),
                                ci_low=float(lower), ci_high=float(upper),
                                positive_starts=int((delta > 0).sum()),
                                positive_fraction=float((delta > 0).mean()), pooled_delta_pp=pooled,
                                favorable=bool(len(items)>=24 and delta.mean()>=1 and lower>0
                                               and (delta>0).mean()>=.75)))
    out = a.run_dir / 'degree_patterns'
    out.mkdir(exist_ok=True)
    for name, rows in [('paired_starts.csv', paired), ('conditional_effects.csv', results)]:
        with (out/name).open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    good = sorted([r for r in results if r['favorable']], key=lambda r: -r['mean_delta_pp'])
    lines = ['# Dが大きいと再アクセス率が高い条件', '',
             'D大−D小を、同じ始点・型・他要素の大小ごとに計算。各側20アクセス以上の始点だけを採用し、始点を等重みで平均。', '',
             '95%区間は始点ペアを単位とする5000回のpercentile bootstrap（固定seed）。多重比較補正なしの探索的区間であり、因果効果や確証的な有意差を意味しない。', '',
             '候補条件：有効始点24以上、平均差1ポイント以上、区間下限>0、75%以上の始点で正。この閾値は探索用の表示基準。', '',
             '大小の区切りは始点・型・ペア別。HはRW内の現在の歩数でありBFS距離ではない。再アクセス間隔は同一始点のRWを連結した列のアクセス数。', '',
             f'分析した条件数={len(results)}、上記候補条件数={len(good)}。', '',
             '| graph | alpha | horizon | type | condition | starts | mean Δ [pp] | 95% interval | positive starts |',
             '|---|---:|---:|---|---|---:|---:|---|---:|']
    for r in good:
        lines.append(f"| {r['graph']} | {r['alpha']} | {r['horizon']} | {r['kind']} | {r['factor']} {r['level']} | {r['n_starts']} | {r['mean_delta_pp']:+.2f} | [{r['ci_low']:+.2f}, {r['ci_high']:+.2f}] | {r['positive_starts']} |")
    (out/'README.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42})
    graphs = ['er_mid', 'ba_mid', 'amazon', 'vldb', 'slashdot', 'enron']
    columns = [(f, level) for f in ['H', 'O', 'R'] for level in ['Low', 'High']]
    row_keys = [(g, h) for g in graphs for h in [10, 100, 1000]]
    lookup = {(r['kind'], r['alpha'], r['graph'], r['horizon'], r['factor'], r['level']): r for r in results}
    for kind in ['node', 'edge']:
        fig, axes = plt.subplots(1, 2, figsize=(15, 13), layout='constrained')
        limit = max(abs(r['mean_delta_pp']) for r in results if r['kind'] == kind)
        for ax, alpha in zip(axes, ['0.1', '0.01']):
            grid = np.full((len(row_keys), len(columns)), np.nan)
            for i, (graph, horizon) in enumerate(row_keys):
                for j, (factor, level) in enumerate(columns):
                    r = lookup.get((kind, alpha, graph, horizon, factor, level))
                    if r:
                        grid[i, j] = r['mean_delta_pp']
                        ax.text(j, i, f"{r['mean_delta_pp']:+.1f}" + (' *' if r['favorable'] else ''),
                                ha='center', va='center', fontsize=8,
                                color='white' if abs(r['mean_delta_pp']) > .65*limit else 'black')
            image = ax.imshow(grid, cmap='RdBu_r', vmin=-limit, vmax=limit, aspect='auto')
            ax.set_xticks(range(6), [f'{f}\n{level}' for f, level in columns])
            ax.set_yticks(range(18), [f'{g} / h={h}' for g, h in row_keys])
            ax.set_title(f'{kind} / alpha={alpha}')
            for y in [2.5, 5.5, 8.5, 11.5, 14.5]:
                ax.axhline(y, color='black', linewidth=.6)
        fig.colorbar(image, ax=axes, label='D-high minus D-low reuse rate [percentage points]')
        fig.suptitle('D benefit patterns: equal-weight paired-start contrasts\n'
                     '* exploratory candidate: mean >= 1 pp, CI lower > 0, positive in >= 75% of starts, n >= 24', fontsize=12)
        for ext in ['png', 'pdf']:
            fig.savefig(out/f'degree_effect_patterns_{kind}.{ext}', dpi=150)
        plt.close(fig)
    print(f'{len(results)} conditions, {len(good)} favorable candidates -> {out}')
    for r in good:
        print(r['graph'],r['alpha'],r['horizon'],r['kind'],r['factor'],r['level'],
              round(r['mean_delta_pp'],2),[round(r['ci_low'],2),round(r['ci_high'],2)],
              str(r['positive_starts'])+'/'+str(r['n_starts']))


if __name__ == '__main__':
    main()
