import matplotlib.pyplot as plt
import numpy as np

# =========================
# Data
# =========================

methods = ["None", "LRU", "ARC", "Proposed"]

# Amazon0601
# amazon = [
#     3150,  # None
#     2783,  # LRU
#     2862,  # ARC
#     2708,  # Proposed
# ]

# # VLDB
# vldb = [
#     4300,  # None
#     3650,  # LRU
#     3800,  # ARC
#     3500,  # Proposed
# ]

amazon = [
    430,
    405,
    410,
    400,
]

vldb = [
    610,
    585,
    590,
    580,
]

# =========================
# Plot
# =========================

x = np.arange(len(methods))
width = 0.35

plt.rcParams["font.size"] = 14

fig, ax = plt.subplots(figsize=(8, 5))

bars1 = ax.bar(x - width / 2, amazon, width, label="Amazon")

bars2 = ax.bar(x + width / 2, vldb, width, label="VLDB")

# -------------------------
# Axis
# -------------------------

ax.set_xlabel("Method")
ax.set_ylabel("Total Time (s)")
ax.set_xticks(x)
ax.set_xticklabels(methods)

# Y軸をいい感じに切る
all_values = amazon + vldb

ymin = min(all_values) * 0.9
ymax = max(all_values) * 1.05

ax.set_ylim(ymin, ymax)

# -------------------------
# Value labels
# -------------------------

for bars in [bars1, bars2]:
    ax.bar_label(bars, fmt="%.0f", padding=3)

# -------------------------
# Style
# -------------------------

ax.grid(axis="y", linestyle="--", alpha=0.4)

ax.legend()

plt.tight_layout()
plt.savefig("total_time_comparison.png", dpi=300)
plt.show()
