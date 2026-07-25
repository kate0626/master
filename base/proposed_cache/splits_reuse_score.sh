#!/bin/zsh
set -euo pipefail

############################################################
#  Remote server が "READY" になってから controller を実行する版
#  -- 全キャッシュポリシーを順番に回す版 --
# baseディレクトリから実行する必要あり

# デフォルト値: 200
# 上書き方法: 環境変数 CACHE_CAPACITY_OVERRIDE をセットすることで外から変更可能
# （例: CACHE_CAPACITY_OVERRIDE=30 zsh ./base/proposed_cache/splits.sh）

# # ローカルから (リモートサーバ ab06, ab11 に SSH 接続して並列実行)
# cd /Users/maiko/Documents/GitHub/master-progrem
# GRAPH_OVERRIDE=amazon0601 zsh base/proposed_cache/splits_proposed.sh
# # Amazon0601 が終わったら
# GRAPH_OVERRIDE=vldb zsh base/proposed_cache/splits_proposed.sh

# 始点距離を強く・次数を無視（純距離）
# NO_BACKTRACK=1 CACHE_POLICIES_OVERRIDE="ppr_demand" \
# GRAPH_OVERRIDE=vldb CACHE_CAPACITY_OVERRIDE=100 \
# LEARNING_WALKS_OVERRIDE=20 \
# PPR_PRIOR_HOP_EXP_OVERRIDE=3.0 \
# PPR_PRIOR_DEG_EXP_OVERRIDE=1 \
# zsh base/proposed_cache/splits.sh

# ADMIT_HOPS_LIST_OVERRIDE="100" \
# trueがエッジのみ考慮する

# NEXT>>以下で結果の確認
# cd ~/Documents/GitHub/master-progrem/base/proposed_cache
# cd ~/Documents/GitHub/master-progrem/base/proposed_cache
# python3 base/proposed_cache/compare_policy_results.py \
#   --input base/proposed_cache/results/alpha0.01_walks_100_capa_100/vldb_nobt \
#   --policies memo_100 lru_100 ppr_demand_cap100_t1.0_d0.5　ppr_demand_cap100_t1.0_d1 ppr_demand_cap100_t2.0_d0.7_l1.0_b3.0_g1_h-0_lw20\


# ここの実行からやる

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
# 環境変数で上書き可能 (sweep_ppr_params.sh から呼ばれる場合に使用)
GRAPH=${GRAPH_OVERRIDE:-amazon0601}
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
RW_WALKS=10000
# α (リスタート確率)。環境変数で上書き可: ALPHA_OVERRIDE=0.1
# 出力先が results/alpha${ALPHA}_... になるので α 別に分かれる。
ALPHA=${ALPHA_OVERRIDE:-0.01}
NG_RATE="0.3"
# 始点リスト。環境変数で上書き可: START_NODES_OVERRIDE="0 1 2 3"
if [[ -n "${START_NODES_OVERRIDE:-}" ]]; then
  eval "START_NODES_LIST=(${START_NODES_OVERRIDE})"
else
  START_NODES_LIST=(0)
fi

# ====== 全キャッシュポリシーを順番に試す ======
# 既存ポリシー (none/memo/lru/arc) と提案ポリシー (bfs_prefetch/bfs_score) を比較
# 環境変数で上書き可能: CACHE_POLICIES_OVERRIDE="lru ppr_demand" のように指定
if [[ -n "${CACHE_POLICIES_OVERRIDE:-}" ]]; then
  eval "CACHE_POLICIES=(${CACHE_POLICIES_OVERRIDE})"
else
  CACHE_POLICIES=("reuse_score")  # デフォルト: reuse_score (新提案: O^θ × L^λ × H^β × D^γ)
fi
## 全ポリシーを回す場合は以下を使用（ただし時間がかかるので注意）
## （下は実行例。誤爆防止のためコメントアウト）
# CACHE_POLICIES_OVERRIDE="none lru arc" zsh base/proposed_cache/splits_reuse_score.sh

# そして本命の ppr_demand はこのコストを一切払いません: _prefetch_* は呼ばれず、seeded=0、BFS もなし。
# 距離は walker の hops_done から取るだけ。なので「prefetch が遅い」という懸念は ppr_gdsf 固有で、p
# pr_demand では構造的に解消済み、というのが現状です。

CACHE_CAPACITY=${CACHE_CAPACITY_OVERRIDE:-100}

##TODO: ここで ppr_demand のパラメータを環境変数で上書き可能にする
# ppr_demand (本命: prefetch なし・加法スコア) のパラメータ
# 環境変数で上書き可能 (sweep_ppr_params.sh から呼ばれる場合に使用)
PPR_THETA=${PPR_THETA_OVERRIDE:-1.0}   # 構造prior の強さ (近さ×次数をどれだけ重視するか)
PPR_DELTA=${PPR_DELTA_OVERRIDE:-1.0}   # 初回ヒットの割引 (0=なし, 1 に近いほど 1-hit-wonder を排除)
PPR_LAMBDA=${PPR_LAMBDA_OVERRIDE:-0.5}   # 学習係数 λ: V に +λ·learn(e) を加える強さ (learn=Phase1観測アクセス回数)
# w_prior = (1−α)^(β·hop) × deg^γ の距離/次数の指数 (α は walk と共用なので触らない)
PPR_PRIOR_HOP_EXP=${PPR_PRIOR_HOP_EXP_OVERRIDE:-2.0}   # β: 大=始点距離を重視 (距離の減衰を急に)
PPR_PRIOR_DEG_EXP=${PPR_PRIOR_DEG_EXP_OVERRIDE:-1.0}   # γ: 小=次数を軽視 (0.5=√deg, 0=次数無視=純距離)
# === reuse_score 用の飽和定数 === (data 統計から決める。デフォルトはざっくり初期値)
REUSE_CF=${REUSE_CF_OVERRIDE:-5.0}   # C_F: freq の中央値 (vldb 周辺で 5 程度)
REUSE_CL=${REUSE_CL_OVERRIDE:-5.0}   # C_L: learn の中央値
REUSE_CD=${REUSE_CD_OVERRIDE:-10.0}  # C_D: degree の中央値 (vldb≈5, amazon0601≈15)
# recency (LRU 的最近性) 因子 R^ρ の重み。ρ=0 で現行スコアと完全一致。
REUSE_RHO=${REUSE_RHO_OVERRIDE:-0.0} # ρ: recency の指数 (0=無効, 1〜2 で最近性を重視)
REUSE_CR=${REUSE_CR_OVERRIDE:-10.0}  # C_R: recency の飽和点 (再アクセス距離の中央値想定)
# admit_max_hops: キャッシュ admission の距離閾値 (bipartite hop)
# 0=無制限(デフォルト)  4=元グラフ2hop以内のみ  6=3hop以内のみ  8=4hop以内のみ
# ADMIT_HOPS_LIST: 複数閾値を一括で回す場合に使用
# 環境変数で上書き可能: ADMIT_HOPS_LIST_OVERRIDE="0 4 6" のように指定
if [[ -n "${ADMIT_HOPS_LIST_OVERRIDE:-}" ]]; then
  eval "ADMIT_HOPS_LIST=(${ADMIT_HOPS_LIST_OVERRIDE})"
else
  ADMIT_HOPS_LIST=(-0)   # デフォルト: 閾値なし1本のみ
fi
ADMIT_MAX_HOPS=${ADMIT_HOPS_LIST[1]}
# admit_edge_only_scope: 距離 admission を edge 実体にだけ適用
# true のとき node は距離に関係なくキャッシュ対象に残す
ADMIT_EDGE_ONLY_SCOPE=${ADMIT_EDGE_ONLY_SCOPE_OVERRIDE:-false}
# admit_node_only: ノードのみキャッシュ(エッジを除外)
# ADMIT_NODE_ONLY_LIST: 複数モードを一括で回す場合に使用
# 環境変数で上書き可能: ADMIT_NODE_ONLY_LIST_OVERRIDE="false true" のように指定
if [[ -n "${ADMIT_NODE_ONLY_LIST_OVERRIDE:-}" ]]; then
  eval "ADMIT_NODE_ONLY_LIST=(${ADMIT_NODE_ONLY_LIST_OVERRIDE})"
else
  ADMIT_NODE_ONLY_LIST=(false)   # デフォルト: 通常モードのみ
fi
ADMIT_NODE_ONLY=${ADMIT_NODE_ONLY_LIST[1]}

# learning_walks: 学習フェーズのwalk数 (ppr_demand専用)
# 0=無効(デフォルト), N>0: 最初のN walksで実アクセス頻度を学習し、
# キャッシュリセット後にそのfreqをw_priorとして注入してから残りを実行。
# 例: LEARNING_WALKS_OVERRIDE=10 zsh base/proposed_cache/splits.sh
LEARNING_WALKS=${LEARNING_WALKS_OVERRIDE:-0}

# 非バックトラック (ユーザRWモデル: 認可あれば反対端へ/ダメなら戻る) を
# リモートサーバプロセスに渡す。1=有効。SSH越しに env は自動転送されないため、
# REMOTE_CMD_BASE の先頭に明示的に付与する (下の run_one_policy 参照)。
# 既定0=従来どおり(バックトラックあり)。
NO_BACKTRACK=${NO_BACKTRACK:-1}

# 出力ディレクトリのサフィックス (上書き防止)。
#   OUT_TAG=foo  → 出力先が .../<GRAPH>_foo/ になる (入力パスは <GRAPH> のまま)。
#   未指定でも NO_BACKTRACK=1 のときは自動で "nobt" を付け、
#   バックトラックあり版 (.../<GRAPH>/) を上書きしない。
OUT_TAG="${OUT_TAG:-}"
if [[ -z "${OUT_TAG}" && "${NO_BACKTRACK}" == "1" ]]; then
  OUT_TAG="nobt"
fi
OUT_GRAPH="${GRAPH}${OUT_TAG:+_${OUT_TAG}}"
echo ">>> [OUTPUT] results 配下のグラフ別ディレクトリ = ${OUT_GRAPH}  (NO_BACKTRACK=${NO_BACKTRACK}, OUT_TAG='${OUT_TAG}')"

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

# ====== サーバ定義 (servers.conf から K 台ぶん読み込み) ======
# servers.conf: 1行1台で "host id ip port"。id は 0 から連番。空行/# コメント可。
# 別ファイルを使う場合: SERVERS_CONF=/path/to/other.conf zsh splits_reuse_score.sh
# node_to_starts のファイル名 stem を差し替えたい場合 (NG deny リスト等):
#   NTS_STEM=entity_to_denied_starts zsh splits_reuse_score.sh
SERVERS_CONF="${SERVERS_CONF:-${SCRIPT_DIR}/servers.conf}"
NTS_STEM="${NTS_STEM:-node_to_starts}"
NTS_DIR="base/auth-many-server/data/splits/${GRAPH}/${NG_RATE}"

if [[ ! -f "${SERVERS_CONF}" ]]; then
  echo "[FATAL] servers.conf が見つかりません: ${SERVERS_CONF}" >&2
  exit 1
fi

# nts パスは id テンプレートから自動生成する (server<id> を付与)。
SERVERS=()
while read -r _host _id _ip _port _rest; do
  # 空行 / コメント行 skip
  [[ -z "${_host}" || "${_host}" == \#* ]] && continue
  SERVERS+=("host=${_host} id=${_id} ip=${_ip} port=${_port} nts=${NTS_DIR}/${NTS_STEM}_server${_id}.json")
done < "${SERVERS_CONF}"

if (( ${#SERVERS[@]} == 0 )); then
  echo "[FATAL] servers.conf に有効なサーバ行がありません: ${SERVERS_CONF}" >&2
  exit 1
fi

SERVER_COUNT=${#SERVERS[@]}
SERVER_ENDPOINTS=()
for entry in "${SERVERS[@]}"; do
  eval "$entry"
  SERVER_ENDPOINTS+=("${ip}:${port}")
done
SERVER_ENDPOINTS_STR="${SERVER_ENDPOINTS[*]}"
echo ">>> [SERVERS] ${SERVER_COUNT} 台 (conf=${SERVERS_CONF}): ${SERVER_ENDPOINTS_STR}"

# ====== 後始末（全ポリシー共通） ======
cleanup() {
  echo ">>> [CLEANUP] 全サーバ停止中..."
  for entry in "${SERVERS[@]}"; do
    eval "$entry"
    ssh -o ConnectTimeout=300 "$host" "pkill -f base/proposed_cache/split_remote_server_reuse_score.py || true" >/dev/null 2>&1 || true
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
  local POLICY_TAG
  POLICY_TAG="$(build_policy_tag "${CACHE_POLICY}")"
  local LOG_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${OUT_GRAPH}/${POLICY_TAG}"
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

    local REMOTE_CMD_BASE="NO_BACKTRACK=${NO_BACKTRACK} python3 base/proposed_cache/split_remote_server_reuse_score.py \
  --server-count ${SERVER_COUNT} \
  --edges ${EDGE_FILE} \
  --server-endpoints ${SERVER_ENDPOINTS_STR} \
  --owned-hints-only \
  --request-timeout 30000 \
  --cache-policy ${CACHE_POLICY} \
  --cache-capacity ${CACHE_CAPACITY}"
    # ppr_demand: 加法スコア V=max(0,freq−δ)+θ·w_prior+λ·learn の θ/δ/λ と
    #             w_prior=(1−α)^(β·hop)×deg^γ の β/γ をサーバへ渡す
    if [[ "${CACHE_POLICY}" == "ppr_demand" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --ppr-theta ${PPR_THETA} --ppr-delta ${PPR_DELTA} --ppr-lambda ${PPR_LAMBDA} --ppr-prior-hop-exp ${PPR_PRIOR_HOP_EXP} --ppr-prior-deg-exp ${PPR_PRIOR_DEG_EXP}"
    fi
    if [[ "${CACHE_POLICY}" == "reuse_score" ]]; then
      # 新提案: Score = O^θ × L^λ × H^β × D^γ
      #   θ=PPR_THETA, λ=PPR_LAMBDA, β=PPR_PRIOR_HOP_EXP, γ=PPR_PRIOR_DEG_EXP
      #   C_F=REUSE_CF, C_L=REUSE_CL, C_D=REUSE_CD
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --ppr-theta ${PPR_THETA} --ppr-lambda ${PPR_LAMBDA} --ppr-prior-hop-exp ${PPR_PRIOR_HOP_EXP} --ppr-prior-deg-exp ${PPR_PRIOR_DEG_EXP} --reuse-cf ${REUSE_CF} --reuse-cl ${REUSE_CL} --reuse-cd ${REUSE_CD} --reuse-rho ${REUSE_RHO} --reuse-cr ${REUSE_CR}"
    fi
      # admit_max_hops: 全ポリシー共通 (0 または - は無制限なので指定時のみ付加)
    if [[ "${ADMIT_MAX_HOPS}" != "0" && "${ADMIT_MAX_HOPS}" != "-" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-max-hops ${ADMIT_MAX_HOPS}"
    fi
    if [[ "${ADMIT_EDGE_ONLY_SCOPE}" == "true" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-edge-only-scope"
    fi
    # admit_node_only: ノード専用モード
    if [[ "${ADMIT_NODE_ONLY}" == "true" ]]; then
      REMOTE_CMD_BASE="${REMOTE_CMD_BASE} --admit-node-only"
    fi

    # 念のため前回の残骸を停止
    echo ">>> [PRE-CLEANUP] 既存サーバプロセスを停止..."
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      ssh -o ConnectTimeout=30 "$host" "pkill -f base/proposed_cache/split_remote_server_reuse_score.py || true" >/dev/null 2>&1 || true
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
    # 1台でも READY にならなければ、そのポリシーは中止する。
    # (以前はここで戻り値を無視していたため、サーバ障害時でも
    #  片肺のまま controller を実行し、偽の hit_rate=0.994 等の
    #  壊れた結果が results/ に混入していた。)
    _health_ok=1
    for ep in "${SERVER_ENDPOINTS[@]}"; do
      if ! wait_health "$ep"; then
        echo "[FATAL] ${ep} が READY になりません (policy=${CACHE_POLICY})。このポリシーを中止します。"
        _health_ok=0
      fi
    done

    # リモートログ末尾を表示
    for entry in "${SERVERS[@]}"; do
      eval "$entry"
      echo "[INFO] ${host}: 起動ログ（末尾20行）"
      ssh "$host" "cd ${REPO_DIR} && tail -n 20 base/auth-baseline-cache/split_remote_server.log || true"
    done

    if (( _health_ok == 0 )); then
      echo "[FATAL] サーバ起動に失敗したため controller を実行しません。全サーバを停止します。 (policy=${CACHE_POLICY})"
      cleanup
      return 1
    fi

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
    elif [[ "${CACHE_POLICY}" == "ppr_gdsf" ]]; then
      PREFETCH_ARGS=(--prefetch-mode ppr_gdsf --prefetch-capacity "${PREFETCH_CAPACITY}")
    fi

    # ====== controller 実行 ======
    for start_node in "${START_NODES_LIST[@]}"; do
      echo "=== [START_NODE] ${start_node} (policy=${CACHE_POLICY}) ==="
      local OUT_PREFIX="start=${start_node}_walks=${RW_WALKS}_alpha=${ALPHA}_seed=42_${POLICY_TAG}"
      python3 base/proposed_cache/split_controller_reuse_score.py \
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
        --learning-walks "${LEARNING_WALKS}" \
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
      ssh -o ConnectTimeout=30 "$host" "pkill -f base/proposed_cache/split_remote_server_reuse_score.py || true" >/dev/null 2>&1 || true
    done
    sleep 3

  } 2>&1 | tee -a "${LOG_FILE}"
}

build_policy_tag() {
  local policy="$1"
  local tag="${policy}_${CACHE_CAPACITY}"
  if [[ "${policy}" == "bfs_prefetch" ]]; then
    tag="${policy}_K${PREFETCH_K}"
  elif [[ "${policy}" == "bfs_score" ]]; then
    tag="${policy}_N${PREFETCH_CAPACITY}_d${PREFETCH_DECAY}"
  elif [[ "${policy}" == "ppr_gdsf" ]]; then
    tag="${policy}_N${PREFETCH_CAPACITY}"
  elif [[ "${policy}" == "ppr_demand" ]]; then
    tag="${policy}_cap${CACHE_CAPACITY}_t${PPR_THETA}_d${PPR_DELTA}_l${PPR_LAMBDA}"
    # β/γ はデフォルト(1.0)以外のときだけタグに付ける (既存比較を壊さない)
    if [[ "${PPR_PRIOR_HOP_EXP}" != "1.0" ]]; then
      tag="${tag}_b${PPR_PRIOR_HOP_EXP}"
    fi
    if [[ "${PPR_PRIOR_DEG_EXP}" != "1.0" ]]; then
      tag="${tag}_g${PPR_PRIOR_DEG_EXP}"
    fi
  elif [[ "${policy}" == "reuse_score" ]]; then
    # 新提案: O^θ × L^λ × H^β × D^γ
    # tag に θ/λ/β/γ + 飽和定数 (C_F, C_L, C_D) を埋め込み
    tag="${policy}_cap${CACHE_CAPACITY}_t${PPR_THETA}_l${PPR_LAMBDA}_b${PPR_PRIOR_HOP_EXP}_g${PPR_PRIOR_DEG_EXP}"
    tag="${tag}_cf${REUSE_CF}_cl${REUSE_CL}_cd${REUSE_CD}"
    # recency: ρ を常にタグへ反映する。
    #   ρ=0.0 も含めて別ディレクトリに分離することで、
    #   ρ スイープ (0.0/0.5/1.0/2.0) が互いに上書きされず、
    #   出力パス単体で ρ が判別できる (自己記述的)。
    #   ※ 旧来の rho 無し reuse_score 結果とはパスが変わる (別系列として残る)。
    tag="${tag}_rho${REUSE_RHO}_cr${REUSE_CR}"
  fi

  # admission 設定は 0 も含めて常に埋め込む。
  # 結果ファイルを単体で見たときに admit_hops が判別できるようにする。
  tag="${tag}_h${ADMIT_MAX_HOPS}"
  if [[ "${ADMIT_EDGE_ONLY_SCOPE}" == "true" ]]; then
    tag="${tag}_edgeadmit"
  fi
  if [[ "${ADMIT_NODE_ONLY}" == "true" ]]; then
    tag="${tag}_nodeonly"
  fi
  if [[ "${LEARNING_WALKS}" != "0" && -n "${LEARNING_WALKS}" ]]; then
    tag="${tag}_lw${LEARNING_WALKS}"
  fi
  echo "${tag}"
}

# ====== 全ポリシー × 全 admit_hops を順番に実行 ======
SUMMARY_DIR="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${OUT_GRAPH}"
mkdir -p "${SUMMARY_DIR}"
SUMMARY_FILE="${SUMMARY_DIR}/all_policies_summary.log"
: > "${SUMMARY_FILE}"

echo "############################################################"
echo "## [RUN ALL POLICIES] graph=${GRAPH} policies=${CACHE_POLICIES[*]}"
echo "## admit_hops=${ADMIT_HOPS_LIST[*]}  node_only=${ADMIT_NODE_ONLY_LIST[*]}"
echo "## [START] $(date '+%Y-%m-%d %H:%M:%S')"
echo "############################################################"

for ADMIT_NODE_ONLY in "${ADMIT_NODE_ONLY_LIST[@]}"; do
for ADMIT_MAX_HOPS in "${ADMIT_HOPS_LIST[@]}"; do
for policy in "${CACHE_POLICIES[@]}"; do
  echo ""
  echo "============================================================"
  echo "==> RUNNING policy=${policy} admit_max_hops=${ADMIT_MAX_HOPS}"
  echo "============================================================"

  # ポリシー単位で失敗しても他は続行する
  if ! run_one_policy "${policy}"; then
    echo "[WARN] policy=${policy} h=${ADMIT_MAX_HOPS} は失敗しましたが、次へ進みます。" | tee -a "${SUMMARY_FILE}"
    continue
  fi

  # サマリ収集（Length=1 / Traceback の start_node は除外したうえで集計し、n_valid で per-start 平均を計算する）
  # 提案手法用の POLICY_TAG をここでも構築
  POLICY_TAG="$(build_policy_tag "${policy}")"
  local_log="${SCRIPT_DIR}/results/alpha${ALPHA}_walks_${RW_WALKS}_capa_${CACHE_CAPACITY}/${OUT_GRAPH}/${POLICY_TAG}/${GRAPH}.log"
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
    echo "[SUMMARY] policy=${policy} admit_hops=${ADMIT_MAX_HOPS} controller_duration_sum=${ctrl}s authorization_time_sum=${auth}s n_valid=${nval} walk_per_start=${per_w}s auth_per_start=${per_a}s" | tee -a "${SUMMARY_FILE}"
  fi
done  # policy loop
done  # admit_hops loop
done  # node_only loop

echo ""
echo "############################################################"
echo "## [ALL DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## [SUMMARY] ${SUMMARY_FILE}"
echo "############################################################"
cat "${SUMMARY_FILE}" || true
