#!/usr/bin/env bash
# All machines must have the same checkout and graph/shards, as for proposed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
export REMOTE_SERVER_PY="base/related_work/server.py"
export POLICIES="${POLICIES:-none random fifo lru lfu lru_k gclock degree_static soop_admission lac_layout proposed}"
export OUT_DIR="${OUT_DIR:-${HERE}/results/cluster_${GRAPH:-vldb}_cap${CAP:-100}}"
bash "${HERE}/../proposed_cache/dist/run_cluster.sh"
python3 "${HERE}/verify_summary.py" --summary "${OUT_DIR}/summary.tsv" --policies ${POLICIES} \
  --servers-conf "${SERVERS_CONF:-${HERE}/../proposed_cache/dist/servers.conf}"
