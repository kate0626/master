#!/usr/bin/env python3
"""Export actual Low/High thresholds from saved experiments; no RW replay."""
import csv
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAIRS = [('D', 'H'), ('D', 'O'), ('D', 'R'), ('H', 'O'), ('H', 'R'), ('O', 'R')]


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    detail = []
    for path in sorted(HERE.glob('*_by_start.csv')):
        match = re.fullmatch(r'(.+)_a([\d.]+)_w(\d+)_s(\d+)_h(\d+)_by_start.csv', path.name)
        if not match:
            continue
        graph, alpha, walks, starts, horizon = match.groups()
        grouped = defaultdict(list)
        with path.open(newline='') as f:
            for row in csv.DictReader(f):
                grouped[row['start'], row['kind'], row['x'], row['y']].append(row)
        for (start, kind, x, y), rows in grouped.items():
            assert len(rows) == 4 and {(r['x_group'], r['y_group']) for r in rows} == {
                ('Low', 'Low'), ('Low', 'High'), ('High', 'Low'), ('High', 'High')}
            assert len({(r['x_cut'], r['y_cut']) for r in rows}) == 1
            xc, yc = float(rows[0]['x_cut']), float(rows[0]['y_cut'])
            entry = dict(graph=graph, alpha=alpha, walks=int(walks),
                         starts_label=int(starts), horizon=int(horizon), start=int(start),
                         kind=kind, pair=f'{x}×{y}', x=x, y=y,
                         x_cut=xc, y_cut=yc,
                         x_low=f'{x} <= {xc!r}', x_high=f'{x} > {xc!r}',
                         y_low=f'{y} <= {yc!r}', y_high=f'{y} > {yc!r}',
                         r_age_cut=(1 / yc - 1) if y == 'R' else '',
                         n=sum(int(r['n']) for r in rows))
            for a, b in [('Low', 'Low'), ('Low', 'High'), ('High', 'Low'), ('High', 'High')]:
                row = next(r for r in rows if (r['x_group'], r['y_group']) == (a, b))
                entry[f'n_{a}_{b}'] = int(row['n'])
            detail.append(entry)
    if not detail:
        raise SystemExit('No saved threshold data found')
    detail.sort(key=lambda r: (r['graph'], -float(r['alpha']), r['walks'],
                              r['starts_label'], r['horizon'], r['kind'], r['start'], r['pair']))
    out = HERE / 'thresholds'
    out.mkdir(exist_ok=True)
    write_csv(out / 'thresholds_by_start.csv', detail)
    groups = defaultdict(list)
    for row in detail:
        groups[row['graph'], row['alpha'], row['walks'], row['starts_label'],
               row['horizon'], row['kind'], row['pair']].append(row)
    summary = []
    for (graph, alpha, walks, starts, horizon, kind, pair), rows in groups.items():
        summary.append(dict(graph=graph, alpha=alpha, walks=walks, starts_label=starts,
                            horizon=horizon, kind=kind, pair=pair, n_starts=len(rows),
                            x_cut_min=min(r['x_cut'] for r in rows),
                            x_cut_max=max(r['x_cut'] for r in rows),
                            y_cut_min=min(r['y_cut'] for r in rows),
                            y_cut_max=max(r['y_cut'] for r in rows)))
    write_csv(out / 'threshold_ranges.csv', summary)
    lines = ['# 全ペアの大小判定基準（保存済み実験）', '',
             '各要素の値が **境界以下なら小、境界より大きければ大**。', '',
             '境界はグラフ・α・始点・node/edge・ペアごとに異なる。アクセス件数をできるだけ半々にし、同値は分割しない。', '',
             'D＝次数（edgeは両端点次数の平均）、H＝今回のRWの歩数、O＝今回より前の累積アクセス回数、R＝1/(1+A)。Aは前回からのアクセス間隔。', '',
             'R大はAが小さい側。Rの境界cは、R小ならA ≥ 1/c−1、R大ならA < 1/c−1に対応する。初回のRは未定義のため除外。', '',
             '未来horizon件を観測できるアクセスのみを使う。末尾除外は始点ごとの連結アクセス列に対して行う。', '',
             '以下の範囲は「始点間で境界がどれだけ異なるか」であり、小群・大群の値の範囲ではない。', '',
             '[始点別の全境界と4群の件数（CSV）](thresholds_by_start.csv) / [境界範囲（CSV）](threshold_ranges.csv)', '',
             f'抽出件数：始点別 {len(detail)} 行、条件別 {len(summary)} 行。新しいRWは実行せず保存値を抽出。', '']
    configs = sorted({(r['graph'], r['alpha'], r['walks'], r['starts_label'], r['horizon']) for r in detail})
    for graph, alpha, walks, starts, horizon in configs:
        subset = [r for r in detail if (r['graph'], r['alpha'], r['walks'], r['starts_label'], r['horizon']) == (graph, alpha, walks, starts, horizon)]
        lines += [f'## {graph} / α={alpha} / walks={walks} / s={starts} / h={horizon}', '',
                  '| 型 | ペア | 左要素の境界：最小〜最大 | 右要素の境界：最小〜最大 | 始点数 |',
                  '|---|---|---:|---:|---:|']
        for kind in ('node', 'edge'):
            for x, y in PAIRS:
                rs = [r for r in subset if r['kind'] == kind and r['pair'] == f'{x}×{y}']
                if rs:
                    lines.append(f"| {kind} | {x}×{y} | {min(r['x_cut'] for r in rs):.8g}〜{max(r['x_cut'] for r in rs):.8g} | {min(r['y_cut'] for r in rs):.8g}〜{max(r['y_cut'] for r in rs):.8g} | {len(rs)} |")
        lines += ['', '<details>', '<summary>始点別の全境界を表示</summary>', '',
                  '| 型 | 始点 | ペア | 左要素の境界 | 右要素の境界 |', '|---|---:|---|---:|---:|']
        for r in subset:
            lines.append(f"| {r['kind']} | {r['start']} | {r['pair']} | {r['x_cut']!r} | {r['y_cut']!r} |")
        lines += ['', '</details>', '']
    (out / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'{len(detail)} exact rows; {len(summary)} range rows → {out}')


if __name__ == '__main__':
    main()
