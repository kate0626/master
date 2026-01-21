#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# ★ 解析対象ディレクトリ（方法ごと）
# ============================================================
ROOT_DIRS = {
    # "base(no-auth)": Path("../base/0.3"),
    # "auth": Path("./D2/1-approach/0.3/"),
    "cache": Path("./D1:cache/0.3"),
}

LOG_PATTERN = "*.memory.log"

# 出力ファイル
OUT_BREAKDOWN = "cache_memory_breakdown_grouped.png"
OUT_RSS_TOTAL = "cache_rss_total_by_graph.png"

UNIT = "MB"  # bytes_est 図の単位
USE_LOG_SCALE = True  # fb 等を含むなら True 推奨


# ============================================================
# ログ形式
# ============================================================
SNAPSHOT_RE = re.compile(
    r"^=+\n=== \[MEMORY SNAPSHOT .*?\] ===.*?\n=+\n",
    re.MULTILINE,
)
ENDPOINT_RE = re.compile(r"--- endpoint=([^\s]+) ---\s*", re.MULTILINE)

# ============================================================
# bytes_est 内訳で見るコンポーネント
# ============================================================
COMPONENTS = [
    "neighbor_map",
    "node_to_starts",
    "auth_cache",  # authz_cache を吸収
    "counters",
]


# ============================================================
# データ構造
# ============================================================
@dataclass
class ServerAgg:
    rss_kb: float
    rss_kb_current: float
    bytes_est: Dict[str, float]
    bytes_est_total: float


# ============================================================
# graph 名（ファイル名由来）
# ============================================================
def graph_name_from_filename(path: Path) -> str:
    name = path.name
    for suf in [".memory.log", ".log"]:
        if name.endswith(suf):
            return name[: -len(suf)]
    return path.stem


# ============================================================
# snapshot 分割
# ============================================================
def split_into_snapshots(text: str) -> List[str]:
    headers = list(SNAPSHOT_RE.finditer(text))
    if not headers:
        return [text]

    chunks: List[str] = []
    for i, m in enumerate(headers):
        start = m.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        chunks.append(text[start:end])
    return chunks


# ============================================================
# snapshot → endpoint JSON 抽出
# ============================================================
def extract_endpoint_json_from_snapshot(
    snapshot_text: str,
) -> List[Tuple[str, Dict[str, Any]]]:
    matches = list(ENDPOINT_RE.finditer(snapshot_text))
    out: List[Tuple[str, Dict[str, Any]]] = []

    for i, m in enumerate(matches):
        endpoint = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(snapshot_text)
        block = snapshot_text[start:end].strip()

        l = block.find("{")
        r = block.rfind("}")
        if l < 0 or r < 0 or r <= l:
            continue

        try:
            obj = json.loads(block[l : r + 1])
        except json.JSONDecodeError:
            continue

        out.append((endpoint, obj))
    return out


# ============================================================
# JSON → ServerAgg
# ============================================================
# def to_serveragg(obj: Dict[str, Any]) -> ServerAgg:
#     be_raw = obj.get("bytes_est", {}) or {}

#     # authz_cache → auth_cache に統一
#     if "auth_cache" not in be_raw and "authz_cache" in be_raw:
#         be_raw = dict(be_raw)
#         be_raw["auth_cache"] = be_raw.get("authz_cache", 0.0)

#     be = {k: float(be_raw.get(k, 0.0)) for k in COMPONENTS}


#     return ServerAgg(
#         rss_kb=float(obj.get("rss_kb", 0.0)),
#         rss_kb_current=float(obj.get("rss_kb_current", 0.0) or 0.0),
#         bytes_est=be,
#         bytes_est_total=float(obj.get("bytes_est_total", sum(be.values()))),
#     )
def to_serveragg(obj: Dict[str, Any], label: str) -> ServerAgg:
    be_raw = obj.get("bytes_est", {}) or {}

    # authz_cache 吸収
    if "auth_cache" not in be_raw and "authz_cache" in be_raw:
        be_raw = dict(be_raw)
        be_raw["auth_cache"] = be_raw.get("authz_cache", 0.0)

    # ★ ここが本題 ★
    if label == "auth":
        be_raw = dict(be_raw)
        be_raw["auth_cache"] = 0.0

    be = {k: float(be_raw.get(k, 0.0)) for k in COMPONENTS}

    return ServerAgg(
        rss_kb=float(obj.get("rss_kb", 0.0)),
        rss_kb_current=float(obj.get("rss_kb_current", 0.0) or 0.0),
        bytes_est=be,
        bytes_est_total=float(obj.get("bytes_est_total", sum(be.values()))),
    )


# ============================================================
# 集計（平均・加算）
# ============================================================
def mean_serveraggs(items: List[ServerAgg]) -> ServerAgg:
    def avg(f):
        return float(np.mean([f(x) for x in items])) if items else 0.0

    be_mean = (
        {
            k: float(np.mean([x.bytes_est.get(k, 0.0) for x in items]))
            for k in COMPONENTS
        }
        if items
        else {k: 0.0 for k in COMPONENTS}
    )

    return ServerAgg(
        rss_kb=avg(lambda x: x.rss_kb),
        rss_kb_current=avg(lambda x: x.rss_kb_current),
        bytes_est=be_mean,
        bytes_est_total=(
            float(np.mean([x.bytes_est_total for x in items])) if items else 0.0
        ),
    )


def add_serveraggs(a: ServerAgg, b: ServerAgg) -> ServerAgg:
    be_sum = {k: a.bytes_est.get(k, 0.0) + b.bytes_est.get(k, 0.0) for k in COMPONENTS}
    return ServerAgg(
        rss_kb=a.rss_kb + b.rss_kb,
        rss_kb_current=a.rss_kb_current + b.rss_kb_current,
        bytes_est=be_sum,
        bytes_est_total=a.bytes_est_total + b.bytes_est_total,
    )


# ============================================================
# (A) bytes_est 内訳（TOTAL）
# ============================================================
def plot_memory_breakdown_total(summary: Dict[str, Dict[str, Dict[str, ServerAgg]]]):
    graphs = list(summary.keys())
    labels = list(ROOT_DIRS.keys())

    if UNIT == "MB":
        div = 1024 * 1024
        ylabel = "Estimated memory (MB)"
    elif UNIT == "KB":
        div = 1024
        ylabel = "Estimated memory (KB)"
    else:
        div = 1
        ylabel = "Estimated memory (bytes)"

    x = np.arange(len(graphs))
    bar_w = 0.8 / (len(COMPONENTS) * len(labels))

    plt.figure(figsize=(max(8, len(graphs) * 1.3), 5))

    idx = 0
    for label in labels:
        for comp in COMPONENTS:
            vals = [
                (
                    summary[g][label]["TOTAL"].bytes_est.get(comp, 0.0) / div
                    if label in summary[g]
                    else 0.0
                )
                for g in graphs
            ]
            plt.bar(x + idx * bar_w, vals, width=bar_w, label=f"{label}:{comp}")
            idx += 1

    plt.xticks(x + bar_w * (idx - 1) / 2, graphs, rotation=20)
    plt.ylabel(ylabel)
    plt.title("Memory breakdown by component (TOTAL across servers)")
    if USE_LOG_SCALE:
        plt.yscale("log")
    plt.legend(fontsize=8, ncol=2)
    plt.tight_layout()
    plt.savefig(OUT_BREAKDOWN, dpi=200)
    # plt.show()
    print(f"[OK] saved {OUT_BREAKDOWN}")


# ============================================================
# (B) TOTAL RSS 比較
# ============================================================
def plot_rss_total_by_graph(summary: Dict[str, Dict[str, Dict[str, ServerAgg]]]):
    graphs = list(summary.keys())
    labels = list(ROOT_DIRS.keys())

    x = np.arange(len(graphs))
    bar_w = 0.8 / len(labels)

    plt.figure(figsize=(max(7, len(graphs) * 1.2), 4))

    for i, label in enumerate(labels):
        vals = [
            summary[g][label]["TOTAL"].rss_kb / 1024 if label in summary[g] else 0.0
            for g in graphs
        ]
        plt.bar(x + i * bar_w, vals, width=bar_w, label=label)

    plt.xticks(x + bar_w * (len(labels) - 1) / 2, graphs, rotation=20)
    plt.ylabel("Mean TOTAL RSS (MB)")
    plt.title("Mean TOTAL RSS by Graph")
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT_RSS_TOTAL, dpi=200)
    print(f"[OK] saved {OUT_RSS_TOTAL}")


# ============================================================
# main
# ============================================================
def main() -> None:
    # data[graph][label][endpoint] = [ServerAgg, ...]
    data: Dict[str, Dict[str, Dict[str, List[ServerAgg]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for label, root in ROOT_DIRS.items():
        print(f"[INFO] scanning {label}: {root.resolve()}")
        for log_path in root.rglob(LOG_PATTERN):
            graph = graph_name_from_filename(log_path)
            if graph.startswith("test"):
                continue
            text = log_path.read_text(encoding="utf-8", errors="replace")

            for snap_text in split_into_snapshots(text):
                for endpoint, obj in extract_endpoint_json_from_snapshot(snap_text):
                    # data[graph][label][endpoint].append(to_serveragg(obj))
                    data[graph][label][endpoint].append(to_serveragg(obj, label))

    # summary[graph][label][endpoint/TOTAL]
    summary: Dict[str, Dict[str, Dict[str, ServerAgg]]] = {}

    for graph, per_label in data.items():
        summary[graph] = {}
        for label, per_ep in per_label.items():
            total: ServerAgg | None = None
            summary[graph][label] = {}

            for ep, items in per_ep.items():
                m = mean_serveraggs(items)
                summary[graph][label][ep] = m
                total = m if total is None else add_serveraggs(total, m)

            summary[graph][label]["TOTAL"] = (
                total
                if total is not None
                else ServerAgg(0.0, 0.0, {k: 0.0 for k in COMPONENTS}, 0.0)
            )

    plot_memory_breakdown_total(summary)
    plot_rss_total_by_graph(summary)


if __name__ == "__main__":
    main()
