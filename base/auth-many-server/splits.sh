#!/bin/zsh
set -euo pipefail

############################################################
# Remote servers 起動 → 起動確認(十分待機) → Controller 実行

# karate:01234
# amazon0601: 02356
# vldb:0 1 2 10 11
############################################################

# ---- 待機パラメータ ----
TIMEOUT=90          # 最大待機（秒）
INITIAL_GRACE=5     # 起動直後の猶予（秒）
HEALTH_OK_STREAK=2  # /health 連続成功回数
HEALTH_INTERVAL=0.5

GRAPH="fb-caltech-connected"
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"

LOG_DIR="runs/auth/D2/1-approach/0.3/"
RW_WALKS=100
ALPHA=0.1
NG_RATE="0.3"
START_NODES_LIST=(1 2 3 40)

mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${GRAPH}.log"
: > "${LOG_FILE}"
MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
: > "${MEM_LOG_FILE}"

exec > >(tee -a "${LOG_FILE}") 2>&1

############################################################
# Server definitions
############################################################
SERVERS=(
  "host=ab06 id=0 ip=10.58.60.6 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
  "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
)

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

REMOTE_CMD_BASE="python3 base/auth-many-server/split_remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only"

############################################################
# Helpers
############################################################
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" \
      "pkill -f base/auth-many-server/split_remote_server.py || true" \
      >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

wait_server_ready() {
  local ep="$1"
  local timeout_s="$2"
  local t0=$(date +%s)
  local ok_streak=0

  echo "[WAIT] ${ep}: initial grace ${INITIAL_GRACE}s..."
  sleep "${INITIAL_GRACE}"

  while true; do
    if curl -fsS "http://${ep}/health" >/dev/null 2>&1; then
      ok_streak=$((ok_streak + 1))
      echo "[WAIT] ${ep}: health ok (${ok_streak}/${HEALTH_OK_STREAK})"
      if (( ok_streak >= HEALTH_OK_STREAK )); then
        echo "[READY] ${ep}"
        return 0
      fi
    else
      ok_streak=0
    fi

    local now=$(date +%s)
    if (( now - t0 >= timeout_s )); then
      echo "[ERROR] ${ep}: NOT READY (timeout ${timeout_s}s)"
      return 1
    fi
    sleep "${HEALTH_INTERVAL}"
  done
}

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5
  local ep="${ip}:${port}"

  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}
: > split_remote_server.log

${REMOTE_CMD_BASE} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  > split_remote_server.log 2>&1 &

echo "[REMOTE] started pid=\$! (log=split_remote_server.log)"
EOF

  if ! wait_server_ready "${ep}" "${TIMEOUT}"; then
    echo "[ERROR] ${host}: 起動失敗の可能性。ログ末尾を表示します。"
    ssh "$host" "tail -n 120 split_remote_server.log || true" || true
    return 1
  fi

  echo "[INFO] ${host}: 起動ログ（末尾20行）"
  ssh "$host" "tail -n 20 split_remote_server.log || true" || true
}

############################################################
# 1) Start remote servers (parallel) and wait
############################################################
pids=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" "$nts" &
  pids+=($!)
done

ok=1
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then ok=0; fi
done

if [[ "$ok" -ne 1 ]]; then
  echo "[FATAL] サーバ起動が揃わなかったため controller を実行しません。"
  exit 1
fi

echo "[INFO] 全サーバ起動確認OK。controller を開始します。"

############################################################
# 2) Run controller
############################################################
for start_node in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] ${start_node} ==="
  python3 base/auth-many-server/split_controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
    --start-node "${start_node}" \
    --walks ${RW_WALKS} \
    --alpha ${ALPHA} \
    --seed 42
done

############################################################
# Controller duration の合計（ログから集計）
############################################################

controller_total=$(
  awk '
    /Total walk time \(sum over all servers\):/ {
      sum += $(NF-1)
    }
    END {
      printf "%.6f", sum
    }
  ' "${LOG_FILE}"
)

echo "[TOTAL] controller_duration_sum=${controller_total}s"

############################################################
# Authorization time の合計（ログから集計）
############################################################

auth_total=$(
  awk '
    /Total authorization time \(sum over all servers\):/ {
      sum += $(NF-1)
    }
    END {
      printf "%.6f", sum
    }
  ' "${LOG_FILE}"
)

echo "[TOTAL] authorization_time_sum=${auth_total}s"

echo "[DONE]"
