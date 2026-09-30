#!/usr/bin/env bash
# =============================================================================
# run_cluster.sh — 学習キャッシュ（adaptive）を「実際の K 台のマシン」で回す
#
#   前のキャッシュ方式（base/proposed_cache/dist/run_cluster.sh）と同じ流れ・同じ servers.conf
#   （"host id ip port" の4列、行数 = 台数）で、学習キャッシュの方策も回せるようにしたもの。
#   各ホストに SSH してサーバを起動し、手元でコントローラを動かす。
#
#   既定はノード/エッジ分離配置（PLACEMENT=split）。servers.conf の先頭 KN 台がノード担当、
#   残りがエッジ担当。キャッシュはサーバごと。方策名・配置の詳細は adaptive_policies.sh。
#
#   手順（リポジトリルートから）:
#     bash base/new-many-server/gen_servers_conf.sh --count 2          # 台数（cap11 から K 台）
#     GRAPH=vldb bash base/proposed_cache/adaptive/deploy.sh all        # 配布と確認
#     DRY_RUN=1 GRAPH=vldb bash base/proposed_cache/adaptive/run_cluster.sh   # 実行内容の表示だけ
#     GRAPH=vldb WALKS=1000 ALPHA=0.1 SEED=1 bash base/proposed_cache/adaptive/run_cluster.sh
#
#   前提（各リモートホスト側）: deploy.sh で core/・adaptive/・グラフが配布済み。
#     手元から各 host へ鍵認証 SSH が通り、各 ip:port に HTTP で届くこと。
#
#   主な環境変数は run_local.sh と同じ（K は servers.conf の行数で決まる）。加えて:
#     SERVERS_CONF     既定 base/new-many-server/servers.conf
#     REMOTE_REPO_DIR  リモート側のリポジトリルート  既定 ./（= ssh ログイン直後の home）
#     DRY_RUN=1        SSH もコントローラも実行せず、実行するコマンドを表示するだけ
#     SSH_USER         指定すると host 別名の代わりに SSH_USER@ip で接続（リモートから回すとき）
#     CAP_TOTAL  全サーバ合計のキャッシュ容量。指定すると 1台あたり CAP = CAP_TOTAL/K（台数を変えて公平に比べる用）
#
#   出力: base/proposed_cache/adaptive/results/cluster_<GRAPH>[_n<KN>e<KE>]_a<α>_w<walks>_cap<cap>[_seed<SEED>]/
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/adaptive
PC_DIR="$(cd "${SELF_DIR}/.." && pwd -P)"
ROOT="$(cd "${PC_DIR}/../.." && pwd -P)"
cd "${ROOT}"

GRAPH="${GRAPH:-vldb}"
WALKS="${WALKS:-1000}"
ALPHA="${ALPHA:-0.1}"
CAP="${CAP:-100}"
START="${START:-0}"
SEED_SUFFIX="${SEED:+_seed${SEED}}"   # SEED を指定したときだけ出力フォルダ名に付ける（従来の名前は変えない）
SEED="${SEED:-42}"
POLICIES="${POLICIES:-lru arc proposed structured}"
W="${W:-100}"
NO_BACKTRACK="${NO_BACKTRACK:-1}"   # 1 = エッジ直後の引き返しを禁止
PARTITION_DIR="${PARTITION_DIR:-}"
START_SERVER_MODE="${START_SERVER_MODE:-owner}"

SERVERS_CONF="${SERVERS_CONF:-${PC_DIR}/../new-many-server/servers.conf}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-./}"
REMOTE_SERVER_PY="${REMOTE_SERVER_PY:-base/proposed_cache/core/split_remote_server_reuse_score.py}"
CTRL_PY="base/proposed_cache/core/split_controller_reuse_score.py"
EDGES="dataset/Louvain/graph/${GRAPH}.gr"

OUT_DIR_USER="${OUT_DIR:-}"

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
# SSH_USER を指定したら、servers.conf の host 別名ではなく「SSH_USER@ip」で接続する
# （コントローラをリモートのマシンで動かすとき、そのマシンに ~/.ssh/config の別名が無くても繋がるように）
if [[ -n "${SSH_USER:-}" ]]; then
  for ((i = 0; i < ${#HOSTS[@]}; i++)); do HOSTS[i]="${SSH_USER}@${IPS[i]}"; done
fi
((K >= 1)) || { echo "[FATAL] servers.conf からサーバを読めません: ${SERVERS_CONF}" >&2; exit 1; }
DRY_RUN="${DRY_RUN:-0}"
# shellcheck source=adaptive_policies.sh
source "${SELF_DIR}/adaptive_policies.sh"
setup_placement "${K}" || exit 1
# 合計容量を揃えて台数を比べたいとき: CAP_TOTAL を全サーバ合計の容量として、1台あたり CAP_TOTAL/K にする
if [[ -n "${CAP_TOTAL:-}" ]]; then
  CAP=$((CAP_TOTAL / K))
  ((CAP >= 1)) || { echo "[FATAL] CAP_TOTAL=${CAP_TOTAL} が台数 K=${K} より小さい" >&2; exit 1; }
  echo "## CAP_TOTAL=${CAP_TOTAL} → 1台あたり CAP=${CAP}（K=${K}）"
fi
if [[ "${PLACEMENT}" == "split" && -n "${PARTITION_DIR}" ]]; then
  echo "[WARN] PARTITION_DIR の owner 割り当ては分離配置より優先されます。分離配置用に作った deny テーブル以外は渡さないでください" >&2
fi

ENDPOINTS=()
for ((i = 0; i < K; i++)); do ENDPOINTS+=("${IPS[i]}:${PORTS[i]}"); done
OUT_DIR="${OUT_DIR_USER:-${SELF_DIR}/results/cluster_${GRAPH}${PL_SUFFIX}_a${ALPHA}_w${WALKS}_cap${CAP}${SEED_SUFFIX}}"
LOG_DIR="${OUT_DIR}/logs"
mkdir -p "${LOG_DIR}"

echo "============================================================"
echo "## 単一プール提案手法 (cluster)  graph=${GRAPH} K=${K}"
echo "## walks=${WALKS} alpha=${ALPHA} cap=${CAP} start=${START} seed=${SEED} w=${W}"
echo "## hosts     = ${HOSTS[*]}"
echo "## endpoints = ${ENDPOINTS[*]}"
echo "## partition = ${PARTITION_DIR:-modulo (deny なし)}"
echo "## placement = ${PLACEMENT}${KN:+ (KN=${KN}: ${HOSTS[*]:0:${KN}} がノード担当 / KE=${KE:-})}  batch=${BATCH}"
echo "## conf      = ${SERVERS_CONF}"
echo "============================================================"

# pkill / pgrep のパターンは先頭1文字を [ ] で囲む（例 [b]ase/...）。
# そのままだと、SSH 先でこの命令を実行しているシェル自身のコマンドラインにも一致し、
# Linux ではシェルが自分を落として後続の pkill が走らない（= 前の方策のサーバが生き残る）。
self_safe() { echo "[${1:0:1}]${1:1}"; }

kill_all() {
  [[ "${DRY_RUN}" == "1" ]] && return 0
  local p1 p2
  p1="$(self_safe "${REMOTE_SERVER_PY}")"
  p2="$(self_safe "${ADAPTIVE_SERVER_PY}")"
  for h in "${HOSTS[@]}"; do
    # 落として、消えるまで最大 30 秒待つ（消えなければ -9）
    ssh -o ConnectTimeout=30 "${h}" "pkill -f '${p1}'; pkill -f '${p2}'; \
for i in \$(seq 1 30); do pgrep -f '${p1}' >/dev/null || pgrep -f '${p2}' >/dev/null || exit 0; sleep 1; done; \
pkill -9 -f '${p1}'; pkill -9 -f '${p2}'; exit 0" >/dev/null 2>&1
  done
}

# 前のサーバがまだ応答していないか（ポートが空いたか）を確かめる
ensure_ports_free() {
  local ep i
  for ep in "${ENDPOINTS[@]}"; do
    for ((i = 0; i < 30; i++)); do
      curl -fs --max-time 2 "http://${ep}/health" >/dev/null 2>&1 || continue 2
      sleep 1
    done
    echo "[FATAL] ${ep} で前のサーバがまだ動いています（kill できていない）。手で止めてください" >&2
    return 1
  done
}

# 起動したサーバが「今回の方策・容量で、まだ何も処理していない」新しいサーバかを確かめる。
# 古いサーバが残っていると、別の方策のはずなのに最初の方策の結果が並ぶ（2026-09-30 に実際に起きた）
verify_servers() {
  local exp="$1" ep got gp gc gn
  for ep in "${ENDPOINTS[@]}"; do
    got="$(curl -fs --max-time 30 "http://${ep}/access_stats" | python3 -c 'import sys, json
d = json.load(sys.stdin)
print(d.get("auth_cache_policy"), d.get("auth_cache_capacity"),
      int(d.get("auth_cache_hit", 0)) + int(d.get("auth_cache_miss", 0)))' 2>/dev/null)" || got="? ? ?"
    read -r gp gc gn <<<"${got}"
    if [[ "${gp}" != "${exp}" || ( "${gc}" != "None" && "${gc}" != "${CAP}" ) || "${gn}" != "0" ]]; then
      echo "[FATAL] ${ep} は想定外のサーバです（policy=${gp} capacity=${gc} 処理済みアクセス=${gn}。" \
           "期待: policy=${exp} capacity=${CAP} アクセス 0）。前のサーバが残っている可能性があります" >&2
      return 1
    fi
  done
  echo "  [verify] 全 ${#ENDPOINTS[@]} 台が policy=${exp} capacity=${CAP} の新しいサーバであることを確認"
}
trap kill_all EXIT

wait_health() {
  local ep="$1" ok=0 i=0
  while ((i < ${HEALTH_TIMEOUT:-900})); do   # 大きいグラフは起動（グラフ読み込み）に数分かかる
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

  # 学習キャッシュ / 分離配置は adaptive のサーバ・コントローラで回す（それ以外は従来どおり）
  local srv_py="${REMOTE_SERVER_PY}" adaptive=0 cp_flag="--cache-policy ${ctrl_policy}"
  # belady: サーバは LRU でアクセス列を記録し、コントローラが終了後に最適解を計算する
  local lae=0 bel_args=""
  if [[ "${policy}" == "belady" ]]; then
    ctrl_policy="lru"; cp_flag="--cache-policy lru"; lae=1
    bel_args="--belady --cache-capacity ${CAP}"
  fi
  if uses_adaptive "${policy}"; then
    adaptive=1
    srv_py="${ADAPTIVE_SERVER_PY}"
    if is_adaptive_policy "${policy}"; then
      cp_flag="$(adaptive_policy_flags "${policy}")" || return 1
    fi
    extra="${extra} ${SPLIT_ARGS}"
  fi

  echo ""
  echo ">>> [POLICY] ${policy}  (controller policy=${ctrl_policy})"
  if [[ "${DRY_RUN}" == "1" ]]; then
    for ((i = 0; i < K; i++)); do
      local nts=""
      [[ -n "${PARTITION_DIR}" ]] && nts="--node-to-starts-file ${PARTITION_DIR}/node_to_starts_server${IDS[i]}.json"
      echo "[DRY] ssh ${HOSTS[i]}: cd ${REMOTE_REPO_DIR} && LOG_ACCESS_EVENTS=${lae} START=${START} NO_BACKTRACK=${NO_BACKTRACK} python3 ${srv_py}" \
           "--edges ${EDGES} --server-id ${IDS[i]} --server-count ${K} --server-endpoints ${ENDPOINTS[*]}" \
           "--host ${IPS[i]} --port ${PORTS[i]} --owned-hints-only --request-timeout 30000" \
           "${cp_flag} --cache-capacity ${CAP} ${extra} ${nts}"
    done
    if ((adaptive)); then
      echo "[DRY] python3 ${ADAPTIVE_CTRL_PY} --servers ${K} ... --walks ${WALKS} --batch-walks ${BATCH} --tag ${policy} ${SPLIT_ARGS} ${bel_args} ${CTRL_EXTRA}"
    else
      echo "[DRY] python3 ${CTRL_PY} --servers ${K} ... --walks ${WALKS} --cache-policy ${ctrl_policy}"
    fi
    return 0
  fi
  kill_all
  sleep 2
  ensure_ports_free || return 1

  local pids=()
  for ((i = 0; i < K; i++)); do
    local nts=""
    [[ -n "${PARTITION_DIR}" ]] && nts="--node-to-starts-file ${PARTITION_DIR}/node_to_starts_server${IDS[i]}.json"
    local rlog="base/proposed_cache/adaptive/results/remote_${policy}_server${IDS[i]}.log"
    ssh "${HOSTS[i]}" bash <<EOF &
set -uo pipefail
cd ${REMOTE_REPO_DIR}
mkdir -p "\$(dirname ${rlog})"
: > ${rlog}
(LOG_ACCESS_EVENTS=${lae} START=${START} NO_BACKTRACK=${NO_BACKTRACK} python3 ${srv_py} \
  --edges ${EDGES} \
  --server-id ${IDS[i]} \
  --server-count ${K} \
  --server-endpoints ${ENDPOINTS[*]} \
  --host ${IPS[i]} \
  --port ${PORTS[i]} \
  --owned-hints-only \
  --request-timeout 30000 \
  ${cp_flag} \
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
        ssh "${HOSTS[i]}" "cd ${REMOTE_REPO_DIR} && tail -n 20 base/proposed_cache/adaptive/results/remote_${policy}_server${IDS[i]}.log" 2>/dev/null
      done
      kill_all
      return 1
    fi
  done
  local exp_policy
  exp_policy="$(echo "${cp_flag}" | awk '{for (i = 1; i <= NF; i++) if ($i == "--cache-policy") { print $(i + 1); exit }}')"
  verify_servers "${exp_policy}" || { kill_all; return 1; }

  if ((adaptive)); then
    local nts_arg=""
    [[ -n "${PARTITION_DIR}" ]] && nts_arg="--node-to-starts-file ${PARTITION_DIR}/node_to_starts_server0.json"
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
      ${SPLIT_ARGS} ${nts_arg} ${bel_args} ${CTRL_EXTRA} \
      >"${LOG_DIR}/controller_${policy}.log" 2>&1 || return $?
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
    >"${LOG_DIR}/controller_${policy}.log" 2>&1 || return $?
  fi

  if [[ "${policy}" == "belady" ]]; then
    grep "^RESULT" "${LOG_DIR}/controller_${policy}.log" | tee -a "${OUT_DIR}/summary.tsv"
  else
  {
    for ep in "${ENDPOINTS[@]}"; do curl -s "http://${ep}/access_stats"; echo; done
  } | python3 "${PC_DIR}/dist/aggregate_stats.py" "${policy}" | tee -a "${OUT_DIR}/summary.tsv"
  fi

  kill_all
  sleep 2
}

# 足りない方策だけ回す（RESUME）・学習の飽和点を先に探す（AUTO_WALKS）。詳細は adaptive_policies.sh
run_all_policies

echo ""
echo "============================================================"
echo "## まとめ"
cat "${OUT_DIR}/summary.tsv"
echo "## 詳細 CSV/JSON: ${OUT_DIR}"
echo "============================================================"
