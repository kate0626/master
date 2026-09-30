#!/usr/bin/env bash
# =============================================================================
# run_local.sh — 学習キャッシュ（adaptive）を実サーバで回す（localhost に K プロセス）
#
#   前のキャッシュ方式（base/proposed_cache/dist/run_local.sh）と同じ使い方・同じ出力（summary.tsv の
#   RESULT 行）で、学習キャッシュの方策も回せるようにしたもの。SSH 不要。実機に投げる前の動作確認用。
#
#   既定はノード/エッジ分離配置（PLACEMENT=split）。先頭 KN 台がノード担当、残りがエッジ担当で、
#   キャッシュはサーバごと（ノード担当のキャッシュにはエッジ、エッジ担当にはノードだけが入る）。
#   方策名・配置の詳細は adaptive_policies.sh。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/adaptive/run_local.sh
#     GRAPH=vldb WALKS=1000 ALPHA=0.1 SEED=1 bash base/proposed_cache/adaptive/run_local.sh
#     K=4 KN=2 POLICIES="lru structured" bash base/proposed_cache/adaptive/run_local.sh
#     PLACEMENT=modulo POLICIES="lru arc proposed" bash base/proposed_cache/adaptive/run_local.sh   # 従来の分割
#
#   主な環境変数:
#     GRAPH      グラフ名 (dataset/Louvain/graph/<GRAPH>.gr)        既定 karate
#     K          サーバ台数 (localhost のプロセス数)                 既定 2
#     PLACEMENT  split（既定）/ modulo
#     KN         split のときのノード担当の台数                      既定 K/2（切り捨て、最低1）
#     WALKS / ALPHA / CAP / START / SEED                           既定 100 / 0.1 / 100 / 0 / 42
#     POLICIES   方策の列                                          既定 "lru arc proposed structured"
#                従来の方策（lru / arc / memo / proposed …）と学習キャッシュ（structured / structured_frozen /
#                structured_oonly / structured_edc / structured_pair / structured_prior / learned …）を混ぜられる
#     BATCH      walk を何本ずつ投げて学習曲線を記録するか          既定 100
#   関連研究の比較手法: lfu / lecar / cacheus（簡略版）/ belady（最適解。アクセス列から計算）
#     CAP_TOTAL  全サーバ合計のキャッシュ容量。指定すると 1台あたり CAP = CAP_TOTAL/K（台数を変えて公平に比べる用）
#     LEARN_ARGS 学習キャッシュへの追加引数（--learn-window 2000 など）
#     W          proposed（reuse_score）の重み w                    既定 100
#     PARTITION_DIR  deny テーブルのディレクトリ（split では分離配置用に作ったものだけ）
#     NO_BACKTRACK   1 でエッジ直後の引き返しを禁止                 既定 1
#     BASE_PORT      先頭ポート                                     既定 4100
#
#   出力: base/proposed_cache/adaptive/results/<GRAPH>_K<K>[_n<KN>e<KE>]_a<α>_w<walks>_cap<cap>[_seed<SEED>]/
#     summary.tsv（RESULT 行）、curve_<方策>_*.csv/json（バッチごとのヒット率・判断の質・重み）、logs/
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/adaptive
PC_DIR="$(cd "${SELF_DIR}/.." && pwd -P)"                     # base/proposed_cache
ROOT="$(cd "${PC_DIR}/../.." && pwd -P)"                      # リポジトリルート
cd "${ROOT}"

GRAPH="${GRAPH:-karate}"
K="${K:-2}"
WALKS="${WALKS:-100}"
ALPHA="${ALPHA:-0.1}"
CAP="${CAP:-100}"
START="${START:-0}"
SEED_SUFFIX="${SEED:+_seed${SEED}}"   # SEED を指定したときだけ出力フォルダ名に付ける（従来の名前は変えない）
SEED="${SEED:-42}"
POLICIES="${POLICIES:-lru arc proposed structured}"
W="${W:-100}"
BASE_PORT="${BASE_PORT:-4100}"
NO_BACKTRACK="${NO_BACKTRACK:-1}"   # 1 = エッジ直後の引き返しを禁止
PARTITION_DIR="${PARTITION_DIR:-}"
START_SERVER_MODE="${START_SERVER_MODE:-owner}"

EDGES="dataset/Louvain/graph/${GRAPH}.gr"
SERVER_PY="${SERVER_PY:-base/proposed_cache/core/split_remote_server_reuse_score.py}"
CTRL_PY="base/proposed_cache/core/split_controller_reuse_score.py"
# shellcheck source=adaptive_policies.sh
source "${SELF_DIR}/adaptive_policies.sh"
setup_placement "${K}" || exit 1
# 合計容量を揃えて台数を比べたいとき: CAP_TOTAL を全サーバ合計の容量として、1台あたり CAP_TOTAL/K にする
if [[ -n "${CAP_TOTAL:-}" ]]; then
  CAP=$((CAP_TOTAL / K))
  ((CAP >= 1)) || { echo "[FATAL] CAP_TOTAL=${CAP_TOTAL} が台数 K=${K} より小さい" >&2; exit 1; }
  echo "## CAP_TOTAL=${CAP_TOTAL} → 1台あたり CAP=${CAP}（K=${K}）"
fi
OUT_DIR="${OUT_DIR:-${SELF_DIR}/results/${GRAPH}_K${K}${PL_SUFFIX}_a${ALPHA}_w${WALKS}_cap${CAP}${SEED_SUFFIX}}"
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
echo "## placement = ${PLACEMENT}${KN:+ (KN=${KN} KE=${KE:-})}  batch=${BATCH}（学習キャッシュ / split のとき）"
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
  while ((i < ${HEALTH_TIMEOUT:-900})); do   # 大きいグラフは起動（グラフ読み込み）に数分かかる
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

  # 学習キャッシュ / 分離配置は adaptive のサーバ・コントローラで回す（それ以外は従来どおり）
  local srv_py="${SERVER_PY}" adaptive=0 cp_flag=(--cache-policy "${ctrl_policy}")
  # belady: サーバは LRU でアクセス列を記録し、コントローラが終了後に最適解を計算する
  local lae=0 bel_args=()
  if [[ "${policy}" == "belady" ]]; then
    ctrl_policy="lru"; cp_flag=(--cache-policy lru); lae=1
    bel_args=(--belady --cache-capacity "${CAP}")
  fi
  if uses_adaptive "${policy}"; then
    adaptive=1
    srv_py="${ADAPTIVE_SERVER_PY}"
    if is_adaptive_policy "${policy}"; then
      local af
      af="$(adaptive_policy_flags "${policy}")" || return 1
      # shellcheck disable=SC2206
      cp_flag=(${af})
    fi
    # shellcheck disable=SC2206
    extra+=(${SPLIT_ARGS})
  fi

  echo ""
  echo ">>> [POLICY] ${policy}  (controller policy=${ctrl_policy})"
  stop_servers

  for ((i = 0; i < K; i++)); do
    local nts=()
    if [[ -n "${PARTITION_DIR}" ]]; then
      nts=(--node-to-starts-file "${PARTITION_DIR}/node_to_starts_server${i}.json")
    fi
    LOG_ACCESS_EVENTS="${lae}" NO_BACKTRACK="${NO_BACKTRACK}" python3 "${srv_py}" \
      --edges "${EDGES}" \
      --server-id "${i}" \
      --server-count "${K}" \
      --server-endpoints "${ENDPOINTS[@]}" \
      --host 127.0.0.1 \
      --port "$((BASE_PORT + i))" \
      --request-timeout 30000 \
      "${cp_flag[@]}" \
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

  if ((adaptive)); then
    # walk を BATCH 本ずつ投げ、バッチごとのヒット率・判断の質・重み（学習曲線）を記録する
    local nts_arg=()
    [[ -n "${PARTITION_DIR}" ]] && nts_arg=(--node-to-starts-file "${PARTITION_DIR}/node_to_starts_server0.json")
    # shellcheck disable=SC2086
    python3 "${ADAPTIVE_CTRL_PY}" \
      --servers "${K}" \
      --server-endpoints "${ENDPOINTS[@]}" \
      --start-node "${START}" \
      --walks "${WALKS}" \
      --batch-walks "${BATCH}" \
      --alpha "${ALPHA}" \
      --seed "${SEED}" \
      --request-timeout 30000 \
      --start-server-mode "${START_SERVER_MODE}" \
      --tag "${policy}" \
      --out-dir "${OUT_DIR}" \
      ${SPLIT_ARGS} ${nts_arg[@]+"${nts_arg[@]}"} ${bel_args[@]+"${bel_args[@]}"} ${CTRL_EXTRA} \
      > "${LOG_DIR}/controller_${policy}.log" 2>&1 || return $?
  else
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
  fi

  if [[ "${policy}" == "belady" ]]; then
    grep "^RESULT" "${LOG_DIR}/controller_${policy}.log" | tee -a "${OUT_DIR}/summary.tsv"
  else
  {
    for ep in "${ENDPOINTS[@]}"; do curl -s "http://${ep}/access_stats"; echo; done
  } | python3 "${PC_DIR}/dist/aggregate_stats.py" "${policy}" | tee -a "${OUT_DIR}/summary.tsv"
  fi

  stop_servers
}

# 足りない方策だけ回す（RESUME）・学習の飽和点を先に探す（AUTO_WALKS）。詳細は adaptive_policies.sh
run_all_policies

echo ""
echo "============================================================"
echo "## まとめ"
cat "${OUT_DIR}/summary.tsv"
echo "## 詳細 CSV/JSON: ${OUT_DIR}"
echo "## サーバログ    : ${LOG_DIR}"
echo "============================================================"
