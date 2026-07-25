# base-rtt / multiserver — 複数サーバ間 RW に RTT を組み込む(スコア計算なし)

`base/proposed_cache/` の分散 RW コードをコピーし、**RTT を組み込むが提案手法のスコア計算はしない**版。

- `split_remote_server_rtt.py` ← `proposed_cache/split_remote_server_proposed.py` のコピー + RTT 組込み
- `split_controller_rtt.py`     ← `proposed_cache/split_controller_proposed.py` のコピー(無改変)

## 何を足したか(サーバ)

**RTT は walk ループの中で 1 歩ずつ確定する**(= 各キャッシュ判断の時点で RTT が使える。
将来の「キャッシュ in/out スコアリングに RTT を反映」の土台)。RTT は node-consistent なので
サーバ間 handoff をまたいでも `rtt_list` を state で持ち回れば path と 1:1 で揃う。追加点:

1. `load_rtt_field()` / `rtt_of()` … paint_field の距離場 `field.json` を読み、entity→RTT(未訪問は μ)。
2. 引数 `--rtt-field <field.json>` `--walks-out <jsonl>`。
3. `main()` で距離場を読み `server.rtt_field` に載せる(全サーバ同一ファイル)。
4. `continue_from_state`(歩ループ)で、`path.append(next_entity)` と同時に
   `rtt_list.append(rtt_of(next_entity))`。`rtt_list` を **handoff の state と finished 結果に載せて持ち回る**。
5. `_handle_walk_start` で始点 RTT を初期 state に仕込み、完了 walk の `(path, rtt)` を JSONL 追記。

> ここが「各歩で RTT を確定する」= キャッシュ admit/evict のスコアに RTT を差し込める土台。
> スコアリング本体はまだ入れていない(次段)。現状は `--cache-policy none` で走らせる。

### 次段(RTT をスコアに反映する接続点)
キャッシュ admit/evict は `_select_next_neighbor`→`_authorize_candidate`→`_maybe_admit_to_cache`
と `PPRBoundedCache._score` / GDSF 系。ここに **候補 entity の `rtt_of(...)`** を渡し、
`score = f(recency/freq, rtt)`(高RTTほど残す)にすれば RTT 反映スコアになる。
`rtt_list` が歩ごとに確定しているので、その時点値をそのまま使える。

## 使い方(本番: 実クラスタ)= `splits_rtt.sh`

`base/base/splits.sh` を流用した SSH オーケストレーション。**リポジトリルートから**実行:

```sh
# 1) 距離場を作り、全ホストの同じパスに配置(server が各自ローカルで読む)
python3 base/base-rtt/paint_field.py \
  --graph dataset/Louvain/graph/amazon0601.gr \
  --start 0 --direction above --converge-steps 100 --walks 80 \
  --out base/base-rtt/results/field_amazon0601.json

# 2) 起動 → walk 実行(全サーバ --cache-policy none --rtt-field <field> --walks-out <jsonl>)
zsh base/base-rtt/multiserver/splits_rtt.sh
# 上書き例: GRAPH_OVERRIDE=vldb START_NODES_OVERRIDE="0 1 2" zsh .../splits_rtt.sh
```

`splits_rtt.sh` が `base/base/splits.sh` から変えた点だけ:
- server = `split_remote_server_rtt.py` に `--cache-policy none --rtt-field ${FIELD} --walks-out ${WALKS_OUT}` を追加
- controller = `split_controller_rtt.py`
- `SERVERS=(...)` のホスト/IP/nts と `FIELD`/`WALKS_OUT` を環境に合わせて編集する

> field.json は各サーバがローカルで開くので、**全ホストの同じパスに同一ファイル**を置くこと。
> `--walks-out` は start サーバのホスト上に出力される(walk を開始したサーバだけが書く)。

## ローカル実行 = `run_multi_local.sh`(proposed_cache と同じ流儀)

1 台で 2 サーバ(modulo 分割・localhost)を立て、サーバ間 handoff させつつ RTT を組み込んで実行する。
**スコア計算なし(`--cache-policy none`)**。出力は `multiserver/results/` に、**歩数と α をファイル名に含めて**保存。

```sh
# 既定: karate, 50 walks, α=0.02, above
zsh run_multi_local.sh
# 上書き例(実グラフ):
GRAPH_OVERRIDE=com-amazon-connected RW_WALKS_OVERRIDE=100 ALPHA_OVERRIDE=0.01 \
  START_NODES_OVERRIDE="0 1 2" DIRECTION_OVERRIDE=above zsh run_multi_local.sh
```

出力(`multiserver/results/`, proposed_cache 流の命名):
```
walks_<graph>_walks<W>_alpha<A>_<dir>.jsonl              各 walk の (path, rtt)  ← RTT 本体
start=<S>_walks=<W>_alpha=<A>_seed=<seed>_cache=none_cap=na_*.json/csv/png   controller 集計
server{0,1}_<graph>_walks<W>_alpha<A>_<dir>.log          サーバログ
field_<graph>_<dir>.json                                 距離場(paint_field)
```

## 出力

`walks_ms_*.jsonl`(1 行 = 1 walk):
```json
{"walk": 0, "start": 0, "path": ["0","edge_0_8","8", ...], "rtt": [90.0, 72.5, 56.3, ...]}
```
この RTT 系列は `../design_and_run.py` と同じ設計モデル `g_t = μ + a·λ^t` を再現する
(start から出せば above/below/zigzag の軌跡になる)。RTT 軌跡の確認・作図は
`rtt` 配列を歩ごとに平均すればよい。

## 注意

- RTT の割り当ては距離場(`field.json`)依存。start=paint 基準 S から出したときに設計曲線を再現する。
- `--cache-policy` を `none` 以外にすると提案キャッシュ/スコアが動く(この版の意図から外れる)。
