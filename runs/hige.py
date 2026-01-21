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

auth, auth_subgraph size = 2,4,6,8,10 のログファイルを読み込んで記述
HotのPPRの場合は少し異なるのでファイルを参照
"""

import re
import numpy as np
import matplotlib.pyplot as plt

# =========================================================
# ★ ここにあなたが渡すログファイルを並べるだけでOK
#    例：alpha = 0.1, 0.3, 0.5 の3種類
# =========================================================

# 認証ありとサブグラフの大きさによる比較
files_with_auth = {
    0.1: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth/test/exp1_data/result_ng0_3_walks100_alpha0.01.log",
}

files_sub_10 = {
    0.1: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth_subgraph/test/10_result_ng0_3_walks100_alpha0.01.log",
}

files_sub_2 = {
    0.1: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth_subgraph/test/2_result_ng0_3_walks100_alpha0.01.log",
}

files_sub_4 = {
    0.1: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.01.log",
}

files_sub_6 = {
    0.1: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.01.log",
}

files_sub_8 = {
    0.1: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.1.log",
    0.08: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.08.log",
    0.06: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.06.log",
    0.04: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.04.log",
    0.02: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.02.log",
    0.01: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.01.log",
}

# files_no_auth = {
#     0.1: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.1.log",
#     0.08: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.08.log",
#     0.06: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.06.log",
#     0.04: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.04.log",
#     0.02: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.02.log",
#     0.01: "auth_subgraph/test/4_result_ng0_3_walks100_alpha0.01.log",
# }

# files_no_auth = {
#     0.1: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.1.log",
#     0.08: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.08.log",
#     0.06: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.06.log",
#     0.04: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.04.log",
#     0.02: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.02.log",
#     0.01: "auth_subgraph/test/6_result_ng0_3_walks100_alpha0.01.log",
# }

# files_with_auth_visi = {
#     0.1: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.1.log",
#     0.08: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.08.log",
#     0.06: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.06.log",
#     0.04: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.04.log",
#     0.02: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.02.log",
#     0.01: "auth_subgraph/test/8_result_ng0_3_walks100_alpha0.01.log",
# }

# 実験1
# files_no_auth = {
#     0.1: "auth/test/result_ng0_0_walks100_alpha0.1.log",
#     0.08: "auth/test/result_ng0_0_walks100_alpha0.08.log",
#     0.06: "auth/test/result_ng0_0_walks100_alpha0.06.log",
#     0.04: "auth/test/result_ng0_0_walks100_alpha0.04.log",
#     0.02: "auth/test/result_ng0_0_walks100_alpha0.02.log",
#     0.01: "auth/test/result_ng0_0_walks100_alpha0.01.log",
# }


# files_no_auth = {
#     0.1: "base/test/base_many_server_walks100_alpha0.1.log",
#     0.08: "base/test/base_many_server_walks100_alpha0.08.log",
#     0.06: "base/test/base_many_server_walks100_alpha0.06.log",
#     0.04: "base/test/base_many_server_walks100_alpha0.04.log",
#     0.02: "base/test/base_many_server_walks100_alpha0.02.log",
#     0.01: "base/test/base_many_server_walks100_alpha0.01.log",
# }

# files_with_auth_visi = {
#     0.1: "auth/test/_visibleresult_ng0_3_walks100_alpha0.1.log",
#     0.08: "auth/test/_visibleresult_ng0_3_walks100_alpha0.08.log",
#     0.06: "auth/test/_visibleresult_ng0_3_walks100_alpha0.06.log",
#     0.04: "auth/test/_visibleresult_ng0_3_walks100_alpha0.04.log",
#     0.02: "auth/test/_visibleresult_ng0_3_walks100_alpha0.02.log",
#     0.01: "auth/test/_visibleresult_ng0_3_walks100_alpha0.01.log",
# }


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
avg_sub_2 = []
avg_sub_4 = []
avg_sub_6 = []
avg_sub_8 = []
avg_sub_10 = []

for alpha in alphas:
    d1 = extract_remote_durations(files_with_auth[alpha])
    d2 = extract_remote_durations(files_sub_2[alpha])
    d3 = extract_remote_durations(files_sub_4[alpha])
    d4 = extract_remote_durations(files_sub_6[alpha])
    d5 = extract_remote_durations(files_sub_8[alpha])
    d6 = extract_remote_durations(files_sub_10[alpha])

    avg_with_auth.append(np.mean(d1))
    avg_sub_2.append(np.mean(d2))
    avg_sub_4.append(np.mean(d3))
    avg_sub_6.append(np.mean(d4))
    avg_sub_8.append(np.mean(d5))
    avg_sub_10.append(np.mean(d6))


print("with auth:", avg_with_auth)
print("sub2:", avg_sub_2)
print("sub4", avg_sub_4)
print("sub6", avg_sub_6)
print("sub8", avg_sub_8)
print("sub10", avg_sub_10)


## 平均歩調
avglen_with_auth = []
avglen_sub2 = []
avglen_sub4 = []
avglen_sub6 = []
avglen_sub8 = []
avglen_sub10 = []
for alpha in alphas:
    l1 = extract_avg_lengths(files_with_auth[alpha])
    l2 = extract_avg_lengths(files_sub_2[alpha])
    l3 = extract_avg_lengths(files_sub_4[alpha])
    l4 = extract_avg_lengths(files_sub_6[alpha])
    l5 = extract_avg_lengths(files_sub_8[alpha])
    l6 = extract_avg_lengths(files_sub_10[alpha])

    avglen_with_auth.append(np.mean(l1))
    avglen_sub2.append(np.mean(l2))
    avglen_sub4.append(np.mean(l3))
    avglen_sub6.append(np.mean(l4))
    avglen_sub8.append(np.mean(l5))
    avglen_sub10.append(np.mean(l6))
# avglen_no_auth = []
# avglen_with_auth_vis = []

# for alpha in alphas:
#     l1 = extract_avg_lengths(files_with_auth[alpha])
#     l2 = extract_avg_lengths(files_no_auth[alpha])
#     l3 = extract_avg_lengths(files_with_auth_visi[alpha])

#     avglen_with_auth.append(np.mean(l1))
#     avglen_no_auth.append(np.mean(l2))
#     avglen_with_auth_vis.append(np.mean(l3))

print("avg length with auth:", avglen_with_auth)
print("avg length sub2:", avglen_sub2)
print("avg length sub4:", avglen_sub4)
print("avg length sub6:", avglen_sub6)
print("avg length sub8:", avglen_sub8)
print("avg length sub10:", avglen_sub10)

# print("avg length no auth:", avglen_no_auth)
# print("avg visi", avglen_with_auth_vis)


# =========================================================
# 折れ線グラフ
# =========================================================


## 実行時間のグラフを書くとき
# plt.figure(figsize=(8, 5))

# plt.plot(alphas, avg_with_auth, marker="o", label="with auth")
# plt.plot(alphas, avg_sub_2, marker="o", label="subgraph 2")
# plt.plot(alphas, avg_sub_4, marker="o", label="subgraph 4")
# plt.plot(alphas, avg_sub_6, marker="o", label="subgraph 6")
# plt.plot(alphas, avg_sub_8, marker="o", label="subgraph 8")
# plt.plot(alphas, avg_sub_10, marker="o", label="subgraph 10")
# # plt.plot(alphas, avg_with_auth_visi, marker="o", label="visi auth")
# # plt.plot(alphas, avg_no_auth, marker="o", label="no auth")


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
plt.plot(alphas, avglen_sub2, marker="o", label="subgraph 2")
plt.plot(alphas, avglen_sub4, marker="o", label="subgraph 4")
plt.plot(alphas, avglen_sub6, marker="o", label="subgraph 6")
plt.plot(alphas, avglen_sub8, marker="o", label="subgraph 8")
plt.plot(alphas, avglen_sub10, marker="o", label="subgraph 10")
# plt.plot(alphas, avglen_no_auth, marker="o", label="visi auth")
# plt.plot(alphas, avglen_with_auth_vis, marker="o", label="no auth")

plt.xlabel("alpha")
plt.ylabel("avg_length (steps)")
plt.title("Effect of authorization on RW length")
plt.legend()

plt.grid(True)
plt.tight_layout()
plt.savefig("rw_length_change_alpha.png")
plt.show()
