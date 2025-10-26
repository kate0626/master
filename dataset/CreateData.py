# 1〜30の数字を1行ずつ出力してファイルに保存する
import random

# 出力ファイル名
output_file = "./private/karate.txt"

# ファイルを開く（書き込みモード）
with open(output_file, "w") as f:
    for i in range(0, 34):
        label = "Private" if random.random() < 0.1 else "Public"
        f.write(f"{i} {label}\n")

print(f"{output_file} に1〜30の数字を書き込みました。")
