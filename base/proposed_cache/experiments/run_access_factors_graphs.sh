#!/usr/bin/env bash
# =============================================================================
# run_access_factors_graphs.sh
#   複数グラフ × 複数 α で analyze_access_factors.py を回す。
#
#   使い方:
#     bash run_access_factors_graphs.sh                 # 全グラフ・全α (作図まで)
#     MODE=collect bash run_access_factors_graphs.sh    # 収集だけ (numpy 不要)
#     GRAPHS="vldb slashdot" ALPHAS=0.1 bash run_access_factors_graphs.sh
#
#   環境変数:
#     MODE     full (既定) | collect        collect は CSV だけ書いて終了
#     GRAPHS   conf の label を空白区切りで  (既定: conf の全行)
#     ALPHAS   "0.1 0.01"  WALKS 1000  STARTS 8  CAP 100  SPLIT 0.5
#     EXTRA    analyze_access_factors.py へ素通しする追加引数 (例: --no-figs)
#     GRAPH_DIR  .gr の置き場 (既定: <root>/graphs があればそこ、無ければ repo の dataset)
#     OUT_DIR    出力先       (既定: <proposed_cache>/results)
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PC="$(dirname "$HERE")"                 # proposed_cache/
CONF="${CONF:-$HERE/graphs_access_factors.conf}"

if [ -z "${GRAPH_DIR:-}" ]; then
  if [ -d "$PC/graphs" ]; then GRAPH_DIR="$PC/graphs"
  else GRAPH_DIR="$(cd "$PC/../.." && pwd)/dataset/Louvain/graph"; fi
fi
OUT_DIR="${OUT_DIR:-$PC/results}"
FIG_DIR="${FIG_DIR:-$PC/figs}"
MODE="${MODE:-full}"
ALPHAS="${ALPHAS:-0.1 0.01}"
WALKS="${WALKS:-1000}"
STARTS="${STARTS:-32}"
CAP="${CAP:-100}"
SPLIT="${SPLIT:-0.5}"
PY="${PY:-python3}"

mkdir -p "$OUT_DIR" "$FIG_DIR"

# conf -> "label file" の並び (bash 3.2 でも動くように mapfile は使わない)
LABELS=(); FILES=()
while read -r lbl fil _rest; do
  case "$lbl" in ''|\#*) continue ;; esac
  LABELS+=("$lbl"); FILES+=("$fil")
done < "$CONF"

WANT="${GRAPHS:-}"
echo "graph_dir=$GRAPH_DIR"
echo "out_dir=$OUT_DIR  mode=$MODE  alphas=[$ALPHAS]  walks=$WALKS  starts=$STARTS"
echo

for i in "${!LABELS[@]}"; do
  label="${LABELS[$i]}"; file="${FILES[$i]}"
  if [ -n "$WANT" ] && ! echo " $WANT " | grep -q " $label "; then continue; fi
  gr="$GRAPH_DIR/$file"
  if [ ! -f "$gr" ]; then echo "!! skip $label: $gr が無い"; continue; fi
  for a in $ALPHAS; do
    echo "=== $label  alpha=$a ==============================================="
    extra="${EXTRA:-}"
    [ "$MODE" = "collect" ] && extra="$extra --collect-only"
    # shellcheck disable=SC2086
    $PY "$HERE/analyze_access_factors.py" \
      --edges "$gr" --label "$label" --alpha "$a" \
      --walks "$WALKS" --num-starts "$STARTS" --cap "$CAP" --split "$SPLIT" \
      --largest-component \
      --out-dir "$OUT_DIR" --fig-dir "$FIG_DIR" $extra
    echo
  done
done
echo "完了。横断比較: $PY $HERE/aggregate_access_factors.py"
