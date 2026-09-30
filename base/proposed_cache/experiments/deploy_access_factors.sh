#!/usr/bin/env bash
# =============================================================================
# deploy_access_factors.sh — cap サーバで access-factor 実験を回すための環境整備
#
#   使い方:
#     bash deploy_access_factors.sh check  [host]   # 接続と python の確認
#     bash deploy_access_factors.sh push   [host]   # コードとグラフを転送
#     bash deploy_access_factors.sh run    [host]   # 収集 (collect) をリモート実行
#     bash deploy_access_factors.sh fetch  [host]   # 結果 CSV を手元に回収
#     bash deploy_access_factors.sh all    [host]   # check → push → run → fetch
#
#   host 既定は cap11 (~/.ssh/config のエイリアス)。
#   転送先は $REMOTE_DIR (既定 ~/access_factors)。
#
#   収集フェーズは **標準ライブラリだけ** で動く (numpy 不要)。
#   統計と作図は手元で --from-keys を使って行う。
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PC="$(dirname "$HERE")"                      # proposed_cache/
REPO="$(cd "$PC/../.." && pwd)"              # master-progrem/
GRAPH_SRC="${GRAPH_SRC:-$REPO/dataset/Louvain/graph}"

HOST="${2:-${HOST:-cap11}}"
REMOTE_DIR="${REMOTE_DIR:-access_factors}"   # リモートの $HOME からの相対
SSH_OPTS="${SSH_OPTS:--o ConnectTimeout=10}"

# conf から必要な .gr だけ抜き出す
graph_files() {
  while read -r _lbl fil _rest; do
    case "$_lbl" in ''|\#*) continue ;; esac
    echo "$fil"
  done < "$HERE/graphs_access_factors.conf"
}

cmd_check() {
  echo "== $HOST への接続と python を確認 =="
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$HOST" '
    echo "host=$(hostname)  user=$(whoami)  home=$HOME"
    python3 -V || echo "!! python3 が無い"
    python3 -c "import numpy, sys; print(\"numpy\", numpy.__version__)" 2>/dev/null \
      || echo "numpy: なし (収集フェーズだけなら不要)"
    df -h "$HOME" | tail -1
  '
}

cmd_push() {
  echo "== コードを $HOST:$REMOTE_DIR へ転送 =="
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$HOST" "mkdir -p $REMOTE_DIR/core $REMOTE_DIR/experiments $REMOTE_DIR/graphs $REMOTE_DIR/results"
  rsync -av -e "ssh $SSH_OPTS" \
    "$PC/core/split_remote_server_reuse_score.py" \
    "$PC/core/virtual_server_sim_reuse_score.py" \
    "$HOST:$REMOTE_DIR/core/"
  rsync -av -e "ssh $SSH_OPTS" \
    "$HERE/analyze_access_factors.py" \
    "$HERE/aggregate_access_factors.py" \
    "$HERE/analyze_factor_interplay.py" \
    "$HERE/analyze_fetched_keys.sh" \
    "$HERE/run_access_factors_graphs.sh" \
    "$HERE/graphs_access_factors.conf" \
    "$HOST:$REMOTE_DIR/experiments/"
  echo "== グラフを転送 (既にあるものは skip) =="
  for f in $(graph_files); do
    if [ -f "$GRAPH_SRC/$f" ]; then
      rsync -av --partial --inplace -e "ssh $SSH_OPTS" \
        "$GRAPH_SRC/$f" "$HOST:$REMOTE_DIR/graphs/"
    else
      echo "!! $GRAPH_SRC/$f が手元に無い"
    fi
  done
}

cmd_run() {
  echo "== $HOST で収集を実行 (MODE=collect) =="
  # shellcheck disable=SC2086
  ssh $SSH_OPTS "$HOST" "cd $REMOTE_DIR && \
    MODE=collect ALPHAS='${ALPHAS:-0.1 0.01}' WALKS='${WALKS:-1000}' \
    STARTS='${STARTS:-8}' ${GRAPHS:+GRAPHS='$GRAPHS'} \
    bash experiments/run_access_factors_graphs.sh 2>&1 | tail -80"
}

cmd_fetch() {
  echo "== 結果を手元へ回収 =="
  mkdir -p "$PC/results/cap"
  rsync -av -e "ssh $SSH_OPTS" \
    "$HOST:$REMOTE_DIR/results/accessfactors_*" "$PC/results/cap/"
  echo
  echo "手元で統計・作図するには (回収した全部を一括):"
  echo "  bash $HERE/analyze_fetched_keys.sh"
  echo "1本だけなら:"
  echo "  python3 $HERE/analyze_access_factors.py --from-keys $PC/results/cap/accessfactors_<label>_a<α>_w1000_s8_keys.csv \\"
  echo "    --label <label> --alpha <α> --walks 1000 --num-starts 8 --cap 100"
}

case "${1:-check}" in
  check) cmd_check ;;
  push)  cmd_push ;;
  run)   cmd_run ;;
  fetch) cmd_fetch ;;
  all)   cmd_check; cmd_push; cmd_run; cmd_fetch ;;
  *) echo "usage: bash $0 {check|push|run|fetch|all} [host]"; exit 2 ;;
esac
