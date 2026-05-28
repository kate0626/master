#!/bin/zsh
set -euo pipefail

############################################################
#  Remote server が "READY" になってから controller を実行する版
#  -- 全キャッシュポリシーを順番に回す版 --
# baseディレクトリから実行する必要あり


# # ローカルから (リモートサーバ ab06, ab11 に SSH 接続して並列実行)
# cd /Users/maiko/Documents/GitHub/master-progrem
# GRAPH_OVERRIDE=amazon0601 zsh base/proposed_cache/splits_proposed.sh
# # Amazon0601 が終わったら
# GRAPH_OVERRIDE=vldb zsh base/proposed_cache/splits_proposed.sh
############################################################

# ====== 設定 ======
TIMEOUT=30000           # 起動待ち全体の上限（長め）
HEALTH_RETRY=60          # /health の最大試行回数（1回=0.5sなら30秒）
HEALTH_STABLE=2          # 連続OK回数（2回連続OKでREADY扱い）
HEALTH_INTERVAL=1      # 何秒おきに叩くか

# fb-caltechはうまくいかない
# karate, amazon0601, vldb はOK


# 提案手法 (bfs_prefetch / bfs_score) を含む比較実験
# ★ 対象グラフはここを書き換える (amazon0601 / vldb / karate など)
GRAPH=amazon0601
EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
REPO_DIR="./"
# --- スクリプト自身の絶対パスを取得（zsh / bash どちらでも動く） ---
if [ -n "${BASH_SOURCE:-}" ]; then
  _SELF="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
  # zsh: %x は現在のスクリプト自身のパス
  _SELF="${(%):-%x}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF
RW_WALKS=100
ALPHA=0.01
NG_RATE="0.3"
START_NODES_LIST=(0 1 2 3 4)

# ====== 全キャッシュポリシーを順番に試す ======
# 既存ポリシー (none/memo/lru/arc) と提案ポリシー (bfs_prefetch/bfs_score) を比較
CACHE_POLICIES=("bfs_prefetch" "bfs_score")
CACHE_CAPACITY=100

# 提案手法のパラメータ
# ※ node-edge 二部グラフのため BFS K は「実グラフ距離 × 2」になる
#   K=4 → 実 2 hops, K=6 → 実 3 hops 程度
#   K=10 は密グラフで爆発するので 4-6 を推奨
PREFETCH_K=4               # bfs_prefetch: BFS K-hop 範囲 (二部グラフ補正後の実 hop = K/2)
PREFETCH_CAPACITY=100      # bfs_score: 上位 N (LRU と公平比較)
PREFETCH_DECAY=0.7         # bfs_score: 距離減衰率 γ (manual モード時に使用)
PREFETCH_DECAY_MODE=data_fit  # γ 決定モード: manual | data_fit
                              #   manual:   PREFETCH_DECAY を使う
                              #   data_fit: baseline JSON の BFS距離別 attempts 分布から自動推定

# 既存 baseline 実験の出力 (bfs_score の頻度ヒントとして使う)
ATTEMPTS_HINT_DIR="${REPO_DIR}base/auth-baseline-cache/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${GRAPH}/none_100"

# ====== サーバ定義 ======
## TODO: グラフはここで参照
SERVERS=(
  "host=ab06 id=0 ip=10.58.60.6 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server0.json"
  "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/node_to_starts_server1.json"
)

# NG deny を使う時
# SERVERS=(
#   "host=ab06 id=0 ip=10.58.60.6 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/entity_to_denied_starts_server0.json"
#   "host=ab11 id=1 ip=10.58.60.11 port=3000 nts=base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}/entity_to_denied_starts_server1.json"
# )

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"

# ====== 後始末（全ポリシー共通） ======
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=300 "$host" "pkill -f base/proposed_cache/split_remote_server_proposed.py || true" >/dev/null 2>&1 || true
  done
  echo ">>> [CLEANUP] 完了。"
}
trap cleanup EXIT

# ====== メモリスナップショット ======
snapshot_memory() {
  local label="$1"
  local mem_log_file="$2"
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
  } >> "${mem_log_file}" 2>&1
}

# ====== リモート起動 ======
start_remote_server() {
  local host=$1 id=$2 ip=$3 port=$4 nts=$5 remote_cmd_base=$6
  echo "=== [${host}] サーバ起動開始 (ID=${id}) ==="

  # リモートで起動して PID を必ず表示する
  ssh "$host" bash <<EOF
set -euo pipefail
cd ${REPO_DIR}

: > base/auth-baseline-cache/split_remote_server.log

# バックグラウンド起動
(${remote_cmd_base} \
  --server-id ${id} \
  --host ${ip} \
  --port ${port} \
  --node-to-starts-file ${nts} \
  >> base/auth-baseline-cache/split_remote_server.log 2>&1) &

PID=\$!
echo "[REMOTE] started pid=\${PID} (log=base/auth-baseline-cache/split_remote_server.log)"

# 初期待機（少し長め）
echo "[WAIT] ${ip}:${port}: initial grace 5s..."
sleep 5

EOF
}

# ====== ローカルから /health 待ち ======
wait_health() {
  local ep="$1"
  local ok=0
  local i=0

  while (( i < HEALTH_RETRY )); do
    if curl -fs "http://${ep}/health" >/dev/null 2>&1; then
      ok=$((ok+1))
      echo "[WAIT] ${ep}: health ok (${ok}/${HEALTH_STABLE})"
      if (( ok >= HEALTH_STABLE )); then
        echo "[READY] ${ep}"
        return 0
      fi
    else
      ok=0
    fi
    sleep "${HEALTH_INTERVAL}"
    i=$((i+1))
  done

  echo "[ERROR] ${ep}: health check timeout"
  return 1
}

# ====== 1ポリシーぶんを実行する関数 ======
run_one_policy() {
  local CACHE_POLICY="$1"

  # 提案手法では K / N もディレクトリ名に含める
  local POLICY_TAG="${CACHE_POLICY}_${CACHE_CAPACITY}"
  if [[ "${CACHE_POLICY}" == "bfs_prefetch" ]]; then
    POLICY_TAG="${CACHE_POLICY}_K${PREFETCH_K}"
  elif [[ "${CACHE_POLICY}" == "bfs_score" ]]; then
    POLICY_TAG="${CACHE_POLICY}_N${PREFETCH_CAPACITY}_d${PREFETCH_DECAY}"
  fi
  local LOG_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${GRAPH}/${POLICY_TAG}"
  mkdir -p "${LOG_DIR}"
  local LOG_FILE="${LOG_DIR}/${GRAPH}.log"
  : > "${LOG_FILE}"
  local MEM_LOG_FILE="${LOG_DIR}/${GRAPH}.memory.log"
  : > "${MEM_LOG_FILE}"

  # このポリシーぶんの出力を tee でログに残す（サブシェル内で stdout/stderr をリダイレクト）
  {
    echo "############################################################"
    echo "## [POLICY] ${CACHE_POLICY}  capacity=${CACHE_CAPACITY}"
    echo "## [TIME ] $(date '+%Y-%m-%d %H:%M:%S')"
    echo "############################################################"

    local REMOTE_CMD_BASE="python3 base/proposed_cache/split_remote_server_proposed.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 30000 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY}"

    # 念のため前回の残骸を停止
    echo ">>> [PRE-CLEANUP] 既存サーバプロセスを停止..."
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" "pkill -f base/proposed_cache/split_remote_server_proposed.py || true" >/dev/null 2>&1 || true
    done
    sleep 2

    # ====== 起動（並列） ======
    local pids=()
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      start_remote_server "$host" "$id" "$ip" "$port" "$nts" "$REMOTE_CMD_BASE" &
      pids+=($!)
    done

    # 起動コマンド自体の完了待ち
    for pid in "${pids[@]}"; do
      wait "$pid"
    done

    # ====== /health 安定待ち ======
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      wait_health "$ep"
    done

    # リモートログ末尾を表示
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      echo "[INFO] ${host}: 起動ログ（末尾20行）"
      ssh "$host" "cd ${REPO_DIR} && tail -n 20 base/auth-baseline-cache/split_remote_server.log || true"
    done

    echo "[INFO] 全サーバ起動確認OK。controller を開始します。 (policy=${CACHE_POLICY})"

    # 提案手法の prefetch 引数を決定 (zsh で正しく word-split するため配列で構築)
    PREFETCH_ARGS=()
    if [[ "${CACHE_POLICY}" == "bfs_prefetch" ]]; then
      PREFETCH_ARGS=(--prefetch-mode bfs_prefetch --prefetch-k "${PREFETCH_K}")
    elif [[ "${CACHE_POLICY}" == "bfs_score" ]]; then
      PREFETCH_ARGS=(
        --prefetch-mode bfs_score
        --prefetch-capacity "${PREFETCH_CAPACITY}"
        --prefetch-decay "${PREFETCH_DECAY}"
        --prefetch-decay-mode "${PREFETCH_DECAY_MODE}"
      )
      if [[ -d "${ATTEMPTS_HINT_DIR}" ]]; then
        PREFETCH_ARGS+=(--prefetch-attempts-source "${ATTEMPTS_HINT_DIR}")
      fi
    fi

    # ====== controller 実行 ======
    for start_node in "${START_NODES_LIST[@]}"; do
      echo "=== [START_NODE] ${start_node} (policy=${CACHE_POLICY}) ==="
      python3 base/proposed_cache/split_controller_proposed.py \
        --servers 2 \
        --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
        --start-node "${start_node}" \
        --walks ${RW_WALKS} \
        --alpha ${ALPHA} \
        --seed 42 \
        --request-timeout 30000 \
        --out-dir "${LOG_DIR}" \
        --cache-policy "${CACHE_POLICY}" \
        --cache-capacity "${CACHE_CAPACITY}" \
        "${PREFETCH_ARGS[@]}"
    done

    echo "[DONE policy=${CACHE_POLICY}]"

    # ====== 集計（ログから） ======
    # Length=1（avg_length <= 1.001）と Traceback で stats が無い start_node を除外する。
    # n_valid_starts も計算して、後段で per-start 平均を取れるようにする。
    local agg_line
    agg_line=$(
      awk '
        BEGIN { sum_walk=0; sum_auth=0; n_valid=0; current_avg=-1 }
        /=== \[START_NODE\]/ { current_avg=-1 }
        /Avg length:/ {
          if (match($0, /Avg length:[[:space:]]+[0-9.]+/)) {
            chunk = substr($0, RSTART, RLENGTH)
            sub(/Avg length:[[:space:]]+/, "", chunk)
            current_avg = chunk + 0
          }
        }
        /Total authorization time \(sum over all servers\):/ {
          if (current_avg > 1.001) sum_auth += $(NF-1)
        }
        /Total walk time \(sum over all servers\):/ {
          if (current_avg > 1.001) { sum_walk += $(NF-1); n_valid++ }
        }
        END { printf "%.6f %.6f %d", sum_walk, sum_auth, n_valid }
      ' "${LOG_FILE}"
    )
    local controller_total=${agg_line%% *}
    local auth_total=$(echo "${agg_line}" | awk '{print $2}')
    local n_valid_starts=$(echo "${agg_line}" | awk '{print $3}')

    # per-start 平均は 0 除算を防ぐ
    local per_walk per_auth
    if [[ "${n_valid_starts}" -gt 0 ]]; then
      per_walk=$(awk -v s="${controller_total}" -v n="${n_valid_starts}" 'BEGIN{printf "%.6f", s/n}')
      per_auth=$(awk -v s="${auth_total}" -v n="${n_valid_starts}" 'BEGIN{printf "%.6f", s/n}')
    else
      per_walk="nan"
      per_auth="nan"
    fi

    echo "[TOTAL policy=${CACHE_POLICY}] controller_duration_sum=${controller_total}s (n_valid=${n_valid_starts})"
    echo "[TOTAL policy=${CACHE_POLICY}] authorization_time_sum=${auth_total}s (n_valid=${n_valid_starts})"
    echo "[TOTAL policy=${CACHE_POLICY}] walk_time_per_start=${per_walk}s auth_time_per_start=${per_auth}s"

    snapshot_memory "1/3: after totals (immediate) [${CACHE_POLICY}]" "${MEM_LOG_FILE}"
    sleep 3
    snapshot_memory "2/3: after totals (+3s) [${CACHE_POLICY}]" "${MEM_LOG_FILE}"
    sleep 3
    snapshot_memory "3/3: after totals (+6s) [${CACHE_POLICY}]" "${MEM_LOG_FILE}"

    echo "[MEMORY] saved: ${MEM_LOG_FILE}"

    # ====== このポリシーのサーバを停止（次のポリシー用にクリーンに戻す） ======
    echo ">>> [POST-CLEANUP] policy=${CACHE_POLICY} のサーバを停止..."
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" "pkill -f base/proposed_cache/split_remote_server_proposed.py || true" >/dev/null 2>&1 || true
    done
    sleep 3

  } 2>&1 | tee -a "${LOG_FILE}"
}

# ====== 全ポリシーを順番に実行 ======
SUMMARY_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${GRAPH}"
mkdir -p "${SUMMARY_DIR}"
SUMMARY_FILE="${SUMMARY_DIR}/all_policies_summary.log"
: > "${SUMMARY_FILE}"

echo "############################################################"
echo "## [RUN ALL POLICIES] graph=${GRAPH} policies=${CACHE_POLICIES[*]}"
echo "## [START] $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

for policy in "${CACHE_POLICIES[@]}"; do
  echo ""
  echo "============================================================"
  echo "==> RUNNING policy=${policy}"
  echo "============================================================"

  # ポリシー単位で失敗しても他は続行する
  if ! run_one_policy "${policy}"; then
    echo "[WARN] policy=${policy} は失敗しましたが、次のポリシーへ進みます。" | tee -a "${SUMMARY_FILE}"
    continue
  fi

  # サマリ収集（Length=1 / Traceback の start_node は除外したうえで集計し、n_valid で per-start 平均を計算する）
  # 提案手法用の POLICY_TAG をここでも構築
  POLICY_TAG="${policy}_${CACHE_CAPACITY}"
  if [[ "${policy}" == "bfs_prefetch" ]]; then
    POLICY_TAG="${policy}_K${PREFETCH_K}"
  elif [[ "${policy}" == "bfs_score" ]]; then
    POLICY_TAG="${policy}_N${PREFETCH_CAPACITY}_d${PREFETCH_DECAY}"
  fi
  local_log="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${GRAPH}/${POLICY_TAG}/${GRAPH}.log"
  if [[ -f "${local_log}" ]]; then
    agg=$(awk '
      BEGIN { sum_walk=0; sum_auth=0; n_valid=0; current_avg=-1 }
      /=== \[START_NODE\]/ { current_avg=-1 }
      /Avg length:/ {
        if (match($0, /Avg length:[[:space:]]+[0-9.]+/)) {
          chunk = substr($0, RSTART, RLENGTH)
          sub(/Avg length:[[:space:]]+/, "", chunk)
          current_avg = chunk + 0
        }
      }
      /Total authorization time \(sum over all servers\):/ { if (current_avg > 1.001) sum_auth += $(NF-1) }
      /Total walk time \(sum over all servers\):/ { if (current_avg > 1.001) { sum_walk += $(NF-1); n_valid++ } }
      END { printf "%.6f %.6f %d", sum_walk, sum_auth, n_valid }
    ' "${local_log}")
    ctrl=${agg%% *}
    auth=$(echo "${agg}" | awk '{print $2}')
    nval=$(echo "${agg}" | awk '{print $3}')
    if [[ "${nval}" -gt 0 ]]; then
      per_w=$(awk -v s="${ctrl}" -v n="${nval}" 'BEGIN{printf "%.6f", s/n}')
      per_a=$(awk -v s="${auth}" -v n="${nval}" 'BEGIN{printf "%.6f", s/n}')
    else
      per_w="nan"; per_a="nan"
    fi
    echo "[SUMMARY] policy=${policy} controller_duration_sum=${ctrl}s authorization_time_sum=${auth}s n_valid=${nval} walk_per_start=${per_w}s auth_per_start=${per_a}s" | tee -a "${SUMMARY_FILE}"
  fi
done

echo ""
echo "############################################################"
echo "## [ALL DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## [SUMMARY] ${SUMMARY_FILE}"
echo "############################################################"
cat "${SUMMARY_FILE}" || true