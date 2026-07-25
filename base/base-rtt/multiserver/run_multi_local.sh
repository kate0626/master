#!/bin/zsh
# proposed_cache と同じ流儀でローカル実行(スコア計算なし = --cache-policy none)。
# 2 サーバを modulo 分割で立て、サーバ間 handoff させつつ各歩に RTT を組み込む。
# 出力は multiserver/results/ に、ファイル名へ 歩数(walks)と α を含めて保存。
set -euo pipefail
cd "$(dirname "$0")"

# ---- 設定(環境変数で上書き可) ----
GRAPH=${GRAPH_OVERRIDE:-karate-graph}          # 例: com-amazon-connected
DIRECTION=${DIRECTION_OVERRIDE:-above}         # above / below / zigzag
MU=${MU_OVERRIDE:-50}; AMP=${AMP_OVERRIDE:-40}
CONVERGE=${CONVERGE_OVERRIDE:-40}              # 距離場の設計収束歩数
RW_WALKS=${RW_WALKS_OVERRIDE:-50}
ALPHA=${ALPHA_OVERRIDE:-0.02}
SEED=${SEED_OVERRIDE:-42}
CACHE_POLICY=${CACHE_POLICY_OVERRIDE:-none}    # none / lru / arc(スコア計算なしの範囲)
CACHE_CAP=${CACHE_CAP_OVERRIDE:-200}
NO_AUTH=${NO_AUTH_OVERRIDE:-0}                 # 1 で ① 認可なし
if [[ -n "${START_NODES_OVERRIDE:-}" ]]; then
  eval "START_NODES_LIST=(${START_NODES_OVERRIDE})"
else
  START_NODES_LIST=(0)
fi
PORTS=(8098 8099)

GRAPH_FILE="../../../dataset/Louvain/graph/${GRAPH}.gr"
RESULTS="results"
mkdir -p "$RESULTS"

# ---- 距離場(paint_field)。無ければ作る ----
FIELD="$RESULTS/field_${GRAPH}_${DIRECTION}.json"
[ -f "$FIELD" ] || PYTHONPATH="../../base" python3 ../single-server/paint_field.py --graph "$GRAPH_FILE" \
  --start "${START_NODES_LIST[1]}" --mu $MU --amp $AMP --direction "$DIRECTION" \
  --converge-steps $CONVERGE --walks 40 --out "$FIELD"

# ---- 出力名: 歩数と α を命名に含める(proposed_cache 流) ----
AUTHTAG=$([[ "$NO_AUTH" == "1" ]] && echo "noauth" || echo "auth")
TAG="${GRAPH}_walks${RW_WALKS}_alpha${ALPHA}_${DIRECTION}_${AUTHTAG}_${CACHE_POLICY}"
WALKS_OUT="$RESULTS/walks_${TAG}.jsonl"
rm -f "$WALKS_OUT"

# キャッシュ引数(none 以外は容量が要る)+ 認可なしトグル
if [[ "$CACHE_POLICY" == "none" ]]; then
  CACHE_ARGS=(--cache-policy none)
else
  CACHE_ARGS=(--cache-policy "$CACHE_POLICY" --cache-capacity "$CACHE_CAP")
fi
[[ "$NO_AUTH" == "1" ]] && CACHE_ARGS+=(--no-auth)

cleanup() { pkill -f split_remote_server_rtt.py 2>/dev/null || true; }
trap cleanup EXIT

# ---- 2 サーバ起動(スコアなし none, RTT 組み込み) ----
for id in 0 1; do
  python3 -u split_remote_server_rtt.py --server-id $id --server-count 2 \
    --edges "$GRAPH_FILE" --host 127.0.0.1 --port ${PORTS[$((id+1))]} \
    --server-endpoints 127.0.0.1:8098 127.0.0.1:8099 \
    "${CACHE_ARGS[@]}" --rtt-field "$FIELD" --walks-out "$WALKS_OUT" \
    > "$RESULTS/server${id}_${TAG}.log" 2>&1 &
done
for p in $PORTS; do
  curl -s --retry 60 --retry-delay 1 --retry-connrefused "http://127.0.0.1:$p/health" >/dev/null
done

# ---- controller を各始点で実行(出力は results/ に walks/α 入りで保存) ----
for s in "${START_NODES_LIST[@]}"; do
  echo "=== [START_NODE] $s ==="
  python3 split_controller_rtt.py --servers 2 \
    --server-endpoints 127.0.0.1:8098 127.0.0.1:8099 \
    --start-node "$s" --walks $RW_WALKS --alpha $ALPHA --seed $SEED \
    --cache-policy "$CACHE_POLICY" \
    --out-dir "$RESULTS"
done

# ---- 方法ごとの RTT 内訳を methods_summary.csv に1行追記(図の元データ)----
python3 append_summary.py \
  --results "$RESULTS" --graph "$GRAPH" --walks "$RW_WALKS" --alpha "$ALPHA" \
  --cache-policy "$CACHE_POLICY" --authtag "$AUTHTAG" || true

echo "[DONE] walks(path+rtt) -> $WALKS_OUT"
echo "[DONE] controller outputs -> $RESULTS/start=*_walks=${RW_WALKS}_alpha=${ALPHA}_*"
echo "[DONE] summary appended -> $RESULTS/methods_summary.csv"
