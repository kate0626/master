#!/bin/zsh
# =============================================================================
# run_all_reuse_score_experiments.sh
#   reuse_score の全実験（Stage 1〜4）を一気に実行して CSV に集約する。
#
#   構成:
#     - ベースライン (lru, arc, memo)                     1 zshコール (3ポリシー)
#     - Stage 1: uniform baseline (θ=λ=β=γ=1)             1 ラン
#     - Stage 2: 単独因子 (O/L/H/D)                        4 ラン
#     - Stage 3: 非一様スケーリング                         9 ラン
#     - Stage 4: 1D スイープ (θ/λ/β/γ 各 4段階、βは1段階追加) 13 ラン
#     - CSV 集約
#
#   全 27 reuse_score ラン + 4 ベースライン = 約 2 時間
#
# 使い方 (base ディレクトリから):
#   zsh base/proposed_cache/run_all_reuse_score_experiments.sh
#
#   # 条件を変える (デフォルト: vldb, α=0.05, cap=100)
#   ALPHA_OVERRIDE=0.1 CACHE_CAPACITY_OVERRIDE=200 \
#     zsh base/proposed_cache/run_all_reuse_score_experiments.sh
# =============================================================================

set -uo pipefail

# ---------- 実験条件 ----------
ALPHA="${ALPHA_OVERRIDE:-0.05}"
CAP="${CACHE_CAPACITY_OVERRIDE:-100}"
GRAPH="${GRAPH_OVERRIDE:-vldb}"
START="${START_NODES_OVERRIDE:-0}"

# スクリプト自身のパス
if [[ -n "${ZSH_VERSION:-}" ]]; then
  _SELF="${(%):-%x}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
cd "${REPO_ROOT}"

echo "============================================================"
echo "## [run_all_reuse_score_experiments]"
echo "## GRAPH  = ${GRAPH}"
echo "## ALPHA  = ${ALPHA}"
echo "## CAP    = ${CAP}"
echo "## START  = ${START}"
echo "## $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 実行関数 ----------
run_reuse() {
  local T=$1 L=$2 B=$3 G=$4
  echo ""
  echo "===== reuse_score θ=$T λ=$L β=$B γ=$G ====="
  ALPHA_OVERRIDE=$ALPHA \
  GRAPH_OVERRIDE=$GRAPH \
  CACHE_CAPACITY_OVERRIDE=$CAP \
  CACHE_POLICIES_OVERRIDE="reuse_score" \
  START_NODES_OVERRIDE="$START" \
  PPR_THETA_OVERRIDE=$T \
  PPR_LAMBDA_OVERRIDE=$L \
  PPR_PRIOR_HOP_EXP_OVERRIDE=$B \
  PPR_PRIOR_DEG_EXP_OVERRIDE=$G \
  zsh "${SCRIPT_DIR}/splits_reuse_score.sh"
}

# ---------- ベースライン (LRU / ARC / memo) ----------
echo ""
echo "########## [BASELINE] lru + arc + memo ##########"
ALPHA_OVERRIDE=$ALPHA GRAPH_OVERRIDE=$GRAPH CACHE_CAPACITY_OVERRIDE=$CAP \
CACHE_POLICIES_OVERRIDE="lru arc memo" START_NODES_OVERRIDE="$START" \
zsh "${SCRIPT_DIR}/splits_reuse_score.sh"

# ---------- Stage 1: uniform ----------
echo ""
echo "########## [STAGE 1] uniform baseline ##########"
run_reuse 1.0 1.0 1.0 1.0

# ---------- Stage 2: single factor ----------
echo ""
echo "########## [STAGE 2] single factor ablation ##########"
run_reuse 1.0 0.0 0.0 0.0   # O only
run_reuse 0.0 1.0 0.0 0.0   # L only
run_reuse 0.0 0.0 1.0 0.0   # H only
run_reuse 0.0 0.0 0.0 1.0   # D only

# ---------- Stage 3: 9 non-uniform ----------
echo ""
echo "########## [STAGE 3] non-uniform patterns ##########"
run_reuse 1.0 1.0 0.5 1.0   # 3A-1: β=0.5
run_reuse 1.0 1.0 1.5 1.0   # 3A-2: β=1.5
run_reuse 1.0 1.0 2.0 1.0   # 3A-3: β=2.0
run_reuse 1.0 0.5 1.0 0.5   # 3B-1: L,D 半減
run_reuse 1.0 0.0 1.0 0.0   # 3B-2: L,D 除去
run_reuse 1.0 0.5 1.0 1.0   # 3B-3: L だけ半減
run_reuse 1.5 0.0 1.5 0.0   # 3C-1: O+H 両強調
run_reuse 1.0 0.0 2.0 0.0   # 3C-2: H メイン
run_reuse 2.0 0.0 1.0 0.0   # 3C-3: O メイン

# ---------- Stage 4: 1D sweeps ----------
echo ""
echo "########## [STAGE 4] 1D sweeps ##########"
# θ (他=1)
run_reuse 0.0 1.0 1.0 1.0
run_reuse 0.5 1.0 1.0 1.0
run_reuse 1.5 1.0 1.0 1.0
run_reuse 2.0 1.0 1.0 1.0
# λ (他=1)
run_reuse 1.0 0.0 1.0 1.0
run_reuse 1.0 0.5 1.0 1.0
run_reuse 1.0 1.5 1.0 1.0
run_reuse 1.0 2.0 1.0 1.0
# β (0 だけ追加、他は Stage 3A で実施済み)
run_reuse 1.0 1.0 0.0 1.0
# γ (他=1)
run_reuse 1.0 1.0 1.0 0.0
run_reuse 1.0 1.0 1.0 0.5
run_reuse 1.0 1.0 1.0 1.5
run_reuse 1.0 1.0 1.0 2.0

# ---------- CSV 集約 ----------
echo ""
echo "########## [POST] CSV 集約 ##########"
python3 "${SCRIPT_DIR}/generate_results_csv.py" --graph ${GRAPH}

echo ""
echo "============================================================"
echo "## [DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 集約 CSV: ${SCRIPT_DIR}/results/all_${GRAPH}_nobt_settings.csv"
echo "##          ${SCRIPT_DIR}/results/all_${GRAPH}_nobt_results.csv"
echo "============================================================"
