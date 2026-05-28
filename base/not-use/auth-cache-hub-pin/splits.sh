#!/bin/zsh
set -euo pipefail

############################################################
#  auth-cache-hub-pin: lfu / tinylfu / hub-pin-lru の実験
#
#  使い方 (base/ から実行):
#    bash base/auth-cache-hub-pin/splits.sh
#
#  既存 auth-cache-bfs-degree/splits.sh の構造を踏襲し、
#  ポリシー追加・サーバ側コードを統合する前提のテンプレ。
#  実行前に必要:
#   - リモート servers (ab06/ab11) に hub_pin_cache.py を配置
#   - server.py が --cache-policy lfu|tinylfu|hub-pin-lru を受け付けるように改造
#   - hub-pin-lru を使うときは --pin-keys-file または事前生成
############################################################

# --- スクリプト自身の絶対パス ---
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
RW_WALKS=1000      # baseline 実験から LRU→memo gap が大きい条件
ALPHA=0.1
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)
CACHE_CAPACITY=100

# 比較対象ポリシーをまとめて実行する
# 1. ベースライン (none, lru, memo) は既存の auth-baseline-cache で取れているので
#    ここでは新ポリシーだけを走らせる
CACHE_POLICIES=("lfu" "tinylfu" "hub-pin-lru")

# hub-pin-lru 用の pin set 事前生成
PIN_K=50
PIN_KEYS_FILE="${SCRIPT_DIR}/pin_keys_${GRAPH}_top${PIN_K}.json"
if [ ! -f "${PIN_KEYS_FILE}" ]; then
  echo "[setup] generate top-${PIN_K} degree pin set for ${GRAPH}..."
  python3 - <<PY
import json, os
from pathlib import Path
import sys
sys.path.insert(0, "${SCRIPT_DIR}")
from hub_pin_cache import compute_top_k_by_degree
path = Path("${EDGE_FILE}")
if not path.exists():
    print(f"[warn] edge file not found: {path}")
    print(f"[warn] you must generate ${PIN_KEYS_FILE} manually before running hub-pin-lru.")
    sys.exit(0)
keys = compute_top_k_by_degree(path, ${PIN_K})
out = Path("${PIN_KEYS_FILE}")
out.write_text(json.dumps(keys, indent=2))
print(f"[ok] wrote {out} ({len(keys)} keys)")
PY
fi

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

cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=30 "$host" \
      "pkill -f base/auth-cache-hub-pin/server.py || true" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5 remote_cmd_base=$6
  echo "=== [${host}] サーバ起動 (ID=${id}) ==="
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}
: > base/auth-cache-hub-pin/server.log
(${remote_cmd_base} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-cache-hub-pin/server.log 2>&1) &
PID=\$!
echo "[REMOTE] started pid=\${PID}"
sleep 5
EOF
}

wait_health() {
  local ep="$1" ok=0 i=0
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
  echo "[ERROR] ${ep}: health timeout"
  return 1
}

run_one_policy() {
  local CACHE_POLICY="$1"
  local LOG_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks${RW_WALKS}_capa${CACHE_CAPACITY}/${GRAPH}/${CACHE_POLICY}_${CACHE_CAPACITY}"
  mkdir -p "${LOG_DIR}"
  local LOG_FILE="${LOG_DIR}/${GRAPH}.log"
  : > "${LOG_FILE}"

  {
    echo "############################################################"
    echo "## [POLICY] ${CACHE_POLICY}  capacity=${CACHE_CAPACITY}"
    echo "## [TIME ] $(date '+%Y-%m-%d %H:%M:%S')"
    echo "############################################################"

    local POLICY_OPTS=""
    case "${CACHE_POLICY}" in
      hub-pin-lru)
        POLICY_OPTS="--pin-keys-file ${PIN_KEYS_FILE}"
        ;;
    esac

    # ★ NOTE: server.py 側で from hub_pin_cache import make_cache してあること
    local REMOTE_CMD_BASE="python3 base/auth-cache-hub-pin/server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 120 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY} \
  ${POLICY_OPTS}"

    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" \
        "pkill -f base/auth-cache-hub-pin/server.py || true" >/dev/null 2>&1 || true
    done
    sleep 2

    local pids=()
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      start_remote_server "$host" "$id" "$ip" "$port" "$nts" "$REMOTE_CMD_BASE" &
      pids+=($!)
    done
    for pid in "${pids[@]}"; do wait "$pid"; done

    for ep in "${SERVER_ENDPOINTS[@]}"; do wait_health "$ep"; done

    echo "[INFO] 全サーバOK。controller 開始 (policy=${CACHE_POLICY})"
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

    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" \
        "pkill -f base/auth-cache-hub-pin/server.py || true" >/dev/null 2>&1 || true
    done
    sleep 3
  } 2>&1 | tee -a "${LOG_FILE}"
}

# ===== サマリ書き込み (Length=1 除外、auth-cache-bfs-degree と同じ集計) =====
RESULTS_BASE="${SCRIPT_DIR}/results/alpha${ALPHA}_walks${RW_WALKS}_capa${CACHE_CAPACITY}/${GRAPH}"
mkdir -p "${RESULTS_BASE}"
SUMMARY_FILE="${RESULTS_BASE}/all_policies_summary.log"
: > "${SUMMARY_FILE}"

for policy in "${CACHE_POLICIES[@]}"; do
  echo ""
  echo "==> RUNNING policy=${policy}"
  if ! run_one_policy "${policy}"; then
    echo "[WARN] policy=${policy} 失敗" | tee -a "${SUMMARY_FILE}"
    continue
  fi
  local_log="${RESULTS_BASE}/${policy}_${CACHE_CAPACITY}/${GRAPH}.log"
  if [[ -f "${local_log}" ]]; then
    agg=$(awk '
      BEGIN { sum_walk=0; sum_auth=0; n_valid=0; current_avg=-1 }
      /=== \[START_NODE\]/ { current_avg=-1 }
      /Avg length:/ {
        if (match($0, /Avg length:[[:space:]]+[0-9.]+/)) {
          chunk = substr($0, RSTART, RLENGTH)
          sub(/Avg length:[[:space:]]+/, "", chunk)
          current_avg = chunk + 0
        }
      }
      /Total authorization time \(sum over all servers\):/ { if (current_avg > 1.001) sum_auth += $(NF-1) }
      /Total walk time \(sum over all servers\):/ { if (current_avg > 1.001) { sum_walk += $(NF-1); n_valid++ } }
      END { printf "%.6f %.6f %d", sum_walk, sum_auth, n_valid }
    ' "${local_log}")
    ctrl=${agg%% *}
    auth=$(echo "${agg}" | awk '{print $2}')
    nval=$(echo "${agg}" | awk '{print $3}')
    if [[ "${nval}" -gt 0 ]]; then
      per_w=$(awk -v s="${ctrl}" -v n="${nval}" 'BEGIN{printf "%.6f", s/n}')
      per_a=$(awk -v s="${auth}" -v n="${nval}" 'BEGIN{printf "%.6f", s/n}')
    else per_w="nan"; per_a="nan"; fi
    echo "[SUMMARY] policy=${policy} controller_duration_sum=${ctrl}s authorization_time_sum=${auth}s n_valid=${nval} walk_per_start=${per_w}s auth_per_start=${per_a}s" \
      | tee -a "${SUMMARY_FILE}"
  fi
done

echo ""
echo "## [ALL DONE] $(date '+%Y-%m-%d %H:%M:%S')"
cat "${SUMMARY_FILE}" || true
