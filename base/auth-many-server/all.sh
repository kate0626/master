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
# TIMEOUT=10  # 秒
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
#   --walks 3 --alpha 0.5 --start-node 1 --seed 42

# echo "=== ローカルジョブ完了 ==="


#!/bin/zsh
set -euo pipefail

## 実行例:
## ./base/auth-many-server/all.sh 10
## ↑ 10回実行。省略時は5回。

# ======== 設定 ========
EDGE_FILE="dataset/Louvain/graph/karate.gr"
AUTH_JSON="base/auth-many-server/auth_by_start.json"
SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

RUN_COUNT=${1:-5}
LOG_FILE="runs/auth_many_server.log"
TIMEOUT=10  # 秒
REPO_DIR="./"

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --auth-file ${AUTH_JSON}"

TARGET_LOG="^\\[Server"

# ======== クリーンアップ ========
cleanup() {
  echo ">>> [CLEANUP] 実験終了検知。全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    echo "  - ${host} 上のプロセスを停止します..."
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ======== 認可テーブル生成 ========
echo ">>> auth_by_start.json を再生成中..."
python3 base/auth-many-server/create_json_table.py "${EDGE_FILE}" -o "${AUTH_JSON}" --ng-ratio 0.0

# ======== サーバ起動 ========
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash -c "'
set -euo pipefail
cd ${REPO_DIR}
${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
PID=\$!
echo \"[INFO] remote_server.py started on ${host} (PID=\$PID)\"

timeout ${TIMEOUT}s bash -c \"grep -m1 '${TARGET_LOG}' <(tail -f remote_server.log)\" \
  && echo \"[INFO] ${host}: 起動確認OK\" || echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
'"
}

# ======== コントローラ設定 ========
CONTROLLER_CMD=(
  python3 base/auth-many-server/controller.py
  --servers "${SERVER_COUNT}"
  --server-endpoints "${SERVER_ENDPOINTS[@]}"
  --walks 10
  --alpha 0.5
  --start-node 1
  --seed 42
)

# ======== 平均計算ユーティリティ ========
calc_average() {
  if (( $# == 0 )); then
    echo "NaN"
    return 0
  fi
  python3 - "$@" <<'PY'
import sys
vals = []
for v in sys.argv[1:]:
    try:
        if v.strip():
            vals.append(float(v))
    except ValueError:
        continue
print(f"{(sum(vals)/len(vals)):.6f}" if vals else "NaN")
PY
}

# ======== メイン処理 ========
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" &
done
wait
echo "=== 全サーバ起動確認完了 ==="

mkdir -p "$(dirname "${LOG_FILE}")"
echo "=== AUTH MANY SERVER RUN START ===" > "${LOG_FILE}"

durations=()
avg_lengths=()
total_steps_list=()
successful_runs=0

for ((run=1; run<=RUN_COUNT; run++)); do
  echo ">>> [RUN ${run}/${RUN_COUNT}] start: $(date '+%Y-%m-%d %H:%M:%S')" >> "${LOG_FILE}"

  run_output="$("${CONTROLLER_CMD[@]}" 2>&1)"
  echo "${run_output}" >> "${LOG_FILE}"

  # ======== 結果抽出（シェルの grep/sed を使って堅牢にパース） ========
  # 期待される行例:
  # [Controller] Received 10 walks in 0.180s. Avg length: 1.700, total steps: 17
  parsed_line=$(echo "${run_output}" | grep -Eo '\[Controller\] Received [0-9]+ walks in [0-9.]+s\. Avg length: [0-9.]+, total steps: [0-9]+' | tail -n1 || true)

  if [[ -n "${parsed_line}" ]]; then
    duration=$(echo "${parsed_line}" | sed -E 's/.* in ([0-9.]+)s.*/\1/')
    avg_len=$(echo "${parsed_line}" | sed -E 's/.*Avg length: ([0-9.]+).*/\1/')
    total_steps=$(echo "${parsed_line}" | sed -E 's/.*total steps: ([0-9]+).*/\1/')

    # 配列へ追加（空でなければ）
    [[ -n "${duration}"    ]] && durations+=("${duration}")
    [[ -n "${avg_len}"     ]] && avg_lengths+=("${avg_len}")
    [[ -n "${total_steps}" ]] && total_steps_list+=("${total_steps}")
    ((successful_runs++))
    echo ">>> [RUN ${run}] duration=${duration}s, avg_length=${avg_len}, total_steps=${total_steps}" >> "${LOG_FILE}"
  else
    echo ">>> [RUN ${run}] controller.py の結果を解析できませんでした（該当行が見つかりません）" >> "${LOG_FILE}"
  fi
  echo "" >> "${LOG_FILE}"
done

# ======== 平均出力 ========
durations=("${durations[@]:-}")
avg_lengths=("${avg_lengths[@]:-}")
total_steps_list=("${total_steps_list[@]:-}")

{
  echo "=== ローカルジョブ完了 (${successful_runs}/${RUN_COUNT} runs) ==="
  if (( successful_runs > 0 )); then
    avg_duration=$(calc_average "${durations[@]}")
    avg_walk_length=$(calc_average "${avg_lengths[@]}")
    avg_total_steps=$(calc_average "${total_steps_list[@]}")

    echo ">>> 平均値 (成功した実行のみ)"
    echo "    - duration: ${avg_duration}s"
    echo "    - avg_length: ${avg_walk_length}"
    echo "    - total_steps: ${avg_total_steps}"
  else
    echo ">>> 実行に成功した run がありませんでした"
  fi
  echo "=== AUTH MANY SERVER RUN END ==="
} >> "${LOG_FILE}"
