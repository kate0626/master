#!/bin/zsh
set -euo pipefail

############################################################
#  固定コマンド実行用（all.sh と同じ起動/終了フロー）
############################################################

TIMEOUT=30
GRAPH=fb-caltech-connected
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
LOG_DIR="runs/auth/C1:bunsan"
RW_WAKLS=10
ALPHA=0.1
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${GRAPH}.log"
# exec > >(tee -a "${LOG_FILE}") 2>&1
exec > "${LOG_FILE}" 2>&1


SERVERS=(
  "host=ab05 id=0 ip=10.58.60.5 port=3000 nts=base/auth-many-server/${GRAPH}/node_to_starts_server0.json"
  "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/${GRAPH}/node_to_starts_server1.json"
)

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
TARGET_LOG="^\\[Server"

REMOTE_CMD_BASE="python3 base/auth-many-server/remote_server.py \\
  --server-count ${SERVER_COUNT} \\
  --edges ${EDGE_FILE} \\
  --server-endpoints ${SERVER_ENDPOINTS_STR}"

cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/remote_server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash -c "'
set -euo pipefail
cd ${REPO_DIR}
${REMOTE_CMD_BASE} --server-id ${id} --host ${ip} --port ${port} --node-to-starts-file ${nts} > remote_server.log 2>&1 &
PID=\$!
timeout ${TIMEOUT}s bash -c \"grep -m1 '${TARGET_LOG}' <(tail -f remote_server.log)\" \\
  && echo \"[INFO] ${host}: 起動OK\" || echo \"[WARN] ${host}: タイムアウト (${TIMEOUT}s)\"
'"
}

for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" "$nts" &
done
wait

# node_to_starts からスタートノードを全取得（all.sh のループ構成に合わせる）
START_NODES_LIST=(${(s: :)$(
  python3 - "base/auth-many-server/${GRAPH}/node_to_starts_server0.json" "base/auth-many-server/${GRAPH}/node_to_starts_server1.json" <<'PY'
import json, sys
starts=set()
for path in sys.argv[1:]:
    try:
        data=json.load(open(path))
    except Exception:
        continue
    for vals in data.values():
        if not isinstance(vals, list):
            continue
        for v in vals:
            try:
                starts.add(int(v))
            except Exception:
                pass
print(" ".join(str(x) for x in sorted(starts)))
PY
)})

total_start=$(python3 - <<'PY'
import time; print(time.time())
PY
)

for start_node in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] ${start_node} ==="
  python3 base/auth-many-server/controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.5:3000 10.58.60.11:3000 \
    --start-node "${start_node}" \
    --walks ${RW_WAKLS} \
    --alpha ${ALPHA} \
    --seed 42
done

############################################################
# Controller duration の合計（ログから集計）
############################################################

controller_total=$(
  awk '
    /Total walk time \(sum over all servers\):/ {
      sum += $(NF-1)
    }
    END {
      printf "%.6f", sum
    }
  ' "${LOG_FILE}"
)

echo "[TOTAL] controller_duration_sum=${controller_total}s"

############################################################
# Authorization time の合計（ログから集計）
############################################################

auth_total=$(
  awk '
    /Total authorization time \(sum over all servers\):/ {
      sum += $(NF-1)
    }
    END {
      printf "%.6f", sum
    }
  ' "${LOG_FILE}"
)

echo "[TOTAL] authorization_time_sum=${auth_total}s"
