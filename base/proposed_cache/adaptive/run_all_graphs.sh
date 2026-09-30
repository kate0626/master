#!/usr/bin/env bash
# =============================================================================
# run_all_graphs.sh — 複数グラフ × α × seed を、localhost の実サーバでまとめて回す
#
#   1 ジョブ = adaptive/run_local.sh を1回（全方策）。ジョブごとにポートを分けて PARALLEL 本ずつ並列に回す。
#   終わったジョブ（summary.tsv に全方策の RESULT 行がある）は飛ばすので、止まっても再実行で続きから回る。
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/adaptive/run_all_graphs.sh
#     GRAPHS="vldb amazon0601" ALPHAS="0.1" SEEDS="1 1001 2001" PARALLEL=4 bash .../run_all_graphs.sh
#
#   環境変数:
#     GRAPHS    既定 "er_mid amazon0601 ba_mid vldb soc-slashdot email-enron-connected"
#     ALPHAS    既定 "0.1 0.01"
#     AUTO_WALKS=1 / MAX_WALKS / PROBE_POLICY / CONV_ARGS / RESUME   adaptive_policies.sh の run_all_policies を参照
#               （AUTO_WALKS=1 で SEEDS 未指定なら seed を MAX_WALKS ずつ離す）
#     SEEDS     既定 "1 1001 2001"（walk 集合が重ならないよう WALKS ずつずらす）
#     SEEDS_LOW α=0.01 用の seed（重いので別に指定可）。既定 = SEEDS
#     POLICIES  既定 "lru arc lfu lecar cacheus proposed structured_oonly structured structured_prior belady"
#     K / KN / WALKS / BATCH / CAP / CAP_TOTAL / LEARN_ARGS   run_local.sh と同じ（既定 2 / 1 / 1000 / 100 / 100）
#     PARALLEL  同時に回すジョブ数  既定 3
#     BIG_GRAPHS / PARALLEL_BIG  大きいグラフは同時実行数を分ける（既定 "amazon0601" / 1）。
#               amazon0601 はサーバ1台で約 3.3 GB 使うので、16 GB の Mac では 1 ジョブ（2 台）ずつにする
#     OUT_ROOT  既定 base/proposed_cache/adaptive/results/allgraphs
#   始点: soc-slashdot はノード番号が 1 始まりなので START=1、ほかは 0。
#   図: python3 base/proposed_cache/adaptive/plot_compare.py <OUT_ROOT>
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "${SELF_DIR}/../../.." && pwd -P)"
cd "${ROOT}"

GRAPHS="${GRAPHS:-er_mid amazon0601 ba_mid vldb soc-slashdot email-enron-connected}"
ALPHAS="${ALPHAS:-0.1 0.01}"
# 学習の飽和点を探す（AUTO_WALKS=1）と walk 数が増えるので、seed を MAX_WALKS ずつ離して
# walk の集合が重ならないようにする（サーバは i 本目の walk に seed+i を使う）
export AUTO_WALKS="${AUTO_WALKS:-0}" RESUME="${RESUME:-1}" PROBE_POLICY="${PROBE_POLICY:-structured_prior}"
export CONV_ARGS="${CONV_ARGS:-}"
if [[ "${AUTO_WALKS}" == "1" ]]; then
  export MAX_WALKS="${MAX_WALKS:-$(( ${WALKS:-1000} * 10 ))}"
  SEEDS="${SEEDS:-1 $((1 + MAX_WALKS)) $((1 + 2 * MAX_WALKS))}"
else
  SEEDS="${SEEDS:-1 1001 2001}"
fi
SEEDS_LOW="${SEEDS_LOW:-${SEEDS}}"
POLICIES="${POLICIES:-lru arc lfu lecar cacheus proposed structured_oonly structured structured_prior belady}"
export K="${K:-2}" KN="${KN:-1}" WALKS="${WALKS:-1000}" BATCH="${BATCH:-100}" CAP="${CAP:-100}"
export CAP_TOTAL="${CAP_TOTAL:-}" LEARN_ARGS="${LEARN_ARGS:-}" POLICIES
PARALLEL="${PARALLEL:-3}"
BIG_GRAPHS="${BIG_GRAPHS:-amazon0601}"
PARALLEL_BIG="${PARALLEL_BIG:-1}"
OUT_ROOT="${OUT_ROOT:-${SELF_DIR}/results/allgraphs}"
mkdir -p "${OUT_ROOT}"

JOBS="${OUT_ROOT}/jobs.txt"
: > "${JOBS}"
n=0
for a in ${ALPHAS}; do
  seeds="${SEEDS}"
  [[ "${a}" == "0.01" ]] && seeds="${SEEDS_LOW}"
  for g in ${GRAPHS}; do
    for s in ${seeds}; do
      start=0
      [[ "${g}" == "soc-slashdot" ]] && start=1
      echo "${n} ${g} ${a} ${s} ${start}" >> "${JOBS}"
      n=$((n + 1))
    done
  done
done
n_pol=$(echo ${POLICIES} | wc -w | tr -d ' ')
echo "== ${n} ジョブ（グラフ × α × seed）、1 ジョブ ${n_pol} 方策、並列 ${PARALLEL}  → ${OUT_ROOT}"

run_job() {
  local idx="$1" g="$2" a="$3" s="$4" start="$5"
  local out="${OUT_ROOT}/${g}_a${a}_seed${s}"
  if [[ -f "${out}/summary.tsv" ]] && (( $(grep -c "^RESULT" "${out}/summary.tsv") >= n_pol )); then
    echo "[skip] ${g} α=${a} seed=${s}（済み）"
    return 0
  fi
  local t0=$(date +%s)
  GRAPH="${g}" ALPHA="${a}" SEED="${s}" START="${start}" BASE_PORT=$((5000 + idx * 10)) \
    OUT_DIR="${out}" bash "${SELF_DIR}/run_local.sh" > "${OUT_ROOT}/${g}_a${a}_seed${s}.log" 2>&1
  local rc=$?
  echo "[done] ${g} α=${a} seed=${s}  rc=${rc}  $(( $(date +%s) - t0 ))s  RESULT=$(grep -c "^RESULT" "${out}/summary.tsv" 2>/dev/null)/${n_pol}"
}
export -f run_job
export OUT_ROOT SELF_DIR n_pol

# α ごとに「小さいグラフを PARALLEL 並列」→「大きいグラフを PARALLEL_BIG 並列」の順に回す
is_big() { for b in ${BIG_GRAPHS}; do [[ "$1" == "${b}" ]] && return 0; done; return 1; }
for a in ${ALPHAS}; do
  : > "${JOBS}.small"; : > "${JOBS}.big"
  while read -r idx g ja s start; do
    [[ "${ja}" == "${a}" ]] || continue
    if is_big "${g}"; then echo "${idx} ${g} ${ja} ${s} ${start}" >> "${JOBS}.big"
    else echo "${idx} ${g} ${ja} ${s} ${start}" >> "${JOBS}.small"; fi
  done < "${JOBS}"
  xargs -P "${PARALLEL}" -L 1 bash -c 'run_job "$@"' _ < "${JOBS}.small"
  xargs -P "${PARALLEL_BIG}" -L 1 bash -c 'run_job "$@"' _ < "${JOBS}.big"
done
echo "== 全ジョブ終了。図: python3 ${SELF_DIR}/plot_compare.py ${OUT_ROOT}"
