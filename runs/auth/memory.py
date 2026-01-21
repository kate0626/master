"""
2通りの実行方法が存在
- それぞれのサーバにおけるメモリの利用料の差分を見たい時に、それぞれのサーバの値＆それを合計した値を出力するもの
- C1とD1など方法が異なる場合において、グラフごとにメモリの使用量を比較する
"""

# #!/usr/bin/env python3
# from __future__ import annotations

# import json
# import re
# from collections import defaultdict
# from pathlib import Path
# from typing import Dict, Tuple, List

# import matplotlib.pyplot as plt


# RE_SNAP = re.compile(r"^=== \[MEMORY SNAPSHOT\]")
# RE_EP = re.compile(r"^--- endpoint=(.+) ---\s*$")


# def parse_memory_log(path: Path) -> Tuple[Dict[str, int], int]:
#     """
#     Returns:
#       per_endpoint_peak_rss_kb: endpoint -> max rss_kb in the file
#       peak_total_rss_kb: max over snapshots of sum(rss_kb across endpoints)
#     """
#     lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

#     snap_id = -1
#     endpoint = None

#     # snap_id -> endpoint -> rss_kb
#     snap_rss: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
#     per_ep_peak: Dict[str, int] = defaultdict(int)

#     in_json = False
#     buf: List[str] = []

#     def flush_json():
#         nonlocal in_json, buf, endpoint, snap_id
#         if not in_json:
#             return
#         raw = "\n".join(buf).strip()
#         in_json = False
#         buf = []
#         if endpoint is None or snap_id < 0 or not raw:
#             return
#         try:
#             obj = json.loads(raw)
#         except Exception:
#             return

#         rss = obj.get("rss_kb")
#         if isinstance(rss, (int, float)):
#             rss_i = int(rss)
#             snap_rss[snap_id][endpoint] = rss_i
#             if rss_i > per_ep_peak[endpoint]:
#                 per_ep_peak[endpoint] = rss_i

#     for line in lines:
#         if RE_SNAP.search(line):
#             flush_json()
#             snap_id += 1
#             endpoint = None
#             continue

#         m = RE_EP.match(line)
#         if m:
#             flush_json()
#             endpoint = m.group(1).strip()
#             continue

#         # start of json
#         if line.strip().startswith("{"):
#             flush_json()
#             in_json = True
#             buf = [line]
#             continue

#         if in_json:
#             buf.append(line)
#             if line.strip().endswith("}"):
#                 flush_json()

#     flush_json()

#     peak_total = 0
#     for _, epmap in snap_rss.items():
#         peak_total = max(peak_total, sum(epmap.values()))

#     return dict(per_ep_peak), int(peak_total)


# def aggregate_logs(log_dir: Path, pattern: str = "*.memory.log"):
#     """
#     Build:
#       graphs: list[str]
#       endpoints: sorted unique endpoints
#       per_graph_per_ep_peak: graph -> endpoint -> peak rss_kb
#       per_graph_peak_total: graph -> peak total rss_kb
#     """
#     per_graph_per_ep_peak: Dict[str, Dict[str, int]] = {}
#     per_graph_peak_total: Dict[str, int] = {}
#     endpoints_set = set()

#     files = sorted(log_dir.glob(pattern))
#     if not files:
#         raise FileNotFoundError(f"No files matched {pattern} in {log_dir}")

#     for p in files:
#         graph = p.name.replace(".memory.log", "")
#         per_ep_peak, peak_total = parse_memory_log(p)
#         per_graph_per_ep_peak[graph] = per_ep_peak
#         per_graph_peak_total[graph] = peak_total
#         endpoints_set.update(per_ep_peak.keys())

#     graphs = sorted(per_graph_per_ep_peak.keys())
#     endpoints = sorted(endpoints_set)
#     return graphs, endpoints, per_graph_per_ep_peak, per_graph_peak_total


# def plot_grouped_bars(
#     graphs: List[str],
#     endpoints: List[str],
#     per_graph_per_ep_peak: Dict[str, Dict[str, int]],
#     per_graph_peak_total: Dict[str, int],
#     save_path: Path | None = None,
# ):
#     # columns: each endpoint + "TOTAL"
#     series_names = [f"server:{ep}" for ep in endpoints] + ["TOTAL(sum servers)"]
#     n_series = len(series_names)

#     x = list(range(len(graphs)))
#     width = 0.8 / max(1, n_series)

#     plt.figure(figsize=(max(10, len(graphs) * 1.2), 5))

#     for i, name in enumerate(series_names):
#         vals = []
#         if name == "TOTAL(sum servers)":
#             for g in graphs:
#                 vals.append(int(per_graph_peak_total.get(g, 0)))
#         else:
#             ep = name.replace("server:", "", 1)
#             for g in graphs:
#                 vals.append(int(per_graph_per_ep_peak.get(g, {}).get(ep, 0)))

#         xs = [xi - 0.4 + width / 2 + i * width for xi in x]
#         plt.bar(xs, vals, width=width, label=name)

#     print(vals)
#     plt.xticks(x, graphs, rotation=30, ha="right")
#     plt.ylabel("Peak RSS (kB)")
#     plt.title("Peak RSS per server and peak TOTAL RSS (sum over servers) by graph")
#     plt.legend(fontsize=8)
#     plt.tight_layout()

#     if save_path is not None:
#         plt.savefig(save_path, dpi=200)
#         print(f"Saved: {save_path}")

#     plt.show()


# def main():
#     # ★ここだけあなたの環境に合わせて変える
#     LOG_DIR = Path("./")

#     graphs, endpoints, per_graph_per_ep_peak, per_graph_peak_total = aggregate_logs(
#         LOG_DIR, pattern="*.memory.log"
#     )

#     # 保存したいならファイル名を指定
#     plot_grouped_bars(
#         graphs,
#         endpoints,
#         per_graph_per_ep_peak,
#         per_graph_peak_total,
#         save_path=LOG_DIR / "memory_peaks_by_graph.png",
#     )


# if __name__ == "__main__":
#     main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Tuple, List

import matplotlib.pyplot as plt


# ============================================================
# 設定（★変更不要）
# ============================================================

ROOT_DIRS = {
    # "auth": Path("./D2/1-approach/0.3/"),
    "cache": Path("./D1:cache/0.3"),
    # "cache": Path("./../../D1:cache"),
}

LOG_PATTERN = "*.memory.log"


# ============================================================
# 正規表現
# ============================================================

RE_SNAP = re.compile(r"^=== \[MEMORY SNAPSHOT\]")
RE_EP = re.compile(r"^--- endpoint=(.+) ---\s*$")


# ============================================================
# メモリログ解析
# ============================================================


def parse_memory_log(path: Path) -> int:
    """
    Returns:
      peak_total_rss_kb: int
      （全スナップショット中の「サーバ合計 RSS」の最大値）
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    snap_id = -1
    endpoint = None

    snap_rss = defaultdict(lambda: defaultdict(int))

    in_json = False
    buf: List[str] = []

    def flush_json():
        nonlocal in_json, buf, endpoint, snap_id
        if not in_json:
            return
        raw = "\n".join(buf).strip()
        in_json = False
        buf = []
        if endpoint is None or snap_id < 0 or not raw:
            return
        try:
            obj = json.loads(raw)
        except Exception:
            return

        rss = obj.get("rss_kb")
        if isinstance(rss, (int, float)):
            snap_rss[snap_id][endpoint] = int(rss)

    for line in lines:
        if RE_SNAP.search(line):
            flush_json()
            snap_id += 1
            endpoint = None
            continue

        m = RE_EP.match(line)
        if m:
            flush_json()
            endpoint = m.group(1).strip()
            continue

        if line.strip().startswith("{"):
            flush_json()
            in_json = True
            buf = [line]
            continue

        if in_json:
            buf.append(line)
            brace_depth = 0

            for line in lines:
                if line.strip().startswith("{"):
                    in_json = True
                    buf = [line]
                    brace_depth = line.count("{") - line.count("}")
                    continue
                if in_json:
                    buf.append(line)
                    brace_depth += line.count("{") - line.count("}")
                    if brace_depth == 0:
                        flush_json()
                flush_json()

    peak_total = 0
    for epmap in snap_rss.values():
        peak_total = max(peak_total, sum(epmap.values()))

    return int(peak_total)


# ============================================================
# 複数フォルダ × graph 集約
# ============================================================


def collect_memory_peaks() -> Dict[str, Dict[str, int]]:
    """
    memory_peaks[graph][label] = peak_total_rss_kb
    """
    data = defaultdict(dict)

    for label, root in ROOT_DIRS.items():
        print(f"[INFO] scanning: {root.resolve()}")
        files = list(root.rglob(LOG_PATTERN))
        print(f"[INFO] found {len(files)} files")
        if not files:
            print(f"[WARN] no memory logs found in {root}")
            continue

        for p in files:
            graph = p.name.replace(".memory.log", "")
            peak_total = parse_memory_log(p)
            data[graph][label] = peak_total
            print(f"[{label}] {graph}: peak total RSS = {peak_total} kB")

    if not data:
        raise RuntimeError("No memory data collected")

    return data


# ============================================================
# 可視化（横並び棒 + %変化）
# ============================================================


def plot_memory_comparison(memory_data: Dict[str, Dict[str, int]]):
    graphs = sorted(memory_data.keys())
    labels = list(ROOT_DIRS.keys())

    fig, ax = plt.subplots(1, 1, figsize=(max(10, len(graphs) * 1.4), 5))

    x = range(len(graphs))
    width = 0.8 / len(labels)
    color = ["grey", "navy"]

    for i, label in enumerate(labels):
        vals = [memory_data[g].get(label, 0) / 1024 for g in graphs]  # kB → MB
        xs = [xi - 0.4 + width / 2 + i * width for xi in x]
        ax.bar(xs, vals, width=width, label=label, color=color[i])

    # %変化（base → cache）
    if len(labels) == 2:
        a, b = labels
        for i, g in enumerate(graphs):
            if a in memory_data[g] and b in memory_data[g]:
                v1 = memory_data[g][a]
                v2 = memory_data[g][b]
                if v1 > 0:
                    pct = (v2 - v1) / v1 * 100
                    ax.text(
                        i,
                        max(v1, v2) / 1024 * 1.02,
                        f"{pct:+.1f}%",
                        ha="center",
                        va="bottom",
                        fontsize=9,
                    )

    ax.set_xticks(list(x))
    ax.set_xticklabels(graphs, rotation=30, ha="right")
    ax.set_ylabel("Peak total RSS (MB)")
    ax.set_title("Peak memory usage (sum of servers)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig("D1:cache のメモリ比較")
    plt.show()


# ============================================================
# main
# ============================================================


def main():
    memory_data = collect_memory_peaks()
    plot_memory_comparison(memory_data)


if __name__ == "__main__":
    main()
