# """
# GRAPH
# 任意の数のファイルを選んで箱ひげ図かくプログラム
# """
#
# import re
# import matplotlib.pyplot as plt

# # 読み込むファイル
# log_files = {
#     "no-auth": "./noauth_base_many_server_walks100_alpha0.1.log",
#     "with-auth": "./auth_result_walks100_alpha0.1.log",
# }


# def extract_durations(path):
#     durations = []
#     with open(path, "r") as f:
#         for line in f:
#             m = re.search(r"\[Controller\] duration ([0-9.]+)", line)
#             if m:
#                 durations.append(float(m.group(1)))
#     return durations


# # -----------------------
# # 各ログの duration を抽出
# # -----------------------
# labels = []
# data = []

# for label, file_path in log_files.items():
#     labels.append(label)
#     data.append(extract_durations(file_path))

# print("labels:", labels)
# print("data:", data)

# # -----------------------
# # 箱ひげ図（横に2つ）
# # -----------------------
# plt.figure(figsize=(8, 6))

# plt.boxplot(data, labels=labels, vert=True)  # ←縦箱ひげ（デフォルト）

# plt.ylabel("duration (seconds)")
# plt.title("Duration comparison between files")

# plt.tight_layout()
# plt.savefig("noauth&withauth.png")
# plt.show()


"""
GRAPH
ここから折れ線グラフを書くプログラム
"""

import re
import numpy as np
import matplotlib.pyplot as plt

# =========================================================
# ★ ここにあなたが渡すログファイルを並べるだけでOK
#    例：alpha = 0.1, 0.3, 0.5 の3種類
# =========================================================

files_with_auth = {
    0.1: "result_walks100_alpha0.1.log",
    0.05: "result_walks100_alpha0.05.log",
    0.04: "result_walks100_alpha0.04.log",
    0.03: "result_walks100_alpha0.03.log",
    0.02: "result_walks100_alpha0.02.log",
    0.01: "result_walks100_alpha0.01.log",
}

files_no_auth = {
    0.1: "base_many_server_walks100_alpha0.1.log",
    0.05: "base_many_server_walks100_alpha0.05.log",
    0.04: "base_many_server_walks100_alpha0.04.log",
    0.03: "base_many_server_walks100_alpha0.03.log",
    0.02: "base_many_server_walks100_alpha0.02.log",
    0.01: "base_many_server_walks100_alpha0.01.log",
}


## 実行時間の長さを計算
def extract_remote_durations(path):
    durations = []
    with open(path, "r") as f:
        for line in f:
            m = re.search(r"remote_duration:\s*([0-9.]+)", line)
            if m:
                durations.append(float(m.group(1)))
    return durations


## 歩調の長さを計算
def extract_avg_lengths(path):
    lengths = []
    with open(path, "r") as f:
        for line in f:
            # 例: "    - avg_length: 9.686000"
            m = re.search(r"avg_length:\s*([0-9.]+)", line)
            if m:
                lengths.append(float(m.group(1)))
    return lengths


# =========================================================
# α ごとの平均値を計算
# =========================================================

alphas = sorted(files_with_auth.keys())

## 平均時間
avg_with_auth = []
avg_no_auth = []

for alpha in alphas:
    d1 = extract_remote_durations(files_with_auth[alpha])
    d2 = extract_remote_durations(files_no_auth[alpha])

    avg_with_auth.append(np.mean(d1))
    avg_no_auth.append(np.mean(d2))

print("with auth:", avg_with_auth)
print("no auth:", avg_no_auth)


## 平均歩調
avglen_with_auth = []
avglen_no_auth = []

for alpha in alphas:
    l1 = extract_avg_lengths(files_with_auth[alpha])
    l2 = extract_avg_lengths(files_no_auth[alpha])

    avglen_with_auth.append(np.mean(l1))
    avglen_no_auth.append(np.mean(l2))

print("avg length with auth:", avglen_with_auth)
print("avg length no auth:", avglen_no_auth)


# =========================================================
# 折れ線グラフ
# =========================================================


# ## 実行時間のグラフを書くとき
# plt.figure(figsize=(8, 5))

# plt.plot(alphas, avg_with_auth, marker="o", label="with auth")
# plt.plot(alphas, avg_no_auth, marker="o", label="no auth")

# plt.xlabel("alpha")
# plt.ylabel("remote_duration (seconds)")
# plt.title("Effect of authorization on execution time")
# plt.legend()

# plt.grid(True)
# plt.tight_layout()
# plt.savefig("rwtime_change_alpha.png")
# plt.show()


## 歩調のグラフを書く時
plt.figure(figsize=(8, 5))

plt.plot(alphas, avglen_with_auth, marker="o", label="with auth")
plt.plot(alphas, avglen_no_auth, marker="o", label="no auth")

plt.xlabel("alpha")
plt.ylabel("avg_length (steps)")
plt.title("Effect of authorization on RW length")
plt.legend()

plt.grid(True)
plt.tight_layout()
plt.savefig("rw_length_change_alpha.png")
plt.show()
