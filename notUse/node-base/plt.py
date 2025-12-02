import re
import matplotlib.pyplot as plt

# === 設定 ===
log_file = "all_results.txt"  # あなたのログファイル名に合わせて変更
scripts = ["baseRW.py", "before.py", "after.py"]

# === データ構造 ===
data_length = {s: [] for s in scripts}
data_time = {s: [] for s in scripts}

# === ログ読み込み ===
current_script = None
with open(log_file, "r") as f:
    for line in f:
        # 実行スクリプト名の抽出
        if line.startswith("=== Running"):
            m = re.search(r"Running (.+?) \(Trial", line)
            if m:
                current_script = m.group(1)

        # Average length
        elif "Average length" in line:
            m = re.search(r"Average length:\s*([\d.]+)", line)
            if m and current_script:
                data_length[current_script].append(float(m.group(1)))

        # Total time
        elif "Total time" in line:
            m = re.search(r"Total time:\s*([\d.]+)", line)
            if m and current_script:
                data_time[current_script].append(float(m.group(1)))

# === データ確認 ===
print("Average length data:")
for s in scripts:
    print(f"  {s}: {data_length[s]}")
print("\nTotal time data:")
for s in scripts:
    print(f"  {s}: {data_time[s]}")

# === 箱ひげ図1: Average length ===
plt.figure(figsize=(8, 6))
plt.boxplot(
    [data_length[s] for s in scripts],
    labels=["Base RW", "Before", "After"],
    patch_artist=True,
)
plt.title("Comparison of Average Path Lengths (10 trials each)")
plt.ylabel("Average length")
plt.grid(axis="y", linestyle="--", alpha=0.7)
plt.tight_layout()
plt.savefig("average_length_boxplot.png")
# plt.show()

# === 箱ひげ図2: Total time ===
plt.figure(figsize=(8, 6))
plt.boxplot(
    [data_time[s] for s in scripts],
    labels=["Base RW", "Before", "After"],
    patch_artist=True,
)
plt.title("Comparison of Total Execution Time (10 trials each)")
plt.ylabel("Total time")
plt.grid(axis="y", linestyle="--", alpha=0.7)
plt.tight_layout()
plt.savefig("total_time_boxplot.png")
# plt.show()

print("\n✅ 2つの箱ひげ図を出力しました：")
print(" - average_length_boxplot.png")
print(" - total_time_boxplot.png")
