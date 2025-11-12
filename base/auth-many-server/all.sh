# #!/bin/zsh
# set -euo pipefail

# ## NOTE: 実行コマンド
# ## ./base/auth-many-server/all.sh

# # ======== 設定 ========
# EDGE_FILE="dataset/Louvain/graph/karate.gr"
# AUTH_JSON="base/auth-many-server/auth_by_start.json"
# SERVERS=(
#   "host=ab05 id=0 ip=10.58.60.5 port=3000"
#   "host=ab06 id=1 ip=10.58.60.6 port=3000"
# )

# SERVER_COUNT=${#SERVERS[@]}
# SERVER_ENDPOINTS=()
# for entry in "${SERVERS[@]}"; do
#   eval "$entry"
#   SERVER_ENDPOINTS+=("${ip}:${port}")
# done
# SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

# REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \
#   --server-count ${SERVER_COUNT} \
#   --edges ${EDGE_FILE} \
#   --server-endpoints ${SERVER_ENDPOINTS_STR} \
#   --auth-file ${AUTH_JSON}"

# TARGET_LOG="^\[Server"
# TIMEOUT=0  # 秒
# REPO_DIR="./"

# # ======== クリーンアップ処理 ========
# cleanup() {
#   echo ">>> [CLEANUP] 実験終了検知。全サーバの auth remote_server.py を停止中..."
#   for entry in "${SERVERS[@]}"; do
#     eval "$entry"
#     echo "  - ${host} 上のプロセスを停止します..."
#     ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
#   done
#   echo ">>> [CLEANUP] 全サーバの停止完了。"
# }
# trap cleanup EXIT

# # ======== 認可テーブル生成 ========
# echo ">>> auth_by_start.json を再生成中..."
# python3 base/auth-many-server/create_json_table.py "${EDGE_FILE}" -o "${AUTH_JSON}" --ng-ratio 0.0

# # ======== 関数定義 ========
# start_remote_server() {
#   local host=$1 id=$2 ip=$3 port=$4
#   echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

#   ssh "$host" bash -c "'
# set -euo pipefail
# cd ${REPO_DIR}
# ${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
# PID=\$!
# echo \"[INFO] auth remote_server.py started on ${host} (PID=\$PID)\"

# ( tail -n0 -f remote_server.log & ) >/dev/null 2>&1 &
# TAIL_PID=\$!

# for ((i=0; i<${TIMEOUT}*2; i++)); do
#   if grep -q \"${TARGET_LOG}\" remote_server.log; then
#     echo \"[INFO] ${host}: ログ確認OK ([Server 行を検出])\"
#     kill \$TAIL_PID || true
#     exit 0
#   fi
#   sleep 0.5
# done

# echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s) 経過\"
# kill \$TAIL_PID || true
# exit 1
# '"
# }

# # ======== メイン処理 ========
# for entry in "${SERVERS[@]}"; do
#   eval "$entry"
#   start_remote_server "$host" "$id" "$ip" "$port" &
# done

# echo ">>> 各サーバの起動確認を待機中..."
# wait

# echo "=== 全サーバ起動確認完了 ==="

# # ======== ローカルジョブ実行 ========
# echo ">>> 分散ランダムウォーク開始 (auth)"

# python3 base/auth-many-server/controller.py --servers ${SERVER_COUNT} \
#   --server-endpoints "${SERVER_ENDPOINTS[@]}" \
#   --walks 10 --alpha 0.5 --start-node 1 --seed 42

# echo "=== ローカルジョブ完了 ==="


#!/bin/zsh
set -euo pipefail

## ./base/auth-many-server/all.sh
## 使い方: ./base/auth-many-server/all.sh <繰り返し回数>
## 例: ./base/auth-many-server/all.sh 5

REPS=${1:-1}            # 繰り返し回数（デフォルト1）
EDGE_FILE="dataset/Louvain/graph/karate.gr"
AUTH_JSON="base/auth-many-server/auth_by_start.json"

SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done

# zsh の join 展開
SERVER_ENDPOINTS_STR="${(j: :)SERVER_ENDPOINTS}"

REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --auth-file ${AUTH_JSON}"

TARGET_LOG="^\[Server"
TIMEOUT=10
REPO_DIR="./"

# ======== ログ設定 ========
RUN_DIR="./runs"
mkdir -p "${RUN_DIR}"
MAIN_LOG="${RUN_DIR}/all_runs.log"
: >| "${MAIN_LOG}"  # 上書き初期化（zshの強制上書き演算子）

AVG_FILE="${RUN_DIR}/avg_lengths.txt"
TOTALS_FILE="${RUN_DIR}/total_steps.txt"
TIME_FILE="${RUN_DIR}/times.txt"
SERVER_COUNTS_FILE="${RUN_DIR}/server_counts.txt"
: >| "${AVG_FILE}"
: >| "${TOTALS_FILE}"
: >| "${TIME_FILE}"
: >| "${SERVER_COUNTS_FILE}"

# ======== クリーンアップ ========
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..." | tee -a "${MAIN_LOG}"
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    echo "  - ${host} 上のプロセスを停止します..." | tee -a "${MAIN_LOG}"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了" | tee -a "${MAIN_LOG}"
}
trap cleanup EXIT INT TERM

# ======== 認可テーブル生成 ========
echo ">>> auth_by_start.json を再生成中..." | tee -a "${MAIN_LOG}"
python3 base/auth-many-server/create_json_table.py "${EDGE_FILE}" -o "${AUTH_JSON}" --ng-ratio 0.0 | tee -a "${MAIN_LOG}"

# ======== サーバ起動 ========
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4
  echo "=== [${host}] サーバ起動 (ID=${id}) ===" | tee -a "${MAIN_LOG}"

  ssh "$host" bash -c "'
set -euo pipefail
cd ${REPO_DIR}
${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
PID=\$!
echo \"[INFO] auth remote_server.py started on ${host} (PID=\$PID)\"

( tail -n0 -f remote_server.log & ) >/dev/null 2>&1 &
TAIL_PID=\$!

for ((i=0; i<${TIMEOUT}*2; i++)); do
  if grep -q \"${TARGET_LOG}\" remote_server.log; then
    echo \"[INFO] ${host}: ログ確認OK ([Server 行を検出])\"
    kill \$TAIL_PID || true
    exit 0
  fi
  sleep 0.5
done

echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s) 経過\"
kill \$TAIL_PID || true
exit 1
'" | tee -a "${MAIN_LOG}"
}

for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" &
done

echo ">>> サーバ起動確認待機..." | tee -a "${MAIN_LOG}"
wait
echo "=== 全サーバ起動完了 ===" | tee -a "${MAIN_LOG}"

# ======== 繰り返し実行 ========
echo ">>> controller を ${REPS} 回実行します..." | tee -a "${MAIN_LOG}"

for ((i=1; i<=REPS; i++)); do
  print -n "\n========== Run ${i}/${REPS} 開始 ==========\n" | tee -a "${MAIN_LOG}"
  START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[${START_TIME}] Controller 実行開始" | tee -a "${MAIN_LOG"

  # controller 実行（server-endpoints は配列で渡す）
  echo ">>> 実行コマンド: python3 base/auth-many-server/controller.py --servers ${SERVER_COUNT} --server-endpoints ${SERVER_ENDPOINTS_STR} --walks 10 --alpha 0.5 --start-node 1 --seed $((42 + i))" | tee -a "${MAIN_LOG}"
  # zshの配列展開を利用して引数を渡す
  CONTROLLER_CMD=(python3 base/auth-many-server/controller.py --servers "${SERVER_COUNT}")
  for ep in "${SERVER_ENDPOINTS[@]}"; do
    CONTROLLER_CMD+=(--server-endpoints "${ep}")
  done
  CONTROLLER_CMD+=(--walks 10 --alpha 0.5 --start-node 1 --seed $((42 + i)))

  "${(j: :)CONTROLLER_CMD}" >>| "${MAIN_LOG}" 2>&1 || echo "!!! controller returned non-zero (run ${i})" | tee -a "${MAIN_LOG}"

  END_TIME=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[${END_TIME}] Controller 実行終了" | tee -a "${MAIN_LOG}"
  print -n "========== Run ${i} 終了 ==========\n\n" | tee -a "${MAIN_LOG}"

  # --- 結果抽出: MAIN_LOG の最新該当行を抜く ---
  awk '/Average walk length:/ {val=$4} END{if(val) print val; else print "NaN"}' "${MAIN_LOG}" >>| "${AVG_FILE}" || echo "NaN" >>| "${AVG_FILE}"
  awk '/Total steps taken:/ {val=$4} END{if(val) print val; else print "0"}' "${MAIN_LOG}" >>| "${TOTALS_FILE}" || echo "0" >>| "${TOTALS_FILE}"
  awk '/Completed in:/ {val=$3} END{if(val) print val; else print "0"}' "${MAIN_LOG}" >>| "${TIME_FILE}" || echo "0" >>| "${TIME_FILE}"

  # --- サーバ訪問数（最後に出た SERVER_COUNT 行を拾う） ---
  grep -E 'Server [0-9]+:' "${MAIN_LOG}" | tail -n "${SERVER_COUNT}" >| "${RUN_DIR}/server_tmp.txt" || true
  if [[ ! -s "${RUN_DIR}/server_tmp.txt" ]]; then
    # fallback zeros
    : >| "${RUN_DIR}/server_tmp.txt"
    for ((k=0;k<SERVER_COUNT;k++)); do
      printf "Server %d: 0\n" "${k}" >>| "${RUN_DIR}/server_tmp.txt"
    done
  fi

  awk -v n="${SERVER_COUNT}" '
  BEGIN { for(i=0;i<n;i++) a[i]=0 }
  {
    for(j=1;j<=NF;j++){
      if($j=="Server"){
        sid=$(j+1); gsub(":", "", sid); count=$(j+2); a[sid]=count
      }
    }
  }
  END {
    for(i=0;i<n;i++) printf "%s%s", (i==0?"":" "), a[i]
    printf "\n"
  }' "${RUN_DIR}/server_tmp.txt" >>| "${SERVER_COUNTS_FILE}" || true
done

# ======== 集計 ========
echo ">>> 集計結果:" | tee -a "${MAIN_LOG}"

echo -n "Average walk length 平均: " | tee -a "${MAIN_LOG}"
awk '{s+=$1;n++}END{if(n) printf "%.6f\n", s/n; else print "NaN"}' "${AVG_FILE}" | tee -a "${MAIN_LOG}"

echo -n "Total steps taken 平均: " | tee -a "${MAIN_LOG}"
awk '{s+=$1;n++}END{if(n) printf "%.3f\n", s/n; else print "NaN"}' "${TOTALS_FILE}" | tee -a "${MAIN_LOG}"

echo -n "Completed in 平均 (s): " | tee -a "${MAIN_LOG}"
awk '{s+=$1;n++}END{if(n) printf "%.6f\n", s/n; else print "NaN"}' "${TIME_FILE}" | tee -a "${MAIN_LOG}"

echo -n "Server visit counts 平均: " | tee -a "${MAIN_LOG}"
awk '
{
  for(i=1;i<=NF;i++) sum[i]+=$i
  rows++
}
END{
  for(i=1;i<=length(sum);i++){
    if(i>1) printf " ";
    printf "%.3f", sum[i]/rows
  }
  printf "\n"
}' "${SERVER_COUNTS_FILE}" | tee -a "${MAIN_LOG}"

print -n "\n=== 全処理完了 ===\n" | tee -a "${MAIN_LOG}"
echo "ログファイル: ${MAIN_LOG}"
