#!/usr/bin/env bash
set -euo pipefail
###############################################################################
# 分割フェーズ (任意K) — このフォルダ内だけで自己完結して動く版
#
#   グラフを METIS で K 分割し、node_to_starts_server{0..K-1}.json を生成する。
#   認可の意味論は 2 通り (サーバは deny 方式で解釈する点に注意):
#
#   ● --mode deny (デフォルト, 本番と同じ):
#       各 entity -> 「その entity を NG(拒否) にする start の集合」= 疎リスト。省メモリ。
#       生成: metis.py → gpmetis → create_json_table_auth_table.py(--partitioner-type metis)
#
#   ● --mode allow (比較用):
#       各 entity -> 「その entity を許可する start の集合」= 密リスト。
#       ※ 現行サーバは deny 解釈なので、allow データを入れると認可が反転する点に注意。
#       生成: create_json_table.py → metis.py → gpmetis → only_split_auth_tables.py(metis)
#
# 使い方:
#   bash base/new-many-server/partition.sh --graph karate --server-count 3
#   bash base/new-many-server/partition.sh --graph vldb -k 4 --starts "0"      # 大規模は starts 限定推奨
#   bash base/new-many-server/partition.sh --graph karate -k 3 --mode allow
#
# 出力 (すべてこのフォルダ配下 = 自己完結):
#   data/metis/<graph>/bipartite.metis[.part.K]
#   data/splits/<graph>/<ng>/node_to_starts_server{0..K-1}.json
#
# 前提: dataset/Louvain/graph/<graph>.gr が repo ルートに存在 / gpmetis が PATH。
#   node-shift: 0始まりグラフ=1, 1始まり=0 (metis と split で自動一致)。
###############################################################################

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SELF_DIR}/../.." && pwd)"
CODE="${SELF_DIR}/code"
DATA="${SELF_DIR}/data"

GRAPH=""
K=""
NG_RATIO="0.3"
NODE_SHIFT="1"
MODE="deny"
STARTS="all"
SEED="0"
OUT_DIR=""

usage() {
  cat <<EOF
Usage: bash base/new-many-server/partition.sh --graph NAME --server-count K [options]
  --graph NAME          Graph name (uses dataset/Louvain/graph/NAME.gr). Required.
  --server-count K, -k  Number of partitions/servers (K >= 2). Required.
  --mode deny|allow     認可の意味論。Default: deny (本番と同じ・省メモリ)
  --ng-ratio FLOAT      NG ratio. Default: 0.3
  --starts SPEC         deny生成の対象start ("all"|"0"|"1-10"|"1,3,7"). Default: all
                        (大規模グラフは all だと爆発するので実験で使う start に限定推奨)
  --seed N              deny生成の乱数seed. Default: 0
  --node-shift N        1 for 0-based graphs, 0 for 1-based. Default: 1
  --out-dir PATH        Split output dir. Default: <self>/data/splits/<graph>/<ng>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph)            GRAPH="${2:-}"; shift 2 ;;
    --server-count|-k)  K="${2:-}"; shift 2 ;;
    --mode)             MODE="${2:-}"; shift 2 ;;
    --ng-ratio)         NG_RATIO="${2:-}"; shift 2 ;;
    --starts)           STARTS="${2:-}"; shift 2 ;;
    --seed)             SEED="${2:-}"; shift 2 ;;
    --node-shift)       NODE_SHIFT="${2:-}"; shift 2 ;;
    --out-dir)          OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -z "${GRAPH}" ]] && { echo "--graph is required" >&2; usage; exit 1; }
[[ -z "${K}" ]] && { echo "--server-count is required" >&2; usage; exit 1; }
if ! [[ "${K}" =~ ^[0-9]+$ ]] || (( K < 2 )); then
  echo "--server-count must be an integer >= 2 (got '${K}')" >&2; exit 1
fi
[[ "${MODE}" == "deny" || "${MODE}" == "allow" ]] || { echo "--mode must be deny|allow" >&2; exit 1; }
command -v gpmetis >/dev/null 2>&1 || { echo "gpmetis not found in PATH (brew install metis)" >&2; exit 1; }

EDGE_FILE="${REPO_ROOT}/dataset/Louvain/graph/${GRAPH}.gr"
[[ -f "${EDGE_FILE}" ]] || { echo "edge file not found: ${EDGE_FILE}" >&2; exit 1; }

METIS_DIR="${DATA}/metis/${GRAPH}"
METIS_FILE="${METIS_DIR}/bipartite.metis"
PART_FILE="${METIS_FILE}.part.${K}"
[[ -z "${OUT_DIR}" ]] && OUT_DIR="${DATA}/splits/${GRAPH}/${NG_RATIO}"
mkdir -p "${METIS_DIR}" "${OUT_DIR}"

echo "=================================================================="
echo ">>> [partition] graph=${GRAPH} K=${K} mode=${MODE} ng=${NG_RATIO} node-shift=${NODE_SHIFT} starts=${STARTS}"
echo ">>> edge=${EDGE_FILE}"
echo ">>> out-dir=${OUT_DIR}"
echo "=================================================================="

# --- 共通: 2部METISグラフ → gpmetis K ---
echo ">>> [metis] build bipartite METIS graph -> ${METIS_FILE}"
python3 "${CODE}/metis.py" --edges "${EDGE_FILE}" --out "${METIS_FILE}" --node-shift "${NODE_SHIFT}"
echo ">>> [gpmetis] ${METIS_FILE} ${K}"
gpmetis "${METIS_FILE}" "${K}" >/dev/null
[[ -f "${PART_FILE}" ]] || { echo "gpmetis did not produce ${PART_FILE}" >&2; exit 1; }
echo "    -> ${PART_FILE}"

if [[ "${MODE}" == "deny" ]]; then
  echo ">>> [split:deny] create_json_table_auth_table.py (metis) -> ${K} shards"
  python3 "${CODE}/create_json_table_auth_table.py" \
    --edges "${EDGE_FILE}" \
    --ng-ratio "${NG_RATIO}" \
    --seed "${SEED}" \
    --server-count "${K}" \
    --partitioner-type metis \
    --metis-partition-file "${PART_FILE}" \
    --metis-base 0 \
    --metis-node-shift "${NODE_SHIFT}" \
    --starts "${STARTS}" \
    --out-dir "${OUT_DIR}"
else
  TABLE_DIR="${DATA}/tables/${GRAPH}"; mkdir -p "${TABLE_DIR}"
  NTS_FILE="${TABLE_DIR}/node_to_starts_${NG_RATIO}.json"
  echo ">>> [table:allow] create_json_table.py -> ${NTS_FILE}"
  python3 "${CODE}/create_json_table.py" "${EDGE_FILE}" --ng-ratio "${NG_RATIO}" --out-dir "${TABLE_DIR}"
  echo ">>> [split:allow] only_split_auth_tables.py (metis) -> ${K} shards"
  python3 "${CODE}/only_split_auth_tables.py" \
    --node-to-starts-file "${NTS_FILE}" \
    --server-count "${K}" \
    --partitioner-type metis \
    --metis-partition-file "${PART_FILE}" \
    --metis-base 0 \
    --metis-use-bipartite-edges \
    --edges "${EDGE_FILE}" \
    --metis-node-shift "${NODE_SHIFT}" \
    --out-dir "${OUT_DIR}"
fi

echo "=================================================================="
echo ">>> [DONE] mode=${MODE} generated shards:"
for ((sid=0; sid<K; sid++)); do
  f="${OUT_DIR}/node_to_starts_server${sid}.json"
  if [[ -f "$f" ]]; then
    n=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$f")
    echo "    server${sid}: ${n} entities  (${f})"
  else
    echo "    server${sid}: MISSING (${f})" >&2
  fi
done
echo ">>> 次: deploy.sh で全ホストへ配布 → run_rw.sh で実行。"
echo "=================================================================="
