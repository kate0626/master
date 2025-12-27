#!/bin/zsh
set -euo pipefail

############################################################
#  固定コマンド実行用（all.sh と同じ起動/終了フロー）
############################################################

TIMEOUT=30
GRAPH=fb-caltech-connected

# GRAPH=fb-caltech-connected
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
LOG_DIR="runs/auth/C1:bunsan/split"
RW_WAKLS=100
ALPHA=0.1
NG_RATE="0.0"
# 全ての頂点からではなくて、ランダムな頂点から実行する
START_NODES_LIST=(1 2 3 4 5)
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/${GRAPH}.log"
: > "${LOG_FILE}"
MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
: > "${MEM_LOG_FILE}"

exec > >(tee -a "${LOG_FILE}") 2>&1
# exec > "${LOG_FILE}" 2>&1a

# エッジノード別々のサーバのものを考えるにはSplitをつける
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
TARGET_LOG="^\\[Server"

REMOTE_CMD_BASE="python3 base/auth-many-server/split_remote_server.py \\
  --server-count ${SERVER_COUNT} \\
  --edges ${EDGE_FILE} \\
  --server-endpoints ${SERVER_ENDPOINTS_STR} \\
  --owned-hints-only"

snapshot_memory() {
  local label="$1"
  {
    echo "============================================================"
    echo "=== [MEMORY SNAPSHOT] ${label}  $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      echo "--- endpoint=${ep} ---"
      curl -s "http://${ep}/access_stats" | python3 -c '
import json,sys
d=json.load(sys.stdin)
m=d.get("memory")
if m is None:
    print({"warn":"no memory field in /access_stats", "keys": list(d.keys())})
else:
    print(json.dumps(m, ensure_ascii=False, indent=2))
'
      echo
    done
  } >> "${MEM_LOG_FILE}" 2>&1
}



cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=5 "$host" "pkill -f base/auth-many-server/split_remote_server.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

TARGET_LOG="[Server"

start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}

: > split_remote_server.log

${REMOTE_CMD_BASE} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  > split_remote_server.log 2>&1 &

PID=\$!

# 起動待ち（固定文字列 grep）
timeout ${TIMEOUT}s bash <<'INNER'
set -e
while true; do
  if grep -F -m1 "${TARGET_LOG}" split_remote_server.log >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.2
done
INNER

echo "[INFO] ${host}: 起動OK"
EOF

  if [[ $? -ne 0 ]]; then
    echo "[WARN] ${host}: タイムアウト (${TIMEOUT}s)"
  fi
}


for entry in "${SERVERS[@]}"; do
  eval "$entry"
  start_remote_server "$host" "$id" "$ip" "$port" "$nts" &
done
wait

# node_to_starts からスタートノードを全取得（サーバ起動と同じ splits を参照）
# START_NODES_LIST=(${(s: :)$(
#   python3 - \
#     "base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json" \
#     "base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json" \
#   <<'PY'
# import json, sys
# starts=set()
# for path in sys.argv[1:]:
#     try:
#         data=json.load(open(path))
#     except Exception as e:
#         print(f"[WARN] failed to load {path}: {e}", file=sys.stderr)
#         continue
#     for vals in data.values():
#         if not isinstance(vals, list):
#             continue
#         for v in vals:
#             try:
#                 starts.add(int(v))
#             except Exception:
#                 pass
# print(" ".join(str(x) for x in sorted(starts)))
# PY
# )})

for start_node in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] ${start_node} ==="
  python3 base/auth-many-server/split_controller.py \
    --servers 2 \
    --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
    --start-node "${start_node}" \
    --walks ${RW_WAKLS} \
    --alpha ${ALPHA} \
    --seed 42 \
    --node-to-starts-file "base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts.json"
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

snapshot_memory "1/3: after totals (immediate)"
sleep 2
snapshot_memory "2/3: after totals (+2s)"
sleep 2
snapshot_memory "3/3: after totals (+4s)"

echo "[MEMORY] saved: ${MEM_LOG_FILE}"

