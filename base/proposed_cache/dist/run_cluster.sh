#!/usr/bin/env bash
# =============================================================================
# run_cluster.sh — 提案手法を「実際の K 台のマシン」で回す
#
#   run_local.sh の localhost 版と同じことを、servers.conf に書いた K 台へ
#   SSH で配って実行する。台数を増やす = servers.conf に行を足すだけ。
#
#   提案手法 = 単一プール + スコアリング式（案A）。実サーバ
#   core/split_remote_server_reuse_score.py にフラグ2つを渡すだけで、
#   専用のラッパは要らない。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/dist/run_cluster.sh
#     GRAPH=vldb WALKS=1000 ALPHA=0.1 bash base/proposed_cache/dist/run_cluster.sh
#     SERVERS_CONF=base/new-many-server/servers.conf bash .../run_cluster.sh
#
#   前提（各リモートホスト側）:
#     - リポジトリが ${REMOTE_REPO_DIR} 直下に配置済み（base/ と dataset/ が見える形）
#     - dataset/Louvain/graph/<GRAPH>.gr がある
#     - PARTITION_DIR を使う場合、node_to_starts_server{0..K-1}.json が
#       *全 K 個* 同じディレクトリにある（owner_map は同一 dir を glob して作るため）
#     - ローカルから各 host へ鍵認証 SSH が通る / 各 ip:port へ到達できる
#
#   ★ フォルダを整理し、実サーバにフラグを追加したので、実験前に各ホストへ
#      リポジトリを再配布すること（base/new-many-server/deploy.sh 相当）。
#
#   主な環境変数は run_local.sh と同じ。加えて:
#     SERVERS_CONF     サーバ定義ファイル  既定 base/proposed_cache/dist/servers.conf
#     REMOTE_REPO_DIR  リモート側のリポジトリルート  既定 ./（= ssh ログイン直後の home）
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/dist
PC_DIR="$(cd "${SELF_DIR}/.." && pwd -P)"
ROOT="$(cd "${PC_DIR}/../.." && pwd -P)"
cd "${ROOT}"

GRAPH="${GRAPH:-vldb}"
WALKS="${WALKS:-1000}"
ALPHA="${ALPHA:-0.1}"
CAP="${CAP:-100}"
START="${START:-0}"
SEED="${SEED:-42}"
POLICIES="${POLICIES:-lru arc memo proposed}"
W="${W:-100}"
NO_BACKTRACK="${NO_BACKTRACK:-1}"   # 1 = エッジ直後の引き返しを禁止
PARTITION_DIR="${PARTITION_DIR:-}"
START_SERVER_MODE="${START_SERVER_MODE:-owner}"

SERVERS_CONF="${SERVERS_CONF:-${SELF_DIR}/servers.conf}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-./}"
REMOTE_SERVER_PY="${REMOTE_SERVER_PY:-base/proposed_cache/core/split_remote_server_reuse_score.py}"
CTRL_PY="base/proposed_cache/core/split_controller_reuse_score.py"
EDGES="dataset/Louvain/graph/${GRAPH}.gr"

OUT_DIR="${OUT_DIR:-${SELF_DIR}/results/cluster_${GRAPH}_a${ALPHA}_w${WALKS}_cap${CAP}}"
LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

# --- servers.conf を読む (host id ip port) ----------------------------------
[[ -f "${SERVERS_CONF}" ]] || { echo "[FATAL] servers.conf がありません: ${SERVERS_CONF}" >&2; exit 1; }
HOSTS=(); IDS=(); IPS=(); PORTS=()
while IFS= read -r line || [[ -n "${line}" ]]; do
  line="${line%%#*}"
  [[ -z "${line// /}" ]] && continue
  read -r _host _id _ip _port <<<"${line}"
  [[ -z "${_host}" || -z "${_id}" || -z "${_ip}" || -z "${_port}" ]] && continue
  HOSTS+=("${_host}"); IDS+=("${_id}"); IPS+=("${_ip}"); PORTS+=("${_port}")
done <"${SERVERS_CONF}"

K="${#HOSTS[@]}"
((K >= 1)) || { echo "[FATAL] servers.conf からサーバを読めません: ${SERVERS_CONF}" >&2; exit 1; }

ENDPOINTS=()
for ((i = 0; i < K; i++)); do ENDPOINTS+=("${IPS[i]}:${PORTS[i]}"); done

echo "============================================================"
echo "## 単一プール提案手法 (cluster)  graph=${GRAPH} K=${K}"
echo "## walks=${WALKS} alpha=${ALPHA} cap=${CAP} start=${START} seed=${SEED} w=${W}"
echo "## hosts     = ${HOSTS[*]}"
echo "## endpoints = ${ENDPOINTS[*]}"
echo "## partition = ${PARTITION_DIR:-modulo (deny なし)}"
echo "## conf      = ${SERVERS_CONF}"
echo "============================================================"

kill_all() {
  for h in "${HOSTS[@]}"; do
    ssh -o ConnectTimeout=30 "${h}" "pkill -f ${REMOTE_SERVER_PY} || true" >/dev/null 2>&1
  done
}
trap kill_all EXIT

wait_health() {
  local ep="$1" ok=0 i=0
  while ((i < 120)); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok + 1))
      ((ok >= 2)) && return 0
    else
      ok=0
    fi
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
  local extra=""

  if [[ "${policy}" == "proposed" ]]; then
    ctrl_policy="reuse_score"
    tag="proposed_w${W}"
    extra="--size-neutral --reuse-edge-no-deg --reuse-w ${W}"
    extra="${extra} --ppr-theta 1.0 --ppr-prior-hop-exp 1.0 --ppr-prior-deg-exp 1.0"
    extra="${extra} --reuse-cf 5.0 --reuse-cd 10.0 --reuse-rho 0.0 --reuse-cr 10.0"
  fi

  echo ""
  echo ">>> [POLICY] ${policy}  (controller policy=${ctrl_policy})"
  kill_all
  sleep 2

  local pids=()
  for ((i = 0; i < K; i++)); do
    local nts=""
    [[ -n "${PARTITION_DIR}" ]] && nts="--node-to-starts-file ${PARTITION_DIR}/node_to_starts_server${IDS[i]}.json"
    local rlog="base/proposed_cache/dist/remote_${policy}_server${IDS[i]}.log"
    ssh "${HOSTS[i]}" bash <<EOF &
set -uo pipefail
cd ${REMOTE_REPO_DIR}
mkdir -p "\$(dirname ${rlog})"
: > ${rlog}
(START=${START} NO_BACKTRACK=${NO_BACKTRACK} python3 ${REMOTE_SERVER_PY} \
  --edges ${EDGES} \
  --server-id ${IDS[i]} \
  --server-count ${K} \
  --server-endpoints ${ENDPOINTS[*]} \
  --host ${IPS[i]} \
  --port ${PORTS[i]} \
  --owned-hints-only \
  --request-timeout 30000 \
  --cache-policy ${ctrl_policy} \
  --cache-capacity ${CAP} \
  ${extra} ${nts} >> ${rlog} 2>&1) &
echo "[REMOTE ${HOSTS[i]}] started pid=\$!"
sleep 5
EOF
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "${pid}"; done

  for ep in "${ENDPOINTS[@]}"; do
    if ! wait_health "${ep}"; then
      echo "[FATAL] ${policy}: ${ep} が READY になりません。リモートログを確認してください。" >&2
      for ((i = 0; i < K; i++)); do
        echo "--- ${HOSTS[i]} ---"
        ssh "${HOSTS[i]}" "cd ${REMOTE_REPO_DIR} && tail -n 20 base/proposed_cache/dist/remote_${policy}_server${IDS[i]}.log" 2>/dev/null
      done
      kill_all
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
    --out-prefix "start=${START}_walks=${WALKS}_alpha=${ALPHA}_seed=${SEED}_${tag}" \
    --cache-policy "${ctrl_policy}" \
    --cache-capacity "${CAP}" \
    --start-server-mode "${START_SERVER_MODE}" \
    >"${LOG_DIR}/controller_${policy}.log" 2>&1 || return $?

  {
    for ep in "${ENDPOINTS[@]}"; do curl -s "http://${ep}/access_stats"; echo; done
  } | python3 "${SELF_DIR}/aggregate_stats.py" "${policy}" | tee -a "${OUT_DIR}/summary.tsv"

  kill_all
  sleep 2
}

: >"${OUT_DIR}/summary.tsv"
for p in ${POLICIES}; do
  run_one "${p}" || echo "[WARN] policy=${p} 失敗。次へ。"
done

echo ""
echo "============================================================"
echo "## まとめ"
cat "${OUT_DIR}/summary.tsv"
echo "## 詳細 CSV/JSON: ${OUT_DIR}"
echo "============================================================"
