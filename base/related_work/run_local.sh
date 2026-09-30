#!/usr/bin/env bash
# Delegate processes, controller, RPC, and aggregation to the proposed runner.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
export SERVER_PY="base/related_work/server.py"
export POLICIES="${POLICIES:-none random fifo lru lfu lru_k gclock degree_static soop_admission lac_layout proposed}"
export START="${START:-0}"
export OUT_DIR="${OUT_DIR:-${HERE}/results/local_${GRAPH:-karate}_K${K:-2}_cap${CAP:-100}}"
bash "${HERE}/../proposed_cache/dist/run_local.sh"
# The upstream runner logs failures and continues: turn missing rows into failure.
python3 "${HERE}/verify_summary.py" --summary "${OUT_DIR}/summary.tsv" --policies ${POLICIES} --servers "${K:-2}"
