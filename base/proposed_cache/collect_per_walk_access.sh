#!/bin/zsh
# collect_per_walk_access.sh
#
# cache=none で walk を実行し、per_walk_access.json を収集するスクリプト。
# analyze_capacity_locality.py の入力データを作るためのもの。
#
# 実行例（baseディレクトリから）:
#   zsh base/proposed_cache/collect_per_walk_access.sh
#   GRAPH_OVERRIDE=vldb zsh base/proposed_cache/collect_per_walk_access.sh
#
set -euo pipefail

# ====== 設定 ======
GRAPH=${GRAPH_OVERRIDE:-vldb}        # 対象グラフ: vldb / amazon0601 / karate
ALPHA=${ALPHA_OVERRIDE:-0.01}
RW_WALKS=100
START_NODES_LIST=(0 1 2 3 4)
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"

# スクリプト自身の絶対パスを取得
if [ -n "${ZSH_VERSION:-}" ]; then
  _SELF="${(%):-%x}"
elif [ -n "${BASH_SOURCE:-}" ]; then
  _SELF="${BASH_SOURCE[0]}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF

# 出力先: results/access_locality/{GRAPH}/
OUT_DIR="${SCRIPT_DIR}/results/access_locality/${GRAPH}"
mkdir -p "${OUT_DIR}"

LOG_FILE="${OUT_DIR}/collect.log"
: > "${LOG_FILE}"

TIMEOUT=60
HEALTH_RETRY=60
HEALTH_STABLE=2
HEALTH_INTERVAL=1

# ====== サーバ定義 ======
NG_RATE="0.3"
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

# ====== 後始末 ======
cleanup() {
  echo ">>> [CLEANUP] サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=30 "$host" \
      "pkill -f base/proposed_cache/split_remote_server_proposed.py || true" \
      >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ====== /health 待ち ======
wait_health() {
  local ep="$1"
  local ok=0
  local i=0
  while (( i < HEALTH_RETRY )); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_STABLE})"
      (( ok >= HEALTH_STABLE )) && { echo "[READY] ${ep}"; return 0; }
    else
      ok=0
    fi
    sleep "${HEALTH_INTERVAL}"
    i=$((i+1))
  done
  echo "[ERROR] ${ep}: health check timeout"
  return 1
}

# ====== リモートサーバ起動 ======
REMOTE_CMD_BASE="python3 base/proposed_cache/split_remote_server_proposed.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 30000 \
  --cache-policy none \
  --cache-capacity 0"

echo ">>> [PRE-CLEANUP] 既存プロセスを停止..."
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  ssh -o ConnectTimeout=30 "$host" \
    "pkill -f base/proposed_cache/split_remote_server_proposed.py || true" \
    >/dev/null 2>&1 || true
done
sleep 2

echo ">>> [START] リモートサーバ起動（並列）..."
pids=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  ssh "$host" bash <<EOF &
set -euo pipefail
cd ${REPO_DIR}
: > base/auth-baseline-cache/split_remote_server.log
(${REMOTE_CMD_BASE} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-baseline-cache/split_remote_server.log 2>&1) &
echo "[REMOTE] pid=\$! started"
sleep 5
EOF
  pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid"; done

echo ">>> [WAIT] /health 確認..."
for ep in "${SERVER_ENDPOINTS[@]}"; do
  wait_health "$ep"
done

echo ">>> [INFO] 全サーバ READY。controller 開始 (graph=${GRAPH} alpha=${ALPHA} walks=${RW_WALKS})"

# ====== 各 start_node で controller 実行 ======
{
  for start_node in "${START_NODES_LIST[@]}"; do
    echo ""
    echo "=== [START_NODE] ${start_node} ==="
    python3 base/proposed_cache/split_controller_proposed.py \
      --servers 2 \
      --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
      --start-node "${start_node}" \
      --walks "${RW_WALKS}" \
      --alpha "${ALPHA}" \
      --seed 42 \
      --request-timeout 30000 \
      --cache-policy none \
      --cache-capacity 0 \
      --prefetch-mode none \
      --out-dir "${OUT_DIR}" \
      --out-prefix "start=${start_node}_walks=${RW_WALKS}_alpha=${ALPHA}_seed=42_cache=none_cap=0"
    echo "=== [DONE start_node=${start_node}] ==="
  done
} 2>&1 | tee -a "${LOG_FILE}"

echo ""
echo "############################################################"
echo "## [ALL DONE] graph=${GRAPH}  $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 出力先: ${OUT_DIR}"
echo "## 次のステップ:"
echo "##   python3 base/proposed_cache/analyze_capacity_locality.py \\"
echo "##       --input ${OUT_DIR} \\"
echo "##       --out-dir base/proposed_cache/results/capacity_locality/${GRAPH} \\"
echo "##       --graph-label ${GRAPH} \\"
echo "##       --out-prefix ${GRAPH} \\"
echo "##       --c-list 30 50 100 200 300"
echo "############################################################"
