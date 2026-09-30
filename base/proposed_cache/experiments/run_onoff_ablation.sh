#!/bin/zsh
# =============================================================================
# run_onoff_ablation.sh
#   reuse_score の 4 因子 (O, L, H, D) を ON/OFF で全組合せ回す
#   完全実施要因計画 (2^4 = 16 ラン) + ベースライン (lru/arc/memo)。
#
#   目的:
#     「どの因子が最も効くか」を綺麗に識別する。
#     指数スイープ (0.5/1.5/2.0…) はノイズに埋もれるので使わない。
#     各因子は ON(=1) / OFF(=0) のみ。主効果 = ON平均 − OFF平均 で評価する
#     (analyze_onoff_maineffects.py が集計)。
#
#   因子と環境変数の対応 (splits_reuse_score.sh へ渡す):
#     O 頻度  = θ = PPR_THETA_OVERRIDE
#     L 学習  = λ = PPR_LAMBDA_OVERRIDE
#     H 距離  = β = PPR_PRIOR_HOP_EXP_OVERRIDE
#     D 次数  = γ = PPR_PRIOR_DEG_EXP_OVERRIDE
#
#   ノイズ低減:
#     seed は splits 側で 42 固定のため、複数始点 (START_NODES) の平均で分散を下げる。
#     generate_results_csv.py が始点平均済みの hit_rate を 1 config = 1 行で出力する。
#
#   使い方 (base ディレクトリから):
#     zsh base/proposed_cache/experiments/run_onoff_ablation.sh
#
#     # 条件変更 (デフォルト: amazon0601, α=0.05, cap=100, starts=0..3)
#     GRAPH_OVERRIDE=vldb ALPHA_OVERRIDE=0.01 \
#     START_NODES_OVERRIDE="0 1 2 3 4 5" \
#       zsh base/proposed_cache/experiments/run_onoff_ablation.sh
#
#   ラン数: 16 (reuse_score) + 1 (baseline 3policy) = 17 zshコール
#           × 始点数。始点4なら実測で 30分〜1時間程度が目安。
# =============================================================================

set -uo pipefail

# ---------- 実験条件 ----------
ALPHA="${ALPHA_OVERRIDE:-0.05}"
CAP="${CACHE_CAPACITY_OVERRIDE:-100}"
GRAPH="${GRAPH_OVERRIDE:-amazon0601}"
STARTS="${START_NODES_OVERRIDE:-0 1 2 3}"
WALKS="${RW_WALKS_OVERRIDE:-10000}"     # RW walk 数 (未指定なら splits 既定 10000)
W="${REUSE_W_OVERRIDE:-1.0}"            # w: LRU へのスコア上乗せ重み (H=L+w·V/size)

# スクリプト自身のパス
if [[ -n "${ZSH_VERSION:-}" ]]; then
  _SELF="${(%):-%x}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
PC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"   # base/proposed_cache
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd -P)"
cd "${REPO_ROOT}"

echo "============================================================"
echo "## [run_onoff_ablation]  2^4 factorial (O/L/H/D on-off)"
echo "## GRAPH  = ${GRAPH}"
echo "## ALPHA  = ${ALPHA}"
echo "## CAP    = ${CAP}"
echo "## WALKS  = ${WALKS}"
echo "## W(上乗せ)= ${W}"
echo "## STARTS = ${STARTS}"
echo "## $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 実行関数 ----------
run_reuse() {
  local O=$1 L=$2 H=$3 D=$4     # 引数は 0 か 1
  echo ""
  echo "===== reuse_score  O(θ)=$O L(λ)=$L H(β)=$H D(γ)=$D ====="
  ALPHA_OVERRIDE=$ALPHA \
  GRAPH_OVERRIDE=$GRAPH \
  CACHE_CAPACITY_OVERRIDE=$CAP \
  RW_WALKS_OVERRIDE=$WALKS \
  REUSE_W_OVERRIDE=$W \
  CACHE_POLICIES_OVERRIDE="reuse_score" \
  START_NODES_OVERRIDE="$STARTS" \
  PPR_THETA_OVERRIDE=${O}.0 \
  PPR_LAMBDA_OVERRIDE=${L}.0 \
  PPR_PRIOR_HOP_EXP_OVERRIDE=${H}.0 \
  PPR_PRIOR_DEG_EXP_OVERRIDE=${D}.0 \
  zsh "${PC_DIR}/dist/splits_reuse_score.sh"
}

# ---------- 2^4 = 16 全組合せ (reuse_score のみ / ベースラインなし) ----------
echo ""
echo "########## [FACTORIAL] 2^4 = 16 on/off combos ##########"
for O in 0 1; do
  for L in 0 1; do
    for H in 0 1; do
      for D in 0 1; do
        run_reuse $O $L $H $D
      done
    done
  done
done

# ---------- CSV 集約 ----------
echo ""
echo "########## [POST] CSV 集約 + 主効果集計 ##########"
python3 "${PC_DIR}/sweeps/generate_results_csv.py" --graph ${GRAPH}
python3 "${SCRIPT_DIR}/analyze_onoff_maineffects.py" --graph ${GRAPH} || true

echo ""
echo "============================================================"
echo "## [DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 集約 CSV: ${PC_DIR}/results/all_${GRAPH}_nobt_settings.csv"
echo "##          ${PC_DIR}/results/all_${GRAPH}_nobt_results.csv"
echo "============================================================"
