#!/bin/zsh
set -euo pipefail

############################################################
#  auth-cache-strategies: bfs-lru / degree-lru 実験スクリプト
#  baseディレクトリから実行すること
############################################################

# --- スクリプト自身の絶対パスを取得 ---
if [ -n "${ZSH_VERSION:-}" ]; then
  _SELF="${(%):-%x}"
elif [ -n "${BASH_SOURCE:-}" ]; then
  _SELF="${BASH_SOURCE[0]}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF

# ====== 設定 ======
GRAPH=vldb
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
RW_WALKS=1000
ALPHA=0.01
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)

CACHE_CAPACITY=100
BFS_FAR_THRESHOLD_LIST=(6)   # bfs-lru/hybrid-lru: sweep する far 閾値
BFS_PREFETCH_DEPTH_LIST=(2)  # bfs-lru/hybrid-lru: sweep するプリフェッチ深さ
HUB_THRESHOLD=10                 # degree-lru: ハブ判定の次数閾値

# 比較対象ポリシーをまとめて実行する
# CACHE_POLICIES=("bfs-lru")
CACHE_POLICIES=("hybrid-lru" "bfs-lru" "degree-lru" "lru" "none")


HEALTH_RETRY=60
HEALTH_STABLE=2
HEALTH_INTERVAL=1

# ====== サーバ定義 ======
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
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=30 "$host" \
      "pkill -f base/auth-cache-strategies/server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ====== リモート起動 ======
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5 remote_cmd_base=$6
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}
: > base/auth-cache-bfs-degree/server.log

(${remote_cmd_base} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-cache-bfs-degree/server.log 2>&1) &

PID=\$!
echo "[REMOTE] started pid=\${PID}"
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

# ====== メモリスナップショット ======
snapshot_memory() {
  local label="$1"
  local mem_log_file="$2"
  {
    echo "=== [MEMORY SNAPSHOT] ${label}  $(date '+%Y-%m-%d %H:%M:%S')"
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      echo "--- endpoint=${ep} ---"
      curl -s "http://${ep}/access_stats" | python3 -c '
import json, sys
d = json.load(sys.stdin)
m = d.get("memory")
cs = d.get("cache_stats", {})
print(json.dumps({"memory": m, "cache_stats": cs}, ensure_ascii=False, indent=2))
'
    done
  } >> "${mem_log_file}" 2>&1
}

# ====== 1ポリシーぶんを実行する関数 ======
# 引数: CACHE_POLICY [FAR_THRESHOLD PREFETCH_DEPTH]
#   BFS系ポリシー(bfs-lru/hybrid-lru)は$2,$3を使う。他は省略可。
run_one_policy() {
  local CACHE_POLICY="$1"
  local FAR_THRESHOLD="${2:-}"
  local PREFETCH_DEPTH="${3:-}"

  # BFS パラメータをディレクトリ名に含めて結果を区別する
  local PARAM_TAG=""
  if [[ -n "${FAR_THRESHOLD}" && -n "${PREFETCH_DEPTH}" ]]; then
    PARAM_TAG="_far${FAR_THRESHOLD}_depth${PREFETCH_DEPTH}"
  fi

  local LOG_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks${RW_WALKS}_capa${CACHE_CAPACITY}/${GRAPH}/${CACHE_POLICY}${PARAM_TAG}_${CACHE_CAPACITY}"
  mkdir -p "${LOG_DIR}"
  local LOG_FILE="${LOG_DIR}/${GRAPH}.log"
  : > "${LOG_FILE}"
  local MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
  : > "${MEM_LOG_FILE}"

  {
    echo "############################################################"
    echo "## [POLICY] ${CACHE_POLICY}${PARAM_TAG}  capacity=${CACHE_CAPACITY}"
    echo "## [TIME ] $(date '+%Y-%m-%d %H:%M:%S')"
    echo "############################################################"

    # ポリシー固有のオプションを組み立て
    local POLICY_OPTS=""
    case "${CACHE_POLICY}" in
      bfs-lru)
        POLICY_OPTS="--cache-bfs-far-threshold ${FAR_THRESHOLD} --cache-bfs-prefetch-depth ${PREFETCH_DEPTH}"
        ;;
      degree-lru)
        POLICY_OPTS="--cache-hub-threshold ${HUB_THRESHOLD}"
        ;;
      hybrid-lru)
        POLICY_OPTS="--cache-bfs-far-threshold ${FAR_THRESHOLD} --cache-bfs-prefetch-depth ${PREFETCH_DEPTH} --cache-hub-threshold ${HUB_THRESHOLD}"
        ;;
    esac

    local REMOTE_CMD_BASE="python3 base/auth-cache-bfs-degree/server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 120 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY} \
  ${POLICY_OPTS}"

    # 前回の残骸を停止
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" \
        "pkill -f base/auth-cache-bfs-degree/server.py || true" >/dev/null 2>&1 || true
    done
    sleep 2

    # 起動（並列）
    local pids=()
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      start_remote_server "$host" "$id" "$ip" "$port" "$nts" "$REMOTE_CMD_BASE" &
      pids+=($!)
    done
    for pid in "${pids[@]}"; do
      wait "$pid"
    done

    # /health 安定待ち
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      wait_health "$ep"
    done

    echo "[INFO] 全サーバOK。controller を開始します。 (policy=${CACHE_POLICY})"

    # controller 実行
    for start_node in "${START_NODES_LIST[@]}"; do
      echo "=== [START_NODE] ${start_node} (policy=${CACHE_POLICY}) ==="
      python3 base/auth-cache-bfs-degree/controller.py \
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

    echo "[DONE policy=${CACHE_POLICY}]"

    snapshot_memory "after_run [${CACHE_POLICY}]" "${MEM_LOG_FILE}"

    # このポリシーのサーバを停止
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" \
        "pkill -f base/auth-cache-bfs-degree/server.py || true" >/dev/null 2>&1 || true
    done
    sleep 3

  } 2>&1 | tee -a "${LOG_FILE}"
}

# ====== 実行 + サマリ書き込みをまとめたヘルパー ======
# 引数: policy [far depth]
run_and_summarize() {
  local policy="$1"
  local far="${2:-}"
  local depth="${3:-}"

  local param_tag=""
  if [[ -n "$far" && -n "$depth" ]]; then
    param_tag="_far${far}_depth${depth}"
  fi
  local label="${policy}${param_tag}"

  echo ""
  echo "==> RUNNING policy=${label}"

  if ! run_one_policy "$policy" "$far" "$depth"; then
    echo "[WARN] policy=${label} 失敗。次へ。" | tee -a "${SUMMARY_FILE}"
    return
  fi

  local local_log="${RESULTS_BASE}/${policy}${param_tag}_${CACHE_CAPACITY}/${GRAPH}.log"
  if [[ -f "${local_log}" ]]; then
    local ctrl auth
    ctrl=$(awk '/Total walk time \(sum over all servers\):/ { sum += $(NF-1) } END { printf "%.6f", sum }' "${local_log}")
    auth=$(awk '/Total authorization time \(sum over all servers\):/ { sum += $(NF-1) } END { printf "%.6f", sum }' "${local_log}")
    echo "[SUMMARY] policy=${label} controller_duration_sum=${ctrl}s authorization_time_sum=${auth}s" \
      | tee -a "${SUMMARY_FILE}"
  fi
}

# ====== 全ポリシーを順番に実行 ======
RESULTS_BASE="${SCRIPT_DIR}/results/${GRAPH}/alpha${ALPHA}_walks${RW_WALKS}_capa${CACHE_CAPACITY}_FAR_THRESHOLD_${BFS_FAR_THRESHOLD_LIST[*]}_DEPTH_${BFS_PREFETCH_DEPTH_LIST[*]}"
mkdir -p "${RESULTS_BASE}"
SUMMARY_FILE="${RESULTS_BASE}/all_policies_summary.log"
: > "${SUMMARY_FILE}"

echo "## [RUN ALL] graph=${GRAPH} policies=${CACHE_POLICIES[*]} alpha=${ALPHA}"
echo "## [BFS sweep] far=${BFS_FAR_THRESHOLD_LIST[*]}  depth=${BFS_PREFETCH_DEPTH_LIST[*]}"
echo "## [START] $(date '+%Y-%m-%d %H:%M:%S')"

for policy in "${CACHE_POLICIES[@]}"; do
  case "${policy}" in
    bfs-lru|hybrid-lru)
      # BFS パラメータを全組み合わせで sweep
      for far in "${BFS_FAR_THRESHOLD_LIST[@]}"; do
        for depth in "${BFS_PREFETCH_DEPTH_LIST[@]}"; do
          run_and_summarize "${policy}" "${far}" "${depth}"
        done
      done
      ;;
    *)
      # none / lru / degree-lru などは1回だけ実行
      run_and_summarize "${policy}"
      ;;
  esac
done

echo ""
echo "## [ALL DONE] $(date '+%Y-%m-%d %H:%M:%S')"
cat "${SUMMARY_FILE}" || true
