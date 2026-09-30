#!/usr/bin/env bash
# =============================================================================
# reproduce_all.sh — 追加実験（LOO / Shapley / 始点依存 / パラメータ感度）を
#                    一括で回して4枚の図を再生成する。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/experiments/reproduce_all.sh
#
#   環境変数で条件を変えられる:
#     ALPHA=0.05 WALKS=3000 bash base/proposed_cache/experiments/reproduce_all.sh
#     GRAPHS="vldb"          # amazon0601 を外す
#     RES=/tmp/out           # 出力先を変える（既存結果を上書きしない）
#
#   所要時間の目安: 15〜25 分（VLDB と Amazon を並列実行）
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"   # base/proposed_cache
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd -P)"
cd "${ROOT}"

RES="${RES:-base/proposed_cache/results}"   # 出力先（テスト時は別ディレクトリを指定可）
GRAPH_DIR="dataset/Louvain/graph"
mkdir -p "${RES}"

ALPHA="${ALPHA:-0.1}"
WALKS="${WALKS:-1000}"
CAP="${CAP:-100}"
NSEEDS="${NSEEDS:-5}"
PAPER_SEEDS="${PAPER_SEEDS:-1 7 13 99 123}"
GRAPHS="${GRAPHS:-vldb amazon0601}"

name_of() { case "$1" in vldb) echo VLDB ;; amazon0601) echo Amazon ;; *) echo "$1" ;; esac; }
starts_of() { case "$1" in vldb) echo "0 1 2 3 4" ;; *) echo "0 1 2 3 4 5 6 7 8 9" ;; esac; }

echo "============================================================"
echo "## reproduce_all   alpha=${ALPHA} walks=${WALKS} cap=${CAP} seeds=${NSEEDS}"
echo "## graphs = ${GRAPHS}"
echo "## $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

run_graph() {
  local g="$1" N; N="$(name_of "$g")"
  local E="${GRAPH_DIR}/${g}.gr"
  local LOG="${RES}/_log_${N}.txt"
  : > "${LOG}"

  echo "[${N}] 1/4 leave-one-out"                       | tee -a "${LOG}"
  python3 base/proposed_cache/experiments/run_loo_ablation.py \
    --edges "${E}" --graph-name "${N}" \
    --alpha "${ALPHA}" --walks "${WALKS}" --cache-capacity "${CAP}" \
    --start-nodes 0 --disjoint --n-seeds "${NSEEDS}" \
    --out-csv "${RES}/loo_${N}_a${ALPHA}.csv" \
    --out-raw "${RES}/loo_${N}_a${ALPHA}_raw.csv" >> "${LOG}" 2>&1

  echo "[${N}] 2/2 パラメータ感度"                      | tee -a "${LOG}"
  python3 base/proposed_cache/experiments/run_param_sensitivity.py \
    --edges "${E}" --graph-name "${N}" \
    --alpha "${ALPHA}" --walks "${WALKS}" --cache-capacity "${CAP}" \
    --start-nodes 0 --n-seeds "${NSEEDS}" \
    --out-csv "${RES}/sens_${N}.csv" >> "${LOG}" 2>&1

  echo "[${N}] done  (ログ: ${LOG})"
}

PIDS=()
for g in ${GRAPHS}; do
  run_graph "$g" &
  PIDS+=($!)
done
FAIL=0
for p in "${PIDS[@]}"; do wait "$p" || FAIL=1; done

echo ""
echo "########## 作図 ##########"
python3 base/proposed_cache/figs/plot_loo_ablation.py
python3 base/proposed_cache/figs/plot_shapley_decomposition.py
python3 base/proposed_cache/figs/plot_startnode_dependence.py
python3 base/proposed_cache/figs/plot_param_sensitivity.py

echo ""
echo "============================================================"
echo "## 図:   base/proposed_cache/figs/{loo_ablation,shapley_decomposition,"
echo "##        startnode_dependence,param_sensitivity}.png"
echo "## CSV:  ${RES}/{loo,shapley,startsweep,sens}_*.csv"
echo "## ログ: ${RES}/_log_*.txt"
echo "## $(date '+%Y-%m-%d %H:%M:%S')   exit=${FAIL}"
echo "============================================================"
exit "${FAIL}"
