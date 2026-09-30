#!/usr/bin/env bash
# =============================================================================
# run_local.sh — 提案手法を実サーバ実装で回す（localhost に K プロセス）
#
#   提案手法 = **単一プール + スコアリング式**（案A、2026-09-08 決定）。
#   実サーバ core/split_remote_server_reuse_score.py をそのまま使い、
#   フラグ2つを足すだけで提案手法になる。ラッパは不要になった。
#
#     --size-neutral       退去優先度から /size を外す (H = last_access + w·V)
#                          これがないと占有量による型バイアスが残り、
#                          VLDB で 2.3 pt 損する（実測）。
#     --reuse-edge-no-deg  エッジのスコアから次数 D を外す
#                          （エッジ再訪に次数は逆効果。入れると VLDB −0.68 pt）
#     --reuse-w 100        スコアの重み w。VLDB / Amazon ともに 100 が最良。
#
#   SSH もクラスタも要らないので、実機に投げる前の動作確認と、
#   仮想サーバとの突き合わせはこれで行う。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/dist/run_local.sh
#     GRAPH=vldb WALKS=1000 ALPHA=0.1 bash base/proposed_cache/dist/run_local.sh
#     K=4 POLICIES="lru proposed" bash base/proposed_cache/dist/run_local.sh
#
#   主な環境変数:
#     GRAPH    グラフ名 (dataset/Louvain/graph/<GRAPH>.gr)  既定 karate
#     K        サーバ台数 (localhost のプロセス数)           既定 2
#     WALKS / ALPHA / CAP / START / SEED                     既定 100 / 0.1 / 100 / 0 / 42
#     POLICIES 実行するポリシー列                            既定 "lru arc memo proposed"
#              proposed = 提案手法 / それ以外は実サーバのポリシー名
#     W        スコアの重み w                                既定 100
#     PARTITION_DIR  node_to_starts_server*.json のディレクトリ
#                    （省略時 modulo 分割・deny なし）
#     NO_BACKTRACK   1 でエッジ直後の引き返しを禁止          既定 0 (= 論文条件)
#     START_SERVER_MODE  owner(既定) / zero(旧挙動)
#     BASE_PORT      先頭ポート                              既定 3900
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/dist
PC_DIR="$(cd "${SELF_DIR}/.." && pwd -P)"                     # base/proposed_cache
ROOT="$(cd "${PC_DIR}/../.." && pwd -P)"                      # リポジトリルート
cd "${ROOT}"

GRAPH="${GRAPH:-karate}"
K="${K:-2}"
WALKS="${WALKS:-100}"
ALPHA="${ALPHA:-0.1}"
CAP="${CAP:-100}"
START="${START:-0}"
SEED="${SEED:-42}"
POLICIES="${POLICIES:-lru arc memo proposed}"
W="${W:-100}"
BASE_PORT="${BASE_PORT:-3900}"
NO_BACKTRACK="${NO_BACKTRACK:-1}"   # 1 = エッジ直後の引き返しを禁止
PARTITION_DIR="${PARTITION_DIR:-}"
START_SERVER_MODE="${START_SERVER_MODE:-owner}"

EDGES="dataset/Louvain/graph/${GRAPH}.gr"
SERVER_PY="${SERVER_PY:-base/proposed_cache/core/split_remote_server_reuse_score.py}"
CTRL_PY="base/proposed_cache/core/split_controller_reuse_score.py"
OUT_DIR="${OUT_DIR:-${SELF_DIR}/results/${GRAPH}_K${K}_a${ALPHA}_w${WALKS}_cap${CAP}}"
LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

[[ -f "${EDGES}" ]] || { echo "[FATAL] グラフがありません: ${EDGES}" >&2; exit 1; }

ENDPOINTS=()
for ((i = 0; i < K; i++)); do ENDPOINTS+=("127.0.0.1:$((BASE_PORT + i))"); done

echo "============================================================"
echo "## 単一プール提案手法 (localhost)  graph=${GRAPH} K=${K}"
echo "## walks=${WALKS} alpha=${ALPHA} cap=${CAP} start=${START} seed=${SEED} w=${W}"
echo "## endpoints = ${ENDPOINTS[*]}"
echo "## partition = ${PARTITION_DIR:-modulo (deny なし)}"
echo "## out       = ${OUT_DIR}"
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
  while ((i < 120)); do
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
    extra=(--size-neutral --reuse-edge-no-deg --reuse-w "${W}"
           --ppr-theta 1.0
           --ppr-prior-hop-exp 1.0 --ppr-prior-deg-exp 1.0
           --reuse-cf 5.0 --reuse-cd 10.0 --reuse-rho 0.0 --reuse-cr 10.0)
  fi

  echo ""
  echo ">>> [POLICY] ${policy}  (controller policy=${ctrl_policy})"
  stop_servers

  for ((i = 0; i < K; i++)); do
    local nts=()
    if [[ -n "${PARTITION_DIR}" ]]; then
      nts=(--node-to-starts-file "${PARTITION_DIR}/node_to_starts_server${i}.json")
    fi
    NO_BACKTRACK="${NO_BACKTRACK}" python3 "${SERVER_PY}" \
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
      ${nts[@]+"${nts[@]}"} \
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
    --out-prefix "start=${START}_walks=${WALKS}_alpha=${ALPHA}_seed=${SEED}_${tag}" \
    --cache-policy "${ctrl_policy}" \
    --cache-capacity "${CAP}" \
    --start-server-mode "${START_SERVER_MODE}" \
    > "${LOG_DIR}/controller_${policy}.log" 2>&1 || return $?

  {
    for ep in "${ENDPOINTS[@]}"; do curl -s "http://${ep}/access_stats"; echo; done
  } | python3 "${SELF_DIR}/aggregate_stats.py" "${policy}" | tee -a "${OUT_DIR}/summary.tsv"

  stop_servers
}

: > "${OUT_DIR}/summary.tsv"
for p in ${POLICIES}; do
  run_one "${p}" || echo "[WARN] policy=${p} 失敗。次へ。"
done

echo ""
echo "============================================================"
echo "## まとめ"
cat "${OUT_DIR}/summary.tsv"
echo "## 詳細 CSV/JSON: ${OUT_DIR}"
echo "## サーバログ    : ${LOG_DIR}"
echo "============================================================"
