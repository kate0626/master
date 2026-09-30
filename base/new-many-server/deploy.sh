#!/usr/bin/env bash
set -euo pipefail
###############################################################################
# servers.conf の全ホストへ、実行に必要なファイルを rsync 配布する。
#
#   各ホストに配るもの (リモート home = /home/kate 直下に repo 相対で配置):
#     - base/new-many-server/code/                         (実行本体)
#     - base/new-many-server/data/splits/<graph>/<ng>/     (★全K shard: owner_map用)
#     - dataset/Louvain/graph/<graph>.gr                   (グラフ本体)
#
# 使い方:
#   bash base/new-many-server/deploy.sh --graph vldb                 # servers.conf 全ホストへ
#   bash base/new-many-server/deploy.sh --graph karate --ng-ratio 0.3 --dry-run
#
# 前提: 先に partition.sh で shard を生成済みであること。
###############################################################################

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
SERVERS_CONF="${SERVERS_CONF:-${SELF_DIR}/servers.conf}"

GRAPH=""
NG_RATIO="0.3"
DRY_RUN=0
REMOTE_HOME="."     # ssh ログイン先(=/home/kate)からの相対

usage() {
  cat <<EOF
Usage: bash base/new-many-server/deploy.sh --graph NAME [options]
  --graph NAME     グラフ名 (必須)
  --ng-ratio F     NG ratio (default: 0.3)
  --dry-run        rsync -n で送信内容だけ表示
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph)     GRAPH="${2:-}"; shift 2 ;;
    --ng-ratio)  NG_RATIO="${2:-}"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done
[[ -z "${GRAPH}" ]] && { echo "--graph is required" >&2; usage; exit 1; }
[[ -f "${SERVERS_CONF}" ]] || { echo "servers.conf not found: ${SERVERS_CONF}" >&2; exit 1; }

SPLIT_DIR="${SELF_DIR}/data/splits/${GRAPH}/${NG_RATIO}"
EDGE_FILE="${REPO_ROOT}/dataset/Louvain/graph/${GRAPH}.gr"
[[ -d "${SPLIT_DIR}" ]] || { echo "shard dir not found (先に partition.sh を実行): ${SPLIT_DIR}" >&2; exit 1; }
[[ -f "${EDGE_FILE}" ]] || { echo "edge file not found: ${EDGE_FILE}" >&2; exit 1; }

RSYNC_OPT=(-az)
(( DRY_RUN == 1 )) && RSYNC_OPT+=(-vn)

# ホスト一覧を servers.conf から取得
HOSTS=()
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"; [[ -z "${line// }" ]] && continue
  read -r h _id _ip _port <<< "${line}"
  [[ -n "$h" ]] && HOSTS+=("$h")
done < "${SERVERS_CONF}"
(( ${#HOSTS[@]} >= 1 )) || { echo "no hosts in ${SERVERS_CONF}" >&2; exit 1; }

echo ">>> deploy graph=${GRAPH} ng=${NG_RATIO} hosts=${HOSTS[*]}  (dry-run=${DRY_RUN})"
for h in "${HOSTS[@]}"; do
  echo "=== [${h}] 配布 ==="
  ssh "$h" "mkdir -p ${REMOTE_HOME}/base/new-many-server/code \
                     ${REMOTE_HOME}/base/new-many-server/data/splits/${GRAPH}/${NG_RATIO} \
                     ${REMOTE_HOME}/base/new-many-server/data/logs \
                     ${REMOTE_HOME}/dataset/Louvain/graph"
  rsync "${RSYNC_OPT[@]}" "${SELF_DIR}/code/"        "${h}:base/new-many-server/code/"
  rsync "${RSYNC_OPT[@]}" "${SPLIT_DIR}/"            "${h}:base/new-many-server/data/splits/${GRAPH}/${NG_RATIO}/"
  rsync "${RSYNC_OPT[@]}" "${EDGE_FILE}"             "${h}:dataset/Louvain/graph/"
  echo "    -> ${h} 完了"
done
echo ">>> [DONE] 全 ${#HOSTS[@]} ホストへ配布${DRY_RUN:+ (dry-run)}。次: run_rw.sh を実行。"
