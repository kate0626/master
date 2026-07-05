#!/bin/zsh
# =============================================================================
# run_bgl_sweep.sh
#   β (HOP_EXP) × γ (DEG_EXP) × lw (LEARNING_WALKS) を 3 段階ずつ動かす
#   汎用スイープスクリプト。データ構造（グラフ）を変えた時の挙動差を確認する用途。
#
# デフォルト:
#   β:  0.5, 1.0, 2.0
#   γ:  0.0, 0.5, 1.0
#   lw: 0, 20, 40
#   → 1 (α, cap) あたり 27 combo
#
# 固定パラメータ (vldb_nobt の知見より「効かない」と判明):
#   θ=2  δ=1  λ=1.0
#
# 使い方 (base ディレクトリから):
#
#   # amazon0601 で alpha 3値 × cap 100 → 81 combo (推奨)
#   GRAPH=amazon0601 zsh base/proposed_cache/run_bgl_sweep.sh
#
#   # 単一 (α, cap) のみ 27 combo
#   GRAPH=amazon0601 ALPHA_LIST="0.05" CAP_LIST="100" \
#     zsh base/proposed_cache/run_bgl_sweep.sh
#
#   # 別のグラフで
#   GRAPH=fb-pages-food zsh base/proposed_cache/run_bgl_sweep.sh
#
#   # 各パラメータの値を上書き
#   GRAPH=amazon0601 BETA_LIST="1 2 3" GAMMA_LIST="0 1 2" LW_LIST="0 10 20" \
#     zsh base/proposed_cache/run_bgl_sweep.sh
#
#   # 計画だけ表示 (実行しない)
#   DRY_RUN=1 GRAPH=amazon0601 zsh base/proposed_cache/run_bgl_sweep.sh
#
# =============================================================================

set -uo pipefail

# --- スクリプト自身のパス ---
if [[ -n "${ZSH_VERSION:-}" ]]; then
  _SELF="${(%):-%x}"
elif [[ -n "${BASH_SOURCE:-}" ]]; then
  _SELF="${BASH_SOURCE[0]}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
unset _SELF
cd "${REPO_ROOT}"

# ====== 設定 ======
GRAPH="${GRAPH:-amazon0601}"               # 対象グラフ
ALPHA_LIST="${ALPHA_LIST:-0.01 0.05 0.1}"  # α リスト
CAP_LIST="${CAP_LIST:-100}"                # キャッシュ容量リスト

# 動かす 3 変数 (各 3 段階)
BETA_LIST="${BETA_LIST:-0.5 1.0 2.0}"      # β = hop 指数
GAMMA_LIST="${GAMMA_LIST:-0.0 0.5 1.0}"    # γ = 次数指数
LW_LIST="${LW_LIST:-0 20 40}"              # lw = 学習 walk 数

# 固定 (vldb_nobt で確認済み)
THETA="${THETA:-2}"
DELTA="${DELTA:-1}"
LAMBDA_FIX="${LAMBDA_FIX:-1.0}"

DRY_RUN="${DRY_RUN:-0}"

# ====== 計画表示 ======
N_ALPHA=$(echo "${ALPHA_LIST}" | wc -w | tr -d ' ')
N_CAP=$(echo "${CAP_LIST}" | wc -w | tr -d ' ')
N_BETA=$(echo "${BETA_LIST}" | wc -w | tr -d ' ')
N_GAMMA=$(echo "${GAMMA_LIST}" | wc -w | tr -d ' ')
N_LW=$(echo "${LW_LIST}" | wc -w | tr -d ' ')
N_INNER=$(( N_BETA * N_GAMMA * N_LW ))
N_OUTER=$(( N_ALPHA * N_CAP ))
N_TOTAL=$(( N_OUTER * N_INNER ))

echo "============================================================"
echo "## [run_bgl_sweep] β × γ × lw 3x3x3 sweep"
echo "## 対象グラフ        : ${GRAPH}"
echo "## ALPHA_LIST       : ${ALPHA_LIST}  (${N_ALPHA} 値)"
echo "## CAP_LIST         : ${CAP_LIST}  (${N_CAP} 値)"
echo "## BETA_LIST  (β)   : ${BETA_LIST}  (${N_BETA} 値)"
echo "## GAMMA_LIST (γ)   : ${GAMMA_LIST}  (${N_GAMMA} 値)"
echo "## LW_LIST    (lw)  : ${LW_LIST}  (${N_LW} 値)"
echo "## 固定              : θ=${THETA}, δ=${DELTA}, λ=${LAMBDA_FIX}"
echo "## 内側 combo       : ${N_INNER}"
echo "## 外側 (α × cap)   : ${N_OUTER}"
echo "## 総 run 数         : ${N_TOTAL}"
echo "## DRY_RUN           : ${DRY_RUN}"
echo "============================================================"

# ====== 実行 ======
TS="$(date '+%Y%m%d_%H%M%S')"
LOG_DIR="${SCRIPT_DIR}/results/_sweeps/bgl_${GRAPH}_${TS}"
mkdir -p "${LOG_DIR}"
MAIN_LOG="${LOG_DIR}/main.log"
: > "${MAIN_LOG}"

run_count=0
for alpha in $(echo ${ALPHA_LIST}); do
  for cap in $(echo ${CAP_LIST}); do
    run_count=$((run_count + 1))
    echo ""
    echo "============================================================"
    echo "## [${run_count}/${N_OUTER}] α=${alpha}, cap=${cap}, graph=${GRAPH}"
    echo "##   β × γ × lw = ${N_INNER} combos"
    echo "============================================================"

    if [[ "${DRY_RUN}" == "1" ]]; then
      echo "[DRY] sweep_ppr_params.sh を呼び出す予定:"
      echo "      ALPHA=${alpha} CAP=${cap} GRAPH=${GRAPH}"
      echo "      β=${BETA_LIST} γ=${GAMMA_LIST} lw=${LW_LIST}"
      continue
    fi

    # sweep_ppr_params.sh は ALPHA_OVERRIDE を解釈しないため、
    # splits.sh が読む ALPHA_OVERRIDE を export しておく
    export ALPHA_OVERRIDE="${alpha}"
    export GRAPH_OVERRIDE="${GRAPH}"
    export CACHE_CAPACITY_OVERRIDE="${cap}"
    export CACHE_POLICIES_OVERRIDE="ppr_demand"

    THETA_LIST_OVERRIDE="${THETA}" \
    DELTA_LIST_OVERRIDE="${DELTA}" \
    LAMBDA_LIST_OVERRIDE="${LAMBDA_FIX}" \
    HOP_EXP_LIST_OVERRIDE="${BETA_LIST}" \
    DEG_EXP_LIST_OVERRIDE="${GAMMA_LIST}" \
    LEARNING_WALKS_LIST_OVERRIDE="${LW_LIST}" \
    zsh "${SCRIPT_DIR}/sweep_ppr_params.sh" 2>&1 | tee -a "${MAIN_LOG}"

    # ベースライン (lru / arc / memo) も α, cap ごとに 1 度だけ実行
    # (まだその α, cap の結果が無いときだけ)
    BASE_RESULT_DIR="${SCRIPT_DIR}/results/alpha${alpha}_walks_100_capa_${cap}/${GRAPH}_nobt"
    if [[ ! -d "${BASE_RESULT_DIR}/lru_${cap}_h-0" ]]; then
      echo ""
      echo ">>> baseline (lru/arc/memo) を実行 (まだ未収集の α=${alpha}, cap=${cap})"
      ALPHA_OVERRIDE="${alpha}" \
      GRAPH_OVERRIDE="${GRAPH}" \
      CACHE_CAPACITY_OVERRIDE="${cap}" \
      CACHE_POLICIES_OVERRIDE="lru arc memo" \
      START_NODES_OVERRIDE="0" \
      zsh "${SCRIPT_DIR}/splits.sh" 2>&1 | tee -a "${MAIN_LOG}"
    else
      echo ">>> baseline (lru) は既存。スキップ。"
    fi
  done
done

# ====== 集計 ======
echo ""
echo "============================================================"
echo "## [DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## ログ: ${MAIN_LOG}"
echo "## CSV 再生成して結果確認:"
echo "##   python3 base/proposed_cache/generate_results_csv.py"
echo "##   (但し ${GRAPH}_nobt 用に拡張が必要)"
echo "============================================================"

# 簡易サマリ (各 sweep_summary.csv から auth_per_start 良い順 top-5)
echo ""
echo ">>> 各 (α, cap) のスイープ結果 top5:"
for sd in "${SCRIPT_DIR}/results/_sweeps/"*"${TS}"*/; do
  [[ -d "${sd}" ]] || continue
  csv_path="${sd}sweep_summary.csv"
  if [[ -f "${csv_path}" ]]; then
    echo ""
    echo "--- ${sd}sweep_summary.csv ---"
    head -1 "${csv_path}"
    tail -n +2 "${csv_path}" | awk -F, '$14!="nan" && $14!="NA"' | sort -t, -k14,14g | head -5
  fi
done
