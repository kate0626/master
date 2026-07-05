#!/bin/bash
# localhost 2 サーバを LOG_ACCESS_EVENTS=1 で起動し、start ごとに
#   /cache/reset -> controller(/walk, cache=none) -> /access_events 取得
# を行って、到着順のキャッシュアクセス列 (walk_id, entity, hop, hit) を JSON 保存する。
# 仮説検証 (early-walker hit が最終 reuse を予測するか) のデータ収集用。
#
#   bash base/proposed_cache/run_walk_events_local.sh <GRAPH> [WALKS] [ALPHA] [STARTS...]
#   例: bash base/proposed_cache/run_walk_events_local.sh vldb 100 0.01 0 1 2 3 4
set -euo pipefail

GRAPH="${1:?usage: run_walk_events_local.sh <GRAPH> [WALKS] [ALPHA] [STARTS...]}"
WALKS="${2:-100}"
ALPHA="${3:-0.01}"
shift $(( $# >= 3 ? 3 : $# )) || true
STARTS=("$@"); [[ ${#STARTS[@]} -eq 0 ]] && STARTS=(0 1 2 3 4)

NG_RATE="0.3"
PORT0=8911; PORT1=8912
EP0="127.0.0.1:${PORT0}"; EP1="127.0.0.1:${PORT1}"

cd "$(dirname "$0")/../.."   # repo root
EDGES="dataset/Louvain/graph/${GRAPH}.gr"
NTS0="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
NTS1="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
OUT_DIR="base/proposed_cache/results/walk_events/${GRAPH}${OUT_SUFFIX:-}"
mkdir -p "${OUT_DIR}"

SERVER=base/proposed_cache/split_remote_server_proposed.py
CTRL=base/proposed_cache/split_controller_proposed.py

cleanup() { pkill -f "${SERVER}" 2>/dev/null || true; }
trap cleanup EXIT
cleanup; sleep 1

echo ">>> [${GRAPH}] launching 2 local servers (LOG_ACCESS_EVENTS=1, none policy)..."
LOG_ACCESS_EVENTS=1 python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
  --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
  --cache-policy none --server-id 0 --host 127.0.0.1 --port "${PORT0}" \
  --node-to-starts-file "${NTS0}" > "${OUT_DIR}/server0.log" 2>&1 &
LOG_ACCESS_EVENTS=1 python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
  --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
  --cache-policy none --server-id 1 --host 127.0.0.1 --port "${PORT1}" \
  --node-to-starts-file "${NTS1}" > "${OUT_DIR}/server1.log" 2>&1 &

echo ">>> waiting for /health..."
for ep in "${EP0}" "${EP1}"; do
  for i in $(seq 1 600); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then echo "  [READY] ${ep}"; break; fi
    sleep 1
    [[ "$i" == "600" ]] && { echo "  [ERROR] ${ep} timeout"; tail -20 "${OUT_DIR}"/server*.log; exit 1; }
  done
done

for s in "${STARTS[@]}"; do
  echo ">>> [${GRAPH}] start=${s}: reset -> walk -> fetch events"
  curl -fs -X POST "http://${EP0}/cache/reset" -d '{}' >/dev/null
  curl -fs -X POST "http://${EP1}/cache/reset" -d '{}' >/dev/null
  python3 "${CTRL}" --servers 2 --server-endpoints "${EP0}" "${EP1}" \
    --start-node "${s}" --walks "${WALKS}" --alpha "${ALPHA}" --seed 42 \
    --request-timeout 30000 --out-dir "${OUT_DIR}" \
    --cache-policy none --cache-capacity 0 2>&1 | grep -E "Avg length" || true
  curl -fs "http://${EP0}/access_events" -o "${OUT_DIR}/start=${s}_server0_events.json"
  curl -fs "http://${EP1}/access_events" -o "${OUT_DIR}/start=${s}_server1_events.json"
  n0=$(python3 -c "import json,sys;print(len(json.load(open('${OUT_DIR}/start=${s}_server0_events.json'))['access_events']))")
  n1=$(python3 -c "import json,sys;print(len(json.load(open('${OUT_DIR}/start=${s}_server1_events.json'))['access_events']))")
  echo "    events: server0(node)=${n0}  server1(edge)=${n1}"
done

echo ">>> [${GRAPH}] done. events in ${OUT_DIR}"
