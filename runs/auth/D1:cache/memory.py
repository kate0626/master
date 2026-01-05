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
# ★ 解析対象ログ（固定）★　ここにファイルを追加していく
# ============================================================
LOG_FILES = [
    Path("test.memory.log"),
    Path("karate.memory.log"),
    Path("fb-caltech-connected.memory.log"),
]

# 出力ファイル
OUT_BREAKDOWN = "memory_breakdown_grouped.png"  # (A) bytes_est 内訳
OUT_RSS_TOTAL = "rss_total_by_graph.png"  # (B) TOTAL RSS

UNIT = "MB"  # bytes_est 図の単位: "bytes" | "KB" | "MB"
USE_LOG_SCALE = True  # fb を含むなら True 推奨


# ============================================================
# ログ形式
# ============================================================
SNAPSHOT_RE = re.compile(r"^=+\n=== \[MEMORY SNAPSHOT .*?\] ===.*?\n=+\n", re.MULTILINE)
ENDPOINT_RE = re.compile(r"--- endpoint=([^\s]+) ---\s*", re.MULTILINE)


# ============================================================
# (A) bytes_est の内訳で見るコンポーネント
# ============================================================
COMPONENTS = [
    "neighbor_map",
    "node_to_starts",
    # "owner_map",
    "auth_cache",  # ログによって authz_cache の場合があるので吸収する
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
# 共通：グラフ名
# ============================================================
def graph_name_from_filename(path: Path) -> str:
    name = path.name
    for suf in [".memory.log", ".log"]:
        if name.endswith(suf):
            return name[: -len(suf)]
    return path.stem


# ============================================================
# 1ファイルを snapshot 単位に分割
# ============================================================
def split_into_snapshots(text: str) -> List[str]:
    """
    '=== [MEMORY SNAPSHOT ...] ===' のヘッダで確実に区切る。
    返す各要素は「その snapshot に対応するテキスト全体」。
    """
    # ヘッダ位置を見つける
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
# snapshot テキストから endpoint の JSON を抜く
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
# JSON -> ServerAgg
# ============================================================
def to_serveragg(obj: Dict[str, Any]) -> ServerAgg:
    be_raw = obj.get("bytes_est", {}) or {}

    # ログによって authz_cache のキー名が違うのを吸収
    if "auth_cache" not in be_raw and "authz_cache" in be_raw:
        be_raw = dict(be_raw)
        be_raw["auth_cache"] = be_raw.get("authz_cache", 0.0)

    be = {k: float(be_raw.get(k, 0.0)) for k in COMPONENTS}

    return ServerAgg(
        rss_kb=float(obj.get("rss_kb", 0.0)),
        rss_kb_current=float(obj.get("rss_kb_current", 0.0) or 0.0),
        bytes_est=be,
        bytes_est_total=float(obj.get("bytes_est_total", sum(be.values()))),
    )


# ============================================================
# 集計：平均/合計
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
# (A) bytes_est 内訳 grouped bar（TOTAL）
# ============================================================
def plot_memory_breakdown_total(summary: Dict[str, Dict[str, ServerAgg]]) -> None:
    graphs = list(summary.keys())
    x_base = np.arange(len(graphs))
    bar_w = 0.14

    if UNIT == "MB":
        div = 1024 * 1024
        ylabel = "Estimated memory (MB)"
    elif UNIT == "KB":
        div = 1024
        ylabel = "Estimated memory (KB)"
    else:
        div = 1
        ylabel = "Estimated memory (bytes)"

    plt.figure(figsize=(max(8, len(graphs) * 1.2), 5))
    for i, comp in enumerate(COMPONENTS):
        vals = [summary[g]["TOTAL"].bytes_est.get(comp, 0.0) / div for g in graphs]
        plt.bar(x_base + i * bar_w, vals, width=bar_w, label=comp)

    plt.xticks(x_base + bar_w * (len(COMPONENTS) - 1) / 2, graphs, rotation=20)
    plt.ylabel(ylabel)
    plt.title("Memory breakdown by component (TOTAL across servers)")
    plt.legend()
    if USE_LOG_SCALE:
        plt.yscale("log")
    plt.tight_layout()
    plt.savefig(OUT_BREAKDOWN, dpi=200)
    print(f"[OK] saved {OUT_BREAKDOWN}")


# ============================================================
# (B) TOTAL RSS だけをグラフ横並び
# ============================================================
def plot_rss_total_by_graph(summary: Dict[str, Dict[str, ServerAgg]]) -> None:
    graphs = list(summary.keys())
    # TOTAL RSS は「endpointごとの平均RSSを合計」= summary[g]["TOTAL"].rss_kb
    values_mb = [summary[g]["TOTAL"].rss_kb / 1024.0 for g in graphs]

    plt.figure(figsize=(max(7, len(graphs) * 1.2), 4))
    plt.bar(graphs, values_mb)
    plt.ylabel("Mean TOTAL RSS (MB)")
    plt.title("Mean TOTAL RSS by Graph")
    plt.xticks(rotation=20)
    plt.tight_layout()
    plt.savefig(OUT_RSS_TOTAL, dpi=200)
    print(f"[OK] saved {OUT_RSS_TOTAL}")


# ============================================================
# main：両方出す
# ============================================================
def main() -> None:
    # data[graph][endpoint] = [ServerAgg(snapshot1), ServerAgg(snapshot2), ...]
    data: Dict[str, Dict[str, List[ServerAgg]]] = defaultdict(lambda: defaultdict(list))

    for log_path in LOG_FILES:
        graph = graph_name_from_filename(log_path)
        text = log_path.read_text(encoding="utf-8", errors="replace")

        # snapshotごとに分けて読み取る（ここが重要）
        for snap_text in split_into_snapshots(text):
            for endpoint, obj in extract_endpoint_json_from_snapshot(snap_text):
                data[graph][endpoint].append(to_serveragg(obj))

    # summary[graph][endpoint] = mean over snapshots
    summary: Dict[str, Dict[str, ServerAgg]] = {}

    for graph, per_ep in data.items():
        summary[graph] = {}
        total: ServerAgg | None = None

        for ep, items in per_ep.items():
            m = mean_serveraggs(items)
            summary[graph][ep] = m
            total = m if total is None else add_serveraggs(total, m)

        summary[graph]["TOTAL"] = (
            total
            if total is not None
            else ServerAgg(0.0, 0.0, {k: 0.0 for k in COMPONENTS}, 0.0)
        )

    # 2図出力
    plot_memory_breakdown_total(summary)
    plot_rss_total_by_graph(summary)

    # もし手元で表示もしたいならコメント解除
    # plt.show()


if __name__ == "__main__":
    main()
