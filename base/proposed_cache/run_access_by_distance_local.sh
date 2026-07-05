#!/bin/bash
# ローカル(localhost)で 2 サーバを起動し、複数 start_node で controller を回して
# access-by-distance CSV/PNG を出す。リモート(ab06/ab11)不要のローカル検証用。
#
#   bash base/proposed_cache/run_access_by_distance_local.sh <GRAPH> [WALKS] [ALPHA]
#
# 例: bash base/proposed_cache/run_access_by_distance_local.sh vldb 100 0.01
set -euo pipefail

GRAPH="${1:?usage: run_access_by_distance_local.sh <GRAPH> [WALKS] [ALPHA]}"
WALKS="${2:-100}"
ALPHA="${3:-0.01}"
NG_RATE="0.3"
PORT0=8901
PORT1=8902
EP0="127.0.0.1:${PORT0}"
EP1="127.0.0.1:${PORT1}"
STARTS=(0 1 2 3 4)

cd "$(dirname "$0")/../.."   # repo root
REPO="$(pwd)"
EDGES="dataset/Louvain/graph/${GRAPH}.gr"
NTS0="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
NTS1="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
OUT_DIR="base/proposed_cache/results/access_locality/${GRAPH}"
mkdir -p "${OUT_DIR}"

SERVER=base/proposed_cache/split_remote_server_proposed.py
CTRL=base/proposed_cache/split_controller_proposed.py

cleanup() { pkill -f "${SERVER}" 2>/dev/null || true; }
trap cleanup EXIT
cleanup; sleep 1

echo ">>> [${GRAPH}] launching 2 local servers (none policy)..."
python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
  --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
  --cache-policy none --server-id 0 --host 127.0.0.1 --port "${PORT0}" \
  --node-to-starts-file "${NTS0}" > "${OUT_DIR}/server0.log" 2>&1 &
python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
  --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
  --cache-policy none --server-id 1 --host 127.0.0.1 --port "${PORT1}" \
  --node-to-starts-file "${NTS1}" > "${OUT_DIR}/server1.log" 2>&1 &

echo ">>> waiting for /health (large graphs take a while to load)..."
for ep in "${EP0}" "${EP1}"; do
  for i in $(seq 1 600); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then echo "  [READY] ${ep}"; break; fi
    sleep 1
    if [[ "$i" == "600" ]]; then echo "  [ERROR] ${ep} health timeout"; tail -20 "${OUT_DIR}/server0.log" "${OUT_DIR}/server1.log"; exit 1; fi
  done
done

for s in "${STARTS[@]}"; do
  echo ">>> [${GRAPH}] controller start_node=${s}"
  python3 "${CTRL}" --servers 2 --server-endpoints "${EP0}" "${EP1}" \
    --start-node "${s}" --walks "${WALKS}" --alpha "${ALPHA}" --seed 42 \
    --request-timeout 30000 --out-dir "${OUT_DIR}" \
    --cache-policy none --cache-capacity 0 2>&1 | grep -E "Avg length|access-by-distance" || true
done

echo ">>> [${GRAPH}] done. CSVs in ${OUT_DIR}"
ls -1 "${OUT_DIR}"/*_access_by_distance.csv
