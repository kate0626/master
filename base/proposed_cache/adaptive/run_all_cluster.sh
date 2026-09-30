#!/usr/bin/env bash
# =============================================================================
# run_all_cluster.sh — 実機（K 台）で「グラフ × α × seed」をまとめて回す
#
#   1 ジョブ = adaptive/run_cluster.sh を1回（全方策）。実機は同じホスト・ポートを使うので1本ずつ順に回す。
#   終わったジョブ（summary.tsv に全方策の RESULT 行がある）は飛ばすので、止まっても再実行で続きから回る。
#   手元の Mac からでも、リモートのマシン（remote.sh start）からでも同じように動く。
#
#   台数の指定:
#     COUNT=4 [HOST_START=11]  → cap11..cap14 の servers.conf を OUT_ROOT/servers.conf に作る（共有の servers.conf は触らない）
#     SERVERS_CONF=<path>      → 既存の servers.conf を使う（COUNT は無視）
#     KN                       → 先頭 KN 台がノード担当、残りがエッジ担当（既定 COUNT/2、最低1）
#
#   使い方（リポジトリルートから）:
#     COUNT=4 GRAPHS="vldb" ALPHAS="0.1" SEEDS="1" DRY_RUN=1 bash base/proposed_cache/adaptive/run_all_cluster.sh
#     COUNT=4 DEPLOY=1 bash base/proposed_cache/adaptive/run_all_cluster.sh
#
#   環境変数（run_all_graphs.sh と同じもの）:
#     GRAPHS    既定 "er_mid amazon0601 ba_mid vldb soc-slashdot email-enron-connected"
#     ALPHAS    既定 "0.1 0.01"
#     AUTO_WALKS=1 / MAX_WALKS / PROBE_POLICY / CONV_ARGS / RESUME   adaptive_policies.sh の run_all_policies を参照
#               （AUTO_WALKS=1 で SEEDS 未指定なら seed を MAX_WALKS ずつ離す）
#     SEEDS     既定 "1 1001 2001"      SEEDS_LOW  α=0.01 用（既定 = SEEDS）
#     POLICIES  既定 "lru arc lfu lecar cacheus proposed structured_oonly structured structured_prior belady"
#     WALKS / BATCH / CAP / CAP_TOTAL / LEARN_ARGS / NO_BACKTRACK   run_cluster.sh と同じ
#     DEPLOY=1  最初に deploy.sh all でコードとグラフを全ホストへ配る
#     SSH_USER  指定すると host 別名の代わりに SSH_USER@ip で接続（リモートから回すとき）
#     OUT_ROOT  既定 base/proposed_cache/adaptive/results/cluster_all/n<KN>e<KE>
#     DRY_RUN=1 実行せずにコマンドだけ表示
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/adaptive
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
COUNT="${COUNT:-}"
HOST_START="${HOST_START:-11}"
DEPLOY="${DEPLOY:-0}"
DRY_RUN="${DRY_RUN:-0}"

# --- servers.conf（台数）-----------------------------------------------------
if [[ -n "${SERVERS_CONF:-}" ]]; then
  [[ -f "${SERVERS_CONF}" ]] || { echo "[FATAL] SERVERS_CONF がありません: ${SERVERS_CONF}" >&2; exit 1; }
  COUNT=$(grep -v '^[[:space:]]*#' "${SERVERS_CONF}" | grep -c '[^[:space:]]')
else
  [[ -n "${COUNT}" ]] || { echo "[FATAL] 台数 COUNT（例 COUNT=4）か SERVERS_CONF を指定してください" >&2; exit 1; }
fi
KN="${KN:-$((COUNT / 2))}"
((KN >= 1)) || KN=1
((COUNT >= 2 && KN <= COUNT - 1)) || { echo "[FATAL] COUNT は 2 以上、KN は 1..COUNT-1（COUNT=${COUNT} KN=${KN}）" >&2; exit 1; }
KE=$((COUNT - KN))
OUT_ROOT="${OUT_ROOT:-${SELF_DIR}/results/cluster_all/n${KN}e${KE}}"
mkdir -p "${OUT_ROOT}"
if [[ -z "${SERVERS_CONF:-}" ]]; then
  SERVERS_CONF="${OUT_ROOT}/servers.conf"
  bash base/new-many-server/gen_servers_conf.sh --count "${COUNT}" --start "${HOST_START}" --stdout > "${SERVERS_CONF}" \
    || { echo "[FATAL] servers.conf を作れません（COUNT=${COUNT} HOST_START=${HOST_START}）" >&2; exit 1; }
fi
export SERVERS_CONF KN

n_pol=$(echo ${POLICIES} | wc -w | tr -d ' ')
echo "============================================================"
echo "## 実機まとめ実行  台数=${COUNT}（ノード担当 ${KN} / エッジ担当 ${KE}）"
echo "## servers.conf=${SERVERS_CONF}"
grep -v '^[[:space:]]*#' "${SERVERS_CONF}" | sed 's/^/##   /'
echo "## graphs=${GRAPHS}  alphas=${ALPHAS}  seeds=${SEEDS}（α=0.01: ${SEEDS_LOW}）"
echo "## policies=${POLICIES}"
echo "## out=${OUT_ROOT}  deploy=${DEPLOY}  ssh_user=${SSH_USER:-（servers.conf の別名）}  dry_run=${DRY_RUN}"
echo "## 開始 $(date '+%F %T')"
echo "============================================================"

if [[ "${DEPLOY}" == "1" ]]; then
  echo ">>> 配布: deploy.sh all（graph=${GRAPHS}）"
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "[DRY] GRAPH=\"${GRAPHS}\" bash ${SELF_DIR}/deploy.sh all"
  else
    GRAPH="${GRAPHS}" bash "${SELF_DIR}/deploy.sh" all || { echo "[FATAL] 配布に失敗しました" >&2; exit 1; }
  fi
fi

total=0; done_n=0
for a in ${ALPHAS}; do
  seeds="${SEEDS}"
  [[ "${a}" == "0.01" ]] && seeds="${SEEDS_LOW}"
  for g in ${GRAPHS}; do
    for s in ${seeds}; do
      total=$((total + 1))
      out="${OUT_ROOT}/${g}_a${a}_seed${s}"
      if [[ -f "${out}/summary.tsv" ]] && (( $(grep -c "^RESULT" "${out}/summary.tsv") >= n_pol )); then
        echo "[skip] ${g} α=${a} seed=${s}（済み）"
        done_n=$((done_n + 1))
        continue
      fi
      start=0
      [[ "${g}" == "soc-slashdot" ]] && start=1       # ノード番号が 1 始まり
      t0=$(date +%s)
      echo "[run ] ${g} α=${a} seed=${s}  $(date '+%T')"
      GRAPH="${g}" ALPHA="${a}" SEED="${s}" START="${start}" OUT_DIR="${out}" POLICIES="${POLICIES}" \
        DRY_RUN="${DRY_RUN}" bash "${SELF_DIR}/run_cluster.sh" > "${OUT_ROOT}/${g}_a${a}_seed${s}.log" 2>&1
      rc=$?
      got=$(grep -c "^RESULT" "${out}/summary.tsv" 2>/dev/null); got=${got:-0}
      echo "[done] ${g} α=${a} seed=${s}  rc=${rc}  $(( $(date +%s) - t0 ))s  RESULT=${got}/${n_pol}"
      ((got >= n_pol)) && done_n=$((done_n + 1))
    done
  done
done

echo "== 全ジョブ終了 $(date '+%F %T')  完了 ${done_n}/${total}"
if [[ "${DRY_RUN}" != "1" ]]; then
  python3 "${SELF_DIR}/plot_compare.py" "${OUT_ROOT}" 2>/dev/null \
    || echo "（図は手元で: python3 base/proposed_cache/adaptive/plot_compare.py ${OUT_ROOT}）"
fi
