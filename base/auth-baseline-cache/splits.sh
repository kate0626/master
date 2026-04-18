#!/bin/zsh
set -euo pipefail

############################################################
#  Remote server が "READY" になってから controller を実行する版
############################################################

# ====== 設定 ======
TIMEOUT=300               # 起動待ち全体の上限（長め）
HEALTH_RETRY=60          # /health の最大試行回数（1回=0.5sなら30秒）
HEALTH_STABLE=2          # 連続OK回数（2回連続OKでREADY扱い）
HEALTH_INTERVAL=1      # 何秒おきに叩くか

GRAPH=karate
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
SCRIPT_DIR="${0:A:h}"
RW_WALKS=100
ALPHA=0.1
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)
CACHE_POLICY="lru"
# CACHE_POLICY="arc"  # 追加: ARC キャッシュを試す場合はこれに変更
CACHE_CAPACITY=500
LOG_DIR="${SCRIPT_DIR}/results/${GRAPH}/${CACHE_POLICY}_${CACHE_CAPACITY}"


mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${GRAPH}.log"
: > "${LOG_FILE}"
MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
: > "${MEM_LOG_FILE}"



exec > >(tee -a "${LOG_FILE}") 2>&1

# ====== サーバ定義 ======
SERVERS=(
  "host=ab06 id=0 ip=10.58.60.6 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
  "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
)

# NG deny を使う時
# SERVERS=(
#   "host=ab06 id=0 ip=10.58.60.6 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/entity_to_denied_starts_server0.json"
#   "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/entity_to_denied_starts_server1.json"
# )

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

REMOTE_CMD_BASE="python3 base/auth-baseline-cache/split_remote_server_volume_base.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 120 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY}"

# ====== メモリスナップショット ======
snapshot_memory() {
  local label="$1"
  {
    echo "============================================================"
    echo "=== [MEMORY SNAPSHOT] ${label}  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      echo "--- endpoint=${ep} ---"
      curl -s "http://${ep}/access_stats" | python3 -c '
import json,sys
d=json.load(sys.stdin)
m=d.get("memory")
if m is None:
    print({"warn":"no memory field in /access_stats", "keys": list(d.keys())})
else:
    print(json.dumps(m, ensure_ascii=False, indent=2))
'
      echo
    done
  } >> "${MEM_LOG_FILE}" 2>&1
}

# ====== 後始末 ======
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=300 "$host" "pkill -f base/auth-baseline-cache/split_remote_server_volume_base.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ====== リモート起動 ======

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

  # リモートで起動して PID を必ず表示する
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}

: > base/auth-baseline-cache/split_remote_server.log

# バックグラウンド起動
(${REMOTE_CMD_BASE} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-baseline-cache/split_remote_server.log 2>&1) &

PID=\$!
echo "[REMOTE] started pid=\${PID} (log=base/auth-baseline-cache/split_remote_server.log)"

# 初期待機（少し長め）
echo "[WAIT] ${ip}:${port}: initial grace 5s..."
sleep 5

EOF
}

# ====== ローカルから /health 待ち ======
wait_health() {
  local ep="$1"
  local ok=0
  local i=0

  while (( i < HEALTH_RETRY )); do
    # /health が取れたらOK（レスポンス内容までは見ない。必要なら jq してもOK）
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_STABLE})"
      if (( ok >= HEALTH_STABLE )); then
        echo "[READY] ${ep}"
        return 0
      fi
    else
      ok=0
      # echo "[WAIT] ${ep}: health not ready yet..."
    fi
    sleep "${HEALTH_INTERVAL}"
    i=$((i+1))
  done

  echo "[ERROR] ${ep}: health check timeout"
  return 1
}

# ====== 起動（並列） ======
pids=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" "$nts" &
  pids+=($!)
done

# 起動コマンド自体の完了待ち
for pid in "${pids[@]}"; do
  wait "$pid"
done

# ====== /health が安定するまで待つ（逐次でも並列でもOK。まず逐次で堅牢に） ======
for ep in "${SERVER_ENDPOINTS[@]}"; do
  wait_health "$ep"
done

# リモートログ末尾を表示（目視確認用）
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  echo "[INFO] ${host}: 起動ログ（末尾20行）"
  ssh "$host" "cd ${REPO_DIR} && tail -n 20 base/auth-baseline-cache/split_remote_server.log || true"
done

echo "[INFO] 全サーバ起動確認OK。controller を開始します。"

# ====== controller 実行 ======
for start_node in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] ${start_node} ==="
  python3 base/auth-baseline-cache/split_controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
    --start-node "${start_node}" \
    --walks ${RW_WALKS} \
    --alpha ${ALPHA} \
    --seed 42 \
    --request-timeout 120 \
    --out-dir "${LOG_DIR}" \
    --cache-policy "${CACHE_POLICY}" \
    --cache-capacity "${CACHE_CAPACITY}"
done

echo "[DONE]"

# ====== 集計（ログから） ======
controller_total=$(
  awk '
    /Total walk time \(sum over all servers\):/ { sum += $(NF-1) }
    END { printf "%.6f", sum }
  ' "${LOG_FILE}"
)
echo "[TOTAL] controller_duration_sum=${controller_total}s"

auth_total=$(
  awk '
    /Total authorization time \(sum over all servers\):/ { sum += $(NF-1) }
    END { printf "%.6f", sum }
  ' "${LOG_FILE}"
)
echo "[TOTAL] authorization_time_sum=${auth_total}s"

snapshot_memory "1/3: after totals (immediate)"
sleep 3
snapshot_memory "2/3: after totals (+3s)"
sleep 3
snapshot_memory "3/3: after totals (+6s)"

echo "[MEMORY] saved: ${MEM_LOG_FILE}"
