# shellcheck shell=bash
# =============================================================================
# adaptive_policies.sh — adaptive/run_local.sh・run_cluster.sh が読み込む共通部品
#
#   学習キャッシュ（base/proposed_cache/adaptive/）の方策名と、ノード/エッジ分離配置の設定。
#   従来の方策（none / memo / lru / arc / proposed ...）かつ PLACEMENT=modulo のときだけ、
#   core/ のサーバとコントローラ（前の方式と同じ）を使う。
#
#   学習キャッシュの方策名:
#     structured           分析の4要素を足し算にしたスコア。重みを走りながら学習（初期重み 0）
#     structured_frozen    structured の学習停止・経過時間だけ = LRU と厳密一致（配線の検証用）
#     structured_oonly     学習なしで回数 O だけ（全アクセス回数が少ないものから追い出す）
#     structured_prior     分析の向きを初期重みに入れた structured
#     structured_split     キャッシュに入る型ごとに式を分けた版（分離配置向け。距離の重みは1つ、[node] なし）
#                          エッジのキャッシュ: O・R・H ／ ノードのキャッシュ: O・R・H・D（条件付き）
#     structured_split_prior  structured_split の初期重みに分析の向きを入れた版
#     structured_edc       回数を減衰させた版（半減期 1000 アクセス）
#     structured_pair      目的を追い出しの判断（組単位）に合わせた版
#     structured_pair_edc  上の2つを両方
#     learned              4要素の素の足し算（F1、初期の版）
#   追加の学習器引数は LEARN_ARGS="--learn-window 2000 ..." で全学習キャッシュに渡る。
#
#   配置:
#     PLACEMENT=split（既定）   servers.conf（または localhost の K 台）の先頭 KN 台がノード担当、
#                               残りがエッジ担当。キャッシュはサーバごと。ノード担当サーバの
#                               キャッシュにはエッジ、エッジ担当サーバにはノードだけが入る
#                               KN を省略すると K/2（切り捨て、最低1）
#     PLACEMENT=modulo          従来のモジュロ分割（各サーバにノードとエッジが混在）
#   学習キャッシュか PLACEMENT=split のときは、サーバ = adaptive/server.py、
#   コントローラ = adaptive/controller.py（walk を BATCH 本ずつ投げて学習曲線を記録）を使う。
# =============================================================================

ADAPTIVE_SERVER_PY="base/proposed_cache/adaptive/server.py"
ADAPTIVE_CTRL_PY="base/proposed_cache/adaptive/controller.py"
PLACEMENT="${PLACEMENT:-split}"      # このフォルダの既定は分離配置
KN="${KN:-}"
BATCH="${BATCH:-100}"
LEARN_ARGS="${LEARN_ARGS:-}"

is_adaptive_policy() {
  case "$1" in
    learned|learned_*|structured|structured_*) return 0 ;;
    *) return 1 ;;
  esac
}

# 学習キャッシュの方策名 → サーバ引数（1行、空白区切り）
adaptive_policy_flags() {
  case "$1" in
    learned)             echo "--cache-policy learned ${LEARN_ARGS}" ;;
    learned_frozen)      echo "--cache-policy learned --learn-no-learn --learn-init lru" ;;
    structured)          echo "--cache-policy structured ${LEARN_ARGS}" ;;
    structured_frozen)   echo "--cache-policy structured --learn-no-learn --learn-init lru" ;;
    structured_oonly)    echo "--cache-policy structured --learn-no-learn --learn-init 0,-1,0,0,0,0" ;;
    structured_prior)    echo "--cache-policy structured --learn-init prior ${LEARN_ARGS}" ;;
    structured_edc)      echo "--cache-policy structured --learn-freq-scope edc ${LEARN_ARGS}" ;;
    structured_pair)     echo "--cache-policy structured --learn-target pairwise ${LEARN_ARGS}" ;;
    structured_pair_edc) echo "--cache-policy structured --learn-target pairwise --learn-freq-scope edc ${LEARN_ARGS}" ;;
    structured_split)    echo "--cache-policy structured_split ${LEARN_ARGS}" ;;
    structured_split_prior) echo "--cache-policy structured_split --learn-init prior ${LEARN_ARGS}" ;;
    *) echo "[FATAL] 未知の学習キャッシュ方策: $1" >&2; return 1 ;;
  esac
}

# PLACEMENT と台数 K から SPLIT_ARGS / PL_SUFFIX を決める。$1 = K
setup_placement() {
  local k="$1"
  SPLIT_ARGS=""
  PL_SUFFIX=""
  if [[ "${PLACEMENT}" == "split" ]]; then
    if [[ -z "${KN}" ]]; then                 # 省略時は半分（切り捨て、最低1）
      KN=$((k / 2)); ((KN >= 1)) || KN=1
    fi
    ((k >= 2)) || { echo "[FATAL] 分離配置には 2 台以上が必要です（K=${k}）" >&2; return 1; }
    ((KN >= 1 && KN <= k - 1)) || { echo "[FATAL] KN は 1..$((k - 1)) の範囲（K=${k}）" >&2; return 1; }
    KE=$((k - KN))
    SPLIT_ARGS="--node-servers ${KN} --edge-servers ${KE}"
    PL_SUFFIX="_n${KN}e${KE}"
  elif [[ "${PLACEMENT}" != "modulo" ]]; then
    echo "[FATAL] PLACEMENT は modulo か split: ${PLACEMENT}" >&2
    return 1
  fi
}

# この方策を adaptive のサーバ・コントローラで回すか
uses_adaptive() {
  is_adaptive_policy "$1" || [[ "${PLACEMENT}" == "split" ]]
}

# =============================================================================
# 方策を順に回す（run_local.sh / run_cluster.sh から呼ぶ。run_one・OUT_DIR・WALKS・POLICIES を使う）
#
#   足りない方策だけ回す:
#     RESUME=1（既定）  summary.tsv に「RESULT<TAB>方策名」の行がある方策は飛ばす
#     RESUME=0          summary.tsv と walks_used.txt を消して全部やり直す
#   学習の飽和点を探す:
#     AUTO_WALKS=1      先に PROBE_POLICY を「最小 WALKS 〜 最大 MAX_WALKS」で、学習が飽和するまで回す
#                       （コントローラ --until-converged。重みの向きが止まり、区間ヒット率が横ばいになったら止める）。
#                       決まった本数 W* を OUT_DIR/walks_used.txt に書き、残りの方策は全部 W* で回す（公平な比較）
#     PROBE_POLICY      既定 structured_prior（学習キャッシュの方策であること）
#     MAX_WALKS         既定 WALKS の 10 倍
#     CONV_ARGS         判定の細かい指定（例 "--conv-window 5 --conv-cos 0.995 --conv-hit-tol 1.0"）
#   walks_used.txt がある OUT_DIR は、その本数で続きを回す（AUTO_WALKS=0 で WALKS が違うときは止める）
# =============================================================================
CTRL_EXTRA=""

run_all_policies() {
  local summ="${OUT_DIR}/summary.tsv" wfile="${OUT_DIR}/walks_used.txt"
  RESUME="${RESUME:-1}"
  AUTO_WALKS="${AUTO_WALKS:-0}"
  PROBE_POLICY="${PROBE_POLICY:-structured_prior}"
  MAX_WALKS="${MAX_WALKS:-$((WALKS * 10))}"
  CONV_ARGS="${CONV_ARGS:-}"
  if [[ "${RESUME}" != "1" ]]; then
    : > "${summ}"
    rm -f "${wfile}"
  fi
  touch "${summ}"
  # AUTO_WALKS=1 なのに固定本数で回した記録しか無い → 古い結果として退避し、飽和探索からやり直す
  if [[ "${AUTO_WALKS}" == "1" && -f "${wfile}" && "$(cut -f2 "${wfile}")" == "fixed" ]]; then
    echo "[WARN] ${OUT_DIR} は固定本数（$(cut -f1 "${wfile}") walks）で回した結果です。飽和探索からやり直します" >&2
    mv "${summ}" "${summ}.before_auto_walks"; touch "${summ}"
    rm -f "${wfile}"
  fi

  if [[ -f "${wfile}" ]]; then
    local wu
    wu="$(cut -f1 "${wfile}")"
    if [[ "${AUTO_WALKS}" != "1" && "${wu}" != "${WALKS}" ]]; then
      echo "[FATAL] ${OUT_DIR} は WALKS=${wu} で回した途中結果です（今回 WALKS=${WALKS}）。" \
           "RESUME=0 でやり直すか、OUT_DIR を変えてください" >&2
      return 1
    fi
    WALKS="${wu}"
    echo "## walks=${WALKS}（${wfile}: $(cut -f2 "${wfile}")）"
  elif [[ "${AUTO_WALKS}" == "1" ]]; then
    is_adaptive_policy "${PROBE_POLICY}" \
      || { echo "[FATAL] PROBE_POLICY は学習キャッシュの方策にしてください: ${PROBE_POLICY}" >&2; return 1; }
    echo ">>> 飽和点を探す: ${PROBE_POLICY} を最小 ${WALKS} 〜 最大 ${MAX_WALKS} walks で回す"
    # walks_used.txt が無いのに結果がある = 固定本数で回した古い結果。本数が揃わないので全部捨てる
    if grep -q "^RESULT" "${summ}"; then
      echo "[WARN] ${summ} に固定本数の古い結果があります。飽和探索の本数と揃わないので捨てます" >&2
      mv "${summ}" "${summ}.before_auto_walks"
      touch "${summ}"
    fi
    CTRL_EXTRA="--until-converged --max-walks ${MAX_WALKS} --converged-file ${wfile} ${CONV_ARGS}"
    run_one "${PROBE_POLICY}" || { echo "[FATAL] 飽和探索（${PROBE_POLICY}）に失敗しました" >&2; CTRL_EXTRA=""; return 1; }
    CTRL_EXTRA=""
    if [[ -f "${wfile}" ]]; then
      WALKS="$(cut -f1 "${wfile}")"
      echo "## 飽和探索の結果: walks=${WALKS}（$(cut -f2 "${wfile}")）→ 残りの方策をこの本数で回す"
    else
      echo "## （DRY_RUN のため飽和探索は行わず、walks=${WALKS} のまま表示）"
    fi
  elif [[ "${DRY_RUN:-0}" != "1" ]]; then
    printf "%s\tfixed\n" "${WALKS}" > "${wfile}"
  fi

  for p in ${POLICIES}; do
    if grep -q "^RESULT	${p}	" "${summ}"; then
      echo "[skip] ${p}（summary.tsv に結果あり）"
      continue
    fi
    run_one "${p}" || echo "[WARN] policy=${p} 失敗。次へ。"
  done
}
