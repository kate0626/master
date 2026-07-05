#!/usr/bin/env python3
"""RWカバレッジ実験.

RW100(=100本のRandom Walker)までにアクセスされる「ノード」の集合を母数とし,
先頭 K 本(K=10, 20, ...)の RWer でそのうち何割をカバーできるかを調べる.

- entity のうち 'edge_' 始まりはエッジ, それ以外(数値ID)をノードとして扱う.
- per_walk_access.json の per_walk_access(walk_index 昇順)を逐次 union して算出.
- 各 start ノード(ファイル)ごとに計算し, 平均も出す.
"""
import argparse
import glob
import json
import os
import re

DEFAULT_DIR = (
    "results/alpha0.01_walks_100_capa_100/vldb_nobt/none_100"
)


def is_node(entity: str) -> bool:
    return not entity.startswith("edge_")


def coverage_for_file(path: str, ks):
    """1ファイル(=1 start)について, 全ノード集合と各Kでのカバレッジを返す."""
    with open(path) as f:
        data = json.load(f)
    walks = sorted(data["per_walk_access"], key=lambda w: w["walk_index"])
    n_walks = len(walks)

    # 全(RW100)ノード集合
    full_nodes = set()
    for w in walks:
        for ent in w["access"]:
            if is_node(ent):
                full_nodes.add(ent)

    # 先頭K本での累積ノード集合
    seen = set()
    cum = {}  # walk数 -> 累積ノード数
    for i, w in enumerate(walks, start=1):
        for ent in w["access"]:
            if is_node(ent):
                seen.add(ent)
        cum[i] = len(seen)

    result = {
        "path": os.path.basename(path),
        "n_walks": n_walks,
        "total_nodes_rw100": len(full_nodes),
    }
    for k in ks:
        kk = min(k, n_walks)
        covered = cum[kk]
        total = len(full_nodes)
        result[f"k{k}_nodes"] = covered
        result[f"k{k}_coverage"] = (covered / total) if total else 0.0
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--ks", default="10,20", help="カバレッジを測るRWer本数(カンマ区切り)")
    ap.add_argument("--out", default=None, help="CSV出力先(省略時はdir内)")
    args = ap.parse_args()

    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    files = sorted(glob.glob(os.path.join(args.dir, "*_per_walk_access.json")))
    if not files:
        raise SystemExit(f"per_walk_access.json が見つかりません: {args.dir}")

    rows = [coverage_for_file(p, ks) for p in files]

    # 表示
    print(f"対象: {args.dir}")
    print(f"ファイル数(start数): {len(rows)}\n")
    header = f"{'start':<8}{'RW100ノード':>12}"
    for k in ks:
        header += f"{f'K={k}本':>10}{f'cov%':>9}"
    print(header)
    print("-" * len(header))

    def start_label(name):
        m = re.search(r"start=(\d+)", name)
        return f"start={m.group(1)}" if m else name[:8]

    for r in rows:
        line = f"{start_label(r['path']):<8}{r['total_nodes_rw100']:>12}"
        for k in ks:
            line += f"{r[f'k{k}_nodes']:>10}{r[f'k{k}_coverage']*100:>8.1f}%"
        print(line)

    # 平均
    print("-" * len(header))
    avg_line = f"{'平均':<8}{sum(r['total_nodes_rw100'] for r in rows)/len(rows):>12.1f}"
    for k in ks:
        avg_cov = sum(r[f"k{k}_coverage"] for r in rows) / len(rows)
        avg_n = sum(r[f"k{k}_nodes"] for r in rows) / len(rows)
        avg_line += f"{avg_n:>10.1f}{avg_cov*100:>8.1f}%"
    print(avg_line)

    # CSV出力
    out = args.out or os.path.join(args.dir, "rwer_node_coverage.csv")
    cols = ["path", "n_walks", "total_nodes_rw100"]
    for k in ks:
        cols += [f"k{k}_nodes", f"k{k}_coverage"]
    with open(out, "w") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in cols) + "\n")
    print(f"\nCSV出力: {out}")


if __name__ == "__main__":
    main()
