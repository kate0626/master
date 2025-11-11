#!/bin/zsh
set -euo pipefail

# ======== 設定 ========
SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000"
  "host=ab06 id=1 ip=10.58.60.6 port=3000"
)

REMOTE_CMD_BASE="python3 base/base-many-server/remote_server.py \
  --server-count 2 \
  --edges dataset/Louvain/graph/karate.gr \
  --server-endpoints 10.58.60.5:3000 10.58.60.6:3000"

TARGET_LOG="^\[Server"
TIMEOUT=10  # 秒
REPO_DIR="./"

# ======== クリーンアップ処理 ========
cleanup() {
  echo ">>> [CLEANUP] 実験終了検知。全サーバの remote_server.py を停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    echo "  - ${host} 上のプロセスを停止します..."
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/base-many-server/remote_server.py || true" >/dev/null 2>&1 || true
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
echo \"[INFO] remote_server.py started on ${host} (PID=\$PID)\"

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

declare -A SERVER_IPS

for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_IPS["${id}"]="${ip}:${port}"
  start_remote_server "$host" "$id" "$ip" "$port" &
done

echo ">>> 各サーバの起動確認を待機中..."
wait  # 全サーバ起動完了を待機

echo "=== 全サーバ起動確認完了 ==="

# ======== ローカルジョブ実行 ========
echo ">>> 分散ランダムウォーク開始"

python3 base/base-many-server/base.py --servers 2 \
  --server-endpoints 10.58.60.5:3000 10.58.60.6:3000 \
  --walks 3 --alpha 0.5 --start-node 1 --seed 42

echo "=== ローカルジョブ完了 ==="
