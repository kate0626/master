#!/bin/zsh
set -euo pipefail
###############################################################################
# 実行フェーズ (任意K台) — このフォルダ内だけで自己完結して動く版
#
#   servers.conf を読んで台数 K を自動決定し、各リモートサーバを SSH で起動 →
#   /health 安定を待って controller を実行し、キャッシュポリシー別に結果を集計する。
#
# 前提 (各リモートサーバ側):
#   - リポジトリ(= home 直下に base/ dataset/ が見える形)が配置済み
#   - base/new-many-server/code/split_remote_server_reuse_score.py が存在
#   - dataset/Louvain/graph/<graph>.gr が存在
#   - base/new-many-server/data/splits/<graph>/<ng>/node_to_starts_server*.json が
#     「全 K 個」存在すること (owner_map は同一 dir の全 shard を glob して作るため)
#   - ローカルから各 host へ鍵認証 SSH が通ること
#
# 使い方:
#   cd /Users/maiko/Documents/GitHub/master-progrem
#   GRAPH_OVERRIDE=karate zsh base/new-many-server/run_rw.sh
#   GRAPH_OVERRIDE=vldb CACHE_POLICIES_OVERRIDE="reuse_score" zsh base/new-many-server/run_rw.sh
#
# 台数を増やす = servers.conf に行を足すだけ (--servers / endpoints は自動生成)。
###############################################################################

# --- 自身の絶対パス (zsh/bash 両対応) ---
if [ -n "${BASH_SOURCE:-}" ]; then
  _SELF="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  _SELF="${(%):-%x}"
else
  _SELF="$0"
fi
SELF_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF
CODE="${SELF_DIR}/code"

# ====== 起動待ち設定 ======
HEALTH_RETRY=60          # /health の最大試行回数
HEALTH_STABLE=2          # 連続OK回数でREADY扱い
HEALTH_INTERVAL=1        # 何秒おきに叩くか

# ====== グラフ / 実験パラメータ ======
GRAPH=${GRAPH_OVERRIDE:-vldb}
# リモート側(= home)から見た相対パス
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-./}"
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REMOTE_SERVER_PY="base/new-many-server/code/split_remote_server_reuse_score.py"
REMOTE_LOG="base/new-many-server/data/logs/split_remote_server.log"

RW_WALKS=10000
ALPHA=${ALPHA_OVERRIDE:-0.01}
NG_RATE="0.3"
if [[ -n "${START_NODES_OVERRIDE:-}" ]]; then
  eval "START_NODES_LIST=(${START_NODES_OVERRIDE})"
else
  START_NODES_LIST=(0)
fi

# ====== キャッシュポリシー ======
if [[ -n "${CACHE_POLICIES_OVERRIDE:-}" ]]; then
  eval "CACHE_POLICIES=(${CACHE_POLICIES_OVERRIDE})"
else
  CACHE_POLICIES=("memo")
fi
CACHE_CAPACITY=${CACHE_CAPACITY_OVERRIDE:-100}

# ppr_demand / reuse_score 用パラメータ
PPR_THETA=${PPR_THETA_OVERRIDE:-1.0}
PPR_DELTA=${PPR_DELTA_OVERRIDE:-1.0}
PPR_LAMBDA=${PPR_LAMBDA_OVERRIDE:-0.5}
PPR_PRIOR_HOP_EXP=${PPR_PRIOR_HOP_EXP_OVERRIDE:-2.0}
PPR_PRIOR_DEG_EXP=${PPR_PRIOR_DEG_EXP_OVERRIDE:-1.0}
REUSE_CF=${REUSE_CF_OVERRIDE:-5.0}
REUSE_CL=${REUSE_CL_OVERRIDE:-5.0}
REUSE_CD=${REUSE_CD_OVERRIDE:-10.0}
REUSE_RHO=${REUSE_RHO_OVERRIDE:-0.0}
REUSE_CR=${REUSE_CR_OVERRIDE:-10.0}

# admission 設定
if [[ -n "${ADMIT_HOPS_LIST_OVERRIDE:-}" ]]; then
  eval "ADMIT_HOPS_LIST=(${ADMIT_HOPS_LIST_OVERRIDE})"
else
  ADMIT_HOPS_LIST=(-0)
fi
ADMIT_EDGE_ONLY_SCOPE=${ADMIT_EDGE_ONLY_SCOPE_OVERRIDE:-false}
if [[ -n "${ADMIT_NODE_ONLY_LIST_OVERRIDE:-}" ]]; then
  eval "ADMIT_NODE_ONLY_LIST=(${ADMIT_NODE_ONLY_LIST_OVERRIDE})"
else
  ADMIT_NODE_ONLY_LIST=(false)
fi
LEARNING_WALKS=${LEARNING_WALKS_OVERRIDE:-0}
NO_BACKTRACK=${NO_BACKTRACK:-1}

# 出力サフィックス
OUT_TAG="${OUT_TAG:-}"
if [[ -z "${OUT_TAG}" && "${NO_BACKTRACK}" == "1" ]]; then
  OUT_TAG="nobt"
fi
OUT_GRAPH="${GRAPH}${OUT_TAG:+_${OUT_TAG}}"
echo ">>> [OUTPUT] results/${OUT_GRAPH}  (NO_BACKTRACK=${NO_BACKTRACK}, OUT_TAG='${OUT_TAG}')"

# ====== サーバ定義 (servers.conf から任意K台を読み込む) ======
SERVERS_CONF="${SERVERS_CONF:-${SELF_DIR}/servers.conf}"
[[ -f "${SERVERS_CONF}" ]] || { echo "[FATAL] servers.conf not found: ${SERVERS_CONF}" >&2; exit 1; }

SERVERS=()
while IFS= read -r line || [[ -n "$line" ]]; do
  # コメント/空行スキップ
  line="${line%%#*}"
  [[ -z "${line// }" ]] && continue
  # host id ip port
  read -r _host _id _ip _port <<< "${line}"
  [[ -z "${_host}" || -z "${_id}" || -z "${_ip}" || -z "${_port}" ]] && continue
  nts="base/new-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server${_id}.json"
  SERVERS+=("host=${_host} id=${_id} ip=${_ip} port=${_port} nts=${nts}")
done < "${SERVERS_CONF}"

SERVER_COUNT=${#SERVERS[@]}
(( SERVER_COUNT >= 1 )) || { echo "[FATAL] no servers parsed from ${SERVERS_CONF}" >&2; exit 1; }

SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
echo ">>> [SERVERS] ${SERVER_COUNT} 台: ${SERVER_ENDPOINTS[*]}"

# ====== 後始末 ======
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=300 "$host" "pkill -f ${REMOTE_SERVER_PY} || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ====== メモリスナップショット ======
snapshot_memory() {
  local label="$1" mem_log_file="$2"
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
print(json.dumps(m, ensure_ascii=False, indent=2) if m is not None else {"warn":"no memory field","keys":list(d.keys())})
'
      echo
    done
  } >> "${mem_log_file}" 2>&1
}

# ====== リモート起動 ======
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5 remote_cmd_base=$6
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REMOTE_REPO_DIR}
mkdir -p "\$(dirname ${REMOTE_LOG})"
: > ${REMOTE_LOG}
(${remote_cmd_base} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> ${REMOTE_LOG} 2>&1) &
PID=\$!
echo "[REMOTE] started pid=\${PID} (log=${REMOTE_LOG})"
echo "[WAIT] ${ip}:${port}: initial grace 5s..."
sleep 5
EOF
}

# ====== /health 待ち ======
wait_health() {
  local ep="$1" ok=0 i=0
  while (( i < HEALTH_RETRY )); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_STABLE})"
      (( ok >= HEALTH_STABLE )) && { echo "[READY] ${ep}"; return 0; }
    else
      ok=0
    fi
    sleep "${HEALTH_INTERVAL}"
    i=$((i+1))
  done
  echo "[ERROR] ${ep}: health check timeout"
  return 1
}

build_policy_tag() {
  local policy="$1" tag="${policy}_${CACHE_CAPACITY}"
  if [[ "${policy}" == "ppr_demand" ]]; then
    tag="${policy}_cap${CACHE_CAPACITY}_t${PPR_THETA}_d${PPR_DELTA}_l${PPR_LAMBDA}"
    [[ "${PPR_PRIOR_HOP_EXP}" != "1.0" ]] && tag="${tag}_b${PPR_PRIOR_HOP_EXP}"
    [[ "${PPR_PRIOR_DEG_EXP}" != "1.0" ]] && tag="${tag}_g${PPR_PRIOR_DEG_EXP}"
  elif [[ "${policy}" == "reuse_score" ]]; then
    tag="${policy}_cap${CACHE_CAPACITY}_t${PPR_THETA}_l${PPR_LAMBDA}_b${PPR_PRIOR_HOP_EXP}_g${PPR_PRIOR_DEG_EXP}"
    tag="${tag}_cf${REUSE_CF}_cl${REUSE_CL}_cd${REUSE_CD}_rho${REUSE_RHO}_cr${REUSE_CR}"
  fi
  tag="${tag}_h${ADMIT_MAX_HOPS}"
  [[ "${ADMIT_EDGE_ONLY_SCOPE}" == "true" ]] && tag="${tag}_edgeadmit"
  [[ "${ADMIT_NODE_ONLY}" == "true" ]] && tag="${tag}_nodeonly"
  [[ "${LEARNING_WALKS}" != "0" && -n "${LEARNING_WALKS}" ]] && tag="${tag}_lw${LEARNING_WALKS}"
  echo "${tag}"
}

# ====== 1ポリシー実行 ======
run_one_policy() {
  local CACHE_POLICY="$1"
  # この簡易ドライバは demand 系のみ対応 (none/memo/lru/arc/ppr_demand/reuse_score)。
  # prefetch 系 (bfs_prefetch/bfs_score/ppr_gdsf) は元 splits_reuse_score.sh を参照。
  case "${CACHE_POLICY}" in
    bfs_prefetch|bfs_score|ppr_gdsf)
      echo "[SKIP] policy=${CACHE_POLICY} は new-many-server の run_rw.sh では未対応 (prefetch系)。" >&2
      return 1 ;;
  esac
  local POLICY_TAG; POLICY_TAG="$(build_policy_tag "${CACHE_POLICY}")"
  local LOG_DIR="${SELF_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${OUT_GRAPH}/${POLICY_TAG}"
  mkdir -p "${LOG_DIR}"
  local LOG_FILE="${LOG_DIR}/${GRAPH}.log"; : > "${LOG_FILE}"
  local MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"; : > "${MEM_LOG_FILE}"

  {
    echo "############################################################"
    echo "## [POLICY] ${CACHE_POLICY}  capacity=${CACHE_CAPACITY}"
    echo "## [TIME ] $(date '+%Y-%m-%d %H:%M:%S')"
    echo "############################################################"

    local REMOTE_CMD_BASE="NO_BACKTRACK=${NO_BACKTRACK} python3 ${REMOTE_SERVER_PY} \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 30000 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY}"
    if [[ "${CACHE_POLICY}" == "ppr_demand" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --ppr-theta ${PPR_THETA} --ppr-delta ${PPR_DELTA} --ppr-lambda ${PPR_LAMBDA} --ppr-prior-hop-exp ${PPR_PRIOR_HOP_EXP} --ppr-prior-deg-exp ${PPR_PRIOR_DEG_EXP}"
    fi
    if [[ "${CACHE_POLICY}" == "reuse_score" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --ppr-theta ${PPR_THETA} --ppr-lambda ${PPR_LAMBDA} --ppr-prior-hop-exp ${PPR_PRIOR_HOP_EXP} --ppr-prior-deg-exp ${PPR_PRIOR_DEG_EXP} --reuse-cf ${REUSE_CF} --reuse-cl ${REUSE_CL} --reuse-cd ${REUSE_CD} --reuse-rho ${REUSE_RHO} --reuse-cr ${REUSE_CR}"
    fi
    if [[ "${ADMIT_MAX_HOPS}" != "0" && "${ADMIT_MAX_HOPS}" != "-" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-max-hops ${ADMIT_MAX_HOPS}"
    fi
    [[ "${ADMIT_EDGE_ONLY_SCOPE}" == "true" ]] && REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-edge-only-scope"
    [[ "${ADMIT_NODE_ONLY}" == "true" ]] && REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-node-only"

    echo ">>> [PRE-CLEANUP] 既存サーバプロセスを停止..."
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" "pkill -f ${REMOTE_SERVER_PY} || true" >/dev/null 2>&1 || true
    done
    sleep 2

    # ====== 起動(並列) ======
    local pids=()
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      start_remote_server "$host" "$id" "$ip" "$port" "$nts" "$REMOTE_CMD_BASE" &
      pids+=($!)
    done
    for pid in "${pids[@]}"; do wait "$pid"; done

    # ====== /health 安定待ち ======
    _health_ok=1
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      if ! wait_health "$ep"; then
        echo "[FATAL] ${ep} が READY になりません (policy=${CACHE_POLICY})。中止します。"
        _health_ok=0
      fi
    done
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      echo "[INFO] ${host}: 起動ログ(末尾20行)"
      ssh "$host" "cd ${REMOTE_REPO_DIR} && tail -n 20 ${REMOTE_LOG} || true"
    done
    if (( _health_ok == 0 )); then
      echo "[FATAL] サーバ起動に失敗。controller を実行しません。 (policy=${CACHE_POLICY})"
      cleanup
      return 1
    fi
    echo "[INFO] 全サーバ起動OK。controller 開始。 (policy=${CACHE_POLICY})"

    # ====== controller 実行 (任意K: --servers/endpoints を動的に) ======
    for start_node in "${START_NODES_LIST[@]}"; do
      echo "=== [START_NODE] ${start_node} (policy=${CACHE_POLICY}) ==="
      local OUT_PREFIX="start=${start_node}_walks=${RW_WALKS}_alpha=${ALPHA}_seed=42_${POLICY_TAG}"
      python3 "${CODE}/split_controller_reuse_score.py" \
        --servers "${SERVER_COUNT}" \
        --server-endpoints "${SERVER_ENDPOINTS[@]}" \
        --start-node "${start_node}" \
        --walks ${RW_WALKS} \
        --alpha ${ALPHA} \
        --seed 42 \
        --request-timeout 30000 \
        --out-dir "${LOG_DIR}" \
        --out-prefix "${OUT_PREFIX}" \
        --cache-policy "${CACHE_POLICY}" \
        --cache-capacity "${CACHE_CAPACITY}" \
        --learning-walks "${LEARNING_WALKS}"
    done
    echo "[DONE policy=${CACHE_POLICY}]"

    snapshot_memory "after totals [${CACHE_POLICY}]" "${MEM_LOG_FILE}"
    echo "[MEMORY] saved: ${MEM_LOG_FILE}"

    echo ">>> [POST-CLEANUP] policy=${CACHE_POLICY} のサーバを停止..."
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" "pkill -f ${REMOTE_SERVER_PY} || true" >/dev/null 2>&1 || true
    done
    sleep 3
  } 2>&1 | tee -a "${LOG_FILE}"
}

# ====== 全ポリシー × admit_hops × node_only を実行 ======
SUMMARY_DIR="${SELF_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${OUT_GRAPH}"
mkdir -p "${SUMMARY_DIR}"
SUMMARY_FILE="${SUMMARY_DIR}/all_policies_summary.log"; : > "${SUMMARY_FILE}"

echo "############################################################"
echo "## [RUN ALL] graph=${GRAPH} servers=${SERVER_COUNT} policies=${CACHE_POLICIES[*]}"
echo "## [START] $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

for ADMIT_NODE_ONLY in "${ADMIT_NODE_ONLY_LIST[@]}"; do
for ADMIT_MAX_HOPS in "${ADMIT_HOPS_LIST[@]}"; do
for policy in "${CACHE_POLICIES[@]}"; do
  echo ""
  echo "==> RUNNING policy=${policy} admit_max_hops=${ADMIT_MAX_HOPS}"
  if ! run_one_policy "${policy}"; then
    echo "[WARN] policy=${policy} h=${ADMIT_MAX_HOPS} 失敗。次へ。" | tee -a "${SUMMARY_FILE}"
    continue
  fi
  echo "[SUMMARY] policy=${policy} admit_hops=${ADMIT_MAX_HOPS} done" | tee -a "${SUMMARY_FILE}"
done
done
done

echo ""
echo "############################################################"
echo "## [ALL DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## [SUMMARY] ${SUMMARY_FILE}"
echo "############################################################"
