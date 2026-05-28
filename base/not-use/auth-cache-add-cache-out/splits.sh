#!/bin/zsh
set -euo pipefail

############################################################
#  auth-cache-evict: キャッシュポリシーをループで実験する版
#  実行順: none → memo → lru → arc
#  各ポリシーごとにサーバを再起動し、全 start_node を走らせる
############################################################

# ====== 設定 ======
TIMEOUT=300
HEALTH_RETRY=60
HEALTH_STABLE=2
HEALTH_INTERVAL=1

GRAPH=vldb
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
SCRIPT_DIR="${0:A:h}"
RW_WALKS=100
ALPHA=0.1
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)

# ====== ループするポリシー一覧 ======
CACHE_POLICIES=("lru" "arc")

# lru/arc でループするキャパシティ一覧（node=1, edge=2, start=1 の重みベース）
# none/memo はキャパシティ不要なので 0 の1回のみ実行
CACHE_CAPACITIES=(100 500)

# ====== サーバ定義（全ポリシー共通） ======
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

# ====== 後始末（ポリシー切替時・EXIT時共用） ======
cleanup_servers() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=300 "$host" \
      "pkill -f base/auth-cache-add-cache-out/split_remote_server.py || true" \
      >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup_servers EXIT

# ====== リモート起動（引数: host id ip port nts policy capacity） ======
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5 policy=$6 capacity=$7
  echo "=== [${host}] サーバ起動開始 (ID=${id}, policy=${policy}) ==="

  local remote_cmd="python3 base/auth-cache-add-cache-out/split_remote_server.py \
    --server-count ${SERVER_COUNT} \
    --edges ${EDGE_FILE} \
    --server-endpoints ${SERVER_ENDPOINTS_STR} \
    --owned-hints-only \
    --request-timeout 120 \
    --cache-policy ${policy} \
    --cache-capacity ${capacity}"

  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}

mkdir -p base/auth-cache-add-cache-out
: > base/auth-cache-add-cache-out/split_remote_server.log

(${remote_cmd} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-cache-add-cache-out/split_remote_server.log 2>&1) &

PID=\$!
echo "[REMOTE] started pid=\${PID} (log=base/auth-cache-add-cache-out/split_remote_server.log)"
echo "[WAIT] ${ip}:${port}: initial grace 5s..."
sleep 5
EOF
}

# ====== /health 待ち ======
wait_health() {
  local ep="$1"
  local ok=0
  local i=0

  while (( i < HEALTH_RETRY )); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_STABLE})"
      if (( ok >= HEALTH_STABLE )); then
        echo "[READY] ${ep}"
        return 0
      fi
    else
      ok=0
    fi
    sleep "${HEALTH_INTERVAL}"
    i=$((i+1))
  done

  echo "[ERROR] ${ep}: health check timeout"
  return 1
}

# ====== メモリスナップショット（引数: label mem_log_file） ======
snapshot_memory() {
  local label="$1"
  local mem_log="$2"
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
  } >> "${mem_log}" 2>&1
}

# ===========================================================
# ====== ポリシー × キャパシティ ループ ======
# ===========================================================
for CACHE_POLICY in "${CACHE_POLICIES[@]}"; do

  # none/memo はキャパシティ無関係 → 0 の1回だけ
  if [[ "${CACHE_POLICY}" == "none" || "${CACHE_POLICY}" == "memo" ]]; then
    loop_capacities=(0)
  else
    loop_capacities=("${CACHE_CAPACITIES[@]}")
  fi

for CACHE_CAPACITY in "${loop_capacities[@]}"; do

  LOG_DIR="${SCRIPT_DIR}/results/${GRAPH}/${CACHE_POLICY}_${CACHE_CAPACITY}"
  mkdir -p "${LOG_DIR}"
  LOG_FILE="${LOG_DIR}/${GRAPH}.log"
  : > "${LOG_FILE}"
  MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
  : > "${MEM_LOG_FILE}"

  # このポリシーの全出力をログファイルにも流す
  exec > >(tee -a "${LOG_FILE}") 2>&1

  echo ""
  echo "########################################################"
  echo "# POLICY=${CACHE_POLICY}  CAPACITY=${CACHE_CAPACITY}"
  echo "# $(date '+%Y-%m-%d %H:%M:%S')"
  echo "########################################################"

  # ====== サーバ起動（並列） ======
  pids=()
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    start_remote_server "$host" "$id" "$ip" "$port" "$nts" \
      "${CACHE_POLICY}" "${CACHE_CAPACITY}" &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do
    wait "$pid"
  done

  # ====== health 待ち ======
  for ep in "${SERVER_ENDPOINTS[@]}"; do
    wait_health "$ep"
  done

  # 起動ログ確認
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    echo "[INFO] ${host}: 起動ログ（末尾20行）"
    ssh "$host" "cd ${REPO_DIR} && tail -n 20 base/auth-cache-add-cache-out/split_remote_server.log || true"

  done

  echo "[INFO] 全サーバ起動確認OK。controller を開始します。"

  # ====== controller 実行（start_node ループ） ======
  for start_node in "${START_NODES_LIST[@]}"; do
    echo "=== [START_NODE] ${start_node} ==="
    python3 base/auth-cache-add-cache-out/split_controller.py \
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

  echo "[DONE] policy=${CACHE_POLICY}"

  # ====== 集計 ======
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

  snapshot_memory "1/3: after totals (immediate)" "${MEM_LOG_FILE}"
  sleep 3
  snapshot_memory "2/3: after totals (+3s)" "${MEM_LOG_FILE}"
  sleep 3
  snapshot_memory "3/3: after totals (+6s)" "${MEM_LOG_FILE}"

  echo "[MEMORY] saved: ${MEM_LOG_FILE}"

  # ====== 次のイテレーションに備えてサーバ停止 ======
  cleanup_servers

done  # CACHE_CAPACITY loop
done  # CACHE_POLICY loop

echo ""
echo "====== 全ポリシー × 全キャパシティ 完了 ======"
