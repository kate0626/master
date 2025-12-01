#!/bin/zsh
set -euo pipefail

############################################################
#  実験設定パート
#  （すべての数字・ファイルパス・条件はここで定義）
# ./base/auth-many-server/all.sh 2

# VISIBLE_MODE=0 ./base/auth-many-server/all.sh 2
# VISIBLE_MODE=1 ./base/auth-many-server/all.sh 2
############################################################

## --- 実行回数設定 ---
RUN_COUNT=${1:-5}                 # 各パラメータ組み合わせにつき何回実行するか（デフォルト5回）
TIMEOUT=10                        # サーバ起動待機の最大時間 [秒]
SEED_BASE=42                      # 乱数シードの基準値

## --- ファイル設定 ---
EDGE_FILE="dataset/Louvain/graph/karate.gr"
# AUTH_JSON="base/auth-many-server/auth_by_start.json"
AUTH_JSON="base/auth-many-server/auth_by_start.json"
NODE_TO_STARTS_JSON="base/auth-many-server/node_to_starts.json"
REPO_DIR="./"
RUNS_DIR="runs"
LOG_DIR="${RUNS_DIR}/auth"
VISIBLE_SUFFIX=""
if [ "${VISIBLE_MODE:-0}" = "1" ]; then
  VISIBLE_SUFFIX="_visible"
fi

## --- サーバ設定 ---
SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  # "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

## --- スイープパラメータ設定 ---
# ここを変えるだけで一括実験条件が変わる！
WALKS_LIST=(10)
ALPHA_LIST=(0.1)
NG_RATIO_LIST=(0.3)
START_NODE=1                      # RWの開始ノード
DEFAULT_WALKS=10                  # 参考値（controller.pyのデフォルト想定）
# PPR_MODE=1                      # 1 にするとサーバを --ppr-mode で起動
# VISIBLE_MODE=0                    # 1 で remote_server_visi.py を利用

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

if (( VISIBLE_MODE )); then
  REMOTE_SERVER_SCRIPT="remote_server_visi.py"
else
  REMOTE_SERVER_SCRIPT="remote_server.py"
fi

REMOTE_CMD_BASE="python3 base/auth-many-server/${REMOTE_SERVER_SCRIPT} \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR}"

if (( VISIBLE_MODE )); then
  REMOTE_CMD_BASE+=" --auth-file ${AUTH_JSON} --node-to-starts-file ${NODE_TO_STARTS_JSON}"
else
  REMOTE_CMD_BASE+=" --node-to-starts-file ${NODE_TO_STARTS_JSON}"
  if (( PPR_MODE )); then
    REMOTE_CMD_BASE+=" --ppr-mode"
  fi
fi

############################################################
#  関数定義パート
############################################################

# --- クリーンアップ ---
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/${REMOTE_SERVER_SCRIPT} || true" >/dev/null 2>&1 || true
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

# --- リモートで認可テーブル生成 ---
generate_remote_auth_table() {
  local host=$1 ratio=$2
  echo ">>> [${host}] node_to_starts.json を生成中 (ng_ratio=${ratio})..."
  local repo_dir_q edge_file_q auth_json_q ratio_q extra_flags remote_cmd remote_cmd_q
  printf -v repo_dir_q '%q' "${REPO_DIR}"
  printf -v edge_file_q '%q' "${EDGE_FILE}"
  printf -v auth_json_q '%q' "${NODE_TO_STARTS_JSON}"
  printf -v ratio_q '%q' "${ratio}"
  extra_flags=""
  if (( VISIBLE_MODE )); then
    extra_flags="--emit-auth-table"
    printf -v auth_json_q '%q' "${AUTH_JSON}"
  fi
  remote_cmd="set -euo pipefail; cd ${repo_dir_q}; python3 base/auth-many-server/create_json_table.py ${edge_file_q} -o ${auth_json_q} --ng-ratio ${ratio_q} ${extra_flags}"
  printf -v remote_cmd_q '%q' "${remote_cmd}"
  ssh "$host" bash -lc "${remote_cmd_q}"
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

# ログディレクトリ作成（サブディレクトリ test まで）
mkdir -p "${LOG_DIR}/test"

# --- パラメータスイープ ---
for ng_ratio in "${NG_RATIO_LIST[@]}"; do
  echo ">>> 各サーバで node_to_starts.json を再生成中... (ng_ratio=${ng_ratio})"
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    generate_remote_auth_table "$host" "${ng_ratio}"
  done

  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    start_remote_server "$host" "$id" "$ip" "$port" &
  done
  wait
  echo "=== 全サーバ起動完了 (ng_ratio=${ng_ratio}) ==="

  ng_ratio_label=${ng_ratio//./_}

  for walks in "${WALKS_LIST[@]}"; do
    for alpha in "${ALPHA_LIST[@]}"; do
      echo ""
      echo "=== [PARAM SET] ng_ratio=${ng_ratio}, walks=${walks}, alpha=${alpha} ==="
      # LOG_FILE="${LOG_DIR}/test/result_ng${ng_ratio_label}_walks${walks}_alpha${alpha}.log"
      LOG_FILE="${LOG_DIR}/test/${VISIBLE_SUFFIX}result_ng${ng_ratio_label}_walks${walks}_alpha${alpha}.log"
      echo "=== RUN START: walks=${walks}, alpha=${alpha} ===" > "${LOG_FILE}"

      durations=()
      avg_lengths=()
      total_steps_list=()
      successful_runs=0
      remote_durations=()  
      auth_times=()

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
        inner_duration_line=$(echo "${run_output}" | grep -Eo 'duration[[:space:]]+[0-9.]+' | awk '{print $2}' | tail -n1)
        echo ">>> ${inner_duration_line}"
        auth_time_line=$(echo "${run_output}" | grep -E 'Total authorization time .*: [0-9.]+ s' | tail -n1 || true)
        
        if [[ -n "${parsed_line}" ]]; then
          duration=$(echo "${parsed_line}" | sed -E 's/.* in ([0-9.]+)s.*/\1/')
          avg_len=$(echo "${parsed_line}" | sed -E 's/.*Avg length: ([0-9.]+).*/\1/')
          total_steps=$(echo "${parsed_line}" | sed -E 's/.*total steps: ([0-9]+).*/\1/')
          if [[ -n "${inner_duration_line}" ]]; then
            inner_duration=$(echo "${inner_duration_line}" | awk '{print $1}')
          else
            inner_duration="NaN"
          fi

          if [[ -n "${auth_time_line}" ]]; then
            auth_time_val=$(echo "${auth_time_line}" | sed -E 's/.*: ([0-9.]+) s/\1/')
          else
            auth_time_val="NaN"
          fi

          durations+=("${duration}")
          avg_lengths+=("${avg_len}")
          total_steps_list+=("${total_steps}")
          remote_durations+=("${inner_duration}")
          auth_times+=("${auth_time_val}")
          # ((successful_runs++))
          
          # echo ">>> [RUN ${run}] OK: duration=${duration}s, avg_len=${avg_len}, steps=${total_steps}" >> "${LOG_FILE}"
          echo ">>> [RUN ${run}] OK: dur=${duration}s, remote_dur=${remote_durations}s, auth_time=${auth_time_val}s, len=${avg_len}, steps=${total_steps}" >> "${LOG_FILE}"
        else
          echo ">>> [RUN ${run}] controller.py の結果を解析できませんでした" >> "${LOG_FILE}"
        fi
        echo "" >> "${LOG_FILE}"
      done

      # --- 平均出力 ---
      {
        echo "=== 結果集計 (walks=${walks}, alpha=${alpha}) ==="
        if (( successful_runs <10000 )); then
          avg_duration=$(calc_average "${durations[@]}")
          avg_walk_length=$(calc_average "${avg_lengths[@]}")
          avg_total_steps=$(calc_average "${total_steps_list[@]}")
          avg_remote_duration=$(calc_average "${remote_durations[@]:-}")
          avg_auth_time=$(calc_average "${auth_times[@]:-}")
          echo ">>> 平均値 (${successful_runs}/${RUN_COUNT})"
          echo "    - duration: ${avg_duration}s"
          echo "    - remote_duration: ${avg_remote_duration}s"
          echo "    - auth_time_total: ${avg_auth_time}s"
          echo "    - avg_length: ${avg_walk_length}"
          echo "    - total_steps: ${avg_total_steps}"
        else
          echo ">>> 実行成功なし"
        fi
        echo "=== RUN END: walks=${walks}, alpha=${alpha} ==="
      } >> "${LOG_FILE}"

      echo "✅ 完了: ng_ratio=${ng_ratio}, walks=${walks}, alpha=${alpha} (ログ: ${LOG_FILE})"
    done
  done

  echo ">>> ng_ratio=${ng_ratio} の実行が完了しました。サーバを停止します。"
  cleanup
done
