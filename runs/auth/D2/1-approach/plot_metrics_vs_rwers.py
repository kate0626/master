#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import matplotlib.pyplot as plt
import numpy as np

# ============================================================
# Settings
# ============================================================

BASE_DIR = Path("./0.3")
GRAPH_DIRS: Sequence[Path] = sorted(p for p in BASE_DIR.iterdir() if p.is_dir())

# RW counts to evaluate on the x-axis
K_LIST: Sequence[int] = list(range(10, 101, 10))

# Ratio to define top entities
TOP_RATIO = 0.5

# Output file name (avoid overwriting existing figures)
OUTPUT_NAME = "metrics_vs_rwers_all_metrics.png"

# Input file pattern
INPUT_GLOB = "start=*_per_walk_access.json"


# ============================================================
# IO
# ============================================================


def load_per_walk_access(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, dict) and "per_walk_access" in obj:
        return obj["per_walk_access"]
    if isinstance(obj, list):
        return obj
    raise ValueError(f"Unexpected JSON format: {path}")


# ============================================================
# Aggregation helpers
# ============================================================

START_RE = re.compile(r"start=(\d+)")


def extract_start_node_from_filename(path: Path) -> str:
    m = START_RE.search(path.name)
    if not m:
        raise ValueError(f"Cannot find start= in filename: {path.name}")
    return m.group(1)


def extract_access(walk: dict, start_entity: str) -> Dict[str, int]:
    access = walk.get("access", {})
    out: Dict[str, int] = {}
    for k, v in access.items():
        k = str(k)
        if k == start_entity:
            continue
        try:
            out[k] = int(v)
        except Exception:
            out[k] = 0
    return out


def cumulative_counter_until(
    per_walk: List[dict], K: int, start_entity: str
) -> Counter:
    counter = Counter()
    per_walk_sorted = sorted(per_walk, key=lambda w: int(w.get("walk_index", 0)))
    for walk in per_walk_sorted[:K]:
        counter.update(extract_access(walk, start_entity))
    return counter


def select_top_by_ratio(counter: Counter, ratio: float) -> List[Tuple[str, int]]:
    total = sum(counter.values())
    threshold = total * ratio
    selected: List[Tuple[str, int]] = []
    acc = 0
    for entity, count in counter.most_common():
        selected.append((entity, count))
        acc += count
        if acc >= threshold:
            break
    return selected


# ============================================================
# Metrics
# ============================================================


def selected_set(counter: Counter, ratio: float) -> Set[str]:
    return {e for e, _ in select_top_by_ratio(counter, ratio)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def precision(pred: Set[str], gold: Set[str]) -> float:
    return 0.0 if not pred else len(pred & gold) / len(pred)


def recall(pred: Set[str], gold: Set[str]) -> float:
    return 0.0 if not gold else len(pred & gold) / len(gold)


def coverage_on_final(sel: Set[str], counter_final: Counter) -> float:
    total = sum(counter_final.values())
    if total == 0:
        return 0.0
    return sum(counter_final.get(e, 0) for e in sel) / total


def l1_distance(counter_a: Counter, counter_b: Counter) -> float:
    total_a = sum(counter_a.values())
    total_b = sum(counter_b.values())
    if total_a == 0 or total_b == 0:
        return 2.0
    keys = set(counter_a.keys()) | set(counter_b.keys())
    s = 0.0
    for k in keys:
        pa = counter_a.get(k, 0) / total_a
        pb = counter_b.get(k, 0) / total_b
        s += abs(pa - pb)
    return s


def compute_metrics_by_k(
    per_walk: List[dict], start_entity: str, k_list: Sequence[int], ratio: float
) -> Dict[str, List[float]]:
    counters_by_k = {
        K: cumulative_counter_until(per_walk, K, start_entity=start_entity)
        for K in k_list
    }
    k_final = max(k_list)
    counter_final = counters_by_k[k_final]
    s_final = selected_set(counter_final, ratio)

    metrics: Dict[str, List[float]] = {
        "Jaccard": [],
        # "Precision": [],
        # "Recall": [],
        "Coverage": [],
        # "L1": [],
    }

    for K in k_list:
        counter = counters_by_k[K]
        s_k = selected_set(counter, ratio)
        metrics["Jaccard"].append(jaccard(s_k, s_final))
        # metrics["Precision"].append(precision(s_k, s_final))
        # metrics["Recall"].append(recall(s_k, s_final))
        metrics["Coverage"].append(coverage_on_final(s_k, counter_final))
        # metrics["L1"].append(l1_distance(counter, counter_final))

    return metrics


# ============================================================
# Plotting
# ============================================================


def plot_metrics(
    graph: str, k_list: Sequence[int], metrics: Dict[str, List[float]], out_path: Path
) -> None:
    plt.figure(figsize=(8, 5))

    for label, values in metrics.items():
        plt.plot(k_list, values, marker="o", label=label)

    plt.xlabel("RW count (K)")
    plt.ylabel("Metric value")
    plt.title(f"Metrics vs RW count (graph={graph}, top={int(TOP_RATIO*100)}%)")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


def find_input_file(graph_dir: Path) -> Path | None:
    matches = list(graph_dir.glob(INPUT_GLOB))
    if not matches:
        return None
    if len(matches) > 1:
        # Prefer start=0 if present, otherwise first lexicographic
        for m in matches:
            if "start=0_" in m.name:
                return m
        return sorted(matches)[0]
    return matches[0]


def main() -> None:
    for graph_dir in GRAPH_DIRS:
        input_file = find_input_file(graph_dir)
        if input_file is None:
            print(f"[SKIP] no input file in {graph_dir}")
            continue

        per_walk = load_per_walk_access(input_file)
        start_entity = extract_start_node_from_filename(input_file)
        graph_name = graph_dir.name

        metrics = compute_metrics_by_k(
            per_walk=per_walk,
            start_entity=start_entity,
            k_list=K_LIST,
            ratio=TOP_RATIO,
        )

        out_path = graph_dir / OUTPUT_NAME
        plot_metrics(graph_name, K_LIST, metrics, out_path)
        print(f"[OK] saved: {out_path}")


if __name__ == "__main__":
    main()
