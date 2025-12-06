import re
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np

# =========================================================
# Configuration – edit these few items when adding/changing logs
# =========================================================

OUTPUT_PREFIX = "subgraph_size_alpha001"
X_LABEL = "subgraph size"
PLOT_REMOTE_DURATION = True
PLOT_AVG_LENGTH = True

SERIES: Sequence[Tuple[str, Dict[int, str]]] = [
    (
        "Subgraph servers",
        {
            1: "auth_subgraph/test/karate_size1_walks100_alpha0_01.log",
            2: "auth_subgraph/test/karate_size2_walks100_alpha0_01.log",
            4: "auth_subgraph/test/karate_size4_walks100_alpha0_01.log",
            6: "auth_subgraph/test/karate_size6_walks100_alpha0_01.log",
            8: "auth_subgraph/test/karate_size8_walks100_alpha0_01.log",
            10: "auth_subgraph/test/karate_size10_walks100_alpha0_01.log",
        },
    ),
    (
        "Hot=2",
        {
            1: "visit_count/test/hot=2/visit_karate_size1_walks100_alpha0_01.log",
            2: "visit_count/test/hot=2/visit_karate_size2_walks100_alpha0_01.log",
            4: "visit_count/test/hot=2/visit_karate_size4_walks100_alpha0_01.log",
            6: "visit_count/test/hot=2/visit_karate_size6_walks100_alpha0_01.log",
            8: "visit_count/test/hot=2/visit_karate_size8_walks100_alpha0_01.log",
            10: "visit_count/test/hot=2/visit_karate_size10_walks100_alpha0_01.log",
        },
    ),
    (
        "Hot=10000",
        {
            1: "visit_count/test/hot=10000/visit_karate_size1_walks100_alpha0_01.log",
            2: "visit_count/test/hot=10000/visit_karate_size2_walks100_alpha0_01.log",
            4: "visit_count/test/hot=10000/visit_karate_size4_walks100_alpha0_01.log",
            6: "visit_count/test/hot=10000/visit_karate_size6_walks100_alpha0_01.log",
            8: "visit_count/test/hot=10000/visit_karate_size8_walks100_alpha0_01.log",
            10: "visit_count/test/hot=10000/visit_karate_size10_walks100_alpha0_01.log",
        },
    ),
]

# =========================================================
# Metric extraction helpers
# =========================================================


def extract_remote_durations(path: str) -> List[float]:
    """
    Pull per-start remote duration values, falling back to the controller wall
    time when durations are not present in the log.
    """

    durations: List[float] = []
    wall_fallback: List[float] = []
    wall_re = re.compile(r"\[Controller\]\s+PPR timing:\s+wall=([0-9.]+)s")

    with open(path, "r") as f:
        for line in f:
            m = wall_re.search(line)
            if m:
                wall_fallback.append(float(m.group(1)))

    # print(f"[DEBUG] {path}: extracted remote durations: {durations}")
    print(f"[DEBUG] {path}: extracted wall fallback durations: {wall_fallback}")
    return durations if durations else wall_fallback


def extract_avg_lengths(path: str) -> List[float]:
    lengths: List[float] = []
    avg_length_re = re.compile(r"avg_length:\s*([0-9.]+)")

    with open(path, "r") as f:
        for line in f:
            m = avg_length_re.search(line)
            if m:
                lengths.append(float(m.group(1)))

    return lengths


# =========================================================
# Plotting utilities
# =========================================================


def ensure_common_keys(series: Sequence[Tuple[str, Dict[int, str]]]) -> List[int]:
    """Ensure every series uses the same x-axis keys."""

    key_reference: Iterable[int] | None = None
    for label, mapping in series:
        if not mapping:
            raise ValueError(f"Series '{label}' is empty.")
        keys = tuple(sorted(mapping.keys()))
        if key_reference is None:
            key_reference = keys
        elif tuple(key_reference) != keys:
            raise ValueError(
                f"Series '{label}' has keys {keys}, expected {key_reference}."
            )
    return list(key_reference or [])


def compute_means(
    label: str,
    files: Dict[int, str],
    x_values: Sequence[int],
    extractor: Callable[[str], List[float]],
) -> List[float]:
    """Return averaged metrics for each x-axis value."""

    results: List[float] = []
    for x in x_values:
        path = files.get(x)
        if path is None:
            print(f"[WARN] {label}: missing log for {X_LABEL}={x}")
            results.append(np.nan)
            continue

        if not Path(path).is_file():
            print(f"[WARN] {label}: file not found -> {path}")
            results.append(np.nan)
            continue

        values = extractor(path)
        if not values:
            print(f"[WARN] {label}: no values extracted from {path}")
            results.append(np.nan)
            continue

        results.append(float(np.mean(values)))
        # results.append(float(values[0]))  # 一つだけ値を取る場合
    return results


def plot_series(
    x_values: Sequence[int],
    y_series: Dict[str, List[float]],
    ylabel: str,
    title: str,
    output_file: str,
) -> None:
    plt.figure(figsize=(8, 5))
    for label, values in y_series.items():
        plt.plot(x_values, values, marker="o", label=label)

    plt.xlabel(X_LABEL)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(output_file)
    plt.show()
    print(f"[INFO] Saved plot -> {output_file}")


def main() -> None:
    x_values = ensure_common_keys(SERIES)

    if PLOT_REMOTE_DURATION:
        remote_results = {
            label: compute_means(label, mapping, x_values, extract_remote_durations)
            for label, mapping in SERIES
        }
        plot_series(
            x_values,
            remote_results,
            "remote_duration (seconds)",
            "Execution time comparison",
            f"{OUTPUT_PREFIX}_remote_duration.png",
        )

    if PLOT_AVG_LENGTH:
        length_results = {
            label: compute_means(label, mapping, x_values, extract_avg_lengths)
            for label, mapping in SERIES
        }
        print("[DEBUG] Avg length:", length_results)
        plot_series(
            x_values,
            length_results,
            "avg_length (steps)",
            "Random walk length comparison",
            f"{OUTPUT_PREFIX}_avg_length.png",
        )


if __name__ == "__main__":
    main()
