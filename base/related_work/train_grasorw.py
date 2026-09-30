#!/usr/bin/env python3
"""Fit per-block GraSorw loading models from measured calibration CSVs.

CSV columns: block,mode,walks,vertices,seconds (mode: full or on_demand).
These must be actual loading+execution measurements, NOT invented cache scores.
"""
import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from methods.grasorw import LoadingModel


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calibration-csv', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    samples = defaultdict(lambda: {'full': [], 'on_demand': []})
    with open(a.calibration_csv, newline='') as f:
        for row in csv.DictReader(f):
            walks, vertices = int(row['walks']), int(row['vertices'])
            if walks < 0 or vertices <= 0 or row['mode'] not in ('full', 'on_demand'):
                raise ValueError(f'invalid calibration row: {row}')
            samples[row['block']][row['mode']].append((walks / vertices, float(row['seconds'])))
    if not samples:
        raise ValueError('empty calibration CSV')
    models = {}
    for block, data in samples.items():
        model = LoadingModel.fit(data['full'], data['on_demand'])
        models[block] = dict(asdict(model), threshold=model.threshold)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(schema=1, models=models), indent=2) + '\n')


if __name__ == '__main__':
    main()
