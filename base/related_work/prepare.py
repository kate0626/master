#!/usr/bin/env python3
"""Create reusable metadata before graph partitioning; entity IDs stay intact."""
import argparse
import json
from pathlib import Path
from common.preprocess import prepare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--edges', required=True)
    p.add_argument('--ordering', choices=['bfs', 'id'], default='bfs')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    data = prepare(a.edges, a.ordering)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + '\n')
    print(f"{out}: {len(data['layout'])} entities, {data['preprocessing_seconds']:.3f}s")


if __name__ == '__main__':
    main()
