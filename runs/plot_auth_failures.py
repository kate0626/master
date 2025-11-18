"""
失敗回数の確率を可視化するコード
入力：失敗確率のCSVファイル
出力：図３つ
"""

##!/usr/bin/env python3
# from __future__ import annotations

# """
# Visualize authorization failure locality from *_global_transition.json files.

# Outputs:
#   * CSV summary (entity, attempts, failures, failure rate)
#   * Failure-rate heatmap (similar to runs/plot_access_hotspots.py)
#   * Transition heatmap + network where nodes are colored by failure rate
#   * (Optional) Graph view using the original edge list if --edge-file is provided

# Example:
#     python runs/plot_auth_failures.py --json 100_0.1_global_transition.json \
#         --top-n 20
# """

# import argparse
# import csv
# import json
# from collections import Counter
# from pathlib import Path
# from typing import Any, Dict, Iterable, List, Sequence

# import matplotlib.pyplot as plt
# import networkx as nx
# import numpy as np


# def _entity_key(raw: Any) -> str:
#     """Normalize JSON keys (int/str) into a comparable string."""
#     if isinstance(raw, str):
#         return raw
#     return str(raw)


# def _load_counters(json_path: Path):
#     """Load attempt/denied counters and transitions from the aggregated JSON."""
#     data = json.loads(json_path.read_text(encoding="utf-8"))
#     attempts = Counter()
#     denied = Counter()
#     for key, value in data.get("authorization_attempts", {}).items():
#         attempts[_entity_key(key)] = value
#     for key, value in data.get("authorization_denied", {}).items():
#         denied[_entity_key(key)] = value

#     transitions = Counter(data.get("transition", {}))
#     failure_rate_hint = {
#         _entity_key(k): float(v)
#         for k, v in data.get("authorization_failure_rate", {}).items()
#     }
#     return attempts, denied, transitions, failure_rate_hint


# def _compute_metrics(
#     attempts: Counter, denied: Counter, failure_rate_hint: Dict[str, float]
# ) -> List[Dict[str, Any]]:
#     metrics: List[Dict[str, Any]] = []
#     entities = set(attempts) | set(denied) | set(failure_rate_hint)
#     for entity in entities:
#         total_attempts = attempts.get(entity, 0)
#         if total_attempts <= 0:
#             continue
#         failures = denied.get(entity, 0)
#         successes = max(total_attempts - failures, 0)
#         rate = (
#             failure_rate_hint.get(entity)
#             if entity in failure_rate_hint
#             else (failures / total_attempts if total_attempts else 0.0)
#         )
#         metrics.append(
#             {
#                 "entity": entity,
#                 "attempts": total_attempts,
#                 "successes": successes,
#                 "failures": failures,
#                 "failure_rate": rate,
#             }
#         )
#     metrics.sort(
#         key=lambda item: (item["failure_rate"], item["failures"]), reverse=True
#     )
#     return metrics


# def _write_summary_csv(metrics: Iterable[Dict[str, Any]], out_path: Path) -> None:
#     with out_path.open("w", newline="", encoding="utf-8") as handle:
#         writer = csv.writer(handle)
#         writer.writerow(["entity", "attempts", "successes", "failures", "failure_rate"])
#         for row in metrics:
#             writer.writerow(
#                 [
#                     row["entity"],
#                     row["attempts"],
#                     row["successes"],
#                     row["failures"],
#                     f"{row['failure_rate']:.4f}",
#                 ]
#             )
#     print(f"[plot-auth] saved summary csv -> {out_path}")


# def _plot_heatmap(
#     matrix: np.ndarray,
#     labels_x: Sequence[str],
#     labels_y: Sequence[str],
#     title: str,
#     color_label: str,
#     out_path: Path,
# ) -> None:
#     plt.figure(figsize=(max(6, len(labels_x) * 0.5), max(4, len(labels_y) * 0.4)))
#     plt.imshow(matrix, cmap="OrRd", aspect="auto")
#     plt.colorbar(label=color_label)
#     plt.xticks(range(len(labels_x)), labels_x, rotation=45, ha="right")
#     plt.yticks(range(len(labels_y)), labels_y)
#     plt.title(title)
#     plt.tight_layout()
#     plt.savefig(out_path)
#     plt.close()
#     print(f"[plot-auth] saved {out_path}")


# def _plot_failure_rate_heatmap(
#     metrics: List[Dict[str, Any]], top_n: int, prefix: Path
# ) -> None:
#     if not metrics:
#         print("[plot-auth] no authorization metrics to plot")
#         return
#     subset = metrics[: max(1, min(top_n, len(metrics)))]
#     labels = [
#         f"{row['entity']} ({row['failures']}/{row['attempts']})" for row in subset
#     ]
#     values = np.array([row["failure_rate"] for row in subset]).reshape(len(subset), 1)
#     out_path = prefix.with_name(prefix.name + "_failure_heatmap.png")
#     _plot_heatmap(
#         values,
#         ["failure rate"],
#         labels,
#         "Authorization failure hotspot",
#         "rate",
#         out_path,
#     )


# def _build_transition_matrix(
#     edges: List[tuple[str, str, int]],
# ) -> tuple[np.ndarray, List[str]]:
#     nodes: List[str] = []
#     for src, dst, _ in edges:
#         if src not in nodes:
#             nodes.append(src)
#         if dst not in nodes:
#             nodes.append(dst)
#     size = len(nodes)
#     matrix = np.zeros((size, size))
#     idx = {node: i for i, node in enumerate(nodes)}
#     for src, dst, count in edges:
#         matrix[idx[src], idx[dst]] = count
#     return matrix, nodes


# def _plot_transition_heatmap(edges: List[tuple[str, str, int]], prefix: Path) -> None:
#     if not edges:
#         print("[plot-auth] no transition entries to plot")
#         return
#     matrix, nodes = _build_transition_matrix(edges)
#     out_path = prefix.with_name(prefix.name + "_failure_transition_heatmap.png")
#     _plot_heatmap(
#         matrix,
#         nodes,
#         nodes,
#         f"Transition heatmap (top {len(edges)} edges)",
#         "count",
#         out_path,
#     )


# def _plot_failure_transition_network(
#     edges: List[tuple[str, str, int]],
#     metrics_map: Dict[str, Dict[str, Any]],
#     prefix: Path,
# ) -> None:
#     graph = nx.DiGraph()
#     for src, dst, weight in edges:
#         graph.add_edge(src, dst, weight=weight)

#     if not graph:
#         print("[plot-auth] transition graph is empty")
#         return

#     num_nodes = graph.number_of_nodes()
#     k = 1.5 / np.sqrt(max(num_nodes, 1))
#     pos = nx.spring_layout(graph, seed=42, k=k, iterations=100)

#     node_colors = []
#     node_sizes = []
#     for node in graph.nodes():
#         info = metrics_map.get(node)
#         node_colors.append(info["failure_rate"] if info else 0.0)
#         attempts = info["attempts"] if info else 0
#         node_sizes.append(280 + 70 * np.log1p(attempts))

#     node_vmax = max(node_colors) if node_colors else 0.0
#     edge_weights = [
#         0.8 + np.log1p(data["weight"]) for _, _, data in graph.edges(data=True)
#     ]

#     plt.figure(figsize=(8, 6))
#     node_artist = nx.draw_networkx_nodes(
#         graph,
#         pos,
#         node_color=node_colors,
#         cmap="OrRd",
#         vmin=0.0,
#         vmax=node_vmax if node_vmax > 0 else 1.0,
#         node_size=node_sizes,
#         edgecolors="black",
#         linewidths=0.6,
#     )
#     nx.draw_networkx_labels(graph, pos, font_size=8, font_color="white")
#     nx.draw_networkx_edges(
#         graph,
#         pos,
#         width=edge_weights,
#         arrowstyle="->",
#         arrowsize=10,
#         connectionstyle="arc3,rad=0.12",
#         edge_color="#555555",
#         alpha=0.9,
#     )
#     edge_labels = {(u, v): d["weight"] for u, v, d in graph.edges(data=True)}
#     nx.draw_networkx_edge_labels(
#         graph,
#         pos,
#         edge_labels=edge_labels,
#         font_size=7,
#         bbox=dict(boxstyle="round,pad=0.15", fc="#ffffffcc", ec="none"),
#     )
#     plt.title("Authorization failure transition network")
#     cbar = plt.colorbar(node_artist, fraction=0.04, pad=0.03)
#     cbar.set_label("failure rate")
#     plt.tight_layout()
#     out_path = prefix.with_name(prefix.name + "_failure_transition_network.png")
#     plt.savefig(out_path)
#     plt.close()
#     print(f"[plot-auth] saved {out_path}")


# def _load_graph(edge_path: Path) -> nx.Graph:
#     edges = []
#     with edge_path.open("r", encoding="utf-8") as handle:
#         for line in handle:
#             stripped = line.strip()
#             if not stripped:
#                 continue
#             try:
#                 u, v = map(int, stripped.split())
#             except ValueError:
#                 continue
#             edges.append((u, v))
#     g = nx.Graph()
#     g.add_edges_from(edges)
#     return g


# def _edge_id(u: int, v: int) -> str:
#     a, b = sorted((u, v))
#     return f"edge_{a}_{b}"


# def _plot_failure_graph_using_edge_file(
#     metrics: List[Dict[str, Any]], edge_file: Path, out_path: Path
# ) -> None:
#     graph = _load_graph(edge_file)
#     if not graph:
#         print(f"[plot-auth] graph source {edge_file} is empty, skip graph plot")
#         return

#     metrics_map = {str(entry["entity"]): entry for entry in metrics}
#     relevant_nodes = {
#         node for node in graph.nodes() if metrics_map.get(str(node), {}).get("attempts", 0) > 0
#     }
#     for u, v in graph.edges():
#         eid = _edge_id(u, v)
#         info = metrics_map.get(eid)
#         if info and info.get("attempts", 0) > 0:
#             relevant_nodes.add(u)
#             relevant_nodes.add(v)

#     if not relevant_nodes:
#         print("[plot-auth] no nodes with authorization stats to plot on base graph")
#         return

#     # reuse same layout style as runs/graph.py (spring_layout with seed=42)
#     pos = nx.spring_layout(graph, seed=42)

#     edge_values_overlay = []
#     for u, v in graph.edges():
#         eid = _edge_id(u, v)
#         edge_values_overlay.append(metrics_map.get(eid, {}).get("failure_rate", 0.0))

#     node_values = [metrics_map.get(str(node), {}).get("failure_rate", 0.0) for node in graph]
#     node_attempts = [metrics_map.get(str(node), {}).get("attempts", 0) for node in graph]
#     node_sizes = [150 + 80 * np.log1p(attempt) for attempt in node_attempts]
#     node_vmax = max(node_values) if node_values else 0.0

#     overlay_nodes = [node for node in graph.nodes() if node in relevant_nodes]
#     overlay_values = [metrics_map.get(str(node), {}).get("failure_rate", 0.0) for node in overlay_nodes]
#     overlay_attempts = [metrics_map.get(str(node), {}).get("attempts", 0) for node in overlay_nodes]
#     overlay_sizes = [360 + 80 * np.log1p(attempt) for attempt in overlay_attempts]

#     fig, ax = plt.subplots(figsize=(8, 6))
#     node_cmap = plt.cm.OrRd
#     edge_cmap = plt.cm.Blues

#     # draw base graph faintly
#     nx.draw_networkx_edges(
#         graph,
#         pos,
#         edge_color="#d0d0d0",
#         width=1.0,
#         alpha=0.6,
#         ax=ax,
#     )
#     nx.draw_networkx_nodes(
#         graph,
#         pos,
#         node_color="#f2f2f2",
#         node_size=160,
#         edgecolors="#b0b0b0",
#         linewidths=0.5,
#         ax=ax,
#     )

#     node_collection = nx.draw_networkx_nodes(
#         graph,
#         pos,
#         nodelist=overlay_nodes,
#         node_color=overlay_values,
#         cmap=node_cmap,
#         vmin=0,
#         vmax=node_vmax if node_vmax > 0 else 1.0,
#         node_size=overlay_sizes,
#         edgecolors="black",
#         linewidths=0.6,
#         ax=ax,
#     )
#     nx.draw_networkx_labels(
#         graph,
#         pos,
#         font_size=8,
#         bbox=dict(boxstyle="round,pad=0.18", fc="#ffffffcc", ec="none"),
#         ax=ax,
#     )
#     overlay_edges = [
#         (u, v)
#         for u, v in graph.edges()
#         if metrics_map.get(_edge_id(u, v), {}).get("attempts", 0) > 0
#     ]
#     edge_colors = [
#         metrics_map.get(_edge_id(u, v), {}).get("failure_rate", 0.0)
#         for u, v in overlay_edges
#     ]
#     edge_widths = [
#         1.2 + 5.5 * metrics_map.get(_edge_id(u, v), {}).get("failure_rate", 0.0)
#         for u, v in overlay_edges
#     ]
#     edge_vmax = max(edge_colors) if edge_colors else 0.0
#     nx.draw_networkx_edges(
#         graph,
#         pos,
#         edgelist=overlay_edges,
#         edge_color=edge_colors,
#         edge_cmap=edge_cmap,
#         edge_vmin=0,
#         edge_vmax=edge_vmax if edge_vmax > 0 else 1.0,
#         width=edge_widths,
#         alpha=0.85,
#         arrows=False,
#         ax=ax,
#     )
#     ax.set_title("Authorization failure locality (edge file)")
#     ax.axis("off")

#     if node_vmax > 0:
#         node_sm = plt.cm.ScalarMappable(
#             cmap=node_cmap, norm=plt.Normalize(vmin=0, vmax=node_vmax)
#         )
#         node_sm.set_array([])
#         fig.colorbar(
#             node_sm, ax=ax, fraction=0.046, pad=0.03, label="node failure rate"
#         )
#     if edge_vmax > 0:
#         edge_sm = plt.cm.ScalarMappable(
#             cmap=edge_cmap, norm=plt.Normalize(vmin=0, vmax=edge_vmax)
#         )
#         edge_sm.set_array([])
#         fig.colorbar(
#             edge_sm, ax=ax, fraction=0.046, pad=0.08, label="edge failure rate"
#         )

#     plt.tight_layout()
#     fig.savefig(out_path)
#     plt.close(fig)
#     print(f"[plot-auth] saved {out_path}")


# def plot_auth_failures(
#     json_file: Path,
#     top_n: int,
#     transition_top_n: int,
#     output_prefix: Path | None,
#     edge_file: Path | None,
# ) -> None:
#     if not json_file.exists():
#         raise FileNotFoundError(json_file)

#     attempts, denied, transitions, failure_hint = _load_counters(json_file)
#     metrics = _compute_metrics(attempts, denied, failure_hint)
#     if not metrics:
#         print("[plot-auth] no authorization attempt data present")
#         return

#     prefix = output_prefix if output_prefix else json_file.with_suffix("")
#     csv_path = prefix.with_name(prefix.name + "_auth_failure_summary.csv")
#     _write_summary_csv(metrics, csv_path)
#     _plot_failure_rate_heatmap(metrics, top_n, prefix)

#     if transitions:
#         top_edges_raw = transitions.most_common(transition_top_n or top_n)
#         edges_with_counts: List[tuple[str, str, int]] = []
#         for entry, count in top_edges_raw:
#             if "->" not in entry:
#                 continue
#             src, dst = entry.split("->", 1)
#             edges_with_counts.append((src, dst, count))
#         _plot_transition_heatmap(edges_with_counts, prefix)
#         metrics_map = {str(row["entity"]): row for row in metrics}
#         _plot_failure_transition_network(edges_with_counts, metrics_map, prefix)
#     else:
#         print("[plot-auth] transition data missing in JSON")

#     if edge_file:
#         graph_path = prefix.with_name(prefix.name + "_auth_failure_graph.png")
#         _plot_failure_graph_using_edge_file(metrics, edge_file, graph_path)

#     print("[plot-auth] top failure entries:")
#     for row in metrics[: min(10, len(metrics))]:
#         print(
#             f"  {row['entity']}: failures {row['failures']}/{row['attempts']} "
#             f"({row['failure_rate']:.2%})"
#         )


# def main() -> None:
#     parser = argparse.ArgumentParser(
#         description="Plot locality of authorization failures (heatmaps + networks)."
#     )
#     parser.add_argument(
#         "--json", required=True, help="Path to *_global_transition.json"
#     )
#     parser.add_argument(
#         "--top-n", type=int, default=20, help="Number of entities to highlight."
#     )
#     parser.add_argument(
#         "--transition-top-n",
#         type=int,
#         help="Optional override for number of transitions to visualize (default: --top-n).",
#     )
#     parser.add_argument(
#         "--output-prefix",
#         help="Optional prefix for outputs (defaults to JSON filename stem).",
#     )
#     parser.add_argument(
#         "--edge-file",
#         help="Optional edge list to draw the original graph (same format as plot_access_hotspots).",
#     )
#     args = parser.parse_args()

#     json_file = Path(args.json)
#     prefix = Path(args.output_prefix) if args.output_prefix else None
#     edge_file = Path(args.edge_file) if args.edge_file else None
#     plot_auth_failures(
#         json_file,
#         top_n=args.top_n,
#         transition_top_n=args.transition_top_n or args.top_n,
#         output_prefix=prefix,
#         edge_file=edge_file,
#     )


# if __name__ == "__main__":
#     main()


"""
ここからは、グラフにプロットするNWグラフ
"""

import networkx as nx
import matplotlib.pyplot as plt
import csv

# === グラフ構築 ===
EDGE_FILE = "./dataset/Louvain/graph/karate.gr"

edges = []
with open(EDGE_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            u, v = map(int, line.split())
            edges.append((u, v))
        except:
            pass

G = nx.Graph()
G.add_edges_from(edges)

# === レイアウト固定（歪ませないため絶対に固定する） ===
pos = nx.spring_layout(G, seed=42)

# === CSV 読み込み（ノードの failure_rate のみ使う） ===
failure_rate = {}  # node → rate

CSV_FILE = (
    "./100_0.1_global_transition_auth_failure_summary.csv"  # ←あなたの CSV に変更
)

with open(CSV_FILE, "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        entity = row["entity"]
        # node のみ採用（edge_x_y は無視）
        if entity.isdigit():
            failure_rate[int(entity)] = float(row["failure_rate"])

# === ノードごとの色（failure_rate がないノードは 0 扱い） ===
node_colors = []
for n in G.nodes():
    rate = failure_rate.get(n, 0.0)
    node_colors.append(rate)

# === 描画 ===
plt.figure(figsize=(8, 6))

nodes = nx.draw_networkx_nodes(
    G,
    pos,
    node_size=600,
    node_color=node_colors,
    cmap=plt.cm.Reds,  # 赤 = 高失敗
    edgecolors="black",
    linewidths=1.0,
)

nx.draw_networkx_edges(G, pos, width=1.2, alpha=0.7)
nx.draw_networkx_labels(G, pos, font_size=10)

# カラーバー追加
cbar = plt.colorbar(nodes)
cbar.set_label("Failure Rate", fontsize=12)

plt.title("Node Failure Rate Visualization", fontsize=14)
plt.axis("off")
plt.tight_layout()
plt.savefig("karate_failure_rate.png", dpi=300)
plt.show()
