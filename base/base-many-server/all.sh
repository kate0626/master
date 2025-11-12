#!/bin/zsh
set -euo pipefail

############################################################
#  実験設定パート（全変数をここに集約）
############################################################

## --- 実行モードと回数 ---
MODE=${1:-node}                     # node / edge
RUN_COUNT=${2:-5}                   # 各パラメータ組み合わせあたりの繰り返し回数
TIMEOUT=10                          # サーバ起動確認の最大待機秒数
SEED_BASE=42                        # 乱数シード基準値

## --- ファイル設定 ---
EDGE_FILE="dataset/Louvain/graph/karate.gr"
REPO_DIR="./"
RUNS_DIR="runs"
LOG_PREFIX="base_many_server"

## --- サーバ設定 ---
SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

## --- コントローラパラメータ ---
WALKS_LIST=(10 50)
ALPHA_LIST=(0.3)
START_NODE=1

## --- スクリプト名切り替え ---
case "${MODE}" in
  node) REMOTE_SERVER_SCRIPT="remote_server.py" ;;
  edge) REMOTE_SERVER_SCRIPT="remote_server_edge.py" ;;
  *)
    echo "Usage: $0 [node|edge] [runs]"
    exit 1
    ;;
esac
echo ">>> Mode=${MODE}, Server Script=${REMOTE_SERVER_SCRIPT}, RUN_COUNT=${RUN_COUNT}"

############################################################
#  準備・共通変数展開
############################################################

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
TARGET_LOG="^\\[Server"

REMOTE_CMD_BASE="python3 base/base-many-server/${REMOTE_SERVER_SCRIPT} \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR}"

############################################################
#  関数定義パート
############################################################

# --- クリーンアップ ---
cleanup() {
  echo ">>> [CLEANUP] 全サーバで ${REMOTE_SERVER_SCRIPT} を停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/base-many-server/${REMOTE_SERVER_SCRIPT} || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# --- サーバ起動 ---
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash -c "'
set -euo pipefail
cd ${REPO_DIR}
${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
PID=\$!
for ((i=0; i<${TIMEOUT}*2; i++)); do
  if grep -q \"${TARGET_LOG}\" remote_server.log; then
    echo \"[INFO] ${host}: ログ検出OK\"
    exit 0
  fi
  sleep 0.5
done
echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
exit 1
'"
}

# --- 平均計算 ---
calc_average() {
  if (( $# == 0 )); then echo "NaN"; return 0; fi
  python3 - "$@" <<'PY'
import sys
vals=[float(v) for v in sys.argv[1:] if v.strip()]
print(f"{(sum(vals)/len(vals)):.6f}" if vals else "NaN")
PY
}

############################################################
#  実験実行パート
############################################################

# --- サーバ起動 ---
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" &
done
wait
echo "=== 全サーバ起動完了 ==="

mkdir -p "${RUNS_DIR}"

# --- パラメータスイープ ---
for walks in "${WALKS_LIST[@]}"; do
  for alpha in "${ALPHA_LIST[@]}"; do
    echo ""
    echo "=== [PARAM SET] walks=${walks}, alpha=${alpha} ==="
    LOG_FILE="${RUNS_DIR}/${LOG_PREFIX}_walks${walks}_alpha${alpha}.log"
    echo "=== RUN START: walks=${walks}, alpha=${alpha} ===" > "${LOG_FILE}"

    durations=()
    avg_lengths=()
    total_steps_list=()
    successful_runs=0

    for ((run=1; run<=RUN_COUNT; run++)); do
      echo ">>> [RUN ${run}/${RUN_COUNT}] start: $(date '+%Y-%m-%d %H:%M:%S')" >> "${LOG_FILE}"

      CONTROLLER_CMD=(
        python3 base/base-many-server/base.py
        --servers "${SERVER_COUNT}"
        --server-endpoints "${SERVER_ENDPOINTS[@]}"
        --walks "${walks}"
        --alpha "${alpha}"
        --start-node "${START_NODE}"
        --seed $((SEED_BASE + run))
      )

      run_output="$("${CONTROLLER_CMD[@]}" 2>&1)"
      echo "${run_output}" >> "${LOG_FILE}"

      parsed_line=$(echo "${run_output}" | grep -Eo '\[Controller\] Received [0-9]+ walks in [0-9.]+s\. Avg length: [0-9.]+, total steps: [0-9]+' | tail -n1 || true)
      if [[ -n "${parsed_line}" ]]; then
        duration=$(echo "${parsed_line}" | sed -E 's/.* in ([0-9.]+)s.*/\1/')
        avg_len=$(echo "${parsed_line}" | sed -E 's/.*Avg length: ([0-9.]+).*/\1/')
        total_steps=$(echo "${parsed_line}" | sed -E 's/.*total steps: ([0-9]+).*/\1/')
        durations+=("${duration}")
        avg_lengths+=("${avg_len}")
        total_steps_list+=("${total_steps}")
        ((successful_runs++))
        echo ">>> [RUN ${run}] OK: dur=${duration}s, len=${avg_len}, steps=${total_steps}" >> "${LOG_FILE}"
      else
        echo ">>> [RUN ${run}] controller 出力解析失敗" >> "${LOG_FILE}"
      fi
      echo "" >> "${LOG_FILE}"
    done

    {
      echo "=== 結果集計 (walks=${walks}, alpha=${alpha}) ==="
      if (( successful_runs > 0 )); then
        avg_duration=$(calc_average "${durations[@]}")
        avg_walk_length=$(calc_average "${avg_lengths[@]}")
        avg_total_steps=$(calc_average "${total_steps_list[@]}")
        echo ">>> 平均値 (${successful_runs}/${RUN_COUNT})"
        echo "    - duration: ${avg_duration}s"
        echo "    - avg_length: ${avg_walk_length}"
        echo "    - total_steps: ${avg_total_steps}"
      else
        echo ">>> 実行成功なし"
      fi
      echo "=== RUN END: walks=${walks}, alpha=${alpha} ==="
    } >> "${LOG_FILE}"

    echo "✅ 完了: walks=${walks}, alpha=${alpha} (ログ: ${LOG_FILE})"
  done
done

echo "=== 全実験終了。ログは ${RUNS_DIR}/ に保存されました ==="
