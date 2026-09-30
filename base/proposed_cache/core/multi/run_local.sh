#!/usr/bin/env bash
# =============================================================================
# run_local.sh — ノード/エッジ分離配置を localhost の K プロセスで回す
#
#   servers.conf の role 列から Kn / Ke を読み、K = Kn + Ke 個のサーバを
#   localhost に立てて controller を流す。SSH 不要。
#
#     server id 0 .. Kn-1      → ノードのみ所有 (role=node)
#     server id Kn .. K-1      → エッジのみ所有 (role=edge)
#
#   サーバ間の流れは既存と同じ。/walk 1回 → /continue_walk のバケツリレー →
#   /authorize は「ミス かつ 他サーバ所有」のときだけ。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/core/multi/run_local.sh
#     GRAPH=vldb WALKS=1000 bash base/proposed_cache/core/multi/run_local.sh
#     SERVERS_CONF=/path/to/other.conf bash .../run_local.sh
#
#   主な環境変数:
#     GRAPH / WALKS / ALPHA / CAP / START / SEED
#     W          スコアの重み w                       既定 100
#     POLICIES   実行するポリシー列                   既定 "lru arc memo proposed"
#     SERVERS_CONF  サーバ定義                        既定 このフォルダの servers.conf
#     BASE_PORT  先頭ポート                           既定 4300
#     NO_BACKTRACK  1 でエッジ直後の引き返しを禁止    既定 0 (= 論文条件)
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # core/multi
CORE_DIR="$(cd "${SELF_DIR}/.." && pwd -P)"                   # core
ROOT="$(cd "${CORE_DIR}/../../.." && pwd -P)"                 # リポジトリルート
cd "${ROOT}"

GRAPH="${GRAPH:-karate}"
WALKS="${WALKS:-100}"
ALPHA="${ALPHA:-0.1}"
CAP="${CAP:-100}"
START="${START:-0}"
SEED="${SEED:-42}"
W="${W:-100}"
POLICIES="${POLICIES:-lru arc memo proposed}"
BASE_PORT="${BASE_PORT:-4300}"
NO_BACKTRACK="${NO_BACKTRACK:-0}"
START_SERVER_MODE="${START_SERVER_MODE:-owner}"
SERVERS_CONF="${SERVERS_CONF:-${SELF_DIR}/servers.conf}"

EDGES="dataset/Louvain/graph/${GRAPH}.gr"
SERVER_PY="base/proposed_cache/core/multi/server.py"
CTRL_PY="base/proposed_cache/core/split_controller_reuse_score.py"
AGG_PY="base/proposed_cache/dist/aggregate_stats.py"

[[ -f "${EDGES}" ]] || { echo "[FATAL] グラフがありません: ${EDGES}" >&2; exit 1; }
[[ -f "${SERVERS_CONF}" ]] || { echo "[FATAL] servers.conf がありません: ${SERVERS_CONF}" >&2; exit 1; }

# --- servers.conf から Kn / Ke を読む（検証は partition.py に任せる） --------
read -r KN KE < <(python3 - "${SERVERS_CONF}" <<'PY'
import sys
sys.path.insert(0, "base/proposed_cache/core/multi")
from pathlib import Path
import partition as p
entries = p.parse_servers_conf(Path(sys.argv[1]))
kn, ke = p.counts_from_entries(entries)
print(kn, ke)
PY
) || { echo "[FATAL] servers.conf の検証に失敗しました" >&2; exit 1; }

K=$((KN + KE))
OUT_DIR="${OUT_DIR:-${SELF_DIR}/results/${GRAPH}_n${KN}e${KE}_a${ALPHA}_w${WALKS}_cap${CAP}}"
LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

ENDPOINTS=()
for ((i = 0; i < K; i++)); do ENDPOINTS+=("127.0.0.1:$((BASE_PORT + i))"); done

echo "============================================================"
echo "## ノード/エッジ分離配置 (localhost)  graph=${GRAPH}"
echo "## Kn=${KN} (node役: server 0..$((KN - 1)))  Ke=${KE} (edge役: server ${KN}..$((K - 1)))  K=${K}"
echo "## walks=${WALKS} alpha=${ALPHA} cap/台=${CAP} start=${START} seed=${SEED} w=${W}"
echo "## endpoints = ${ENDPOINTS[*]}"
echo "## conf      = ${SERVERS_CONF}"
echo "============================================================"

PIDS=()
stop_servers() {
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "${pid}" 2>/dev/null
    wait "${pid}" 2>/dev/null
  done
  PIDS=()
  sleep 1
}
trap stop_servers EXIT

wait_health() {
  local ep="$1" i=0
  while ((i < 180)); do
    curl -fs "http://${ep}/health" >/dev/null 2>&1 && return 0
    sleep 1
    i=$((i + 1))
  done
  echo "[ERROR] health timeout: ${ep}" >&2
  return 1
}

run_one() {
  local policy="$1"
  local tag="${policy}"
  local ctrl_policy="${policy}"
  local extra=()

  if [[ "${policy}" == "proposed" ]]; then
    ctrl_policy="reuse_score"
    tag="proposed_w${W}"
    extra=(--score-w "${W}")
  fi

  echo ""
  echo ">>> [POLICY] ${policy}  (controller policy=${ctrl_policy})"
  stop_servers

  for ((i = 0; i < K; i++)); do
    NO_BACKTRACK="${NO_BACKTRACK}" python3 "${SERVER_PY}" \
      --node-servers "${KN}" --edge-servers "${KE}" \
      --edges "${EDGES}" \
      --server-id "${i}" \
      --server-count "${K}" \
      --server-endpoints "${ENDPOINTS[@]}" \
      --host 127.0.0.1 \
      --port "$((BASE_PORT + i))" \
      --request-timeout 30000 \
      --cache-policy "${ctrl_policy}" \
      --cache-capacity "${CAP}" \
      ${extra[@]+"${extra[@]}"} \
      > "${LOG_DIR}/server${i}_${policy}.log" 2>&1 &
    PIDS+=($!)
  done

  for ep in "${ENDPOINTS[@]}"; do
    if ! wait_health "${ep}"; then
      echo "[FATAL] ${policy}: サーバが起動しませんでした。ログ: ${LOG_DIR}/server0_${policy}.log" >&2
      tail -n 20 "${LOG_DIR}/server0_${policy}.log" >&2
      stop_servers
      return 1
    fi
  done

  python3 "${CTRL_PY}" \
    --servers "${K}" \
    --server-endpoints "${ENDPOINTS[@]}" \
    --start-node "${START}" \
    --walks "${WALKS}" \
    --alpha "${ALPHA}" \
    --seed "${SEED}" \
    --request-timeout 30000 \
    --out-dir "${OUT_DIR}" \
    --out-prefix "start=${START}_walks=${WALKS}_alpha=${ALPHA}_seed=${SEED}_n${KN}e${KE}_${tag}" \
    --cache-policy "${ctrl_policy}" \
    --cache-capacity "${CAP}" \
    --start-server-mode "${START_SERVER_MODE}" \
    > "${LOG_DIR}/controller_${policy}.log" 2>&1

  {
    for ep in "${ENDPOINTS[@]}"; do curl -s "http://${ep}/access_stats"; echo; done
  } | python3 "${AGG_PY}" "${policy}" | tee -a "${OUT_DIR}/summary.tsv"

  stop_servers
}

: > "${OUT_DIR}/summary.tsv"
for p in ${POLICIES}; do
  run_one "${p}" || echo "[WARN] policy=${p} 失敗。次へ。"
done

echo ""
echo "============================================================"
echo "## まとめ  (Kn=${KN} Ke=${KE} K=${K})"
cat "${OUT_DIR}/summary.tsv"
echo "## 詳細: ${OUT_DIR}"
echo "============================================================"
