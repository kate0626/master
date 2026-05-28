#!/usr/bin/env python3
"""
既存の results/.../all_policies_summary.log を、
Length=1 と Traceback を除外したうえで再生成する。

各 policy ディレクトリ (`<policy>_<cap>` または
`<policy>_far<F>_depth<D>_<cap>`) の `<graph>.log` を順に再パースし、
[SUMMARY] policy=... controller_duration_sum=... authorization_time_sum=...
n_valid=... walk_per_start=... auth_per_start=...
という新フォーマットで書き出す。

オリジナルは `.bak` として保存する。
"""
from __future__ import annotations

import re
from pathlib import Path

RE_START   = re.compile(r"=== \[START_NODE\]\s+(\d+)")
RE_AVG_LEN = re.compile(r"Avg length:\s+([\d.]+)")
RE_AUTH    = re.compile(r"Total authorization time \(sum over all servers\):\s+([\d.]+)")
RE_WALK    = re.compile(r"Total walk time \(sum over all servers\):\s+([\d.]+)")


def filtered_sums(log_path: Path) -> tuple[float, float, int]:
    """Length=1 と Traceback を除外した (sum_walk, sum_auth, n_valid)。"""
    sum_walk = sum_auth = 0.0
    n_valid  = 0
    current_avg = -1.0
    cur_walk = cur_auth = None
    in_block = False

    def flush():
        nonlocal sum_walk, sum_auth, n_valid, cur_walk, cur_auth, current_avg
        if cur_walk is not None and current_avg > 1.001:
            sum_walk += cur_walk
            sum_auth += (cur_auth or 0.0)
            n_valid  += 1
        cur_walk = cur_auth = None
        current_avg = -1.0

    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if RE_START.search(line):
            if in_block:
                flush()
            in_block = True
            continue
        m = RE_AVG_LEN.search(line)
        if m:
            current_avg = float(m.group(1))
            continue
        m = RE_AUTH.search(line)
        if m and cur_auth is None:
            cur_auth = float(m.group(1))
            continue
        m = RE_WALK.search(line)
        if m and cur_walk is None:
            cur_walk = float(m.group(1))
            continue
    if in_block:
        flush()
    return sum_walk, sum_auth, n_valid


def parse_policy_dir(name: str) -> tuple[str, str] | None:
    """`<policy>[_far<F>_depth<D>]_<cap>` → (policy_label, capacity)。"""
    m = re.match(
        r"^(?P<policy>[a-z]+(?:-[a-z]+)*)"
        r"(?:_far(?P<far>\d+))?"
        r"(?:_depth(?P<depth>\d+))?"
        r"_(?P<capacity>\d+)$",
        name,
    )
    if not m:
        return None
    label = m.group("policy")
    if m.group("far") is not None and m.group("depth") is not None:
        label += f"_far{m.group('far')}_depth{m.group('depth')}"
    return label, m.group("capacity")


def regenerate_for_summary(summary_path: Path) -> None:
    parent = summary_path.parent
    new_lines: list[str] = []
    new_lines.append(
        f"## [REGENERATED] excluded Length=1 (avg<=1.001) and Traceback runs.\n"
    )

    for sub in sorted(parent.iterdir()):
        if not sub.is_dir():
            continue
        info = parse_policy_dir(sub.name)
        if not info:
            continue
        label, cap = info

        # graph ログを探す
        log_paths = [p for p in sub.iterdir() if p.suffix == ".log" and not p.name.endswith(".memory.log")]
        log_paths = [p for p in log_paths if p.name != "all_policies_summary.log"]
        if not log_paths:
            continue
        log_path = sorted(log_paths)[0]  # 通常 1 ファイル

        sw, sa, n = filtered_sums(log_path)
        if n > 0:
            per_w = sw / n
            per_a = sa / n
            new_lines.append(
                f"[SUMMARY] policy={label} "
                f"controller_duration_sum={sw:.6f}s "
                f"authorization_time_sum={sa:.6f}s "
                f"n_valid={n} "
                f"walk_per_start={per_w:.6f}s "
                f"auth_per_start={per_a:.6f}s\n"
            )
        else:
            new_lines.append(
                f"[SUMMARY] policy={label} controller_duration_sum=0.000000s "
                f"authorization_time_sum=0.000000s n_valid=0 walk_per_start=nan auth_per_start=nan  "
                f"# all runs excluded (Length=1 or failed)\n"
            )

    # backup
    bak = summary_path.with_suffix(summary_path.suffix + ".bak")
    if not bak.exists() and summary_path.exists():
        bak.write_bytes(summary_path.read_bytes())

    summary_path.write_text("".join(new_lines), encoding="utf-8")
    print(f"[OK] {summary_path}  ({len(new_lines)-1} policies)")


def main() -> None:
    roots = [
        Path("base/auth-baseline-cache/results"),
        Path("base/auth-cache-bfs-degree/results"),
    ]
    for root in roots:
        for summary in root.rglob("all_policies_summary.log"):
            regenerate_for_summary(summary)


if __name__ == "__main__":
    main()
