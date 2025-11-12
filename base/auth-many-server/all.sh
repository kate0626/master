# #!/bin/zsh
# set -euo pipefail

# ############################################################
# #  実験設定パート
# #  （すべての数字・ファイルパス・条件はここで定義）
# ############################################################

# ## --- 実行回数設定 ---
# RUN_COUNT=${1:-5}                 # 各パラメータ組み合わせにつき何回実行するか（デフォルト5回）
# TIMEOUT=10                        # サーバ起動待機の最大時間 [秒]
# SEED_BASE=42                      # 乱数シードの基準値

# ## --- ファイル設定 ---
# EDGE_FILE="dataset/Louvain/graph/karate.gr"
# AUTH_JSON="base/auth-many-server/auth_by_start.json"
# REPO_DIR="./"
# RUNS_DIR="runs"

# ## --- サーバ設定 ---
# SERVERS=(
#   "host=ab05 id=0 ip=10.58.60.5 port=3000"
#   "host=ab06 id=1 ip=10.58.60.6 port=3000"
# )

# ## --- スイープパラメータ設定 ---
# # ここを変えるだけで一括実験条件が変わる！
# WALKS_LIST=(10 50)
# ALPHA_LIST=(0.3)
# START_NODE=1                      # RWの開始ノード
# DEFAULT_WALKS=10                  # 参考値（controller.pyのデフォルト想定）

# ############################################################
# #  実行前の準備パート
# ############################################################

# SERVER_COUNT=${#SERVERS[@]}
# SERVER_ENDPOINTS=()
# for entry in "${SERVERS[@]}"; do
#   eval "$entry"
#   SERVER_ENDPOINTS+=("${ip}:${port}")
# done
# SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
# TARGET_LOG="^\\[Server"

# REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \
#   --server-count ${SERVER_COUNT} \
#   --edges ${EDGE_FILE} \
#   --server-endpoints ${SERVER_ENDPOINTS_STR} \
#   --auth-file ${AUTH_JSON}"

# ############################################################
# #  関数定義パート
# ############################################################

# # --- クリーンアップ ---
# cleanup() {
#   echo ">>> [CLEANUP] 全サーバ停止中..."
#   for entry in "${SERVERS[@]}"; do
#     eval "$entry"
#     ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
#   done
#   echo ">>> [CLEANUP] 完了。"
# }
# trap cleanup EXIT

# # --- サーバ起動 ---
# start_remote_server() {
#   local host=$1 id=$2 ip=$3 port=$4
#   echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
#   ssh "$host" bash -c "'
# set -euo pipefail
# cd ${REPO_DIR}
# ${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
# PID=\$!
# timeout ${TIMEOUT}s bash -c \"grep -m1 '${TARGET_LOG}' <(tail -f remote_server.log)\" \
#   && echo \"[INFO] ${host}: 起動OK\" || echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
# '"
# }

# # --- 平均計算 ---
# calc_average() {
#   if (( $# == 0 )); then
#     echo "NaN"
#     return 0
#   fi
#   python3 - "$@" <<'PY'
# import sys
# vals = [float(v) for v in sys.argv[1:] if v.strip()]
# print(f"{(sum(vals)/len(vals)):.6f}" if vals else "NaN")
# PY
# }

# ############################################################
# #  実験実行パート
# ############################################################

# # 認可テーブルを再生成
# echo ">>> auth_by_start.json を再生成中..."
# python3 base/auth-many-server/create_json_table.py "${EDGE_FILE}" -o "${AUTH_JSON}" --ng-ratio 0.0

# # サーバ起動
# for entry in "${SERVERS[@]}"; do
#   eval "$entry"
#   start_remote_server "$host" "$id" "$ip" "$port" &
# done
# wait
# echo "=== 全サーバ起動完了 ==="

# mkdir -p "${RUNS_DIR}"

# # --- パラメータスイープ ---
# for walks in "${WALKS_LIST[@]}"; do
#   for alpha in "${ALPHA_LIST[@]}"; do
#     echo ""
#     echo "=== [PARAM SET] walks=${walks}, alpha=${alpha} ==="
#     LOG_FILE="${RUNS_DIR}/result_walks${walks}_alpha${alpha}.log"
#     echo "=== RUN START: walks=${walks}, alpha=${alpha} ===" > "${LOG_FILE}"

#     durations=()
#     avg_lengths=()
#     total_steps_list=()
#     successful_runs=0

#     for ((run=1; run<=RUN_COUNT; run++)); do
#       echo ">>> [RUN ${run}/${RUN_COUNT}] start: $(date '+%Y-%m-%d %H:%M:%S')" >> "${LOG_FILE}"

#       CONTROLLER_CMD=(
#         python3 base/auth-many-server/controller.py
#         --servers "${SERVER_COUNT}"
#         --server-endpoints "${SERVER_ENDPOINTS[@]}"
#         --walks "${walks}"
#         --alpha "${alpha}"
#         --start-node "${START_NODE}"
#         --seed $((SEED_BASE + run))
#       )

#       run_output="$("${CONTROLLER_CMD[@]}" 2>&1)"
#       echo "${run_output}" >> "${LOG_FILE}"

#       parsed_line=$(echo "${run_output}" | grep -Eo '\[Controller\] Received [0-9]+ walks in [0-9.]+s\. Avg length: [0-9.]+, total steps: [0-9]+' | tail -n1 || true)
#       if [[ -n "${parsed_line}" ]]; then
#         duration=$(echo "${parsed_line}" | sed -E 's/.* in ([0-9.]+)s.*/\1/')
#         avg_len=$(echo "${parsed_line}" | sed -E 's/.*Avg length: ([0-9.]+).*/\1/')
#         total_steps=$(echo "${parsed_line}" | sed -E 's/.*total steps: ([0-9]+).*/\1/')

#         durations+=("${duration}")
#         avg_lengths+=("${avg_len}")
#         total_steps_list+=("${total_steps}")
#         ((successful_runs++))
#         echo ">>> [RUN ${run}] OK: duration=${duration}s, avg_len=${avg_len}, steps=${total_steps}" >> "${LOG_FILE}"
#       else
#         echo ">>> [RUN ${run}] controller.py の結果を解析できませんでした" >> "${LOG_FILE}"
#       fi
#       echo "" >> "${LOG_FILE}"
#     done

#     # --- 平均出力 ---
#     {
#       echo "=== 結果集計 (walks=${walks}, alpha=${alpha}) ==="
#       if (( successful_runs > 0 )); then
#         avg_duration=$(calc_average "${durations[@]}")
#         avg_walk_length=$(calc_average "${avg_lengths[@]}")
#         avg_total_steps=$(calc_average "${total_steps_list[@]}")
#         echo ">>> 平均値 (${successful_runs}/${RUN_COUNT})"
#         echo "    - duration: ${avg_duration}s"
#         echo "    - avg_length: ${avg_walk_length}"
#         echo "    - total_steps: ${avg_total_steps}"
#       else
#         echo ">>> 実行成功なし"
#       fi
#       echo "=== RUN END: walks=${walks}, alpha=${alpha} ==="
#     } >> "${LOG_FILE}"

#     echo "✅ 完了: walks=${walks}, alpha=${alpha} (ログ: ${LOG_FILE})"
#   done
# done


#!/bin/zsh
set -euo pipefail

############################################################
#  実験設定パート
#  （すべての数字・ファイルパス・条件はここで定義）
############################################################

## --- 実行回数設定 ---
RUN_COUNT=${1:-5}                 # 各パラメータ組み合わせにつき何回実行するか（デフォルト5回）
TIMEOUT=10                        # サーバ起動待機の最大時間 [秒]
SEED_BASE=42                      # 乱数シードの基準値

## --- ファイル設定 ---
EDGE_FILE="dataset/Louvain/graph/karate.gr"
AUTH_JSON="base/auth-many-server/auth_by_start.json"
REPO_DIR="./"
RUNS_DIR="runs"

## --- サーバ設定 ---
SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

## --- スイープパラメータ設定 ---
# ここを変えるだけで一括実験条件が変わる！
WALKS_LIST=(10 50)
ALPHA_LIST=(0.3)
START_NODE=1                      # RWの開始ノード
DEFAULT_WALKS=10                  # 参考値（controller.pyのデフォルト想定）

############################################################
#  実行前の準備パート
############################################################

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
TARGET_LOG="^\\[Server"

REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --auth-file ${AUTH_JSON}"

############################################################
#  関数定義パート
############################################################

# --- クリーンアップ ---
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
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
timeout ${TIMEOUT}s bash -c \"grep -m1 '${TARGET_LOG}' <(tail -f remote_server.log)\" \
  && echo \"[INFO] ${host}: 起動OK\" || echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
'"
}

# --- 平均計算 ---
calc_average() {
  if (( $# == 0 )); then
    echo "NaN"
    return 0
  fi
  python3 - "$@" <<'PY'
import sys
vals = [float(v) for v in sys.argv[1:] if v.strip()]
print(f"{(sum(vals)/len(vals)):.6f}" if vals else "NaN")
PY
}

############################################################
#  実験実行パート
############################################################

# 認可テーブルを再生成
echo ">>> auth_by_start.json を再生成中..."
python3 base/auth-many-server/create_json_table.py "${EDGE_FILE}" -o "${AUTH_JSON}" --ng-ratio 0.0

# サーバ起動
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
    LOG_FILE="${RUNS_DIR}/result_walks${walks}_alpha${alpha}.log"
    echo "=== RUN START: walks=${walks}, alpha=${alpha} ===" > "${LOG_FILE}"

    durations=()
    avg_lengths=()
    total_steps_list=()
    successful_runs=0

    for ((run=1; run<=RUN_COUNT; run++)); do
      echo ">>> [RUN ${run}/${RUN_COUNT}] start: $(date '+%Y-%m-%d %H:%M:%S')" >> "${LOG_FILE}"

      CONTROLLER_CMD=(
        python3 base/auth-many-server/controller.py
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
        echo ">>> [RUN ${run}] OK: duration=${duration}s, avg_len=${avg_len}, steps=${total_steps}" >> "${LOG_FILE}"
      else
        echo ">>> [RUN ${run}] controller.py の結果を解析できませんでした" >> "${LOG_FILE}"
      fi
      echo "" >> "${LOG_FILE}"
    done

    # --- 平均出力 ---
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
