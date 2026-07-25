#!/bin/zsh
set -euo pipefail
############################################################
# base/base/splits.sh を流用した RTT 組み込み版(スコア計算なし)。
#   - split_remote_server_rtt.py を --cache-policy none で起動(提案スコアは使わない)
#   - --rtt-field <field.json> で各歩に RTT を割り当て(walk ループ内で1歩ずつ)
#   - --walks-out <jsonl> に (path, rtt) を記録
#
# 実行はリポジトリルート(master-progrem/)から:
#   zsh base/base-rtt/multiserver/splits_rtt.sh
# 事前に field.json を作り、全ホストの同じパスに配置しておくこと:
#   PYTHONPATH=base/base python3 base/base-rtt/single-server/paint_field.py \
#     --graph dataset/Louvain/graph/amazon0601.gr \
#     --start 0 --direction above --converge-steps 100 --walks 80 \
#     --out base/base-rtt/results/field_amz.json
############################################################

TIMEOUT=120
GRAPH=${GRAPH_OVERRIDE:-amazon0601}
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
LOG_DIR="runs/rtt/${GRAPH}/base"
RW_WALKS=${RW_WALKS_OVERRIDE:-100}
ALPHA=${ALPHA_OVERRIDE:-0.1}
NG_RATE="0.3"

# ★ RTT 用: 距離場 と walk 出力(全サーバに同じ field を配ること)
FIELD=${FIELD_OVERRIDE:-"base/base-rtt/results/field_${GRAPH}.json"}
WALKS_OUT=${WALKS_OUT_OVERRIDE:-"base/base-rtt/results/walks_ms_${GRAPH}.jsonl"}

if [[ -n "${START_NODES_OVERRIDE:-}" ]]; then
  eval "START_NODES_LIST=(${START_NODES_OVERRIDE})"
else
  START_NODES_LIST=(0)
fi

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${GRAPH}.log"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

# ====== サーバ構成(base/base/splits.sh と同じ形式) ======
SERVERS=(
  "host=ab06 id=0 ip=10.58.60.6  port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
  "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
)
SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

# ====== リモート起動コマンド(★RTT 引数を追加, スコアは none) ======
REMOTE_CMD_BASE="python3 base/base-rtt/multiserver/split_remote_server_rtt.py \\
  --server-count ${SERVER_COUNT} \\
  --edges ${EDGE_FILE} \\
  --server-endpoints ${SERVER_ENDPOINTS_STR} \\
  --owned-hints-only \\
  --cache-policy none \\
  --rtt-field ${FIELD} \\
  --walks-out ${WALKS_OUT}"

cleanup() {
  echo ">>> [CLEANUP] Killing all servers..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f split_remote_server_rtt.py || true" >/dev/null 2>&1 || true
  done
  pkill -f split_controller_rtt.py || true
  echo ">>> [CLEANUP] Done."
}
trap cleanup EXIT

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5
  echo "=== [${host}] サーバ起動 (ID=${id}) ==="
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}
: > split_remote_server_rtt.log
${REMOTE_CMD_BASE} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  > split_remote_server_rtt.log 2>&1 &
echo \$! > split_remote_server_rtt.pid
timeout ${TIMEOUT}s bash <<INNER
set -e
until curl -sf "http://${ip}:${port}/health" >/dev/null 2>&1; do sleep 0.2; done
INNER
echo "[INFO] ${host}: 起動OK (/health)"
EOF
  if [[ $? -ne 0 ]]; then
    echo "[WARN] ${host}: 起動タイムアウト (${TIMEOUT}s)"
    ssh "$host" "tail -n 80 split_remote_server_rtt.log || true"
  fi
}

for entry in "${SERVERS[@]}"; do
  eval "$entry"
  sleep 1
  start_remote_server "$host" "$id" "$ip" "$port" "$nts" &
done
wait

# ====== controller(RTT版, スコア計算なし)を各始点で実行 ======
for start_node in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] ${start_node} ==="
  python3 base/base-rtt/multiserver/split_controller_rtt.py \
    --servers ${SERVER_COUNT} \
    --server-endpoints ${SERVER_ENDPOINTS_STR} \
    --start-node "${start_node}" \
    --walks ${RW_WALKS} \
    --alpha ${ALPHA} \
    --seed 42 \
    --out-dir "${LOG_DIR}"
done

echo "[DONE] walks(path+rtt) -> ${WALKS_OUT}"
