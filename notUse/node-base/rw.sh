#!/bin/bash


## 全てタイミングにおいて、それぞれ実行し、結果を一つのファイル

## 実行したいファイル
## python a.py 
## python b.py
## python c.py

# 出力ファイル
output_file="all_results.txt"

# 既存の出力ファイルをクリア
> "$output_file"

# 実行するスクリプト一覧
scripts=("baseRW.py" "before.py" "after.py")

# 各スクリプトを10回ずつ実行
for script in "${scripts[@]}"; do
    for i in {1..10}; do
        echo "=== Running $script (Trial $i) ===" >> "$output_file"
        python "$script" >> "$output_file" 2>&1
        echo "" >> "$output_file"
    done
done

echo "✅ 全てのスクリプトを10回ずつ実行し、結果を $output_file に保存しました。"

python plt.py