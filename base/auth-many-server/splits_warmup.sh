#!/bin/zsh
# splits_warmup.sh
#
# alpha=0.1, amazon0601 / vldb で 2段階 warmup キャッシュ実験を行うスクリプト。
#
# 実験条件:
#   baseline:      K=0,  C=0  (warmup なし、キャッシュ無効)
#   warmup sweep:  K∈{30,50,70}  ×  C∈{50,100,200}
#
# 実行例:
#   zsh base/auth-many-server/splits_warmup.sh
#   GRAPH_OVERRIDE=vldb zsh base/auth-many-server/splits_warmup.sh
#
set -euo pipefail

# ══════════════════════════════════════════
# 設定
# ══════════════════════════════════════════
GRAPH=${GRAPH_OVERRIDE:-amazon0601}   # amazon0601 / vldb
ALPHA=0.1
RW_WALKS=100
NG_RATE="0.3"
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"

case "${GRAPH}" in
  amazon0601) START_NODES_LIST=(0 2 3 5 6) ;;
  vldb)       START_NODES_LIST=(0 1 2 10 11) ;;
  *)          START_NODES_LIST=(0 1 2 3 4) ;;
esac

LOG_DIR="runs/auth/warmup-cache/${GRAPH}"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/experiment.log"
: > "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

# ══════════════════════════════════════════
# サーバ定義
# ══════════════════════════════════════════
TIMEOUT=90
INITIAL_GRACE=5
HEALTH_OK_STREAK=2
HEALTH_INTERVAL=0.5

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

# ══════════════════════════════════════════
# ヘルパー関数
# ══════════════════════════════════════════
cleanup() {
  echo ">>> [CLEANUP] サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" \
      "pkill -f base/auth-many-server/split_remote_server.py || true" \
      >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

wait_health() {
  local ep="$1" timeout_s="$2"
  local t0=$(date +%s) ok=0
  echo "[WAIT] ${ep}: initial grace ${INITIAL_GRACE}s..."
  sleep "${INITIAL_GRACE}"
  while true; do
    if curl -fsS "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_OK_STREAK})"
      (( ok >= HEALTH_OK_STREAK )) && { echo "[READY] ${ep}"; return 0; }
    else
      ok=0
    fi
    local now=$(date +%s)
    (( now - t0 >= timeout_s )) && { echo "[ERROR] ${ep}: health check timeout"; return 1; }
    sleep "${HEALTH_INTERVAL}"
  done
}

start_servers() {
  local cap=$1
  echo ">>> [START SERVERS] cache_capacity=${cap} graph=${GRAPH}"
  local pids=()
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh "$host" bash <<EOF &
set -euo pipefail
cd ${REPO_DIR}
pkill -f base/auth-many-server/split_remote_server.py 2>/dev/null || true
sleep 1
: > split_remote_server_warmup.log
python3 base/auth-many-server/split_remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS[*]} \
  --owned-hints-only \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  --cache-capacity ${cap} \
  >> split_remote_server_warmup.log 2>&1 &
echo "[REMOTE] pid=\$! started"
EOF
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done

  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    wait_health "${ip}:${port}" "${TIMEOUT}" || return 1
  done
  echo "[INFO] 全サーバ READY (cache_capacity=${cap})"
}

stop_servers() {
  echo ">>> [STOP SERVERS]"
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=10 "$host" \
      "pkill -f base/auth-many-server/split_remote_server.py || true" \
      >/dev/null 2>&1 || true
  done
  sleep 2
}

run_condition() {
  local K=$1 C=$2
  local out_base="${LOG_DIR}/K=${K}_C=${C}"
  mkdir -p "${out_base}"
  echo ""
  echo "---------- K=${K}  C=${C}  GRAPH=${GRAPH}  $(date '+%H:%M:%S') ----------"
  for start_node in "${START_NODES_LIST[@]}"; do
    echo "[START_NODE=${start_node}]"
    python3 base/auth-many-server/split_controller.py \
      --servers 2 \
      --server-endpoints "${SERVER_ENDPOINTS[@]}" \
      --start-node "${start_node}" \
      --walks ${RW_WALKS} \
      --alpha ${ALPHA} \
      --seed 42 \
      --warmup-walks ${K} \
      --cache-capacity ${C} \
      --out-dir "${out_base}" \
      --out-prefix "start=${start_node}_walks=${RW_WALKS}_alpha=${ALPHA}_K=${K}_C=${C}"
  done
  echo "---------- K=${K} C=${C} done $(date '+%H:%M:%S') ----------"
}

# ══════════════════════════════════════════
# メイン実験ループ
# ══════════════════════════════════════════
echo "############################################################"
echo "## warmup-cache sweep: GRAPH=${GRAPH}  $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

# 1) baseline (キャッシュ無効)
start_servers 0
run_condition 0 0
stop_servers

# 2) warmup sweep: C ごとにサーバ再起動、K をスイープ
for C in 50 100 200; do
  start_servers ${C}
  for K in 30 50 70; do
    run_condition ${K} ${C}
  done
  stop_servers
done

echo ""
echo "############################################################"
echo "## [ALL DONE] GRAPH=${GRAPH}  $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 結果ディレクトリ: ${LOG_DIR}"
echo "##"
echo "## 次のステップ（結果の集計・比較）:"
echo "##   python3 base/auth-many-server/analyze_warmup_results.py \\"
echo "##       --input ${LOG_DIR} \\"
echo "##       --out-dir ${LOG_DIR}/summary"
echo "############################################################"
