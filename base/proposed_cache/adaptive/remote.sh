#!/usr/bin/env bash
# =============================================================================
# remote.sh — 実験のコントローラごとリモートのマシン（CTRL_HOST）で動かす
#
#   手元の Mac は「送る・始める・様子を見る・結果を取ってくる」だけ。実験本体（run_all_cluster.sh →
#   run_cluster.sh → 各サーバへの SSH とコントローラ）は CTRL_HOST 上で nohup で動くので、
#   Mac を閉じても VPN が切れても止まらない。
#
#   使い方（手元のリポジトリルートから）:
#     CTRL_HOST=cap11 bash base/proposed_cache/adaptive/remote.sh setup           # CTRL_HOST へ実行一式を送る
#     CTRL_HOST=cap11 COUNT=4 SSH_USER=kate bash .../remote.sh check              # CTRL_HOST → 各サーバへの SSH を確認
#     CTRL_HOST=cap11 COUNT=4 SSH_USER=kate DEPLOY=1 GRAPHS="vldb" bash .../remote.sh start
#     CTRL_HOST=cap11 bash .../remote.sh status                                    # 進み具合
#     CTRL_HOST=cap11 bash .../remote.sh fetch                                     # 結果を手元へ（図も作る）
#     CTRL_HOST=cap11 bash .../remote.sh stop                                      # 止める（サーバも落とす）
#
#   モード:
#     setup   CTRL_HOST に core/・adaptive/・dist/aggregate_stats.py・gen_servers_conf.sh・グラフを rsync
#     check   CTRL_HOST の python3 / curl / rsync と、CTRL_HOST から各サーバへの鍵認証 SSH を確認
#     start   CTRL_HOST で run_all_cluster.sh を nohup 起動（下の実験用の環境変数をそのまま渡す）
#     status  ログの末尾と完了ジョブ数
#     fetch   CTRL_HOST の results/cluster_all/ を手元へ rsync し、plot_compare.py で図を作る
#     stop    run_all_cluster.sh / run_cluster.sh / コントローラを止め、全サーバの server.py を落とす
#
#   環境変数:
#     CTRL_HOST        コントローラを動かすマシン（ssh の接続先）  既定 cap11
#                      local にすると、このマシン自身で実行する（cap11 などにログインして直接打つとき。
#                      setup は不要・fetch は図を作るだけ。start / status / check / stop は同じ使い方）
#     REMOTE_REPO_DIR  CTRL_HOST 上のリポジトリルート            既定 .（ログイン直後の home）
#     RUN_NAME         ログの名前                                既定 run（同名で start すると上書きしない → 別名を付ける）
#     実験用（start で CTRL_HOST に渡す。意味は run_all_cluster.sh と同じ）:
#       COUNT HOST_START KN SERVERS_CONF GRAPHS ALPHAS SEEDS SEEDS_LOW POLICIES WALKS BATCH CAP CAP_TOTAL
#       LEARN_ARGS NO_BACKTRACK DEPLOY SSH_USER OUT_ROOT HEALTH_TIMEOUT AUTO_WALKS MAX_WALKS PROBE_POLICY CONV_ARGS RESUME
#     SSH_USER は CTRL_HOST から各サーバへの接続に使う（CTRL_HOST に ~/.ssh/config の別名が無いとき）
#     DRY_RUN=1        ssh / rsync を実行せず表示するだけ
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "${SELF_DIR}/../../.." && pwd -P)"
cd "${ROOT}"

MODE="${1:-status}"
CTRL_HOST="${CTRL_HOST:-cap11}"
R="${REMOTE_REPO_DIR:-.}"
RUN_NAME="${RUN_NAME:-run}"
DRY_RUN="${DRY_RUN:-0}"
SSH_OPTS="${SSH_OPTS:--o ConnectTimeout=15}"
GRAPHS="${GRAPHS:-er_mid amazon0601 ba_mid vldb soc-slashdot email-enron-connected}"
LOGDIR="base/proposed_cache/adaptive/results/remote_runs"
FORWARD_VARS="COUNT HOST_START KN SERVERS_CONF GRAPHS ALPHAS SEEDS SEEDS_LOW POLICIES WALKS BATCH CAP CAP_TOTAL \
LEARN_ARGS NO_BACKTRACK DEPLOY SSH_USER OUT_ROOT HEALTH_TIMEOUT AUTO_WALKS MAX_WALKS PROBE_POLICY CONV_ARGS RESUME"

run() {   # ssh / rsync を実行（DRY_RUN なら表示だけ）
  if [[ "${DRY_RUN}" == "1" ]]; then printf '[DRY]'; printf ' %q' "$@"; echo; return 0; fi
  "$@"
}
# CTRL_HOST=local のときは、このマシン自身をコントローラにする（cap11 などにログインして直接打つ用）。
# このとき「このマシンのリポジトリ」= このスクリプトがあるリポジトリ（R は使わない）。
IS_LOCAL=0
case "${CTRL_HOST}" in local|localhost|self) IS_LOCAL=1 ;; esac

rsh() {   # CTRL_HOST でコマンドを実行
  if ((IS_LOCAL)); then
    run bash -c "cd '${ROOT}' && $1"
    return
  fi
  # shellcheck disable=SC2086
  run ssh ${SSH_OPTS} "${CTRL_HOST}" "cd ${R} && $1"
}

env_string() {   # 実験用の環境変数を「VAR=値 ...」の形に（値は安全にクォート）
  local out="" v
  for v in ${FORWARD_VARS}; do
    if [[ -n "${!v:-}" ]]; then out+=" ${v}=$(printf '%q' "${!v}")"; fi
  done
  echo "${out}"
}

cmd_setup() {
  if ((IS_LOCAL)); then
    echo ">>> CTRL_HOST=local: このマシンで直接動かすので setup は不要です（コードとグラフが揃っているかは check で確認）"
    return 0
  fi
  local rs=(rsync -az -e "ssh ${SSH_OPTS}")
  rsh "mkdir -p base/proposed_cache/core/multi base/proposed_cache/adaptive/results base/proposed_cache/dist \
base/new-many-server dataset/Louvain/graph ${LOGDIR}" || return 1
  run "${rs[@]}" base/proposed_cache/core/*.py "${CTRL_HOST}:${R}/base/proposed_cache/core/" || return 1
  run "${rs[@]}" base/proposed_cache/core/multi/partition.py "${CTRL_HOST}:${R}/base/proposed_cache/core/multi/" || return 1
  run "${rs[@]}" base/proposed_cache/core/cache_extensions.py "${CTRL_HOST}:${R}/base/proposed_cache/core/" 2>/dev/null
  run "${rs[@]}" base/proposed_cache/adaptive/*.py base/proposed_cache/adaptive/*.sh \
      "${CTRL_HOST}:${R}/base/proposed_cache/adaptive/" || return 1
  run "${rs[@]}" base/proposed_cache/dist/aggregate_stats.py "${CTRL_HOST}:${R}/base/proposed_cache/dist/" || return 1
  run "${rs[@]}" base/new-many-server/gen_servers_conf.sh "${CTRL_HOST}:${R}/base/new-many-server/" || return 1
  local g
  for g in ${GRAPHS}; do    # CTRL_HOST から各サーバへ配る（DEPLOY=1）ためにグラフも置く
    [[ -f "dataset/Louvain/graph/${g}.gr" ]] || { echo "[FATAL] 手元にグラフがありません: ${g}" >&2; return 1; }
    run "${rs[@]}" "dataset/Louvain/graph/${g}.gr" "${CTRL_HOST}:${R}/dataset/Louvain/graph/" || return 1
  done
  echo ">>> setup 完了: ${CTRL_HOST}:${R}"
}

cmd_check() {
  [[ -n "${COUNT:-}${SERVERS_CONF:-}" ]] || { echo "[FATAL] check には COUNT（台数）か SERVERS_CONF が要ります" >&2; return 1; }
  local conf_cmd
  if [[ -n "${SERVERS_CONF:-}" ]]; then conf_cmd="cat ${SERVERS_CONF}"
  else conf_cmd="bash base/new-many-server/gen_servers_conf.sh --count ${COUNT} --start ${HOST_START:-11} --stdout"; fi
  rsh "echo \"ctrl=\$(hostname)\"; python3 -V; command -v curl rsync >/dev/null && echo 'curl/rsync: あり' || echo 'curl/rsync: なし（要インストール）'; \
python3 -c 'import matplotlib' 2>/dev/null && echo 'matplotlib: あり' || echo 'matplotlib: なし（図は手元で fetch 後に作る）'; \
${conf_cmd} | grep -v '^#' | while read -r h id ip port; do [ -z \"\$h\" ] && continue; \
t=\$h; [ -n '${SSH_USER:-}' ] && t='${SSH_USER:-}'@\$ip; \
if ssh -o BatchMode=yes -o ConnectTimeout=10 -n \$t 'hostname; python3 -V' >/tmp/_chk_\$\$ 2>&1; then echo \"[OK] \$t: \$(tr '\n' ' ' </tmp/_chk_\$\$)\"; \
else echo \"[NG] \$t: 鍵認証で SSH できません（CTRL_HOST の鍵を相手の authorized_keys に登録するか SSH_USER を確認）\"; fi; done; rm -f /tmp/_chk_\$\$"
}

cmd_start() {
  local log="${LOGDIR}/${RUN_NAME}.log"
  local envs
  envs="$(env_string)"
  # 起動するスクリプト名は変数 S に入れて渡す。コマンド文字列に名前をそのまま書くと、
  # Linux の pgrep -f はこのコマンドを実行している自分のシェルに一致し、毎回「実行中」と誤判定する
  #（Mac の pgrep は祖先を除外するので手元では再現しない）
  rsh "S=base/proposed_cache/adaptive/run_all_clust; mkdir -p ${LOGDIR}; \
if pgrep -f '[r]un_all_cluster.sh' >/dev/null; then echo '[FATAL] すでに実行中です（status / stop）'; exit 1; fi; \
[ -f ${log} ] && { echo '[FATAL] ${log} があります。RUN_NAME を変えてください'; exit 1; }; \
nohup env ${envs} bash \${S}er.sh > ${log} 2>&1 < /dev/null & \
echo \"started pid=\$! log=${R}/${log}\""
  echo ">>> 進み具合: CTRL_HOST=${CTRL_HOST} RUN_NAME=${RUN_NAME} bash ${SELF_DIR}/remote.sh status"
}

cmd_status() {
  local log="${LOGDIR}/${RUN_NAME}.log"
  rsh "if pgrep -f '[r]un_all_cluster.sh' >/dev/null; then echo '状態: 実行中'; else echo '状態: 停止中（終了または未開始）'; fi; \
[ -f ${log} ] || { echo 'ログがありません: ${log}'; exit 0; }; \
echo \"完了 \$(grep -c '^\[done\]' ${log}) / スキップ \$(grep -c '^\[skip\]' ${log})\"; tail -n 15 ${log}"
}

RESULT_DIRS="${RESULT_DIRS:-cluster_all cluster_auto}"   # fetch / stop が対象にする results/ の下のフォルダ

cmd_fetch() {
  local sub d
  if ((IS_LOCAL)); then     # 結果はすでにこのマシンにある → 図だけ作る
    for d in $(for sub in ${RESULT_DIRS}; do ls -d base/proposed_cache/adaptive/results/${sub}/*/ 2>/dev/null; done); do
      echo ">>> 図: ${d}"
      python3 "${SELF_DIR}/plot_compare.py" "${d}" 2>&1 | tail -n 7
    done
    echo ">>> 結果はこのマシンの base/proposed_cache/adaptive/results/ にあります（手元へは Mac から remote.sh fetch）"
    return 0
  fi
  [[ "${DRY_RUN}" == "1" ]] || mkdir -p "${LOGDIR}"
  for sub in ${RESULT_DIRS}; do
    if [[ "${DRY_RUN}" != "1" ]] && ! ssh ${SSH_OPTS} "${CTRL_HOST}" "test -d ${R}/base/proposed_cache/adaptive/results/${sub}"; then
      echo "（${CTRL_HOST} に results/${sub} はまだありません）"
      continue
    fi
    [[ "${DRY_RUN}" == "1" ]] || mkdir -p "base/proposed_cache/adaptive/results/${sub}"
    run rsync -az -e "ssh ${SSH_OPTS}" "${CTRL_HOST}:${R}/base/proposed_cache/adaptive/results/${sub}/" \
        "base/proposed_cache/adaptive/results/${sub}/" || return 1
  done
  run rsync -az -e "ssh ${SSH_OPTS}" "${CTRL_HOST}:${R}/${LOGDIR}/" "${LOGDIR}/" || return 1
  [[ "${DRY_RUN}" == "1" ]] && return 0
  for d in $(for sub in ${RESULT_DIRS}; do ls -d base/proposed_cache/adaptive/results/${sub}/*/ 2>/dev/null; done); do
    [[ -d "${d}" ]] || continue
    echo ">>> 図: ${d}"
    python3 "${SELF_DIR}/plot_compare.py" "${d}" | tail -n 7
  done
}

cmd_stop() {
  rsh "pkill -f '[r]un_all_cluster.sh'; pkill -f '[a]daptive/run_cluster.sh'; pkill -f '[a]daptive/controller.py'; \
for f in base/proposed_cache/adaptive/results/cluster_*/*/servers.conf; do [ -f \"\$f\" ] || continue; \
grep -v '^#' \"\$f\" | while read -r h id ip port; do [ -z \"\$h\" ] && continue; t=\$h; [ -n '${SSH_USER:-}' ] && t='${SSH_USER:-}'@\$ip; \
ssh -o BatchMode=yes -o ConnectTimeout=10 -n \$t 'pkill -f \"[a]daptive/server.py\"' && echo \"stopped servers on \$t\"; done; done; echo '停止しました'"
}

case "${MODE}" in
  setup)  cmd_setup ;;
  check)  cmd_check ;;
  start)  cmd_start ;;
  status) cmd_status ;;
  fetch)  cmd_fetch ;;
  stop)   cmd_stop ;;
  *) echo "usage: remote.sh {setup|check|start|status|fetch|stop}" >&2; exit 1 ;;
esac
