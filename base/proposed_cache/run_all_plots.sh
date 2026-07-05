#!/bin/zsh
# =============================================================================
# run_all_plots.sh
#   結果の評価 → 全パラメータの sweep プロットを一括出力するスクリプト。
#
#   実行手順:
#     1) generate_results_csv.py で最新の結果を CSV に集約
#     2) plot_param_sweep.py で 8 種類のプロットを順次生成
#     3) 最後にサマリを表示
#
# 使い方 (base ディレクトリから):
#   zsh base/proposed_cache/run_all_plots.sh
#
#   # 出力先を変える
#   OUT_DIR=/tmp/myplots zsh base/proposed_cache/run_all_plots.sh
#
#   # 特定のステップだけ実行
#   SKIP_CSV=1 zsh base/proposed_cache/run_all_plots.sh   # CSV 再生成をスキップ
# =============================================================================

set -euo pipefail

# --- スクリプト自身の絶対パス ---
if [[ -n "${ZSH_VERSION:-}" ]]; then
  _SELF="${(%):-%x}"
elif [[ -n "${BASH_SOURCE:-}" ]]; then
  _SELF="${BASH_SOURCE[0]}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
unset _SELF

# リポジトリのルート (base/proposed_cache の 2 階層上)
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
cd "${REPO_ROOT}"

CSV_GEN="${SCRIPT_DIR}/generate_results_csv.py"
PLOTTER="${SCRIPT_DIR}/plot_param_sweep.py"
GRAPH="${GRAPH:-vldb}"                       # 対象グラフ (vldb / amazon0601 ...)
OUT_DIR="${OUT_DIR:-${SCRIPT_DIR}/results/plots/${GRAPH}}"
SKIP_CSV="${SKIP_CSV:-0}"

# プロット時の固定パラメータ (グラフごとに動かしている値が違うので切り替え可能)
# 例: GRAPH=amazon0601 のときは β=2 にしないとデータ無し → SKIP される
if [[ "${GRAPH}" == "amazon0601" ]]; then
  FIX_BETA="${FIX_BETA:-2}"      # amazon は β=0.5/2.0 のみ
else
  FIX_BETA="${FIX_BETA:-1}"      # vldb は β=1 がデフォルト
fi

mkdir -p "${OUT_DIR}"
LOG_FILE="${OUT_DIR}/run_all_plots.log"
: > "${LOG_FILE}"

echo "============================================================"
echo "## [run_all_plots] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## OUT_DIR  = ${OUT_DIR}"
echo "## LOG      = ${LOG_FILE}"
echo "## SKIP_CSV = ${SKIP_CSV}"
echo "============================================================"

# --- step 1: CSV 再生成 -----------------------------------------------------
if [[ "${SKIP_CSV}" == "1" ]]; then
  echo ""
  echo ">>> [STEP 1/2] CSV 再生成をスキップ (SKIP_CSV=1)"
else
  echo ""
  echo ">>> [STEP 1/2] CSV 再生成 (graph=${GRAPH})"
  python3 "${CSV_GEN}" --graph "${GRAPH}" 2>&1 | tee -a "${LOG_FILE}"
fi

# --- step 2: プロット 8 種 --------------------------------------------------
echo ""
echo ">>> [STEP 2/2] プロット生成"

# 各プロットを 1 関数で実行 (失敗しても次に進む)
run_plot() {
  local label="$1"; shift
  echo ""
  echo "----------------------------------------------------------------"
  echo "## [PLOT] ${label}"
  echo "## cmd  : python3 ${PLOTTER} $*"
  echo "----------------------------------------------------------------"
  if python3 "${PLOTTER}" "$@" --graph "${GRAPH}" --out-dir "${OUT_DIR}" 2>&1 | tee -a "${LOG_FILE}"; then
    echo "  [OK] ${label}"
  else
    echo "  [WARN] ${label} は失敗。次のプロットへ。" | tee -a "${LOG_FILE}"
  fi
}

# --- 既存データだけで描けるプロット ------------------------------------------
run_plot "[1] gamma の影響 (alpha=0.01, cap=100)" \
  --x-param gamma \
  --theta 2 --delta 1 --lambda 1.0 --beta ${FIX_BETA} --lw 0 \
  --alpha 0.01 --cap 100

run_plot "[2] delta の影響 (alpha=0.01, cap=100)" \
  --x-param delta \
  --theta 2 --lambda 1.0 --beta ${FIX_BETA} --gamma 0 --lw 0 \
  --alpha 0.01 --cap 100

run_plot "[3] lambda の影響 (lw=20, alpha 3値比較)" \
  --x-param lambda \
  --theta 2 --delta 1 --beta ${FIX_BETA} --gamma 0 --lw 20 \
  --alpha 0.01 0.05 0.1 --cap 100

run_plot "[4] lw の影響 (alpha 3値比較)" \
  --x-param lw \
  --theta 2 --delta 1 --lambda 1.0 --beta ${FIX_BETA} --gamma 0 \
  --alpha 0.01 0.05 0.1 --cap 100

# --- スイープ後に意味のあるプロット (alpha=0.05, 0.1 のデータが必要) ---------
run_plot "[5] theta の影響 (lw=0, alpha 3値比較)" \
  --x-param theta \
  --delta 1 --lambda 1.0 --beta ${FIX_BETA} --gamma 0 --lw 0 \
  --alpha 0.01 0.05 0.1 --cap 100

run_plot "[6] theta の影響 (lw=20, alpha=0.05/0.1, cap=100/200)" \
  --x-param theta \
  --delta 1 --lambda 1.0 --beta ${FIX_BETA} --gamma 0 --lw 20 \
  --alpha 0.05 0.1 --cap 100 200

run_plot "[7] beta の影響 (alpha 3値比較)" \
  --x-param beta \
  --theta 2 --delta 1 --lambda 1.0 --gamma 0 --lw 0 \
  --alpha 0.01 0.05 0.1 --cap 100

run_plot "[8] lw の影響 (cap 比較, alpha 3値)" \
  --x-param lw \
  --theta 2 --delta 1 --lambda 1.0 --beta ${FIX_BETA} --gamma 0 \
  --alpha 0.01 0.05 0.1 --cap 100 200

# --- サマリ -----------------------------------------------------------------
echo ""
echo "============================================================"
echo "## [完了] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 生成ファイル一覧 (${OUT_DIR}):"
echo "============================================================"
ls -1 "${OUT_DIR}"/*.png 2>/dev/null | sort | nl || echo "  PNG ファイルなし"
echo ""
echo "ログ: ${LOG_FILE}"
