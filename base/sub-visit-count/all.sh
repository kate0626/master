#!/bin/zsh
set -euo pipefail

############################################################
#  実験設定パート
#  （すべての数字・ファイルパス・条件はここで定義）
#  例: ./base/sub-visit-count/all.sh 2
############################################################

## --- 実行回数設定 ---
RUN_COUNT=${1:-3}                # 各パラメータ組み合わせにつき何回実行するか（デフォルト3回）
TIMEOUT=10                       # サーバ起動待機の最大時間 [秒]
SEED_BASE=42                     # 乱数シードの基準値

## --- ファイル設定 ---
EDGE_FILE="dataset/Louvain/graph/karate.gr"
NODE_TO_STARTS_JSON="base/auth-many-server/node_to_starts.json"
SUBGRAPH_SOURCE_DIR="base/auth-subgraph-server"

## ここで使用するサブグラフを定義
SUBGRAPH_GRAPH_NAME="karate"
# ここを配列にしてサイズをスイープ
SUBGRAPH_GROUP_SIZE_LIST=(10)     # 例: (1 2 4 6) にすると 4種類まとめて回る
REPO_DIR="./"
RUNS_DIR="runs"
LOG_DIR="${RUNS_DIR}/visit_count"

## --- サーバ設定 ---
# IDも変更するように注意
SERVERS=(
  # "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=0 ip=10.58.60.6 port=3000"
)

## --- スイープパラメータ設定 ---
WALKS_LIST=(100)
ALPHA_LIST=(0.01)
START_NODE=ALL                  # RWの開始ノード（ALL で全始点PPR）

TARGET_LOG="^\\[Server"

############################################################
#  関数定義パート
############################################################

# Python で time.time() を呼んで、EPOCHREALTIME に依存しないようにする
now_realtime() {
  python3 - <<'PY'
import time
print(f"{time.time():.6f}")
PY
}

cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/sub-visit-count/remote_server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# REMOTE_CMD_BASE はサイズごとに変わるので、ここでは引数で受け取る
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4
  local remote_cmd_base=$5
  local target_log=$6
  local repo_dir=$7
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash -c "'
set -euo pipefail
cd ${repo_dir}
${remote_cmd_base} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
PID=\$!
timeout ${TIMEOUT}s bash -c \"grep -m1 '${target_log}' <(tail -f remote_server.log)\" \
  && echo \"[INFO] ${host}: 起動OK\" || echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
'"
}

calc_average() {
  if (( $# == 0 )); then
    echo "NaN"
    return 0
  fi
  python3 - "$@" <<'PY'
import sys
vals = [float(v) for v in sys.argv[1:] if v.strip() and v.strip() != "NaN"]
print(f"{(sum(vals)/len(vals)):.6f}" if vals else "NaN")
PY
}

############################################################
#  実験実行パート
############################################################

mkdir -p "${LOG_DIR}/test"

for SUBGRAPH_GROUP_SIZE in "${SUBGRAPH_GROUP_SIZE_LIST[@]}"; do
  echo ""
  echo "=== [SUBGRAPH SIZE] size=${SUBGRAPH_GROUP_SIZE} ==="

  ##########################################################
  # サブグラフ定義の解決（サイズごとにやり直す）
  ##########################################################
  if [[ -n "${SUB_VISIT_SUBGRAPH_FILE:-}" ]]; then
    # 環境変数でファイルを直指定したい場合はこちらを優先
    SUBGRAPH_JSON="${SUB_VISIT_SUBGRAPH_FILE}"
  else
    # 単純に固定のパスだけを見る
    SUBGRAPH_JSON="${SUBGRAPH_SOURCE_DIR}/subgraph_index_${SUBGRAPH_GRAPH_NAME}_size${SUBGRAPH_GROUP_SIZE}.json"
  fi

  if [[ ! -f "${SUBGRAPH_JSON}" ]]; then
    echo ">>> [WARN] サブグラフ定義ファイルがありません: ${SUBGRAPH_JSON} (graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE})"
    # ここでスクリプトを落としたいなら exit 1 にする
    # exit 1
    # 今は「そのサイズだけスキップして次へ」にしておく
    continue
  fi


  GRAPH_LABEL_CLEAN=${SUBGRAPH_GRAPH_NAME//[^A-Za-z0-9_-]/_}
  GROUP_LABEL=${SUBGRAPH_GROUP_SIZE//./_}

  echo ">>> 使用するサブグラフ: ${SUBGRAPH_JSON} (graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE})"

  ##########################################################
  #  実行前の準備パート（サイズごとにサーバ構成を作る）
  ##########################################################
  SERVER_COUNT=${#SERVERS[@]}
  SERVER_ENDPOINTS=()
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    SERVER_ENDPOINTS+=("${ip}:${port}")
  done
  SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

  REMOTE_CMD_BASE="python3 base/sub-visit-count/remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --subgraph-file ${SUBGRAPH_JSON} \
  --node-to-starts-file ${NODE_TO_STARTS_JSON}"

  # サーバ起動（このサイズ用）
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    start_remote_server "$host" "$id" "$ip" "$port" "${REMOTE_CMD_BASE}" "${TARGET_LOG}" "${REPO_DIR}" &
  done
  wait
  echo "=== 全サーバ起動完了 (graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE}) ==="

  ##########################################################
  #  PPR 実験パート（walks, alpha, RUN_COUNT）
  ##########################################################
  for walks in "${WALKS_LIST[@]}"; do
    for alpha in "${ALPHA_LIST[@]}"; do
      echo ""
      echo "=== [PARAM SET] graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE}, walks=${walks}, alpha=${alpha} ==="
      alpha_label=${alpha//./_}
      LOG_FILE="${LOG_DIR}/test/visit_${GRAPH_LABEL_CLEAN}_size${GROUP_LABEL}_walks${walks}_alpha${alpha_label}.log"
      echo "=== RUN START: walks=${walks}, alpha=${alpha}, size=${SUBGRAPH_GROUP_SIZE} ===" > "${LOG_FILE}"

      wall_times=()          # Controller から見た全体時間
      per_start_times=()     # 始点ごとの時間（Controller 側集計）
      server_sums=()         # 各リモートサーバの remote_duration の合計 = リモート側時間
      avg_lengths=()
      total_steps_list=()
      auth_times=()
      shell_elapsed_times=()
      successful_runs=0

      for ((run=1; run<=RUN_COUNT; run++)); do
        start_line=">>> [RUN ${run}/${RUN_COUNT}] start: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "### DEBUG: now running loop ${run}/${RUN_COUNT}"
        echo "${start_line}" | tee -a "${LOG_FILE}"

        run_start_rt=$(now_realtime)

        CONTROLLER_CMD=(
          python3 base/sub-visit-count/controller.py
          --servers "${SERVER_COUNT}"
          --server-endpoints "${SERVER_ENDPOINTS[@]}"
          --walks "${walks}"
          --alpha "${alpha}"
          --seed $((SEED_BASE + run))
        )

        if [[ "${START_NODE}" == "ALL" || "${START_NODE}" == "all" ]]; then
          CONTROLLER_CMD+=(--start-node-all --subgraph-file "${SUBGRAPH_JSON}")
        else
          CONTROLLER_CMD+=(--start-node "${START_NODE}")
        fi

        set +e
        run_output="$("${CONTROLLER_CMD[@]}" 2>&1)"
        controller_status=$?
        set -e

        echo "${run_output}" >> "${LOG_FILE}"

        if (( controller_status != 0 )); then
          fail_line=">>> [RUN ${run}] controller.py failed (exit=${controller_status})"
          echo "${fail_line}" | tee -a "${LOG_FILE}"
          echo "${run_output}"
          continue
        fi

        timing_line=$(echo "${run_output}" | grep -E '\[Controller\] PPR timing:' | tail -n1 || true)
        if [[ -n "${timing_line}" ]]; then
          echo "${timing_line}"
        fi
        agg_line=$(echo "${run_output}" | grep -E '\[Controller\] Aggregated [0-9]+' | tail -n1 || true)
        fallback_line=""
        if [[ -z "${agg_line}" ]]; then
          fallback_line=$(echo "${run_output}" | grep -E '\[Controller\] .*Received [0-9]+ walks in [0-9.]+s\. Avg length: [0-9.]+, total steps: [0-9]+' | tail -n1 || true)
        fi
        auth_time_line=$(echo "${run_output}" | grep -E 'Total authorization time .*: [0-9.]+ s' | tail -n1 || true)

        wall_val="NaN"
        per_start_val="NaN"
        server_sum_val="NaN"
        if [[ -n "${timing_line}" ]]; then
          wall_val=$(echo "${timing_line}" | sed -E 's/.*wall=([0-9.]+)s.*/\1/')
          per_start_val=$(echo "${timing_line}" | sed -E 's/.*per_start=([0-9.]+)s.*/\1/')
          server_sum_val=$(echo "${timing_line}" | sed -E 's/.*server_sum=([0-9.]+)s.*/\1/')
        fi

        avg_len_val="NaN"
        total_steps_val="NaN"
        if [[ -n "${agg_line}" ]]; then
          avg_len_val=$(echo "${agg_line}" | sed -E 's/.*Avg length: ([0-9.]+), total steps: [0-9]+/\1/')
          total_steps_val=$(echo "${agg_line}" | sed -E 's/.*total steps: ([0-9]+)/\1/')
        elif [[ -n "${fallback_line}" ]]; then
          avg_len_val=$(echo "${fallback_line}" | sed -E 's/.*Avg length: ([0-9.]+), total steps: [0-9]+/\1/')
          total_steps_val=$(echo "${fallback_line}" | sed -E 's/.*total steps: ([0-9]+)/\1/')
        fi

        auth_time_val="NaN"
        if [[ -n "${auth_time_line}" ]]; then
          auth_time_val=$(echo "${auth_time_line}" | sed -E 's/.*: ([0-9.]+) s/\1/')
        fi

        run_end_rt=$(now_realtime)
        run_elapsed=$(python3 - "$run_start_rt" "$run_end_rt" <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
print(f"{end - start:.6f}")
PY
        )

        if [[ -n "${timing_line}" ]]; then
          wall_times+=("${wall_val}")
          per_start_times+=("${per_start_val}")
          server_sums+=("${server_sum_val}")
          avg_lengths+=("${avg_len_val}")
          total_steps_list+=("${total_steps_val}")
          auth_times+=("${auth_time_val}")
          shell_elapsed_times+=("${run_elapsed}")
          ((successful_runs++))
          run_summary=">>> [RUN ${run}] OK: duration=${wall_val}s, per_start=${per_start_val}s, server_sum=${server_sum_val}s, avg_len=${avg_len_val}, total_steps=${total_steps_val}, auth_time=${auth_time_val}s, shell_elapsed=${run_elapsed}s"
          echo "${run_summary}" | tee -a "${LOG_FILE}"
        else
          warn_line=">>> [RUN ${run}] controller.py の結果を解析できませんでした"
          echo "${warn_line}" | tee -a "${LOG_FILE}"
        fi
        echo "" >> "${LOG_FILE}"
      done

      summary_block=$(
        {
          echo "=== 結果集計 (graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE}, walks=${walks}, alpha=${alpha}) ==="
          if (( successful_runs > 0 )); then
            avg_wall=$(calc_average "${wall_times[@]:-}")
            avg_per_start=$(calc_average "${per_start_times[@]:-}")
            avg_server_sum=$(calc_average "${server_sums[@]:-}")      # リモート側時間（合計）の平均
            avg_walk_length=$(calc_average "${avg_lengths[@]:-}")
            avg_total_steps=$(calc_average "${total_steps_list[@]:-}")
            avg_auth_time=$(calc_average "${auth_times[@]:-}")
            avg_shell_elapsed=$(calc_average "${shell_elapsed_times[@]:-}")
            echo ">>> 平均値 (${successful_runs}/${RUN_COUNT})"
            echo "    - duration: ${avg_wall}s"
            echo "    - per_start_wall: ${avg_per_start}s"
            echo "    - server_duration_sum: ${avg_server_sum}s"
            echo "    - avg_length: ${avg_walk_length}"
            echo "    - total_steps: ${avg_total_steps}"
            echo "    - auth_time_total: ${avg_auth_time}s"
            echo "    - shell_elapsed: ${avg_shell_elapsed}s"
          else
            echo ">>> 実行成功なし"
          fi
          echo "=== RUN END: graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE}, walks=${walks}, alpha=${alpha} ==="
        }
      )
      echo "${summary_block}" | tee -a "${LOG_FILE}"

      echo "✅ 完了: graph=${SUBGRAPH_GRAPH_NAME}, size=${SUBGRAPH_GROUP_SIZE}, walks=${walks}, alpha=${alpha} (ログ: ${LOG_FILE})"
    done
  done

  echo ">>> size=${SUBGRAPH_GROUP_SIZE} の実行が完了しました。サーバを停止します。"
  cleanup
done

echo ">>> 全てのサブグラフサイズでの実行が完了しました。"
trap - EXIT
cleanup || true
