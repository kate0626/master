#!/usr/bin/env python3
"""
amazon0601_nobt/test/ にテスト用データを作成する。

構成:
  none_100/           モック（仮置き、5 starts）
  lru_100/            モック（vldb_nobt の比率から推定、5 starts）
  arc_100_h-0/        実測値のコピー（start=0 のみ）
  ppr_demand_cap100/  仮置き（none〜lru の間、LRU寄り、5 starts）

その後 compare_policy_results.py を実行して図を生成する。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
NOBT_DIR = REPO / "base" / "proposed_cache" / "results" / "alpha0.01_walks_100_capa_100" / "amazon0601_nobt"
TEST_DIR = NOBT_DIR / "test"

REAL_ARC_DIR = NOBT_DIR / "arc_100_h-0"

# ===== 1 start ぶんのアクセス規模 (arc 実測 start=0 から) =====
# server0=edge: hit=156, miss=866, total=1022
# server1=node: hit=242, miss=684, total=926
EDGE_TOTAL = 1022
NODE_TOTAL = 926

WALK_NONE = 30.6   # s/start (amazon regular none を参考)
AUTH_NONE = 4.9    # s/start

WALK_LRU  = 28.5   # s/start
AUTH_LRU  = 2.7    # s/start

WALK_PPR  = 29.0   # s/start (LRU より少し遅い＝prefetch なし)
AUTH_PPR  = 2.8    # s/start


def make_transition_json(
    policy_name: str,
    start: int,
    node_hit: int, node_miss: int,
    edge_hit: int, edge_miss: int,
    walk_time: float, auth_time: float,
) -> dict:
    """compare_policy_results.py が読む最小 JSON を返す"""
    total_hit  = node_hit + edge_hit
    total_miss = node_miss + edge_miss
    return {
        "policy": policy_name,
        "start_node": start,
        "cache hit":  total_hit,
        "cache miss": total_miss,
        "walk_time_total": walk_time,
        "auth_time_total": auth_time,
        "per_server_access_stats": [
            {
                "server_id": 0,
                "stats": {
                    "auth_cache_hit":  edge_hit,
                    "auth_cache_miss": edge_miss,
                },
            },
            {
                "server_id": 1,
                "stats": {
                    "auth_cache_hit":  node_hit,
                    "auth_cache_miss": node_miss,
                },
            },
        ],
    }


def write_policy_dir(
    policy_dir: Path,
    policy_name: str,
    *,
    n_starts: int = 5,
    node_hit_rate: float,
    edge_hit_rate: float,
    walk_per_start: float,
    auth_per_start: float,
    filename_cache_tag: str | None = None,
) -> None:
    policy_dir.mkdir(parents=True, exist_ok=True)
    tag = filename_cache_tag or policy_name

    node_hit  = round(node_hit_rate * NODE_TOTAL)
    node_miss = NODE_TOTAL - node_hit
    edge_hit  = round(edge_hit_rate * EDGE_TOTAL)
    edge_miss = EDGE_TOTAL - edge_hit

    for s in range(n_starts):
        data = make_transition_json(
            policy_name, s,
            node_hit, node_miss,
            edge_hit, edge_miss,
            walk_per_start, auth_per_start,
        )
        fname = f"start={s}_walks=100_alpha=0.01_seed=42_cache={tag}_global_transition.json"
        (policy_dir / fname).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    combined = (node_hit + edge_hit) / (NODE_TOTAL + EDGE_TOTAL)
    print(
        f"  [{policy_name}] n={n_starts}  "
        f"node_hit={node_hit_rate:.0%}  edge_hit={edge_hit_rate:.0%}  "
        f"combined={combined:.1%}  walk={walk_per_start}s/start"
    )


def copy_real_arc(test_dir: Path) -> None:
    arc_dst = test_dir / "arc_100_h-0"
    arc_dst.mkdir(parents=True, exist_ok=True)

    copied = 0
    for src in sorted(REAL_ARC_DIR.glob("start=*_global_transition.json")):
        dst = arc_dst / src.name
        shutil.copy2(src, dst)
        copied += 1

    data = json.loads((arc_dst / list(arc_dst.glob("start=*_global_transition.json"))[0]).read_text())
    ch = data.get("cache hit", 0)
    cm = data.get("cache miss", 0)
    print(f"  [arc_100_h-0] n={copied} start(s) (実測) hit_rate={100*ch/(ch+cm):.1f}%")


def main() -> None:
    print(f"=== テストフォルダ作成: {TEST_DIR} ===\n")
    TEST_DIR.mkdir(parents=True, exist_ok=True)

    # ---- none_100: ヒット率 0% ----
    write_policy_dir(
        TEST_DIR / "none_100", "none_cap=0",
        node_hit_rate=0.0, edge_hit_rate=0.0,
        walk_per_start=WALK_NONE, auth_per_start=AUTH_NONE,
        filename_cache_tag="none_cap=0",
    )

    # ---- lru_100: 推定 ~29% ----
    # vldb_nobt の lru: node=38.5%, edge=21.5% を流用
    write_policy_dir(
        TEST_DIR / "lru_100", "lru_cap=100",
        node_hit_rate=0.385, edge_hit_rate=0.215,
        walk_per_start=WALK_LRU, auth_per_start=AUTH_LRU,
        filename_cache_tag="lru_cap=100",
    )

    # ---- arc_100_h-0: 実測値コピー ----
    copy_real_arc(TEST_DIR)

    # ---- ppr_demand_cap100: 仮置き (combined ~35%) ----
    # node_hit_rate=0.46, edge_hit_rate=0.25 → combined=(426+256)/1948≈35%
    write_policy_dir(
        TEST_DIR / "ppr_demand_cap100", "ppr_demand_cap=100",
        node_hit_rate=0.46, edge_hit_rate=0.25,
        walk_per_start=WALK_PPR, auth_per_start=AUTH_PPR,
        filename_cache_tag="ppr_demand_cap=100",
    )

    print()
    # ---- compare_policy_results.py を実行 ----
    compare_script = REPO / "base" / "proposed_cache" / "compare_policy_results.py"
    vldb_summary = (REPO / "base" / "proposed_cache" / "results"
                    / "alpha0.01_walks_100_capa_100" / "vldb_nobt"
                    / "policy_compare" / "policy_compare_summary.csv")
    cmd = [
        sys.executable, str(compare_script),
        "--input", str(TEST_DIR),
        "--policies", "none_100", "lru_100", "arc_100_h-0", "ppr_demand_cap100",
        "--compare-csv", str(vldb_summary),
        "--compare-label", "VLDB",
    ]
    print(f">>> {' '.join(cmd)}\n")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print("[ERROR] compare_policy_results.py failed")
        return

    # ---- rtt_time_compare.py を実行 ----
    rtt_script = REPO / "base" / "proposed_cache" / "rtt_time_compare.py"
    policy_compare_dir = TEST_DIR / "policy_compare"
    cmd2 = [
        sys.executable, str(rtt_script),
        "--input", str(policy_compare_dir),
        "--rtt-sweep-ms", "0,0.5,1,2,5,10",
    ]
    print(f"\n>>> {' '.join(cmd2)}\n")
    result2 = subprocess.run(cmd2, capture_output=False)
    if result2.returncode != 0:
        print("[ERROR] rtt_time_compare.py failed")
        return

    print(f"\n=== 完了 ===")
    print(f"出力先: {policy_compare_dir}")
    for f in sorted(policy_compare_dir.glob("*.png")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
