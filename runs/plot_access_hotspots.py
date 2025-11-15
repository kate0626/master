#!/usr/bin/env python3
from __future__ import annotations

"""
Utility to visualize access hotspots from *_global_transition.json.

Usage:
    python runs/plot_access_hotspots.py --json 100_0.1_global_transition.json --top-n 15
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Tuple

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np


def _load_global_stats(json_path: Path) -> Tuple[Counter, Counter]:
    """Return (access_counter, transition_counter) from a JSON report."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    access = Counter(data.get("access", {}))
    transitions = Counter(data.get("transition", {}))
    return access, transitions


def _plot_heatmap(
    matrix: np.ndarray, labels_x, labels_y, title: str, out_path: Path
) -> None:
    plt.figure(figsize=(max(6, len(labels_x) * 0.5), max(4, len(labels_y) * 0.4)))
    plt.imshow(matrix, cmap="YlOrRd", aspect="auto")
    plt.colorbar(label="count")
    plt.xticks(range(len(labels_x)), labels_x, rotation=45, ha="right")
    plt.yticks(range(len(labels_y)), labels_y)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path)
    # plt.show()
    print(f"[plot] saved {out_path}")


def _plot_transition_network(edges: list[tuple[str, str, int]], prefix: Path) -> None:
    graph = nx.DiGraph()
    for src, dst, weight in edges:
        graph.add_edge(src, dst, weight=weight)

    if not graph:
        print("[plot] transition graph is empty")
        return

    k = 1.5 / np.sqrt(max(graph.number_of_nodes(), 1))
    pos = nx.spring_layout(graph, seed=42, k=k, iterations=100)
    weights = [0.8 + np.log1p(data["weight"]) for _, _, data in graph.edges(data=True)]
    node_sizes = []
    for node in graph.nodes():
        degree = graph.out_degree(node) + graph.in_degree(node)
        node_sizes.append(300 + degree * 80)

    plt.figure(figsize=(8, 6))
    nx.draw_networkx_nodes(graph, pos, node_color="#1f78b4", node_size=node_sizes, alpha=0.9)
    nx.draw_networkx_labels(graph, pos, font_size=8, font_color="white")
    nx.draw_networkx_edges(
        graph,
        pos,
        width=weights,
        arrowstyle="->",
        arrowsize=10,
        connectionstyle="arc3,rad=0.12",
        edge_color="#ff7f0e",
    )
    edge_labels = {(u, v): d["weight"] for u, v, d in graph.edges(data=True)}
    nx.draw_networkx_edge_labels(
        graph,
        pos,
        edge_labels=edge_labels,
        font_size=7,
        bbox=dict(boxstyle="round,pad=0.15", fc="#ffffffaa", ec="none"),
    )
    plt.title("Transition network (top edges)")
    plt.tight_layout()
    out_path = prefix.with_name(prefix.name + "_transition_network.png")
    plt.savefig(out_path)
    # plt.show()
    print(f"[plot] saved {out_path}")


def plot_access_hotspots(json_file: str, top_n: int, output_prefix: str | None) -> None:
    json_path = Path(json_file)
    if not json_path.exists():
        raise FileNotFoundError(f"{json_path} not found")

    access, transitions = _load_global_stats(json_path)
    prefix = Path(output_prefix) if output_prefix else json_path.with_suffix("")

    if access:
        top_access = access.most_common(top_n)
        labels = [label for label, _ in top_access]
        values = np.array([count for _, count in top_access]).reshape(
            len(top_access), 1
        )
        out_path = prefix.with_name(prefix.name + "_access_heatmap.png")
        _plot_heatmap(
            values, ["visits"], labels, f"Access heatmap (top {len(labels)})", out_path
        )
    else:
        print(f"[plot] no access data in {json_file}")

    if transitions:
        top_edges = transitions.most_common(top_n)
        nodes: list[str] = []
        edges_with_counts = []
        for entry, count in top_edges:
            src, dst = entry.split("->", 1)
            if src not in nodes:
                nodes.append(src)
            if dst not in nodes:
                nodes.append(dst)
            edges_with_counts.append((src, dst, count))

        size = len(nodes)
        matrix = np.zeros((size, size))
        idx = {node: i for i, node in enumerate(nodes)}
        for src, dst, count in edges_with_counts:
            matrix[idx[src], idx[dst]] = count

        out_path = prefix.with_name(prefix.name + "_transition_heatmap.png")
        _plot_heatmap(
            matrix, nodes, nodes, f"Transition heatmap (top {len(top_edges)})", out_path
        )

        _plot_transition_network(edges_with_counts, prefix)
    else:
        print(f"[plot] no transition data in {json_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Visualize access hotspots from global_transition JSON."
    )
    parser.add_argument(
        "--json", required=True, help="Path to *_global_transition.json."
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of entries to include when drawing heatmaps/networks.",
    )
    parser.add_argument(
        "--output-prefix",
        help="Optional path prefix for output figures (defaults to JSON filename stem).",
    )
    args = parser.parse_args()

    plot_access_hotspots(args.json, top_n=args.top_n, output_prefix=args.output_prefix)


if __name__ == "__main__":
    main()
