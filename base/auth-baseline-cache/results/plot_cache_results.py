import json
import re
from pathlib import Path
from collections import defaultdict
from statistics import mean
from itertools import cycle

import matplotlib.pyplot as plt


GRAPH = "karate"  # "karate", "dolphins", "polbooks", "amazon0601"

"""
時間の格納先:
  *_global_transition.json

メモリの格納先:
  同じ json 内の rss_kb, bytes_est, cache_entries など
"""

POLICY_COLORS = {
    "none": "#4c566a",
    "fifo": "#dd8452",
    "lru": "#4c72b0",
    "lrb": "#55a868",
    "s3lru": "#c44e52",
    "arc": "#8172b2",
    "cacheus": "#937860",
    "unknown": "#8c8c8c",
}


# =========================
# filename parsing
# =========================
def parse_filename(path: Path):
    name = path.name
    pattern = (
        r"start=(?P<start>[^_]+)_"
        r"walks=(?P<walks>[^_]+)_"
        r"alpha=(?P<alpha>[^_]+)_"
        r"seed=(?P<seed>[^_]+)_"
        r"cache=(?P<cache>[^_]+)_"
        r"cap=(?P<cap>[^_]+)_"
        r"global_transition\.json$"
    )
    m = re.match(pattern, name)
    if not m:
        return None

    d = m.groupdict()

    def to_int_or_str(x):
        if x in ("na", "None", "none", "unknown", "null"):
            return x
        try:
            return int(x)
        except Exception:
            return x

    def to_float_or_str(x):
        if x in ("na", "None", "none", "unknown", "null"):
            return x
        try:
            return float(x)
        except Exception:
            return x

    return {
        "start": to_int_or_str(d["start"]),
        "walks": to_int_or_str(d["walks"]),
        "alpha": to_float_or_str(d["alpha"]),
        "seed": to_int_or_str(d["seed"]),
        "cache": str(d["cache"]),
        "cap": to_int_or_str(d["cap"]),
        "filename": name,
    }


def fallback_meta_from_filename(path: Path):
    return {
        "start": "unknown",
        "walks": "unknown",
        "alpha": "unknown",
        "seed": "unknown",
        "cache": "unknown",
        "cap": "unknown",
        "filename": path.name,
    }


# =========================
# helpers
# =========================
def safe_number(x, default=0):
    if x is None:
        return default

    if isinstance(x, (int, float)):
        return x

    if isinstance(x, str):
        s = x.strip()
        if s == "":
            return default
        if s.lower() in {"none", "null", "na", "nan", "unknown"}:
            return default
        try:
            if "." in s:
                return float(s)
            return int(s)
        except Exception:
            return default

    return default


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# aggregation
# =========================
def aggregate_servers(data):
    """
    Accept either:
      - [ {...}, {...} ]
      - {"servers": [ ... ]}
      - single dict for one server

    方針:
    - メモリ系は従来通り total を保持
    - 時間系は以下
        auth_time_total : sum
        walk_time_total : max  (並列実行前提)
        auth_calls_total: sum
        walk_calls_total: sum
    """
    top_level = data if isinstance(data, dict) else {}

    if isinstance(data, list):
        server_list = data
    elif isinstance(data, dict):
        if isinstance(data.get("per_server_access_stats"), list):
            server_list = [
                item.get("stats", {})
                for item in data["per_server_access_stats"]
                if isinstance(item, dict)
            ]
        elif isinstance(data.get("servers"), list):
            server_list = data["servers"]
        else:
            server_list = [data]
    else:
        raise ValueError("Unsupported JSON structure")

    result = {
        # basic info
        "num_servers": 0,
        "server_ids": [],
        # memory / cache
        "rss_kb_total": 0,
        "rss_kb_max_total": 0,
        "cache_entries_total": 0,
        "cache_weight_total": 0,
        "cache_bytes_total": 0,
        "cache_capacity": None,
        # graph / auth table sizes
        "graph_entities_total": 0,
        "graph_total_neighbors_total": 0,
        "local_entities_total": 0,
        "auth_entities_total": 0,
        "auth_total_starts_total": 0,
        "auth_table_entries_total": 0,
        "auth_table_nodes_total": 0,
        "auth_table_edges_total": 0,
        "owner_map_size_total": 0,
        # counters
        "access_total": 0,
        "authorized_total": 0,
        "attempts_total": 0,
        "denied_total": 0,
        "transition_total": 0,
        # derived from counters
        "auth_success_rate": None,
        "deny_rate": None,
        "transition_per_access": None,
        # time / calls
        "auth_time_total": 0.0,
        "walk_time_total": 0.0,
        "auth_calls_total": 0,
        "walk_calls_total": 0,
        "remote_auth_calls_total": 0,
        "local_auth_calls_total": 0,
        # cache hit/miss
        "auth_cache_hit_total": 0,
        "auth_cache_miss_total": 0,
        "auth_cache_hit_rate": None,
        # derived from time
        "avg_auth_time": None,
        "remote_ratio": None,
        "cost_per_walk": None,
    }

    capacities = []
    walk_times = []

    for i, s in enumerate(server_list):
        if not isinstance(s, dict):
            print(f"[warn] server_list[{i}] is not dict: {type(s)}")
            continue

        result["num_servers"] += 1
        if "server_id" in s:
            result["server_ids"].append(s["server_id"])

        memory = s.get("memory", {})
        if not isinstance(memory, dict):
            memory = {}

        # memory / sizes
        result["rss_kb_total"] += safe_number(memory.get("rss_kb"))
        result["rss_kb_max_total"] += safe_number(memory.get("rss_kb_max"))
        result["graph_entities_total"] += safe_number(memory.get("graph_entities"))
        result["graph_total_neighbors_total"] += safe_number(
            memory.get("graph_total_neighbors")
        )
        result["local_entities_total"] += safe_number(memory.get("local_entities"))
        result["auth_entities_total"] += safe_number(memory.get("auth_entities"))
        result["auth_total_starts_total"] += safe_number(
            memory.get("auth_total_starts")
        )
        result["auth_table_entries_total"] += safe_number(
            memory.get("auth_table_entries")
        )
        result["auth_table_nodes_total"] += safe_number(memory.get("auth_table_nodes"))
        result["auth_table_edges_total"] += safe_number(memory.get("auth_table_edges"))
        result["owner_map_size_total"] += safe_number(memory.get("owner_map_size"))
        result["cache_entries_total"] += safe_number(memory.get("cache_entries"))
        result["cache_weight_total"] += safe_number(memory.get("cache_weight"))

        cap = safe_number(memory.get("cache_capacity"), default=None)
        if isinstance(cap, (int, float)):
            capacities.append(cap)

        bytes_est = memory.get("bytes_est", {})
        if isinstance(bytes_est, dict):
            result["cache_bytes_total"] += safe_number(bytes_est.get("authz_cache"))

        # counters_total
        counters_total = memory.get("counters_total", {})
        if isinstance(counters_total, dict):
            result["access_total"] += safe_number(counters_total.get("access"))
            result["authorized_total"] += safe_number(counters_total.get("authorized"))
            result["attempts_total"] += safe_number(counters_total.get("attempts"))
            result["denied_total"] += safe_number(counters_total.get("denied"))
            result["transition_total"] += safe_number(counters_total.get("transition"))

        # time / calls
        result["auth_time_total"] += safe_number(s.get("auth_time_total"), 0.0)
        result["auth_calls_total"] += safe_number(s.get("auth_calls"))
        result["walk_calls_total"] += safe_number(s.get("walk_calls"))
        result["remote_auth_calls_total"] += safe_number(s.get("remote_auth_calls"))
        result["local_auth_calls_total"] += safe_number(s.get("local_auth_calls"))

        wt = safe_number(s.get("walk_time_total"), 0.0)
        walk_times.append(wt)

        # cache hit / miss
        result["auth_cache_hit_total"] += safe_number(s.get("auth_cache_hit"))
        result["auth_cache_miss_total"] += safe_number(s.get("auth_cache_miss"))

    if capacities:
        result["cache_capacity"] = capacities[0]

    # Prefer controller top-level totals when present.
    result["auth_time_total"] = safe_number(
        top_level.get("auth_time_total"), result["auth_time_total"]
    )
    result["walk_time_total"] = safe_number(
        top_level.get("walk_time_total"), result["walk_time_total"]
    )
    result["auth_calls_total"] = safe_number(
        top_level.get("auth_calls"), result["auth_calls_total"]
    )
    result["walk_calls_total"] = safe_number(
        top_level.get("walk_calls"), result["walk_calls_total"]
    )
    result["auth_cache_hit_total"] = safe_number(
        top_level.get("cache hit"), result["auth_cache_hit_total"]
    )
    result["auth_cache_miss_total"] = safe_number(
        top_level.get("cache miss"), result["auth_cache_miss_total"]
    )

    if result["attempts_total"] > 0:
        result["auth_success_rate"] = (
            result["authorized_total"] / result["attempts_total"]
        )
        result["deny_rate"] = result["denied_total"] / result["attempts_total"]

    if result["access_total"] > 0:
        result["transition_per_access"] = (
            result["transition_total"] / result["access_total"]
        )

    hit_denom = result["auth_cache_hit_total"] + result["auth_cache_miss_total"]
    if hit_denom > 0:
        result["auth_cache_hit_rate"] = result["auth_cache_hit_total"] / hit_denom

    # time derived
    if walk_times:
        result["walk_time_total"] = max(walk_times)
    else:
        result["walk_time_total"] = 0.0

    if result["auth_calls_total"] > 0:
        result["avg_auth_time"] = result["auth_time_total"] / result["auth_calls_total"]

    auth_call_denom = (
        result["remote_auth_calls_total"] + result["local_auth_calls_total"]
    )
    if auth_call_denom > 0:
        result["remote_ratio"] = result["remote_auth_calls_total"] / auth_call_denom

    if result["walk_calls_total"] > 0:
        result["cost_per_walk"] = result["auth_time_total"] / result["walk_calls_total"]

    return result


# =========================
# loading all experiment files
# =========================
def load_all_results(folder):
    folder = Path(folder)
    rows = []

    for path in sorted(folder.glob("*_global_transition.json")):
        print(f"Processing {path}...")

        try:
            data = load_json(path)
        except Exception as e:
            print(f"[skip] failed to load json: {path.name}: {e}")
            continue

        meta = parse_filename(path)
        if meta is None:
            print(f"[warn] filename did not match expected pattern: {path.name}")
            meta = fallback_meta_from_filename(path)

        try:
            agg = aggregate_servers(data)
        except Exception as e:
            print(f"[skip] failed to aggregate: {path.name}: {e}")
            continue

        row = {}
        row.update(meta)
        row.update(agg)

        if not isinstance(row["cap"], int) and isinstance(
            row["cache_capacity"], (int, float)
        ):
            row["cap"] = int(row["cache_capacity"])

        if isinstance(row["cap"], int):
            row["cap_label"] = str(row["cap"])
            row["cap_sort_key"] = (0, row["cap"])
        else:
            row["cap_label"] = str(row["cap"])
            row["cap_sort_key"] = (1, str(row["cap"]))

        print(
            "[debug]",
            path.name,
            "num_servers=",
            row["num_servers"],
            "rss_kb_total=",
            row["rss_kb_total"],
            "cache_bytes_total=",
            row["cache_bytes_total"],
            "auth_time_total=",
            row["auth_time_total"],
            "walk_time_total=",
            row["walk_time_total"],
        )

        rows.append(row)

    return rows


# =========================
# grouping / averaging
# =========================
def normalize_policy_name(name: str):
    return str(name).lower()


def group_by_policy(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[normalize_policy_name(r.get("cache", "unknown"))].append(r)
    return grouped


def average_by_xlabel(rows, y_key):
    bucket = defaultdict(list)
    sort_keys = {}
    x_values = {}

    for r in rows:
        y = r.get(y_key)
        if y is None:
            continue
        x = r.get("cap_label", "unknown")
        bucket[x].append(y)
        sort_keys[x] = r.get("cap_sort_key", (1, x))
        cap = r.get("cap")
        x_values[x] = cap if isinstance(cap, (int, float)) else x

    ordered_labels = sorted(bucket.keys(), key=lambda x: sort_keys[x])
    xs = [x_values[x] for x in ordered_labels]
    ys = [mean(bucket[x]) for x in ordered_labels]
    return ordered_labels, xs, ys


# =========================
# plotting
# =========================


def plot_metric(grouped, y_key, ylabel, output_path, title=None):
    plt.figure(figsize=(8, 5))

    any_series = False
    fallback_colors = cycle(plt.rcParams["axes.prop_cycle"].by_key()["color"])
    x_is_numeric = True

    for policy in sorted(grouped.keys()):
        rows = grouped[policy]
        _, xs, ys = average_by_xlabel(rows, y_key)
        if xs:
            any_series = True
            if any(not isinstance(x, (int, float)) for x in xs):
                x_is_numeric = False
            color = POLICY_COLORS.get(policy, next(fallback_colors))
            plt.plot(
                xs,
                ys,
                marker="o",
                linewidth=2,
                markersize=6,
                color=color,
                label=policy,
            )

    if not any_series:
        plt.text(0.5, 0.5, f"No data for {y_key}", ha="center", va="center")

    plt.xlabel("Cache capacity")
    plt.ylabel(ylabel)
    if x_is_numeric:
        xticks = sorted(
            {
                r.get("cap")
                for rows in grouped.values()
                for r in rows
                if isinstance(r.get("cap"), (int, float))
            }
        )
        if xticks:
            plt.xticks(xticks)
    plt.grid(True, axis="y", alpha=0.3)
    if title:
        plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def plot_metric_boxplot(grouped, y_key, ylabel, output_path, title=None):
    """
    各 capacity ごとに、cache policy 別の箱ひげを横並びで表示する。
    例:
      cap=10 の位置に [lru][fifo][lfu]
      cap=50 の位置に [lru][fifo][lfu]
    """

    policies = sorted(grouped.keys())

    # 全 capacity ラベルを収集
    all_labels = []
    label_sort_keys = {}
    for policy, rows in grouped.items():
        for r in rows:
            x = r.get("cap_label", "unknown")
            if x not in all_labels:
                all_labels.append(x)
            label_sort_keys[x] = r.get("cap_sort_key", (1, x))

    all_labels = sorted(all_labels, key=lambda x: label_sort_keys[x])

    # policyごと・labelごとのデータを集める
    policy_label_data = {}
    for policy, rows in grouped.items():
        bucket = defaultdict(list)
        for r in rows:
            y = r.get(y_key)
            if y is None:
                continue
            x = r.get("cap_label", "unknown")
            bucket[x].append(y)
        policy_label_data[policy] = bucket

    plt.figure(figsize=(10, 5))

    n_labels = len(all_labels)
    n_policies = len(policies)

    if n_labels == 0 or n_policies == 0:
        plt.text(0.5, 0.5, f"No data for {y_key}", ha="center", va="center")
        plt.xlabel("Cache capacity")
        plt.ylabel(ylabel)
        if title:
            plt.title(title)
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        return

    base_positions = list(range(1, n_labels + 1))

    total_width = 0.8
    box_width = total_width / max(n_policies, 1)

    from matplotlib.patches import Patch

    legend_handles = []

    for i, policy in enumerate(policies):
        offset = -total_width / 2 + box_width / 2 + i * box_width
        positions = [p + offset for p in base_positions]

        data = []
        for label in all_labels:
            vals = policy_label_data[policy].get(label, [])
            data.append(vals)

        if not any(len(d) > 0 for d in data):
            continue

        bp = plt.boxplot(
            data,
            positions=positions,
            widths=box_width * 0.9,
            patch_artist=True,
            manage_ticks=False,
        )

        for box in bp["boxes"]:
            box.set_alpha(0.5)

        legend_handles.append(Patch(alpha=0.5, label=policy))

    plt.xticks(base_positions, all_labels)
    plt.xlabel("Cache capacity")
    plt.ylabel(ylabel)

    if title:
        plt.title(title)

    if legend_handles:
        plt.legend(handles=legend_handles, title="Cache policy")

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def debug_metric_values(rows, grouped, y_keys):
    print("\n=== Debug metric values before plotting ===")

    # 行ごとの値確認
    for y_key in y_keys:
        print(f"\n--- Metric: {y_key} ---")
        non_none_count = 0
        non_zero_count = 0

        for r in rows:
            v = r.get(y_key)
            print(
                f"{r.get('filename')} | cache={r.get('cache')} | cap={r.get('cap_label')} | {y_key}={v}"
            )
            if v is not None:
                non_none_count += 1
            if isinstance(v, (int, float)) and v != 0:
                non_zero_count += 1

        print(
            f"[summary] {y_key}: non_none={non_none_count}, non_zero={non_zero_count}, total_rows={len(rows)}"
        )

    # policy / capacity ごとの分布確認
    print("\n=== Debug grouped values ===")
    for y_key in y_keys:
        print(f"\n--- Grouped Metric: {y_key} ---")
        for policy, group_rows in grouped.items():
            bucket = defaultdict(list)
            for r in group_rows:
                x = r.get("cap_label", "unknown")
                v = r.get(y_key)
                if v is not None:
                    bucket[x].append(v)

            print(f"[policy={policy}]")
            for cap_label in sorted(bucket.keys(), key=lambda x: str(x)):
                print(f"  cap={cap_label}: values={bucket[cap_label]}")


# =========================
# reporting
# =========================
def print_summary(rows):
    print(f"Loaded rows: {len(rows)}")
    if not rows:
        return

    print("\n=== First rows ===")
    preview_keys = [
        "filename",
        "cache",
        "cap_label",
        "num_servers",
        "cache_entries_total",
        "cache_weight_total",
        "cache_bytes_total",
        "rss_kb_total",
        "rss_kb_max_total",
        "graph_entities_total",
        "auth_entities_total",
        "auth_table_entries_total",
        "access_total",
        "attempts_total",
        "authorized_total",
        "denied_total",
        "transition_total",
        "auth_time_total",
        "walk_time_total",
        "auth_calls_total",
        "walk_calls_total",
        "avg_auth_time",
        "remote_auth_calls_total",
        "local_auth_calls_total",
        "remote_ratio",
        "cost_per_walk",
    ]
    for r in rows[:5]:
        preview = {k: r.get(k) for k in preview_keys}
        print(preview)

    print("\n=== Warnings ===")
    if all(r.get("auth_time_total", 0) == 0 for r in rows):
        print("[warn] auth_time_total is zero for all rows.")
    if all(r.get("walk_time_total", 0) == 0 for r in rows):
        print("[warn] walk_time_total is zero for all rows.")
    if all(r.get("auth_cache_hit_rate") is None for r in rows):
        print("[warn] auth_cache_hit_rate is None for all rows.")
    if all(not isinstance(r.get("cap"), int) for r in rows):
        print("[warn] no numeric cache capacity found.")


# =========================
# main
# =========================
from pathlib import Path
import json


def main():
    base_path = Path(f"./{GRAPH}")

    # 全フォルダ取得
    folders = [p for p in base_path.glob("*/") if p.is_dir()]
    print(folders)

    if not folders:
        print("No subfolders found.")
        return

    all_rows = []

    # ★ 全フォルダのデータを結合
    for folder in folders:
        print(f"Loading: {folder}")
        rows = load_all_results(folder)

        if not rows:
            continue

        # フォルダ情報を追加（あとで分析に使える）
        for r in rows:
            r["folder"] = folder.name

        all_rows.extend(rows)

    if not all_rows:
        print("No valid JSON files found.")
        return

    print_summary(all_rows)

    # ★ まとめてgroup化
    grouped = group_by_policy(all_rows)

    debug_metric_values(
        all_rows,
        grouped,
        y_keys=[
            "cache_entries_total",
            "cache_bytes_total",
            "rss_kb_total",
            "rss_kb_max_total",
            "graph_entities_total",
            "auth_entities_total",
            "auth_table_entries_total",
            "auth_time_total",
            "walk_time_total",
        ],
    )

    print(f"\nGrouped by policy: {len(grouped)} groups")
    for policy, group_rows in grouped.items():
        print(f"- {policy}: {len(group_rows)} rows")

    # ★ 出力先（1箇所）
    out_dir = base_path

    # === fig1〜fig9: line plots ===
    plot_metric(
        grouped,
        y_key="cache_entries_total",
        ylabel="Cache entries (sum over servers)",
        output_path=out_dir / "fig1_capacity_vs_cache_entries.png",
        title="Cache entries by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="cache_bytes_total",
        ylabel="Estimated cache memory (bytes)",
        output_path=out_dir / "fig2_capacity_vs_cache_bytes.png",
        title="Cache bytes by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="rss_kb_total",
        ylabel="RSS total (KB)",
        output_path=out_dir / "fig3_capacity_vs_rss_kb.png",
        title="RSS by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="attempts_total",
        ylabel="Authorization attempts (sum over servers)",
        output_path=out_dir / "fig4_capacity_vs_attempts.png",
        title="Authorization attempts by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="auth_time_total",
        ylabel="Total auth time (sum over servers)",
        output_path=out_dir / "fig5_capacity_vs_auth_time_total.png",
        title="Auth time by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="avg_auth_time",
        ylabel="Average auth time per call",
        output_path=out_dir / "fig6_capacity_vs_avg_auth_time.png",
        title="Average auth time by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="walk_time_total",
        ylabel="Walk time (parallel max over servers)",
        output_path=out_dir / "fig7_capacity_vs_walk_time_total.png",
        title="Walk time by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="remote_ratio",
        ylabel="Remote auth ratio",
        output_path=out_dir / "fig8_capacity_vs_remote_ratio.png",
        title="Remote auth ratio by capacity and policy",
    )

    plot_metric(
        grouped,
        y_key="cost_per_walk",
        ylabel="Auth time per walk call",
        output_path=out_dir / "fig9_capacity_vs_cost_per_walk.png",
        title="Auth cost per walk by capacity and policy",
    )

    # JSON保存
    out_path = out_dir / "summary_rows.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)

    print("\nSaved summary graphs and JSON.")


if __name__ == "__main__":
    main()
