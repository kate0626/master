#!/usr/bin/env bash
set -euo pipefail


# 大規模データを作成したい時
# auth-many-server 用の前処理を 1 本にまとめたラッパー。
  # ./base/auth-many-server/prepare_auth_data.sh \
  #   --graph com-friendster \
  #   --ng-ratio 0.3 \
  #   --server-count 2 \
  #   --partitioner-type node-edge-fixed \
  #   --mode deny-direct \
  #   --starts "1"
# --starts "1-10"
# nohup   ./base/auth-many-server/prepare_auth_data.sh --graph com-friendster     --ng-ratio 0.3     --server-count 2     --partitioner-type node-edge-fixed     --mode deny-direct    --starts "388990" > run.log 2>&1 &



usage() {
  cat <<'EOF'
Usage:
  bash base/auth-many-server/prepare_auth_data.sh [options]

Required:
  --graph NAME                     Graph name. Uses dataset/Louvain/graph/NAME.gr

Main options:
  --mode MODE                      One of: allow-split, deny-direct
                                   Default: allow-split
  --ng-ratio FLOAT                 NG ratio. Default: 0.3
  --server-count N                 Server count. Default: 2
  --partitioner-type TYPE          modulo | node-edge-fixed | community | metis
                                   Default: node-edge-fixed
  --seed N                         Seed for deny-direct mode. Default: 0
  --starts SPEC                    Starts spec for deny-direct mode. Default: all
  --out-dir PATH                   Output directory. Default depends on mode

Allow/split mode options:
  --emit-auth-table                Also emit auth_by_start.json before splitting

Community/METIS options:
  --community-file PATH            Required when --partitioner-type community
  --metis-partition-file PATH      Required when --partitioner-type metis
  --metis-base N                   Default: 0
  --metis-use-bipartite-edges      Pass through to only_split_auth_tables.py
  --metis-node-shift N             Default: 0

Copy options:
  --copy-target DEST               scp edge file to DEST. Repeatable.
  --copy-only                      Only run scp and exit

Misc:
  --dry-run                        Print commands without executing
  -h, --help                       Show this help

Notes:
  allow-split:
    1. create_json_table.py で base/auth-many-server/<graph>/node_to_starts_<ratio>.json を作る
    2. only_split_auth_tables.py で server ごとに分割する

  deny-direct:
    create_json_table_auth_table.py を使って、
    server ごとの deny テーブルを out-dir に直接出力する
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

GRAPH=""
MODE="allow-split"
NG_RATIO="0.3"
SERVER_COUNT="2"
PARTITIONER_TYPE="node-edge-fixed"
SEED="0"
STARTS="all"
OUT_DIR=""
EMIT_AUTH_TABLE=0
DRY_RUN=0
COPY_ONLY=0

COMMUNITY_FILE=""
METIS_PARTITION_FILE=""
METIS_BASE="0"
METIS_USE_BIPARTITE_EDGES=0
METIS_NODE_SHIFT="0"

COPY_TARGETS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph)
      GRAPH="${2:-}"; shift 2 ;;
    --mode)
      MODE="${2:-}"; shift 2 ;;
    --ng-ratio)
      NG_RATIO="${2:-}"; shift 2 ;;
    --server-count)
      SERVER_COUNT="${2:-}"; shift 2 ;;
    --partitioner-type)
      PARTITIONER_TYPE="${2:-}"; shift 2 ;;
    --seed)
      SEED="${2:-}"; shift 2 ;;
    --starts)
      STARTS="${2:-}"; shift 2 ;;
    --out-dir)
      OUT_DIR="${2:-}"; shift 2 ;;
    --emit-auth-table)
      EMIT_AUTH_TABLE=1; shift ;;
    --community-file)
      COMMUNITY_FILE="${2:-}"; shift 2 ;;
    --metis-partition-file)
      METIS_PARTITION_FILE="${2:-}"; shift 2 ;;
    --metis-base)
      METIS_BASE="${2:-}"; shift 2 ;;
    --metis-use-bipartite-edges)
      METIS_USE_BIPARTITE_EDGES=1; shift ;;
    --metis-node-shift)
      METIS_NODE_SHIFT="${2:-}"; shift 2 ;;
    --copy-target)
      COPY_TARGETS+=("${2:-}"); shift 2 ;;
    --copy-only)
      COPY_ONLY=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ -z "${GRAPH}" ]]; then
  echo "--graph is required" >&2
  usage
  exit 1
fi

EDGE_FILE="dataset/Louvain/graph/${GRAPH}.gr"
if [[ ! -f "${EDGE_FILE}" ]]; then
  echo "Edge file not found: ${EDGE_FILE}" >&2
  exit 1
fi

run_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    "$@"
  fi
}

if [[ "${#COPY_TARGETS[@]}" -gt 0 ]]; then
  for dest in "${COPY_TARGETS[@]}"; do
    run_cmd scp -r "${EDGE_FILE}" "${dest}"
  done
fi

if [[ "${COPY_ONLY}" -eq 1 ]]; then
  exit 0
fi

case "${MODE}" in
  allow-split)
    if [[ -z "${OUT_DIR}" ]]; then
      OUT_DIR="base/auth-many-server/data/splits/${GRAPH}/${NG_RATIO}"
    fi

    NODE_TO_STARTS_FILE="base/auth-many-server/${GRAPH}/node_to_starts_${NG_RATIO}.json"
    AUTH_FILE="base/auth-many-server/${GRAPH}/auth_by_start.json"

    CREATE_CMD=(
      python3
      base/auth-many-server/create_json_table.py
      "${EDGE_FILE}"
      --ng-ratio "${NG_RATIO}"
    )
    if [[ "${EMIT_AUTH_TABLE}" -eq 1 ]]; then
      CREATE_CMD+=(--emit-auth-table)
    fi

    run_cmd "${CREATE_CMD[@]}"

    SPLIT_CMD=(
      python3
      base/auth-many-server/only_split_auth_tables.py
      --node-to-starts-file "${NODE_TO_STARTS_FILE}"
      --server-count "${SERVER_COUNT}"
      --partitioner-type "${PARTITIONER_TYPE}"
      --out-dir "${OUT_DIR}"
    )

    if [[ "${EMIT_AUTH_TABLE}" -eq 1 ]]; then
      SPLIT_CMD+=(--auth-file "${AUTH_FILE}")
    fi

    case "${PARTITIONER_TYPE}" in
      community)
        if [[ -z "${COMMUNITY_FILE}" ]]; then
          echo "--community-file is required for partitioner-type=community" >&2
          exit 1
        fi
        SPLIT_CMD+=(--community-file "${COMMUNITY_FILE}")
        ;;
      metis)
        if [[ -z "${METIS_PARTITION_FILE}" ]]; then
          echo "--metis-partition-file is required for partitioner-type=metis" >&2
          exit 1
        fi
        SPLIT_CMD+=(--metis-partition-file "${METIS_PARTITION_FILE}" --metis-base "${METIS_BASE}")
        if [[ "${METIS_USE_BIPARTITE_EDGES}" -eq 1 ]]; then
          SPLIT_CMD+=(--metis-use-bipartite-edges --edges "${EDGE_FILE}" --metis-node-shift "${METIS_NODE_SHIFT}")
        fi
        ;;
    esac

    run_cmd "${SPLIT_CMD[@]}"

    echo "Done: allow-split"
    echo "  graph: ${GRAPH}"
    echo "  node_to_starts: ${NODE_TO_STARTS_FILE}"
    if [[ "${EMIT_AUTH_TABLE}" -eq 1 ]]; then
      echo "  auth_by_start: ${AUTH_FILE}"
    fi
    echo "  split_dir: ${OUT_DIR}"
    ;;

  deny-direct)
    if [[ -z "${OUT_DIR}" ]]; then
      OUT_DIR="base/auth-many-server/data/splits/${GRAPH}/${NG_RATIO}"
    fi

    DENY_CMD=(
      python3
      -u
      base/auth-many-server/create_json_table_auth_table.py
      --edges "${EDGE_FILE}"
      --ng-ratio "${NG_RATIO}"
      --seed "${SEED}"
      --server-count "${SERVER_COUNT}"
      --partitioner-type "${PARTITIONER_TYPE}"
      --starts "${STARTS}"
      --out-dir "${OUT_DIR}"
    )

    if [[ "${PARTITIONER_TYPE}" != "modulo" && "${PARTITIONER_TYPE}" != "node-edge-fixed" ]]; then
      echo "deny-direct currently supports partitioner-type=modulo or node-edge-fixed" >&2
      exit 1
    fi

    run_cmd "${DENY_CMD[@]}"

    echo "Done: deny-direct"
    echo "  graph: ${GRAPH}"
    echo "  out_dir: ${OUT_DIR}"
    echo "  starts: ${STARTS}"
    ;;

  *)
    echo "Unsupported mode: ${MODE}" >&2
    usage
    exit 1
    ;;
esac
