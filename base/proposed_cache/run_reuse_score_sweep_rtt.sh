#!/bin/zsh
# =============================================================================
# run_reuse_score_sweep_rtt.sh
#   reuse_score の重み (θ/λ/β/γ) と学習walk数 (lw) を「規則的に」振って実験し、
#   CSV に集約したうえで RTT を考慮した実行時間比較 (rtt_compare_reuse_score.py)
#   まで一気通貫で回すドライバ。
#
#   固定条件: GRAPH / ALPHA / CAP は 1 通り (既定 vldb, α=0.05, cap=100)。
#             複数条件を回したい場合は環境変数を変えて本スクリプトを複数回呼ぶ。
#
#   規則的スイープ (OAT = One-At-a-Time, 中心 θ=λ=β=γ=1, lw=0 を基準):
#     - ベースライン (lru, arc, memo)                         1 zshコール (3ポリシー)
#     - Stage 0: 中心 (uniform θ=λ=β=γ=1, lw=0)               1 ラン
#     - Stage 1: 単独因子 ablation (O/L/H/D のみ)              4 ラン
#     - Stage 2: θ 1D スイープ (中心1.0を除く)                 4 ラン
#     - Stage 3: λ 1D スイープ                                4 ラン
#     - Stage 4: β 1D スイープ                                4 ラン
#     - Stage 5: γ 1D スイープ                                4 ラン
#     - Stage 6: lw 1D スイープ (中心0を除く、weights=1固定)   3 ラン
#     - CSV 集約 (generate_results_csv.py)
#     - RTT 分析 (rtt_compare_reuse_score.py) : RTT を規則的に掃引して図/CSV出力
#         = 約 24 reuse_score ラン + 4 ベースライン
#
# 使い方 (リポジトリルート / base から):
#   zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # 条件を変える (既定: vldb, α=0.05, cap=100, START=0)
#   ALPHA_OVERRIDE=0.1 CACHE_CAPACITY_OVERRIDE=200 \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # グラフを外付けで変える (vldb / amazon0601 / karate など)
#   # 飽和定数 (C_F/C_L/C_D) はグラフ別に自動設定される:
#   #   vldb       : C_D=5   (次数中央値)
#   #   amazon0601 : C_D=15  (次数中央値, 密グラフ)
#   #   その他      : C_D=10  (splits.sh の既定)
#   GRAPH_OVERRIDE=amazon0601 \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # 飽和定数を手動で上書きしたい場合 (グラフ別既定より優先)
#   GRAPH_OVERRIDE=vldb REUSE_CD_OVERRIDE=10 \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # スイープ格子を変える (スペース区切り)
#   THETA_SWEEP_OVERRIDE="0 1 2" LW_SWEEP_OVERRIDE="0 20" \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # 飽和定数 (C_F/C_L/C_D/C_R) と recency ρ もスイープする (既定は OFF)
#   #   中心は REUSE_* の値。指定した軸だけ Stage 7〜11 が追加で回る。
#   #   ※ C_R/ρ の掃引は ρ>0 のときのみ意味あり。
#   REUSE_RHO_OVERRIDE=1.0 \
#   CD_SWEEP_OVERRIDE="5 10 15 20" CR_SWEEP_OVERRIDE="5 10 30 50" RHO_SWEEP_OVERRIDE="0 0.5 1 2" \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # RTT 分析だけ設定変更 (メイン RTT と掃引リスト)
#   RTT_MAIN_MS_OVERRIDE=30 RTT_SWEEP_MS_OVERRIDE="1 5 10 30 50 100" \
#     zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
#
#   # 実験をスキップして「集約 + RTT 分析」だけやり直す (既存結果を再利用)
#   SKIP_EXPERIMENTS=1 zsh base/proposed_cache/run_reuse_score_sweep_rtt.sh
# =============================================================================

set -uo pipefail

# ---------- 実験条件 (固定 1 通り) ----------
ALPHA="${ALPHA_OVERRIDE:-0.05}"
CAP="${CACHE_CAPACITY_OVERRIDE:-100}"
GRAPH="${GRAPH_OVERRIDE:-vldb}"
START="${START_NODES_OVERRIDE:-0}"

# ---------- 規則的スイープ格子 (OAT) ----------
# 中心 (基準構成)。各軸の 1D スイープはこの中心の当該軸だけを動かす。
CENTER_T=1.0; CENTER_L=1.0; CENTER_B=1.0; CENTER_G=1.0; CENTER_LW=0
# 各軸の候補値 (中心値は Stage 0 で 1 回だけ回すため、各スイープからは除外する)
if [[ -n "${THETA_SWEEP_OVERRIDE:-}" ]]; then eval "THETA_SWEEP=(${THETA_SWEEP_OVERRIDE})"; else THETA_SWEEP=(0.0 0.5 1.5 2.0); fi
if [[ -n "${LAMBDA_SWEEP_OVERRIDE:-}" ]]; then eval "LAMBDA_SWEEP=(${LAMBDA_SWEEP_OVERRIDE})"; else LAMBDA_SWEEP=(0.0 0.5 1.5 2.0); fi
if [[ -n "${BETA_SWEEP_OVERRIDE:-}" ]]; then eval "BETA_SWEEP=(${BETA_SWEEP_OVERRIDE})"; else BETA_SWEEP=(0.0 0.5 1.5 2.0); fi
if [[ -n "${GAMMA_SWEEP_OVERRIDE:-}" ]]; then eval "GAMMA_SWEEP=(${GAMMA_SWEEP_OVERRIDE})"; else GAMMA_SWEEP=(0.0 0.5 1.5 2.0); fi
if [[ -n "${LW_SWEEP_OVERRIDE:-}" ]]; then eval "LW_SWEEP=(${LW_SWEEP_OVERRIDE})"; else LW_SWEEP=(10 20 40); fi
# 飽和定数 (C_F/C_L/C_D/C_R) と recency ρ の 1D スイープ。
# 既定は空 = OFF (指定した時だけ回る)。中心は下の REUSE_* / _DEF_* 値。
#   ※ C_R / ρ の掃引は ρ>0 のときだけ意味がある (ρ=0 なら recency 無効)。
if [[ -n "${CF_SWEEP_OVERRIDE:-}"  ]]; then eval "CF_SWEEP=(${CF_SWEEP_OVERRIDE})";   else CF_SWEEP=();  fi
if [[ -n "${CL_SWEEP_OVERRIDE:-}"  ]]; then eval "CL_SWEEP=(${CL_SWEEP_OVERRIDE})";   else CL_SWEEP=();  fi
if [[ -n "${CD_SWEEP_OVERRIDE:-}"  ]]; then eval "CD_SWEEP=(${CD_SWEEP_OVERRIDE})";   else CD_SWEEP=();  fi
if [[ -n "${CR_SWEEP_OVERRIDE:-}"  ]]; then eval "CR_SWEEP=(${CR_SWEEP_OVERRIDE})";   else CR_SWEEP=();  fi
if [[ -n "${RHO_SWEEP_OVERRIDE:-}" ]]; then eval "RHO_SWEEP=(${RHO_SWEEP_OVERRIDE})"; else RHO_SWEEP=(); fi

# ---------- reuse_score 飽和定数 (C_F / C_L / C_D) : グラフ別の既定 ----------
# splits_reuse_score.sh の既定は C_F=C_L=5, C_D=10 (「ざっくり初期値」)。
# ここでは data 統計に基づく次数中央値をグラフ別に既定化する。
#   REUSE_CF_OVERRIDE / REUSE_CL_OVERRIDE / REUSE_CD_OVERRIDE を指定すると優先。
case "${GRAPH}" in
  amazon0601) _DEF_CF=5.0; _DEF_CL=5.0; _DEF_CD=15.0 ;;   # 密グラフ: deg 中央値 ≈15
  vldb)       _DEF_CF=5.0; _DEF_CL=5.0; _DEF_CD=10.0  ;;    # deg 中央値 ≈5
  *)          _DEF_CF=5.0; _DEF_CL=5.0; _DEF_CD=10.0 ;;    # 不明グラフ: splits.sh 既定
esac
REUSE_CF="${REUSE_CF_OVERRIDE:-$_DEF_CF}"
REUSE_CL="${REUSE_CL_OVERRIDE:-$_DEF_CL}"
REUSE_CD="${REUSE_CD_OVERRIDE:-$_DEF_CD}"
# recency (LRU 的最近性) 因子 R^ρ。低 α では ρ>0 で頑健化する。
REUSE_RHO="${REUSE_RHO_OVERRIDE:-0.0}"   # ρ: recency の指数 (0=無効, 1〜2 で最近性を重視)
REUSE_CR="${REUSE_CR_OVERRIDE:-10.0}"    # C_R: recency の飽和点

# ---------- RTT 分析設定 ----------
RTT_MAIN_MS="${RTT_MAIN_MS_OVERRIDE:-100}"          # 代表 RTT (棒グラフ/基準比に使用)
RTT_SWEEP_MS="${RTT_SWEEP_MS_OVERRIDE:-1 5 10 30 50 100}"  # 折れ線用の掃引
RTT_BASE_MODE="${RTT_BASE_MODE_OVERRIDE:-walk}"     # walk | walk_minus_auth | zero
RTT_REFERENCE="${RTT_REFERENCE_OVERRIDE:-memo}"     # 時間差% の基準 policy

SKIP_EXPERIMENTS="${SKIP_EXPERIMENTS:-0}"           # 1=実験を飛ばして集約+分析のみ

# ---------- スクリプト自身のパス ----------
if [[ -n "${ZSH_VERSION:-}" ]]; then
  _SELF="${(%):-%x}"
else
  _SELF="$0"
fi
SCRIPT_DIR="$(cd "$(dirname "${_SELF}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
cd "${REPO_ROOT}"

echo "============================================================"
echo "## [run_reuse_score_sweep_rtt]"
echo "## GRAPH  = ${GRAPH}"
echo "## ALPHA  = ${ALPHA}"
echo "## CAP    = ${CAP}"
echo "## START  = ${START}"
echo "## θ sweep = ${THETA_SWEEP[*]}   (center=${CENTER_T})"
echo "## λ sweep = ${LAMBDA_SWEEP[*]}  (center=${CENTER_L})"
echo "## β sweep = ${BETA_SWEEP[*]}    (center=${CENTER_B})"
echo "## γ sweep = ${GAMMA_SWEEP[*]}   (center=${CENTER_G})"
echo "## lw sweep= ${LW_SWEEP[*]}      (center=${CENTER_LW})"
echo "## 飽和定数 = C_F=${REUSE_CF} C_L=${REUSE_CL} C_D=${REUSE_CD}  (graph=${GRAPH} 既定)"
echo "## recency  = ρ=${REUSE_RHO}  C_R=${REUSE_CR}"
echo "## 定数sweep= C_F[${CF_SWEEP[*]:-}] C_L[${CL_SWEEP[*]:-}] C_D[${CD_SWEEP[*]:-}] C_R[${CR_SWEEP[*]:-}] ρ[${RHO_SWEEP[*]:-}]"
echo "## RTT main=${RTT_MAIN_MS}ms  sweep=[${RTT_SWEEP_MS}]  base=${RTT_BASE_MODE}  ref=${RTT_REFERENCE}"
echo "## SKIP_EXPERIMENTS=${SKIP_EXPERIMENTS}"
echo "## $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 実行関数 (lw を含めて splits_reuse_score.sh を1構成呼ぶ) ----------
run_reuse() {
  local T=$1 L=$2 B=$3 G=$4 LW=${5:-0}
  echo ""
  echo "===== reuse_score θ=$T λ=$L β=$B γ=$G lw=$LW ====="
  ALPHA_OVERRIDE=$ALPHA \
  GRAPH_OVERRIDE=$GRAPH \
  CACHE_CAPACITY_OVERRIDE=$CAP \
  CACHE_POLICIES_OVERRIDE="reuse_score" \
  START_NODES_OVERRIDE="$START" \
  PPR_THETA_OVERRIDE=$T \
  PPR_LAMBDA_OVERRIDE=$L \
  PPR_PRIOR_HOP_EXP_OVERRIDE=$B \
  PPR_PRIOR_DEG_EXP_OVERRIDE=$G \
  LEARNING_WALKS_OVERRIDE=$LW \
  REUSE_CF_OVERRIDE=$REUSE_CF \
  REUSE_CL_OVERRIDE=$REUSE_CL \
  REUSE_CD_OVERRIDE=$REUSE_CD \
  REUSE_RHO_OVERRIDE=$REUSE_RHO \
  REUSE_CR_OVERRIDE=$REUSE_CR \
  zsh "${SCRIPT_DIR}/splits_reuse_score.sh"
}

if [[ "${SKIP_EXPERIMENTS}" != "1" ]]; then
  # ---------- ベースライン (LRU / ARC / memo) ----------
  echo ""
  echo "########## [BASELINE] lru + arc + memo ##########"
  # NOTE：ベースケースも合わせて実験したい時はこちら
    # CACHE_POLICIES_OVERRIDE="lru arc memo" \
  ALPHA_OVERRIDE=$ALPHA GRAPH_OVERRIDE=$GRAPH CACHE_CAPACITY_OVERRIDE=$CAP \
  START_NODES_OVERRIDE="$START" \
  zsh "${SCRIPT_DIR}/splits_reuse_score.sh"

  # ---------- Stage 0: 中心 (uniform) ----------
  echo ""
  echo "########## [STAGE 0] center / uniform ##########"
  run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW

  # ---------- Stage 1: 単独因子 ablation ----------
  echo ""
  echo "########## [STAGE 1] single-factor ablation ##########"
  run_reuse 1.0 0.0 0.0 0.0 $CENTER_LW   # O only
  run_reuse 0.0 1.0 0.0 0.0 $CENTER_LW   # L only
  run_reuse 0.0 0.0 1.0 0.0 $CENTER_LW   # H only
  run_reuse 0.0 0.0 0.0 1.0 $CENTER_LW   # D only

  # ---------- Stage 2: θ 1D sweep (中心を除く) ----------
  echo ""
  echo "########## [STAGE 2] theta sweep ##########"
  for v in "${THETA_SWEEP[@]}"; do
    [[ "$v" == "$CENTER_T" ]] && continue
    run_reuse $v $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
  done

  # ---------- Stage 3: λ 1D sweep ----------
  echo ""
  echo "########## [STAGE 3] lambda sweep ##########"
  for v in "${LAMBDA_SWEEP[@]}"; do
    [[ "$v" == "$CENTER_L" ]] && continue
    run_reuse $CENTER_T $v $CENTER_B $CENTER_G $CENTER_LW
  done

  # ---------- Stage 4: β 1D sweep ----------
  echo ""
  echo "########## [STAGE 4] beta sweep ##########"
  for v in "${BETA_SWEEP[@]}"; do
    [[ "$v" == "$CENTER_B" ]] && continue
    run_reuse $CENTER_T $CENTER_L $v $CENTER_G $CENTER_LW
  done

  # ---------- Stage 5: γ 1D sweep ----------
  echo ""
  echo "########## [STAGE 5] gamma sweep ##########"
  for v in "${GAMMA_SWEEP[@]}"; do
    [[ "$v" == "$CENTER_G" ]] && continue
    run_reuse $CENTER_T $CENTER_L $CENTER_B $v $CENTER_LW
  done

  # ---------- Stage 6: lw 1D sweep (weights=中心固定) ----------
  echo ""
  echo "########## [STAGE 6] learning-walks (lw) sweep ##########"
  for v in "${LW_SWEEP[@]}"; do
    [[ "$v" == "$CENTER_LW" ]] && continue
    run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $v
  done

  # ---------- Stage 7: C_F 1D sweep (weights/lw=中心固定) ----------
  # 飽和定数はグローバル REUSE_CF を一時的に差し替えて run_reuse に効かせる。
  if (( ${#CF_SWEEP[@]} > 0 )); then
    echo ""
    echo "########## [STAGE 7] C_F sweep (center=${REUSE_CF}) ##########"
    _save=$REUSE_CF
    for v in "${CF_SWEEP[@]}"; do
      [[ "$v" == "$_save" ]] && continue
      REUSE_CF=$v; run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
    done
    REUSE_CF=$_save
  fi

  # ---------- Stage 8: C_L 1D sweep ----------
  if (( ${#CL_SWEEP[@]} > 0 )); then
    echo ""
    echo "########## [STAGE 8] C_L sweep (center=${REUSE_CL}) ##########"
    _save=$REUSE_CL
    for v in "${CL_SWEEP[@]}"; do
      [[ "$v" == "$_save" ]] && continue
      REUSE_CL=$v; run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
    done
    REUSE_CL=$_save
  fi

  # ---------- Stage 9: C_D 1D sweep ----------
  if (( ${#CD_SWEEP[@]} > 0 )); then
    echo ""
    echo "########## [STAGE 9] C_D sweep (center=${REUSE_CD}) ##########"
    _save=$REUSE_CD
    for v in "${CD_SWEEP[@]}"; do
      [[ "$v" == "$_save" ]] && continue
      REUSE_CD=$v; run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
    done
    REUSE_CD=$_save
  fi

  # ---------- Stage 10: C_R 1D sweep (recency 飽和点; ρ>0 のときのみ有意) ----------
  if (( ${#CR_SWEEP[@]} > 0 )); then
    echo ""
    echo "########## [STAGE 10] C_R sweep (center=${REUSE_CR}, ρ=${REUSE_RHO}) ##########"
    (( $(printf '%.0f' "${REUSE_RHO}") == 0 )) && \
      echo "[WARN] ρ=${REUSE_RHO} のため recency 無効。C_R を振っても結果は変わりません (REUSE_RHO_OVERRIDE>0 を推奨)。"
    _save=$REUSE_CR
    for v in "${CR_SWEEP[@]}"; do
      [[ "$v" == "$_save" ]] && continue
      REUSE_CR=$v; run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
    done
    REUSE_CR=$_save
  fi

  # ---------- Stage 11: ρ 1D sweep (recency 指数) ----------
  if (( ${#RHO_SWEEP[@]} > 0 )); then
    echo ""
    echo "########## [STAGE 11] rho sweep (center=${REUSE_RHO}, C_R=${REUSE_CR}) ##########"
    _save=$REUSE_RHO
    for v in "${RHO_SWEEP[@]}"; do
      [[ "$v" == "$_save" ]] && continue
      REUSE_RHO=$v; run_reuse $CENTER_T $CENTER_L $CENTER_B $CENTER_G $CENTER_LW
    done
    REUSE_RHO=$_save
  fi
else
  echo ""
  echo "########## [SKIP] SKIP_EXPERIMENTS=1 : 実験を飛ばし集約+分析のみ ##########"
fi

# ---------- CSV 集約 ----------
echo ""
echo "########## [POST] CSV 集約 ##########"
python3 "${SCRIPT_DIR}/generate_results_csv.py" --graph ${GRAPH}

# ---------- RTT 分析 (rtt_compare_reuse_score.py) ----------
echo ""
echo "########## [POST] RTT-aware 実行時間比較 ##########"
# reuse_score は既定で hit_rate 最大の構成が代表に選ばれる。
# 特定構成に固定したい場合は --theta 等を足す (下記コメント参照)。
python3 "${SCRIPT_DIR}/rtt_compare_reuse_score.py" \
  --graph "${GRAPH}" --alpha "${ALPHA}" --cap "${CAP}" \
  --rtt-ms "${RTT_MAIN_MS}" \
  --rtt-sweep-ms ${=RTT_SWEEP_MS} \
  --base-mode "${RTT_BASE_MODE}" \
  --reference "${RTT_REFERENCE}"
# 例) reuse_score を θ=2,λ=1,β=1,γ=1,lw=20 に固定して比較する場合:
#   ... --theta 2 --lambda 1 --beta 1 --gamma 1 --lw 20

echo ""
echo "============================================================"
echo "## [DONE] $(date '+%Y-%m-%d %H:%M:%S')"
echo "## 集約 CSV : ${SCRIPT_DIR}/results/all_${GRAPH}_nobt_settings.csv"
echo "##           ${SCRIPT_DIR}/results/all_${GRAPH}_nobt_results.csv"
echo "## RTT 出力 : ${SCRIPT_DIR}/results/alpha${ALPHA}_walks_100_capa_${CAP}/${GRAPH}_nobt/rtt_compare/"
echo "============================================================"
