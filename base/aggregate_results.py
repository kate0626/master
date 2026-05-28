#!/usr/bin/env python3
"""
auth-baseline-cache と auth-cache-bfs-degree の結果を 1 つの DataFrame に集約する。

特徴
----
- per-start_node 行を生成し、Length=1 など walks が死んだ run を除外する。
- 失敗 run（Traceback あり / walks=0）も除外する。
- hit_rate は hit/(hit+miss) で再計算（行ごとに正しい値が出る）。
- CSV / Excel に直接吐ける形にして返す。

使い方
-----
  python3 base/aggregate_results.py \
    --baseline-root base/auth-baseline-cache/results \
    --bfs-degree-root base/auth-cache-bfs-degree/results \
    --out base/results_combined.csv

  # pandas/Jupyter から:
  from aggregate_results import collect_all_rows
  df = collect_all_rows("base/auth-baseline-cache/results",
                        "base/auth-cache-bfs-degree/results")
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

# ----------------------------------------------------------------------
# regex
# ----------------------------------------------------------------------
RE_START   = re.compile(r"\[START_NODE\]\s+(\d+)")
RE_AVG_LEN = re.compile(r"Avg length:\s+([\d.]+),\s+total steps:\s+(\d+)")
RE_AUTH    = re.compile(r"Total authorization time \(sum over all servers\):\s+([\d.]+)")
RE_WALK    = re.compile(r"Total walk time \(sum over all servers\):\s+([\d.]+)")
RE_CALLS   = re.compile(r"Total authorization calls \(sum over all servers\):\s+(\d+)")
RE_HIT     = re.compile(r"Auth cache hit:\s+(\d+),\s+miss:\s+(\d+),\s+hit_rate:\s+([\d.]+)")
RE_LOOKUPS = re.compile(r"Total auth cache lookups:\s+(\d+)")

# 失敗・例外検出
RE_TRACEBACK = re.compile(r"Traceback \(most recent call last\)")

# directory 命名 (e.g. "bfs-lru_far2_depth2_100" or "lru_100")
RE_DIRNAME = re.compile(
    r"^(?P<policy>[a-z]+(?:-[a-z]+)*)"
    r"(?:_far(?P<far>\d+))?"
    r"(?:_depth(?P<depth>\d+))?"
    r"_(?P<capacity>\d+)$"
)

# ----------------------------------------------------------------------
@dataclass
class Run:
    project:    str   # "baseline" / "bfs-degree"
    alpha:      float
    walks:      int
    capacity:   int
    graph:      str
    policy:     str
    far_threshold:    Optional[int]
    prefetch_depth:   Optional[int]
    start_node: int
    avg_length: float
    total_steps: int
    auth_time:  float
    walk_time:  float
    auth_calls: int
    cache_hit:  int
    cache_miss: int
    hit_rate:   float
    # 集計時 filter フラグ
    is_length1: bool          # walk が即死した start_node
    is_failed:  bool          # Traceback 等で stats が空
    source_log: str

# ----------------------------------------------------------------------
def _parse_dir(name: str) -> Optional[dict]:
    m = RE_DIRNAME.match(name)
    if not m:
        return None
    return {
        "policy": m.group("policy"),
        "far":    int(m.group("far"))   if m.group("far")   else None,
        "depth":  int(m.group("depth")) if m.group("depth") else None,
        "capacity": int(m.group("capacity")),
    }


def _detect_graph_from_log(log_path: Path) -> Optional[str]:
    """memory.log から実際の dataset 名（amazon0601 / vldb / karate 等）を抽出。"""
    mem_log = log_path.with_suffix("").parent / log_path.with_suffix("").name
    # memory.log は <graph>.memory.log
    cand = log_path.parent / (log_path.stem + ".memory.log")
    if not cand.exists():
        # fallback: graph.memory.log
        for f in log_path.parent.glob("*.memory.log"):
            cand = f
            break
    if cand.exists():
        try:
            text = cand.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None
        m = re.search(r"dataset/Louvain/graph/(\w+)\.gr", text)
        if m:
            return m.group(1)
    # last-chance: filename of the log
    if "amazon" in log_path.stem: return "amazon0601"
    if "vldb"   in log_path.stem: return "vldb"
    if "karate" in log_path.stem: return "karate"
    return None


def _parse_log(log_path: Path) -> Iterator[dict]:
    """1 つの <graph>.log から per-start_node の run を yield する。"""
    if not log_path.exists():
        return

    text = log_path.read_text(encoding="utf-8", errors="ignore")
    has_traceback = bool(RE_TRACEBACK.search(text))

    # [START_NODE] ブロックで分割する
    blocks = re.split(r"=== \[START_NODE\] ", text)
    if len(blocks) <= 1:
        return

    for blk in blocks[1:]:
        m_start = re.match(r"(\d+)", blk)
        if not m_start:
            continue
        sn = int(m_start.group(1))

        avg_len = 0.0
        total_steps = 0
        m = RE_AVG_LEN.search(blk)
        if m:
            avg_len = float(m.group(1))
            total_steps = int(m.group(2))

        m = RE_AUTH.search(blk);    auth_time  = float(m.group(1)) if m else 0.0
        m = RE_WALK.search(blk);    walk_time  = float(m.group(1)) if m else 0.0
        m = RE_CALLS.search(blk);   calls      = int(m.group(1))    if m else 0
        m = RE_HIT.search(blk)
        if m:
            hit  = int(m.group(1))
            miss = int(m.group(2))
        else:
            hit = miss = 0

        denom = hit + miss
        hit_rate = hit / denom if denom > 0 else 0.0

        # 完全に値が取れない run は failed
        is_failed = (
            auth_time == 0.0 and walk_time == 0.0 and calls == 0 and denom == 0
        )

        # Length=1 = ウォークが即死している start_node（典型的に walk_time < 1.0s）
        # avg_len <= 1.001 で判定（Avg length: 1.000 のみ）
        is_length1 = (avg_len <= 1.001 and total_steps > 0) or walk_time < 1.0

        yield {
            "start_node": sn,
            "avg_length": avg_len,
            "total_steps": total_steps,
            "auth_time": auth_time,
            "walk_time": walk_time,
            "auth_calls": calls,
            "cache_hit": hit,
            "cache_miss": miss,
            "hit_rate": hit_rate,
            "is_length1": is_length1,
            "is_failed": is_failed or has_traceback and (auth_time == 0.0),
        }


def _scan_results_root(root: Path, project_label: str) -> Iterator[Run]:
    """
    結果ディレクトリを再帰スキャンし、<policy>_<cap> ディレクトリ直下の
    <graph>.log を全て読む。
    パスから alpha, walks, graph, policy 系メタを推定する。
    """
    for log_path in root.rglob("*.log"):
        if log_path.name.endswith(".memory.log"):
            continue
        if log_path.name == "all_policies_summary.log":
            continue

        # 親ディレクトリ名 = "<policy>_..._<cap>"
        dir_info = _parse_dir(log_path.parent.name)
        if not dir_info:
            continue

        # メタを上位ディレクトリから推定
        alpha = walks = None
        parts = list(log_path.parts)
        for p in parts:
            m = re.match(r"alpha([\d.]+)_walks_?(\d+)_capa_?\d+", p)
            if m:
                alpha = float(m.group(1))
                walks = int(m.group(2))
                break
        if alpha is None:
            # baseline は "amazon0601/lru_100/..." のようにメタなしのケースもある
            # この場合は walks=100, alpha=0.1（旧設定）扱いにしておく
            alpha = 0.1
            walks = 100

        # 実際のグラフは memory.log の dataset path で判定（ディレクトリ名は嘘の場合あり）
        graph = _detect_graph_from_log(log_path) or log_path.stem

        for r in _parse_log(log_path):
            yield Run(
                project=project_label,
                alpha=alpha,
                walks=walks,
                capacity=dir_info["capacity"],
                graph=graph,
                policy=dir_info["policy"],
                far_threshold=dir_info["far"],
                prefetch_depth=dir_info["depth"],
                start_node=r["start_node"],
                avg_length=r["avg_length"],
                total_steps=r["total_steps"],
                auth_time=r["auth_time"],
                walk_time=r["walk_time"],
                auth_calls=r["auth_calls"],
                cache_hit=r["cache_hit"],
                cache_miss=r["cache_miss"],
                hit_rate=r["hit_rate"],
                is_length1=r["is_length1"],
                is_failed=r["is_failed"],
                source_log=str(log_path),
            )


# ----------------------------------------------------------------------
def collect_all_rows(baseline_root: str, bfs_degree_root: str):
    import pandas as pd
    rows = []
    if baseline_root:
        rows.extend(asdict(r) for r in _scan_results_root(Path(baseline_root), "baseline"))
    if bfs_degree_root:
        rows.extend(asdict(r) for r in _scan_results_root(Path(bfs_degree_root), "bfs-degree"))
    return pd.DataFrame(rows)


def summarize(df, group_cols=None):
    """
    valid run（is_length1=False & is_failed=False）だけを使った平均テーブル。
    hit_rate は重み付き平均（Σhit / Σ(hit+miss)）で算出。
    """
    import pandas as pd
    if group_cols is None:
        group_cols = ["project", "alpha", "walks", "capacity", "graph",
                      "policy", "far_threshold", "prefetch_depth"]

    valid = df[(~df["is_length1"]) & (~df["is_failed"])].copy()

    grouped = valid.groupby(group_cols, dropna=False).agg(
        n_starts=("start_node", "nunique"),
        avg_length=("avg_length", "mean"),
        auth_time_mean=("auth_time", "mean"),
        auth_time_sum =("auth_time", "sum"),
        walk_time_mean=("walk_time", "mean"),
        walk_time_sum =("walk_time", "sum"),
        auth_calls_sum=("auth_calls", "sum"),
        hit_sum =("cache_hit", "sum"),
        miss_sum=("cache_miss", "sum"),
    ).reset_index()

    grouped["hit_rate"] = grouped["hit_sum"] / (grouped["hit_sum"] + grouped["miss_sum"]).replace(0, 1)
    return grouped


# ----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root",   default="base/auth-baseline-cache/results")
    parser.add_argument("--bfs-degree-root", default="base/auth-cache-bfs-degree/results")
    parser.add_argument("--out",    default="base/results_combined.csv")
    parser.add_argument("--summary-out", default="base/results_summary.csv")
    args = parser.parse_args()

    try:
        import pandas as pd  # noqa
    except ImportError:
        print("pandas is required: pip install pandas", file=sys.stderr)
        sys.exit(1)

    df = collect_all_rows(args.baseline_root, args.bfs_degree_root)
    df.to_csv(args.out, index=False)
    print(f"[OK] per-start-node rows: {len(df)}  -> {args.out}")

    n_l1     = df["is_length1"].sum()
    n_failed = df["is_failed"].sum()
    print(f"  excluded length=1 : {n_l1}")
    print(f"  excluded failed   : {n_failed}")

    summary = summarize(df)
    summary.to_csv(args.summary_out, index=False)
    print(f"[OK] summary rows: {len(summary)}    -> {args.summary_out}")


if __name__ == "__main__":
    main()
