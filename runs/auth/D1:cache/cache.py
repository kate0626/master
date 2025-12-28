"""
D1 でキャッシュを導入したので、その評価としてヒット率を導入
キャッシュのヒット率を評価するために、箱ひげ図で評価
"""

import re
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

# =========================
# 設定（そのまま）
# =========================
ROOT_DIRS = {
    # "base": Path("./"),
    "cache": Path("./"),
}

HIT_RATE_PATTERN = re.compile(r"hit_rate:\s*([0-9.]+)")

# =========================
# ログ解析
# =========================
# data[graph][exp] = list of hit_rates
data = {}

for exp, root in ROOT_DIRS.items():
    for log_file in root.glob("*.log"):
        graph = log_file.stem

        hit_rates = []
        with log_file.open() as f:
            for line in f:
                m = HIT_RATE_PATTERN.search(line)
                if m:
                    hit_rates.append(float(m.group(1)))

        if hit_rates:
            data.setdefault(graph, {})[exp] = hit_rates

# =========================
# 可視化（箱ひげ）
# =========================
graphs = sorted(data.keys())
experiments = list(ROOT_DIRS.keys())

fig, axes = plt.subplots(1, len(graphs), figsize=(4 * len(graphs), 4), sharey=True)

if len(graphs) == 1:
    axes = [axes]

for ax, graph in zip(axes, graphs):
    box_data = []
    labels = []

    for exp in experiments:
        if exp in data[graph]:
            box_data.append(data[graph][exp])
            labels.append(exp)

    ax.boxplot(box_data, labels=labels, showmeans=True)
    ax.set_title(graph)
    ax.set_ylabel("Auth cache hit rate")
    ax.set_ylim(0, 1)


plt.suptitle("Auth Cache Hit Rate per Graph", fontsize=14)
plt.tight_layout()
plt.savefig("D1:cache")
plt.show()
