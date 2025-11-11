#!/bin/zsh
set -euo pipefail

## NOTE: 実行コマンド
## ./base/base-many-server/all.sh node
## ./base/base-many-server/all.sh edge

MODE=${1:-node}
case "${MODE}" in
  node) REMOTE_SERVER_SCRIPT="remote_server.py" ;;
  edge) REMOTE_SERVER_SCRIPT="remote_server_edge.py" ;;
  *)
    echo "Usage: $0 [node|edge]"
    exit 1
    ;;
esac

echo ">>> Mode: ${MODE} (server script: ${REMOTE_SERVER_SCRIPT})"

# ======== 設定 ========
EDGE_FILE="dataset/Louvain/graph/karate.gr"
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
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

REMOTE_CMD_BASE="python3 base/base-many-server/${REMOTE_SERVER_SCRIPT} \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR}"

TARGET_LOG="^\[Server"
TIMEOUT=10  # 秒
REPO_DIR="./"

# ======== クリーンアップ処理 ========
cleanup() {
  echo ">>> [CLEANUP] 実験終了検知。全サーバの ${REMOTE_SERVER_SCRIPT} を停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    echo "  - ${host} 上のプロセスを停止します..."
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/base-many-server/${REMOTE_SERVER_SCRIPT} || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 全サーバの停止完了。"
}
# スクリプト終了時（正常終了・Ctrl+C・エラー）に必ずcleanupを実行
trap cleanup EXIT

# ======== 関数定義 ========

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

  ssh "$host" bash -c "'
set -euo pipefail
cd ${REPO_DIR}
${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} > remote_server.log 2>&1 &
PID=\$!
echo \"[INFO] ${REMOTE_SERVER_SCRIPT} started on ${host} (PID=\$PID)\"

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
'"
}

# ======== メイン処理 ========

for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" &
done

echo ">>> 各サーバの起動確認を待機中..."
wait  # 全サーバ起動完了を待機

echo "=== 全サーバ起動確認完了 ==="

# ======== ローカルジョブ実行 ========
echo ">>> 分散ランダムウォーク開始"

python3 base/base-many-server/base.py --servers ${SERVER_COUNT} \
  --server-endpoints "${SERVER_ENDPOINTS[@]}" \
  --walks 3 --alpha 0.5 --start-node 1 --seed 42

echo "=== ローカルジョブ完了 ==="
