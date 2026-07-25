#!/usr/bin/env bash
set -euo pipefail
###############################################################################
# グラフを METIS で K 分割し、node_to_starts_server{0..K-1}.json を生成する。
#
#   1) metis.py           : エッジリスト → 2部 METIS グラフ (bipartite.metis)
#   2) gpmetis file K     : K 分割 → bipartite.metis.part.K
#   3) prepare_auth_data.sh (allow-split, --partitioner-type metis)
#                         : node_to_starts を K サーバへ排他分割
#
# 使い方:
#   bash base/auth-many-server/partition_metis.sh --graph vldb --server-count 4
#   bash base/auth-many-server/partition_metis.sh --graph amazon0601 -k 8 --ng-ratio 0.3
#
# 出力:
#   base/auth-many-server/data/splits/<graph>/<ng-ratio>/node_to_starts_server{0..K-1}.json
#
# 注意:
#   - node-shift は「0始まりグラフなら 1、1始まりグラフなら 0」。metis.py と split 側で必ず一致させる。
#     (partition_metis.sh は両者に同じ値を渡すので、このフラグ 1 つだけ合わせればよい)
#   - gpmetis が PATH に必要 (brew install metis 等)。
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

GRAPH=""
K=""
NG_RATIO="0.3"
NODE_SHIFT="1"
OUT_DIR=""

usage() {
  cat <<EOF
Usage: bash base/auth-many-server/partition_metis.sh --graph NAME --server-count K [options]
  --graph NAME          Graph name (uses dataset/Louvain/graph/NAME.gr). Required.
  --server-count K, -k  Number of partitions/servers (K >= 2). Required.
  --ng-ratio FLOAT      NG ratio for node_to_starts. Default: 0.3
  --node-shift N        1 for 0-based graphs, 0 for 1-based. Default: 1
  --out-dir PATH        Split output dir. Default: base/auth-many-server/data/splits/<graph>/<ng-ratio>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph)        GRAPH="${2:-}"; shift 2 ;;
    --server-count|-k) K="${2:-}"; shift 2 ;;
    --ng-ratio)     NG_RATIO="${2:-}"; shift 2 ;;
    --node-shift)   NODE_SHIFT="${2:-}"; shift 2 ;;
    --out-dir)      OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -z "${GRAPH}" ]] && { echo "--graph is required" >&2; usage; exit 1; }
[[ -z "${K}" ]] && { echo "--server-count is required" >&2; usage; exit 1; }
if ! [[ "${K}" =~ ^[0-9]+$ ]] || (( K < 2 )); then
  echo "--server-count must be an integer >= 2 (got '${K}')" >&2; exit 1
fi
command -v gpmetis >/dev/null 2>&1 || { echo "gpmetis not found in PATH (brew install metis)" >&2; exit 1; }

EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
[[ -f "${EDGE_FILE}" ]] || { echo "edge file not found: ${EDGE_FILE}" >&2; exit 1; }
METIS_FILE="base/auth-many-server/${GRAPH}/bipartite.metis"
PART_FILE="${METIS_FILE}.part.${K}"
[[ -z "${OUT_DIR}" ]] && OUT_DIR="base/auth-many-server/data/splits/${GRAPH}/${NG_RATIO}"

echo "=================================================================="
echo ">>> [partition_metis] graph=${GRAPH} K=${K} ng-ratio=${NG_RATIO} node-shift=${NODE_SHIFT}"
echo ">>> out-dir=${OUT_DIR}"
echo "=================================================================="

echo ">>> [1/3] build bipartite METIS graph -> ${METIS_FILE}"
python3 base/auth-many-server/metis.py --graph "${GRAPH}" --out "${METIS_FILE}" --node-shift "${NODE_SHIFT}"

echo ">>> [2/3] gpmetis ${METIS_FILE} ${K}"
gpmetis "${METIS_FILE}" "${K}" >/dev/null
[[ -f "${PART_FILE}" ]] || { echo "gpmetis did not produce ${PART_FILE}" >&2; exit 1; }
echo "    -> ${PART_FILE}"

echo ">>> [3/3] split node_to_starts into ${K} shards (metis partitioner)"
bash base/auth-many-server/prepare_auth_data.sh \
  --graph "${GRAPH}" \
  --ng-ratio "${NG_RATIO}" \
  --server-count "${K}" \
  --partitioner-type metis \
  --metis-partition-file "${PART_FILE}" \
  --metis-base 0 \
  --metis-use-bipartite-edges \
  --metis-node-shift "${NODE_SHIFT}" \
  --out-dir "${OUT_DIR}"

echo "=================================================================="
echo ">>> [DONE] generated shards:"
for ((sid=0; sid<K; sid++)); do
  f="${OUT_DIR}/node_to_starts_server${sid}.json"
  if [[ -f "$f" ]]; then
    n=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$f")
    echo "    server${sid}: ${n} entities  (${f})"
  else
    echo "    server${sid}: MISSING (${f})" >&2
  fi
done
echo ">>> 次はこの分割ファイルを各サーバへ配置し、servers.conf を K 行にして splits_reuse_score.sh を実行してください。"
echo "=================================================================="
