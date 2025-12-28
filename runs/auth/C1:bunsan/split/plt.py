"""
2種類の使用方法がある
- C1で比較をして、認可なしの部分を引いて求める時
- 複数の種類のファイル同士で総実行時間を比較するとき
"""

# import re
# from pathlib import Path
# import matplotlib.pyplot as plt
# import numpy as np

# LOG_DIR = Path("./")
# LOG_FILES = sorted(LOG_DIR.glob("*.log"))

# graph_names = []
# controller_times = []
# total_times = []

# pattern_controller = re.compile(r"\[TOTAL\]\s*controller_duration_sum=([0-9.]+)s")
# pattern_auth = re.compile(r"\[TOTAL\]\s*authorization_time_sum=([0-9.]+)s")

# for log_file in LOG_FILES:
#     text = log_file.read_text()

#     m_ctrl = pattern_controller.search(text)
#     print(m_ctrl)
#     m_auth = pattern_auth.search(text)
#     print(m_auth)

#     if not m_ctrl or not m_auth:
#         print(f"[SKIP] invalid log (TOTAL not found): {log_file.name}")
#         continue

#     try:
#         controller = float(m_ctrl.group(1))
#         auth = float(m_auth.group(1))
#     except ValueError:
#         print(f"[SKIP] parse error: {log_file.name}")
#         continue

#     graph_name = log_file.stem

#     graph_names.append(graph_name)
#     controller_times.append(controller)
#     total_times.append(controller + auth)

# # ===== 描画 =====
# x = np.arange(len(graph_names))
# width = 0.35

# plt.figure(figsize=(6, 5))
# plt.bar(x - width / 2, controller_times, width, label="base", color="grey")
# plt.bar(x + width / 2, total_times, width, label="with auth", color="navy")

# plt.xticks(x, graph_names, rotation=30)
# plt.ylabel("Time (seconds)")
# plt.xlabel("Graph")
# plt.legend()
# plt.tight_layout()
# plt.savefig("C1: auth time")
# plt.show()
import re
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

# =========================
# 設定（そのまま使用）
# =========================
ROOT_DIRS = {
    "base": Path("./"),
    "cache": Path("./../../D1:cache"),
}

LOG_PATTERN = re.compile(r"\[TOTAL\]\s*controller_duration_sum=([0-9.]+)s")

# =========================
# ログ読み取り
# =========================
# data[graph][exp] = time
data = {}

for exp_name, root_dir in ROOT_DIRS.items():
    for log_file in root_dir.glob("*.log"):
        graph = log_file.stem  # karate.log -> karate
        if graph == "test":
            continue
        with log_file.open() as f:
            for line in f:
                m = LOG_PATTERN.search(line)
                if m:
                    time = float(m.group(1))
                    data.setdefault(graph, {})[exp_name] = time
                    break

# =========================
# 可視化準備
# =========================
graphs = sorted(data.keys())
experiments = list(ROOT_DIRS.keys())

x = np.arange(len(graphs))
width = 0.35

fig, ax = plt.subplots(figsize=(10, 5))

times_0 = [data[g].get(experiments[0], np.nan) for g in graphs]
times_1 = [data[g].get(experiments[1], np.nan) for g in graphs]

bars_0 = ax.bar(x - width / 2, times_0, width, label=experiments[0], color="navy")
bars_1 = ax.bar(x + width / 2, times_1, width, label=experiments[1], color="red")

# =========================
# 変化率表示
# =========================
for i, g in enumerate(graphs):
    if all(e in data[g] for e in experiments):
        a = data[g][experiments[0]]
        b = data[g][experiments[1]]
        change = (b - a) / a * 100

        ax.text(
            x[i], max(a, b), f"{change:+.1f}%", ha="center", va="bottom", fontsize=9
        )

# =========================
# 仕上げ
# =========================
ax.set_ylabel("controller_duration_sum (s)")
ax.set_xticks(x)
ax.set_xticklabels(graphs)
ax.legend()
ax.set_title("Controller Duration Comparison (base vs cache)")

plt.tight_layout()
plt.savefig("base&auth time compare")
plt.show()
