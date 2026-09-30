#!/usr/bin/env bash
# =============================================================================
# deploy.sh — servers.conf の全ホストへ、run_cluster.sh の実行に必要なものを配る
#
#   base/new-many-server/deploy.sh は new-many-server/code（古いコピー）しか送らないので、
#   proposed_cache の実サーバ（core/）と学習キャッシュ（adaptive/）はこちらで送る。
#   servers.conf は run_cluster.sh と同じ "host id ip port" 形式（new-many-server/gen_servers_conf.sh で作れる）。
#
#   配るもの（リモートのリポジトリルート ${REMOTE_REPO_DIR} からの相対パスで置く）:
#     base/proposed_cache/core/*.py                 実サーバ・コントローラ
#     base/proposed_cache/core/multi/partition.py   ノード/エッジ分離配置
#     base/proposed_cache/adaptive/*.py             学習キャッシュ
#     dataset/Louvain/graph/<GRAPH>.gr              グラフ（GRAPH は空白区切りで複数可）
#     ${PARTITION_DIR}/node_to_starts_server*.json   deny テーブル（PARTITION_DIR を指定したときだけ。全 K 個）
#
#   使い方（リポジトリルートから）:
#     bash base/proposed_cache/adaptive/deploy.sh check
#     GRAPH=vldb bash base/proposed_cache/adaptive/deploy.sh push
#     GRAPH=vldb bash base/proposed_cache/adaptive/deploy.sh all
#
#   モード:
#     check   SSH・python3・グラフ・コードの有無を確認するだけ（何も変更しない）
#     push    rsync で送る（DRY_RUN=1 なら送る内容の表示だけ）
#     all     check → push → check
#
#   環境変数:
#     SERVERS_CONF     既定 base/new-many-server/servers.conf（run_cluster.sh と同じ）
#     GRAPH            既定 vldb
#     PARTITION_DIR    deny テーブルのディレクトリ（任意）
#     REMOTE_REPO_DIR  既定 .（ログイン直後のホーム）
#     SSH_OPTS         既定 "-o ConnectTimeout=10"
#     DRY_RUN=1        rsync -n
#     SSH_USER         指定すると host 別名の代わりに SSH_USER@ip で接続（リモートから配るとき）
# =============================================================================
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"   # base/proposed_cache/adaptive
ROOT="$(cd "${SELF_DIR}/../../.." && pwd -P)"
cd "${ROOT}"

MODE="${1:-check}"
SERVERS_CONF="${SERVERS_CONF:-${ROOT}/base/new-many-server/servers.conf}"
GRAPH="${GRAPH:-vldb}"
PARTITION_DIR="${PARTITION_DIR:-}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-.}"
SSH_OPTS="${SSH_OPTS:--o ConnectTimeout=10}"
DRY_RUN="${DRY_RUN:-0}"

[[ -f "${SERVERS_CONF}" ]] || { echo "[FATAL] servers.conf がありません: ${SERVERS_CONF}" >&2; exit 1; }
HOSTS=(); IPS=()
while IFS= read -r line || [[ -n "${line}" ]]; do
  line="${line%%#*}"
  [[ -z "${line// /}" ]] && continue
  read -r _host _id _ip _port _rest <<<"${line}"
  [[ -z "${_host}" || -z "${_port}" ]] && continue
  HOSTS+=("${_host}"); IPS+=("${_ip}")
done <"${SERVERS_CONF}"
((${#HOSTS[@]} >= 1)) || { echo "[FATAL] servers.conf からホストを読めません" >&2; exit 1; }
# SSH_USER を指定したら、servers.conf の host 別名ではなく「SSH_USER@ip」で接続する
# （コントローラをリモートのマシンで動かすとき、そのマシンに ~/.ssh/config の別名が無くても繋がるように）
if [[ -n "${SSH_USER:-}" ]]; then
  for ((i = 0; i < ${#HOSTS[@]}; i++)); do HOSTS[i]="${SSH_USER}@${IPS[i]}"; done
fi


for g in ${GRAPH}; do
  [[ -f "dataset/Louvain/graph/${g}.gr" ]] || { echo "[FATAL] 手元にグラフがありません: dataset/Louvain/graph/${g}.gr" >&2; exit 1; }
done
if [[ -n "${PARTITION_DIR}" ]]; then
  n=$(ls "${PARTITION_DIR}"/node_to_starts_server*.json 2>/dev/null | wc -l | tr -d ' ')
  ((n == ${#HOSTS[@]})) || { echo "[FATAL] ${PARTITION_DIR} の deny テーブルが ${n} 個（servers.conf は ${#HOSTS[@]} 台。全 K 個が必要）" >&2; exit 1; }
fi

echo ">>> mode=${MODE} hosts=${HOSTS[*]} graph=${GRAPH} partition=${PARTITION_DIR:-なし} remote=${REMOTE_REPO_DIR} dry_run=${DRY_RUN}"

cmd_check() {
  local ng=0 out
  for h in "${HOSTS[@]}"; do
    # shellcheck disable=SC2086
    if out="$(ssh ${SSH_OPTS} "${h}" "cd ${REMOTE_REPO_DIR} && echo host=\$(hostname) && python3 -V 2>&1 && \
        for g in ${GRAPH}; do if [ -f dataset/Louvain/graph/\$g.gr ]; then echo graph:\$g=あり; else echo graph:\$g=なし; fi; done && \
        if [ -f base/proposed_cache/core/split_remote_server_reuse_score.py ]; then echo core=あり; else echo core=なし; fi && \
        if [ -f base/proposed_cache/adaptive/server.py ]; then echo adaptive=あり; else echo adaptive=なし; fi" 2>&1)"; then
      echo "[OK]  ${h}: $(echo "${out}" | tr '\n' ' ')"
    else
      echo "[NG]  ${h}: SSH できません（VPN・~/.ssh/config を確認）: $(echo "${out}" | tail -1)"
      ng=$((ng + 1))
    fi
  done
  ((ng == 0)) || { echo "[FATAL] ${ng} 台に接続できません" >&2; return 1; }
}

cmd_push() {
  local ropt=(-az)
  [[ "${DRY_RUN}" == "1" ]] && ropt+=(-vn)
  local R="${REMOTE_REPO_DIR}"
  for h in "${HOSTS[@]}"; do
    echo "=== [${h}] 配布 ==="
    # shellcheck disable=SC2086
    ssh ${SSH_OPTS} "${h}" "mkdir -p ${R}/base/proposed_cache/core/multi ${R}/base/proposed_cache/adaptive \
        ${R}/base/proposed_cache/adaptive/results ${R}/dataset/Louvain/graph${PARTITION_DIR:+ ${R}/${PARTITION_DIR}}" || return 1
    rsync "${ropt[@]}" -e "ssh ${SSH_OPTS}" base/proposed_cache/core/*.py "${h}:${R}/base/proposed_cache/core/" || return 1
    rsync "${ropt[@]}" -e "ssh ${SSH_OPTS}" base/proposed_cache/core/multi/partition.py "${h}:${R}/base/proposed_cache/core/multi/" || return 1
    rsync "${ropt[@]}" -e "ssh ${SSH_OPTS}" base/proposed_cache/adaptive/*.py "${h}:${R}/base/proposed_cache/adaptive/" || return 1
    for g in ${GRAPH}; do
      rsync "${ropt[@]}" -e "ssh ${SSH_OPTS}" "dataset/Louvain/graph/${g}.gr" "${h}:${R}/dataset/Louvain/graph/" || return 1
    done
    if [[ -n "${PARTITION_DIR}" ]]; then
      rsync "${ropt[@]}" -e "ssh ${SSH_OPTS}" "${PARTITION_DIR}"/node_to_starts_server*.json "${h}:${R}/${PARTITION_DIR}/" || return 1
    fi
    echo "    -> ${h} 完了"
  done
}

case "${MODE}" in
  check) cmd_check ;;
  push)  cmd_push ;;
  all)   cmd_check && cmd_push && cmd_check ;;
  *) echo "usage: deploy.sh {check|push|all}" >&2; exit 1 ;;
esac
