#!/bin/zsh
# base-rtt: 対象RWの歩数TでRTTモデル(above/below/zigzag)を設計し、RWを実行して
#           各歩にRTTを組み込み、実行時のRTT軌跡が設計モデルに一致するか確認する。
#           生成物は results/ に出る。土台は base/base(サーバ間RW)の GraphShard。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p results

# --- 対象グラフ(既定: 実データ com-amazon。karate に変えれば数秒の smoke test) ---
GRAPH="../../dataset/Louvain/graph/com-amazon-connected.gr"
# GRAPH="../../dataset/Louvain/graph/karate-graph.gr"

MU=50; AMP=40; START=1; STEPS=100; WALKS=80; SEED=1
TAG=amz

# 設計 → 実行 → 一致確認(above/below/zigzag を横並びで重ね描き)
python3 design_and_run.py --graph "$GRAPH" \
  --start $START --mu $MU --amp $AMP --steps $STEPS --walks $WALKS --seed $SEED \
  --directions above below zigzag \
  --out results/design_run_${TAG}.png

echo "done. see results/design_run_${TAG}.png"
