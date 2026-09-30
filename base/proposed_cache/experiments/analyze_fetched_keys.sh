#!/usr/bin/env bash
# =============================================================================
# analyze_fetched_keys.sh
#   cap から回収したキー CSV (results/cap/accessfactors_*_keys.csv) を
#   まとめて統計・作図する。ファイル名から label/alpha/walks/starts を復元する。
#
#   使い方:
#     bash analyze_fetched_keys.sh                 # results/cap/ を全部
#     bash analyze_fetched_keys.sh <dir>           # 別ディレクトリ
#     NO_FIGS=1 bash analyze_fetched_keys.sh       # 図は作らない
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PC="$(dirname "$HERE")"
SRC="${1:-$PC/results/cap}"
OUT_DIR="${OUT_DIR:-$PC/results}"
FIG_DIR="${FIG_DIR:-$PC/figs}"
CAP="${CAP:-100}"
BINS="${BINS:-8}"
PY="${PY:-python3}"
extra=""
[ -n "${NO_FIGS:-}" ] && extra="--no-figs"

shopt -s nullglob
found=0
for f in "$SRC"/accessfactors_*_keys.csv; do
  found=1
  base="$(basename "$f")"
  rest="${base#accessfactors_}"; rest="${rest%_keys.csv}"
  starts="${rest##*_s}"
  rest2="${rest%_s*}"
  walks="${rest2##*_w}"
  rest3="${rest2%_w*}"
  alpha="${rest3##*_a}"
  label="${rest3%_a*}"
  echo "=== $label  alpha=$alpha  walks=$walks  starts=$starts ==="
  # shellcheck disable=SC2086
  $PY "$HERE/analyze_access_factors.py" --from-keys "$f" \
    --label "$label" --alpha "$alpha" --walks "$walks" --num-starts "$starts" \
    --cap "$CAP" --bins "$BINS" --out-dir "$OUT_DIR" --fig-dir "$FIG_DIR" $extra
  echo
done
[ "$found" = 0 ] && { echo "$SRC に accessfactors_*_keys.csv が無い"; exit 1; }
echo "横断比較: $PY $HERE/aggregate_access_factors.py"
