#!/usr/bin/env bash
# =============================================================================
# run_factor_analysis_all.sh
#   results/ にある **全グラフ・全条件の keys CSV** に対して、要素まわりの分析を
#   まとめて回す。RW は回さない (keys CSV を読むだけなので速い)。
#
#   使い方:
#     bash run_factor_analysis_all.sh                # 6ペア相関を all/node/edge で
#     WHAT=interplay bash run_factor_analysis_all.sh # 冗長/相乗 + Shapley を
#     WHAT=both KINDS="node edge" bash run_factor_analysis_all.sh
#
#   環境変数:
#     WHAT    paircorr (既定) | interplay | both
#     KINDS   "all node edge" (既定)
#     HOP     walk (既定) | walk_min | bfs      H の定義
#     SRC     keys CSV のあるディレクトリ (既定: ../results)
#     NO_FIGS 非空なら図を作らない
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PC="$(dirname "$HERE")"
SRC="${SRC:-$PC/results}"
WHAT="${WHAT:-paircorr}"
KINDS="${KINDS:-all node edge}"
HOP="${HOP:-walk}"
PY="${PY:-python3}"
extra=""
[ -n "${NO_FIGS:-}" ] && extra="--no-figs"

shopt -s nullglob
files=("$SRC"/accessfactors_*_keys.csv)
[ ${#files[@]} -eq 0 ] && { echo "$SRC に keys CSV が無い"; exit 1; }
echo "対象 ${#files[@]} 条件 × kinds=[$KINDS]  hop=$HOP  what=$WHAT"

for f in "${files[@]}"; do
  for k in $KINDS; do
    if [ "$WHAT" = "paircorr" ] || [ "$WHAT" = "both" ]; then
      # shellcheck disable=SC2086
      $PY "$HERE/analyze_pair_correlations.py" --keys "$f" --kind "$k" \
        --hop-metric "$HOP" $extra > /dev/null 2>&1 \
        && echo "  paircorr  $(basename "$f" _keys.csv) [$k]"
    fi
    if [ "$WHAT" = "interplay" ] || [ "$WHAT" = "both" ]; then
      # shellcheck disable=SC2086
      $PY "$HERE/analyze_factor_interplay.py" --keys "$f" --kind "$k" \
        --hop-metric "$HOP" $extra > /dev/null 2>&1 \
        && echo "  interplay $(basename "$f" _keys.csv) [$k]"
    fi
  done
done

echo
if [ "$WHAT" != "interplay" ]; then
  for k in $KINDS; do
    $PY "$HERE/aggregate_pair_correlations.py" --kind "$k" --hop "$HOP"
    echo
  done
fi
