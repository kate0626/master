#!/bin/bash
# localhost 2 サーバで キャッシュポリシーを比較する (memo / lru / ppr_demand)。
# 非バックトラック(NO_BACKTRACK=1, ユーザモデル準拠)で起動。controller は start ごとに
# キャッシュ reset するので 容量は start 単位。各 start の global_transition.json から
# cache hit/miss と auth 時間を集計し、ポリシー横断で比較表を出す。
#
#   bash base/proposed_cache/run_policy_compare_local.sh <GRAPH> [CAP] [WALKS] [ALPHA]
#   例: bash base/proposed_cache/run_policy_compare_local.sh vldb 100 100 0.01
#   ポリシー上書き: POLICIES="lru ppr_demand" bash ...
#   ppr_demand パラメータ: PPR_THETA=1.0 PPR_DELTA=0.5 bash ...
#   バックトラック版で見たい場合: NO_BT=0 bash ...
set -euo pipefail

GRAPH="${1:?usage: run_policy_compare_local.sh <GRAPH> [CAP] [WALKS] [ALPHA]}"
CAP="${2:-100}"
WALKS="${3:-100}"
ALPHA="${4:-0.01}"
POLICIES="${POLICIES:-memo lru ppr_demand}"
PPR_THETA="${PPR_THETA:-1.0}"
PPR_DELTA="${PPR_DELTA:-0.5}"
NO_BT="${NO_BT:-1}"            # 1=非バックトラック(モデル準拠)
NG_RATE="0.3"
PORT0=8921; PORT1=8922
EP0="127.0.0.1:${PORT0}"; EP1="127.0.0.1:${PORT1}"
STARTS=(0 1 2 3 4)

cd "$(dirname "$0")/../.."
EDGES="dataset/Louvain/graph/${GRAPH}.gr"
NTS0="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
NTS1="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
BASE_OUT="base/proposed_cache/results/policy_compare/${GRAPH}"
SERVER=base/proposed_cache/split_remote_server_proposed.py
CTRL=base/proposed_cache/split_controller_proposed.py

cleanup() { pkill -f "${SERVER}" 2>/dev/null || true; }
trap cleanup EXIT

export NO_BACKTRACK="${NO_BT}"

SUMMARY="${BASE_OUT}/SUMMARY.txt"
mkdir -p "${BASE_OUT}"; : > "${SUMMARY}"
echo "graph=${GRAPH} cap=${CAP} walks=${WALKS} alpha=${ALPHA} no_backtrack=${NO_BT} theta=${PPR_THETA} delta=${PPR_DELTA}" | tee "${SUMMARY}"
printf "%-12s %8s %8s %10s %12s\n" "policy" "hit" "miss" "hit_rate" "auth_time_s" | tee -a "${SUMMARY}"

for POLICY in ${POLICIES}; do
  OUT_DIR="${BASE_OUT}/${POLICY}"
  mkdir -p "${OUT_DIR}"
  cleanup; sleep 1

  EXTRA=""
  [[ "${POLICY}" == "ppr_demand" ]] && EXTRA="--ppr-theta ${PPR_THETA} --ppr-delta ${PPR_DELTA}"

  python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
    --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
    --cache-policy "${POLICY}" --cache-capacity "${CAP}" ${EXTRA} \
    --server-id 0 --host 127.0.0.1 --port "${PORT0}" \
    --node-to-starts-file "${NTS0}" > "${OUT_DIR}/server0.log" 2>&1 &
  python3 "${SERVER}" --server-count 2 --edges "${EDGES}" \
    --server-endpoints "${EP0}" "${EP1}" --owned-hints-only --request-timeout 30000 \
    --cache-policy "${POLICY}" --cache-capacity "${CAP}" ${EXTRA} \
    --server-id 1 --host 127.0.0.1 --port "${PORT1}" \
    --node-to-starts-file "${NTS1}" > "${OUT_DIR}/server1.log" 2>&1 &

  for ep in "${EP0}" "${EP1}"; do
    for i in $(seq 1 600); do
      curl -fs "http://${ep}/health" >/dev/null 2>&1 && break
      sleep 1
      [[ "$i" == "600" ]] && { echo "[ERROR] ${ep} timeout"; tail -20 "${OUT_DIR}"/server*.log; exit 1; }
    done
  done

  echo ">>> [${POLICY}] running ${#STARTS[@]} starts (cap=${CAP})..."
  for s in "${STARTS[@]}"; do
    python3 "${CTRL}" --servers 2 --server-endpoints "${EP0}" "${EP1}" \
      --start-node "${s}" --walks "${WALKS}" --alpha "${ALPHA}" --seed 42 \
      --request-timeout 30000 --out-dir "${OUT_DIR}" \
      --cache-policy "${POLICY}" --cache-capacity "${CAP}" --prefetch-mode none \
      > "${OUT_DIR}/ctrl_start${s}.log" 2>&1 || echo "  [WARN] start=${s} failed"
  done

  # 集計: 各 start の global_transition.json から cache hit/miss と auth 時間
  python3 - "${OUT_DIR}" "${POLICY}" "${SUMMARY}" <<'PY'
import json, sys, glob
out_dir, policy, summary = sys.argv[1], sys.argv[2], sys.argv[3]
hit=miss=0; auth=0.0
for f in glob.glob(f"{out_dir}/start=*_global_transition.json"):
    d=json.load(open(f))
    hit += int(d.get("cache hit",0)); miss += int(d.get("cache miss",0))
    auth += float(d.get("auth_time_total",0.0))
tot=hit+miss
rate = hit/tot if tot else 0.0
line = f"{policy:<12} {hit:8d} {miss:8d} {rate:10.3f} {auth:12.4f}"
print(line)
open(summary,"a").write(line+"\n")
PY
  cleanup; sleep 1
done

echo ""
echo "=== 比較表 (${SUMMARY}) ==="
cat "${SUMMARY}"
