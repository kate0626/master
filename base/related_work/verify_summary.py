"""Reject incomplete or non-comparable runs (and optionally compare simulator)."""
import argparse
import json
from pathlib import Path


def verify(path, policies, servers=None, expected=None):
    rows = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith('RESULT\t'):
            _, policy, *fields = line.split('\t')
            if policy in rows:
                raise ValueError(f'duplicate policy: {policy}')
            rows[policy] = dict(f.split('=', 1) for f in fields)
    if set(rows) != set(policies):
        raise ValueError(f'incomplete results: expected {policies}, got {list(rows)}')
    if len({r['accesses'] for r in rows.values()}) != 1:
        raise ValueError('access counts differ across policies')
    for policy, row in rows.items():
        if servers is not None and int(row['servers']) != servers:
            raise ValueError(f'{policy}: missing server statistics')
        if expected is not None:
            matches = [r for r in expected if r['policy'] == policy]
            if len(matches) != 1:
                raise ValueError('expected JSON must contain exactly one seed per policy')
            r = matches[0]
            for k, rk in [('accesses', 'accesses'), ('remote_auth', 'remote_auth_calls'),
                          ('local_auth', 'local_auth_calls')]:
                if int(row[k]) != r[rk]:
                    raise ValueError(f'{policy}: {k} differs: {row[k]} != {r[rk]}')
            if abs(float(row['overall']) - 100 * r['hit_rate']) > .0051:
                raise ValueError(f'{policy}: hit rate differs')
    print(f'verified {len(rows)} policies')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary', required=True)
    p.add_argument('--policies', nargs='+', required=True)
    p.add_argument('--servers', type=int)
    p.add_argument('--servers-conf')
    p.add_argument('--expected-json')
    a = p.parse_args()
    if a.servers_conf:
        a.servers = sum(bool(line.split('#', 1)[0].strip())
                        for line in Path(a.servers_conf).read_text().splitlines())
    verify(a.summary, a.policies, a.servers,
           json.loads(Path(a.expected_json).read_text()) if a.expected_json else None)
