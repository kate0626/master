#!/bin/zsh
# =============================================================================
# sweep_ppr_params.sh
#   ppr_demand の加法スコア V = max(0,freq-d) + th*w_prior + lam*learn,
#   w_prior = (1-alpha)^(beta*hop) * deg^gamma
#   のパラメータ (theta/delta/lambda/beta/gamma) と学習walk数 (lw) をグリッド探索。
#
#   各組み合わせごとに既存の splits.sh を子プロセスとして呼び出す
#   (= サーバ起動/controller/出力/タグ付けは splits.sh に委譲)。
#   結果タグにパラメータが埋まるので組み合わせ同士は別ディレクトリに出る。
#   最後に全組み合わせの [SUMMARY] を 1 つの CSV に集約する。
#
# ----------------------------------------------------------------------------
# 使い方 (base ディレクトリから):
#   zsh base/proposed_cache/sweep_ppr_params.sh
#
#   # まず計画だけ確認 (実行しない)
#   DRY_RUN=1 zsh base/proposed_cache/sweep_ppr_params.sh
#
#   # グリッドを変える (空白区切り文字列で各リストを上書き)
#   THETA_LIST_OVERRIDE="0 1 2" LAMBDA_LIST_OVERRIDE="0.5 1 2" \
#   HOP_EXP_LIST_OVERRIDE="1 2 3" DEG_EXP_LIST_OVERRIDE="0 0.5 1" \
#   LEARNING_WALKS_LIST_OVERRIDE="10 20" DELTA_LIST_OVERRIDE="1" \
#   GRAPH_OVERRIDE=vldb CACHE_CAPACITY_OVERRIDE=100 NO_BACKTRACK=1 \
#   zsh base/proposed_cache/sweep_ppr_params.sh
#
#   # 比較用 baseline (memo/lru/none) も最初に 1 回だけ回す
#   RUN_BASELINES=1 zsh base/proposed_cache/sweep_ppr_params.sh
# =============================================================================

set -uo pipefail   # -e は付けない: 1 組失敗しても sweep 全体は続行する

# --- スクリプト自身の絶対パス ---
if [ -n "${ZSH_VERSION:-}" ]; then
  _SELF="${(%):-%x}"
elif [ -n "${BASH_SOURCE:-}" ]; then
  _SELF="${BASH_SOURCE[0]}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF
SPLITS="${SCRIPT_DIR}/splits.sh"
if [[ ! -f "${SPLITS}" ]]; then
  echo "[ERROR] splits.sh が見つかりません: ${SPLITS}" >&2
  exit 1
fi

# =============================================================================
# 固定設定 (splits.sh へ env で渡す)
# =============================================================================
export GRAPH_OVERRIDE="${GRAPH_OVERRIDE:-vldb}"
export CACHE_CAPACITY_OVERRIDE="${CACHE_CAPACITY_OVERRIDE:-100}"
export NO_BACKTRACK="${NO_BACKTRACK:-1}"
export CACHE_POLICIES_OVERRIDE="${CACHE_POLICIES_OVERRIDE:-ppr_demand}"
export ADMIT_EDGE_ONLY_SCOPE_OVERRIDE="${ADMIT_EDGE_ONLY_SCOPE_OVERRIDE:-false}"

RUN_BASELINES="${RUN_BASELINES:-0}"   # 1 で memo/lru/none を最初に 1 回だけ実行
DRY_RUN="${DRY_RUN:-0}"               # 1 で計画表示のみ (実行しない)
RESUME="${RESUME:-0}"                 # 1 で「完了済みの組」をスキップして続きから実行
ADMIT_TAG="${ADMIT_TAG:--0}"          # RESUME 判定用の admit_hops タグ (splits.sh 既定= -0)

# RESUME 用: splits.sh の build_policy_tag (ppr_demand) を再現して完了済みか判定する。
# 完了マーカー = <tag>/<graph>.log に "[DONE policy=" 行があること。
# 注意: ADMIT_HOPS_LIST を sweep していない (既定 -0) 前提。
RESULTS_ROOT="${SCRIPT_DIR}/results/alpha0.01_walks_100_capa_${CACHE_CAPACITY_OVERRIDE}"

build_ppr_tag() {  # th dl lm be ga lw -> tag (splits.sh と同じ規則)
  local th="$1" dl="$2" lm="$3" be="$4" ga="$5" lw="$6"
  local tag="ppr_demand_cap${CACHE_CAPACITY_OVERRIDE}_t${th}_d${dl}_l${lm}"
  [[ "${be}" != "1.0" ]] && tag="${tag}_b${be}"
  [[ "${ga}" != "1.0" ]] && tag="${tag}_g${ga}"
  tag="${tag}_h${ADMIT_TAG}"
  [[ "${lw}" != "0" && -n "${lw}" ]] && tag="${tag}_lw${lw}"
  echo "${tag}"
}

combo_done() {  # th dl lm be ga lw -> 0(済) / 1(未)
  local tag; tag="$(build_ppr_tag "$@")"
  local log="${RESULTS_ROOT}/${OUT_GRAPH}/${tag}/${GRAPH_OVERRIDE}.log"
  [[ -f "${log}" ]] && grep -q '\[DONE policy=' "${log}" 2>/dev/null
}

# =============================================================================
# 探索グリッド (各リストは *_LIST_OVERRIDE="a b c" で上書き可)
# =============================================================================
parse_list() {  # $1=override値 $2..=default
  local ov="$1"; shift
  if [[ -n "${ov}" ]]; then
    eval "_PARSED=(${ov})"
  else
    _PARSED=("$@")
  fi
}

parse_list "${LEARNING_WALKS_LIST_OVERRIDE:-}" 10 20 ; LEARNING_WALKS_LIST=("${_PARSED[@]}")
parse_list "${THETA_LIST_OVERRIDE:-}"          0 2.0 ; THETA_LIST=("${_PARSED[@]}")
parse_list "${DELTA_LIST_OVERRIDE:-}"          1.0   ; DELTA_LIST=("${_PARSED[@]}")
parse_list "${LAMBDA_LIST_OVERRIDE:-}"         1.0 2.0 ; LAMBDA_LIST=("${_PARSED[@]}")
parse_list "${HOP_EXP_LIST_OVERRIDE:-}"        1.0 3.0 ; HOP_EXP_LIST=("${_PARSED[@]}")
parse_list "${DEG_EXP_LIST_OVERRIDE:-}"        1.0   ; DEG_EXP_LIST=("${_PARSED[@]}")

TOTAL=$(( ${#LEARNING_WALKS_LIST[@]} * ${#THETA_LIST[@]} * ${#DELTA_LIST[@]} \
         * ${#LAMBDA_LIST[@]} * ${#HOP_EXP_LIST[@]} * ${#DEG_EXP_LIST[@]} ))

# =============================================================================
# 出力 (sweep 集約)
# =============================================================================
OUT_TAG=""
if [[ "${NO_BACKTRACK}" == "1" ]]; then OUT_TAG="nobt"; fi
OUT_GRAPH="${GRAPH_OVERRIDE}${OUT_TAG:+_${OUT_TAG}}"
TS="$(date '+%Y%m%d_%H%M%S')"
SWEEP_DIR="${SCRIPT_DIR}/results/_sweeps/${OUT_GRAPH}_${TS}"
mkdir -p "${SWEEP_DIR}"
CSV="${SWEEP_DIR}/sweep_summary.csv"
PLAN="${SWEEP_DIR}/plan.txt"

echo "############################################################"
echo "## [SWEEP] ppr_demand パラメータ探索"
echo "## graph=${GRAPH_OVERRIDE} cap=${CACHE_CAPACITY_OVERRIDE} no_backtrack=${NO_BACKTRACK}"
echo "## policies=${CACHE_POLICIES_OVERRIDE}"
echo "## lw    = ${LEARNING_WALKS_LIST[*]}"
echo "## theta = ${THETA_LIST[*]}    delta=${DELTA_LIST[*]}    lambda=${LAMBDA_LIST[*]}"
echo "## beta  = ${HOP_EXP_LIST[*]}    gamma=${DEG_EXP_LIST[*]}"
echo "## 組み合わせ総数 = ${TOTAL}   RESUME=${RESUME} (1=完了済みをスキップ)"
echo "## 集約先 = ${CSV}"
echo "############################################################"

{
  echo "sweep generated ${TS}"
  echo "graph=${GRAPH_OVERRIDE} cap=${CACHE_CAPACITY_OVERRIDE} no_backtrack=${NO_BACKTRACK} policies=${CACHE_POLICIES_OVERRIDE}"
  echo "lw=${LEARNING_WALKS_LIST[*]}"
  echo "theta=${THETA_LIST[*]} delta=${DELTA_LIST[*]} lambda=${LAMBDA_LIST[*]}"
  echo "beta=${HOP_EXP_LIST[*]} gamma=${DEG_EXP_LIST[*]}"
  echo "total=${TOTAL}"
} > "${PLAN}"

echo "idx,lw,theta,delta,lambda,beta,gamma,policy,admit_hops,controller_sum_s,auth_sum_s,n_valid,walk_per_start_s,auth_per_start_s,rc" > "${CSV}"

# =============================================================================
# 1 組実行 -> 出力ログから [SUMMARY] を抽出して CSV へ
# =============================================================================
run_combo() {
  local idx="$1" lw="$2" th="$3" dl="$4" lm="$5" be="$6" ga="$7"
  local combo_log="${SWEEP_DIR}/combo_$(printf '%03d' "${idx}").log"

  echo ""
  echo "=== [${idx}/${TOTAL}] lw=${lw} theta=${th} delta=${dl} lambda=${lm} beta=${be} gamma=${ga} ==="

  # RESUME: 既に完了している組はスキップ (途中で止まった続きから実行)
  if [[ "${RESUME}" == "1" ]] && combo_done "${th}" "${dl}" "${lm}" "${be}" "${ga}" "${lw}"; then
    echo "    [SKIP] 完了済み: $(build_ppr_tag "${th}" "${dl}" "${lm}" "${be}" "${ga}" "${lw}")"
    echo "${idx},${lw},${th},${dl},${lm},${be},${ga},ppr_demand,SKIP,done,done,done,done,done,0" >> "${CSV}"
    return
  fi

  export LEARNING_WALKS_OVERRIDE="${lw}"
  export PPR_THETA_OVERRIDE="${th}"
  export PPR_DELTA_OVERRIDE="${dl}"
  export PPR_LAMBDA_OVERRIDE="${lm}"
  export PPR_PRIOR_HOP_EXP_OVERRIDE="${be}"
  export PPR_PRIOR_DEG_EXP_OVERRIDE="${ga}"

  local rc=0
  zsh "${SPLITS}" > "${combo_log}" 2>&1 || rc=$?
  if [[ ${rc} -ne 0 ]]; then
    echo "    [WARN] combo rc=${rc} (続行)。詳細: ${combo_log}"
  fi

  # [SUMMARY] policy=X admit_hops=Y controller_duration_sum=Zs authorization_time_sum=Ws n_valid=N walk_per_start=Ps auth_per_start=Qs
  grep -E '^\[SUMMARY\]' "${combo_log}" 2>/dev/null | while IFS= read -r line; do
    local pol ah ctrl auth nval pw pa
    pol=$(echo "${line}"  | sed -n 's/.*policy=\([^ ]*\).*/\1/p')
    ah=$(echo "${line}"   | sed -n 's/.*admit_hops=\([^ ]*\).*/\1/p')
    ctrl=$(echo "${line}" | sed -n 's/.*controller_duration_sum=\([0-9.]*\)s.*/\1/p')
    auth=$(echo "${line}" | sed -n 's/.*authorization_time_sum=\([0-9.]*\)s.*/\1/p')
    nval=$(echo "${line}" | sed -n 's/.*n_valid=\([0-9]*\).*/\1/p')
    pw=$(echo "${line}"   | sed -n 's/.*walk_per_start=\([0-9.nan]*\)s.*/\1/p')
    pa=$(echo "${line}"   | sed -n 's/.*auth_per_start=\([0-9.nan]*\)s.*/\1/p')
    echo "${idx},${lw},${th},${dl},${lm},${be},${ga},${pol},${ah},${ctrl},${auth},${nval},${pw},${pa},${rc}" >> "${CSV}"
  done
  if ! grep -qE '^\[SUMMARY\]' "${combo_log}" 2>/dev/null; then
    echo "${idx},${lw},${th},${dl},${lm},${be},${ga},${CACHE_POLICIES_OVERRIDE},NA,NA,NA,0,nan,nan,${rc}" >> "${CSV}"
  fi
}

# =============================================================================
# (任意) baseline を最初に 1 回だけ
# =============================================================================
if [[ "${RUN_BASELINES}" == "1" && "${DRY_RUN}" != "1" ]]; then
  echo ""
  echo "=== [BASELINE] memo / lru / none を 1 回実行 ==="
  CACHE_POLICIES_OVERRIDE="memo lru none" zsh "${SPLITS}" \
    > "${SWEEP_DIR}/baseline.log" 2>&1 || echo "    [WARN] baseline 失敗 (続行)"
fi

# =============================================================================
# グリッド実行 (ネストループ)
# =============================================================================
idx=0
for lw in "${LEARNING_WALKS_LIST[@]}"; do
 for th in "${THETA_LIST[@]}"; do
  for dl in "${DELTA_LIST[@]}"; do
   for lm in "${LAMBDA_LIST[@]}"; do
    for be in "${HOP_EXP_LIST[@]}"; do
     for ga in "${DEG_EXP_LIST[@]}"; do
       idx=$(( idx + 1 ))
       if [[ "${DRY_RUN}" == "1" ]]; then
         echo "[PLAN ${idx}/${TOTAL}] lw=${lw} theta=${th} delta=${dl} lambda=${lm} beta=${be} gamma=${ga}"
         continue
       fi
       run_combo "${idx}" "${lw}" "${th}" "${dl}" "${lm}" "${be}" "${ga}"
     done
    done
   done
  done
 done
done

if [[ "${DRY_RUN}" == "1" ]]; then
  echo ""
  echo ">>> DRY_RUN: ${TOTAL} 組み合わせを表示しました (実行なし)。"
  exit 0
fi

echo ""
echo "############################################################"
echo "## [SWEEP DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 集約 CSV : ${CSV}"
echo "## 個別ログ : ${SWEEP_DIR}/combo_*.log"
echo "############################################################"

echo ""
echo ">>> auth_per_start の良い順 (上位10):"
{
  head -1 "${CSV}"
  tail -n +2 "${CSV}" | awk -F, '$14!="nan" && $14!="NA"' | sort -t, -k14,14g | head -10
} | column -s, -t 2>/dev/null || cat "${CSV}"
