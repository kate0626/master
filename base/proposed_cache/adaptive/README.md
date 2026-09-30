# adaptive — 重みをオンライン学習する加算スコアのキャッシュ（方式A / F1）

既存の `core/` は変更していません。どのファイルも `core/` を import し、差し込むだけです。

| ファイル | 役割 |
|---|---|
| `learned_cache.py` | 学習キャッシュ本体（式・RLS・memory window）。実機と仮想で共通 |
| `server.py` | **実サーバ**。既存実サーバに `learned` を登録し、hop と次数を渡し、`GET /learner_stats` を足す |
| `controller.py` | **コントローラ**。walk を `--batch-walks` 本ずつ投げ、バッチごとのヒット率と重み（学習曲線）を記録する |
| `run_local.sh` | localhost に K プロセス立てて実サーバで回す |
| `run_cluster.sh` | `servers.conf` の K 台へ SSH で配って回す（実機） |
| `plot_curves.py` | 学習曲線の図（バッチ内ヒット率 + 重みの推移） |
| `sim.py` | 仮想サーバ（1プロセス）。式を詰めるとき用 |

## 実機で回す

```bash
# localhost（動作確認）
GRAPH=vldb WALKS=1000 BATCH=100 ALPHA=0.1 bash base/proposed_cache/adaptive/run_local.sh
# 実機（servers.conf の K 台。各ホストに adaptive/ と core/ を配布しておくこと）
GRAPH=vldb WALKS=1000 BATCH=100 ALPHA=0.1 bash base/proposed_cache/adaptive/run_cluster.sh
# 図
python3 base/proposed_cache/adaptive/plot_curves.py <出力ディレクトリ>
```

- 方策は `POLICIES="lru arc proposed learned learned_frozen"`。`proposed` は現行の reuse_score（w=100）、`learned_frozen` は学習停止で初期重み LRU（**LRU と厳密に一致するはず**＝配線の検証）
- 学習器の設定は `LEARN_ARGS="--learn-window 2000 --learn-forget 0.99"` のように渡す（`server.py` の `--learn-*`）
- 学習の引き継ぎ：キャッシュはサーバ常駐なので、コントローラは開始時に1回だけ `/cache/reset` を呼び、以降のバッチでは重みが引き継がれる
- seed はバッチをまたいで `seed + 投げた本数` を渡すので、**一括で投げた場合と同じ walk 集合**になる
- 出力：`curve_<方策>_*.csv`（バッチごとの行。`batch_hit` / `cum_hit` / node / edge / 遠隔認可数 / 重み `w_s<sid>_<type>_<特徴>`）、同名の `.json`（最終時点の重み履歴つき）、`summary.tsv`

検証済み（karate、localhost、K=2）：
- `learned_frozen` と `lru` が完全一致（38.6041%）
- 実機と仮想サーバが `lru` / `arc` / `proposed` / `learned` の4方策すべてで完全一致（learned 41.84%）。候補抽出の乱数は、どちらも `learn_seed×1000 + サーバ番号` に揃えてある

**引き返し禁止で統一**（2026-09-28 決定）：実機スクリプトは `NO_BACKTRACK=1`、仮想サーバ `sim.py` も既定で禁止（旧条件は `--allow-backtrack`）。

## 式

```
ŷ(e) = b + w_H·z(hop) + w_O·z(freq) + w_D·z(deg) + w_R·z(age)     （F1 = 素の値の加算）
退去 = ŷ 最大（次のアクセスがいちばん遠いと予測したもの）。同点は last_access が古い方
```

- z は Welford によるオンライン標準化（サーバ別・型別）。±3 でクリップ
- hop はそのキーで観測した最小の歩数（既存の bump_prior と同じ。`--hop-agg last` で最新値）
- deg はノードなら次数、エッジなら両端点の次数の平均
- 重みはサーバ別 × 型（node / edge）別

## 学習（LRB 型の教師あり・オンライン）

1. 退去のたびに、犠牲者と無作為の候補 k 個（`--sample-k 8`）の特徴量を記録する
2. 次にアクセスされた時点で y = log1p(経過時計) を確定する。W（`--window 1000`）を過ぎたら打ち切りとして y = log1p(2W)
3. 忘却係数付き RLS（`--forget 0.999`）で重みを更新する
4. 退去したのに W 以内に再来したサンプル（＝自分の退去が起こしたミス）は、重み c = min(10, max(1, freq/平均freq)) で入れる（`--miss-weight rel_freq`）

## 正しさのアンカー

`--selfcheck`：学習停止と初期重み LRU（w_R=1, 他0）で LRU と厳密に一致する（VLDB 3 seed で hits 一致を確認済み）。

## 使い方

```bash
python3 base/proposed_cache/adaptive/sim.py --edges dataset/Louvain/graph/vldb.gr \
  --alpha 0.1 --walks 1000 --n-seeds 3 --disjoint \
  --policies lru arc reuse_score learned --out-json base/proposed_cache/adaptive/results/x.json
```

`--out-json` には最終重み、学習サンプル数、重みの履歴（200 更新ごと）が入ります。

## 最初の結果（2026-09-27、仮想サーバ、walks 1000、cap 100/台、K=2、独立 seed 3、引き返しあり）

※ 候補抽出の乱数 seed を実機と揃える前、かつ**引き返しありの旧条件**での数値。今後の比較には使わない。

| 条件 | LRU | ARC | reuse_score（現行） | learned（F1） |
|---|---|---|---|---|
| VLDB α=0.1 | 45.31 | 51.15 | 52.55 | 48.62 |
| VLDB α=0.01 | 35.21 | 35.18 | 34.93 | 28.36 |
| Amazon α=0.1 | 44.71 | 51.78 | 51.55 | 46.06 |
| Amazon α=0.01 | 29.28 | 29.35 | 29.23 | 18.04 |

- α=0.1 では LRU を +3.3 / +1.4pt 上回るが、ARC と現行の reuse_score には届かない
- α=0.01 では LRU より −7 / −11pt 悪い。学習した重みは O が強い負・R が弱い（LFU 寄り）。古い高頻度エントリが居座っている可能性がある（未検証）
- ミス重み（rel_freq と none）の差は seed 間の誤差の範囲内
- 学習した重みの符号は、どの条件でも H が正（遠いほど先）、O が負（多いほど近い）で、事前の分析と一致する

## O の数え方の比較（2026-09-28、仮想サーバ、引き返し禁止、walks 1000、独立 seed 3）

`--freq-scope cache`（既定。キャッシュに入ってからの回数、退去で 0）と `--freq-scope total`（そのサーバでの全アクセス回数、退去しても保持）。実サーバは `--learn-freq-scope`。

| 条件 | LRU | ARC | reuse_score | learned(cache) | learned(total) |
|---|---|---|---|---|---|
| VLDB α=0.1 | 21.36 | 29.77 | 27.85 | 28.61 | **31.16** |
| Amazon α=0.1 | 18.70 | **28.92** | 25.81 | 23.27 | 27.31 |
| VLDB α=0.01 | **15.79** | 15.74 | 14.01 | 7.61 | 7.32 |
| Amazon α=0.01 | **10.33** | **10.33** | 9.34 | 4.86 | 5.20 |

- α=0.1 では total が cache より +2.5〜+4.0pt 良い（VLDB で ARC を超える）
- α=0.01 ではどちらも LRU の半分程度。学習後の w_R がほぼ 0 で、直近性を捨てている
- total_count は触れたキーを全部覚えるので、長時間動かすとメモリが増え続ける（実機では上限か減衰が要る）

## 重みの推移（2026-09-28、6グラフ × α 0.1/0.01、O=total、仮想サーバ、引き返し禁止、1000 walks、seed 3）

`sim.py --trace-walks 50 --out-json ...` で 50 walk ごとに重みを記録し、`plot_weights.py results/weights/wt_*.json --out-dir results/weights` で作図。
図 `results/weights/weights_trajectory_a{0.1,0.01}.png`、最終重み `results/weights/weights_final.csv`。
slashdot はノード番号が 1 始まりで始点 0 が存在しないため `--start-nodes 1`（他は 0）。

| グラフ | α=0.1 LRU / ARC / learned | α=0.01 LRU / ARC / learned |
|---|---|---|
| er_mid | 16.1 / 24.8 / 22.3 | 6.5 / 6.6 / 3.6 |
| amazon | 18.7 / 28.9 / 27.3 | 10.3 / 10.3 / 5.2 |
| ba_mid | 5.8 / 6.8 / 4.6 | 6.5 / 6.8 / 5.5 |
| vldb | 21.4 / 29.8 / **31.2** | 15.8 / 15.7 / 7.3 |
| slashdot | 5.0 / 5.9 / 4.2 | 5.9 / 6.0 / 3.7 |
| enron | 34.7 / 35.9 / **36.9** | 10.4 / 11.4 / 7.2 |

## structured — 分析結果を項の形に入れた式（2026-09-28、方策名 `structured`、`structured_cache.py`）

```
s = b + w_O·z_O + w_R·z_R + w_H·z_H + w_Hn·[node]·z_H + w_D·[node]·z_D·(1/O)·σ(z_H)   （大きいほど追い出す）
z_O = z(log(1+全アクセス回数))  z_R = z(log(1+age))  z_H = z(歩数)  z_D = z(log(1+次数))（基準はノードのみ）
```

- 重みは node/edge 共通の1組、目的は「滞在時間 h 以内に再利用されるか」の 0/1、初期重み b=0, O=−1, R=+0.3, H=+0.5, H_node=+0.25, D_cond=0
- `--init lru --no-learn` で LRU と厳密一致（確認済み）
- この設定は learned の診断結果による：(1) node/edge 別の重みは型ごとのバイアス差で比較がゆがむ（共有で +1.4〜1.6pt）、(2) 回数・経過時間は log、(3) 目的は 0/1 の方が良い
- 評価（仮想・引き返し禁止・1000 walks・seed 3）、結果 `results/structured/`：

| グラフ | α=0.1 LRU / ARC / reuse_score / **structured** | α=0.01 LRU / ARC / reuse_score / **structured** |
|---|---|---|
| er_mid | 16.1 / 24.8 / 21.7 / **24.9** | 6.5 / 6.6 / 5.7 / 6.4 |
| amazon | 18.7 / 28.9 / 25.8 / **30.1** | 10.3 / 10.3 / 9.3 / 9.9 |
| ba_mid | 5.8 / 6.8 / 6.7 / 5.9 | 6.5 / 6.8 / 6.6 / 6.6 |
| vldb | 21.4 / 29.8 / 27.9 / **33.6** | 15.8 / 15.7 / 14.0 / 15.4 |
| slashdot | 5.0 / 5.9 / 5.5 / 5.4 | 5.9 / 6.0 / 5.3 / 5.5 |
| enron | 34.7 / 35.9 / 36.7 / **39.5** | 10.4 / 11.4 / 10.9 / **11.7** |

- 学習後の重み：O は全条件で負（主軸）、R は小さい正、**H と H_node はほぼ 0〜わずかに負**、D_cond はグラフで符号が割れる（`results/structured/weights_final.csv`）

## structured_split — ノードとエッジで式と重みを分けた版（2026-09-29、`structured_cache.py`）

ノード: `s_n = b_n + w_O z_O + w_R z_R + w_H z_H + w_D z_D (1/O) σ(z_H)` ／ エッジ: `s_e = b_e + w_O' z_O + w_R' z_R + w_H' z_H`（次数なし）。z の基準はサーバ内共通、目的は両方 0/1。`--init lru --no-learn` で LRU と一致確認済み。

structured（重み共有）との差（全体、pt）：α=0.1 では 6/6 で下回る（−0.12〜−1.08）。α=0.01 では 5/6 で上回る（+0.17〜+1.02、enron のみ −0.82）。ba_mid・slashdot の α=0.1 のエッジの負け（ARC 比 −2.25 / −1.72）は分けても直らない。結果 `results/structured/split_*`。

## 実機の前提：ノード/エッジ分離配置（2026-09-29 確定）

- キャッシュはサーバごと。**ノードとエッジは別サーバ**（server id 0..Kn−1 がノード、Kn..K−1 がエッジを所有。`core/multi/partition.py`）
- キャッシュに入るのは認可を問い合わせた候補＝反対の型：**ノード担当サーバのキャッシュにはエッジ、エッジ担当サーバにはノードだけ**（karate で確認：server0 は node の hit/miss が 0、server1 は edge が 0）
- `server.py` / `controller.py` に `--node-servers Kn --edge-servers Ke`。`run_local.sh` は `PLACEMENT=split`（既定）+ `KN`/`KE`、`run_cluster.sh` は role 列つき servers.conf（既定 `core/multi/servers.conf`）から Kn/Ke を読む。`DRY_RUN=1` でコマンド表示のみ
- 方策の既定は `lru arc proposed structured`。`structured_frozen`（学習停止・LRU 重み）は LRU と厳密一致（karate / VLDB で確認）
- `PARTITION_DIR` の deny テーブルは owner 割り当てが分離配置より優先されるので、分離配置用に作ったもの以外は渡さない。渡したときだけ `--owned-hints-only`

## 追い出しの判断の質・組単位の学習・減衰回数（2026-09-29）

**判断の質（関連研究に倣った指標）**。追い出すたびに「追い出したキー」と「残したキー 8 個」を記録し、実際の次回アクセスが確定した時点で数える（W=1000 アクセスだけ遅れて確定）。`/learner_stats` の `decision`、コントローラの `batch_good` / `batch_regret` / `batch_pair_acc`（とその累積）、`summary.tsv` の good% / regret% / pair_acc%。

| 指標 | 定義 | 出典 |
|---|---|---|
| good | 追い出したキーの次回アクセスが境界 h（追い出し時 age の EWMA ≈ 滞在時間）より先 | LRB の good decision ratio（境界は Belady 境界の近似） |
| regret | 追い出したキーが h 以内に戻ってきた（= 1 − good） | LeCaR / CACHEUS の後悔（ghost ヒット） |
| pair_acc | （追い出したキー, 残したキー）の組のうち、追い出した方が本当に後で使われた割合。両方来なかった組は除く | 順位の正しさ（ペア単位の正解率） |

LRU / ARC のクラスには記録がないので、LRU の値は `structured_frozen`（LRU と厳密一致）で取る。

**組単位の学習**（`--learn-target pairwise`、方策 `structured_pair`）。上の組ごとに、実際に後で使われた方のスコアが高くなるよう `w += lr·σ(−w·dx)·dx`（RankNet、AdaGrad）。追い出した方が先に戻ってきた組（高くついた誤り）は訪問回数の比で重みを付ける。学習率 `--learn-pair-lr 0.05`。

**減衰回数**（`--learn-freq-scope edc`、方策 `structured_edc`）。1回のアクセスは半減期（`--learn-decay-halflife 1000`、キャッシュ時計）ごとに重み 1/2。LRB の EDC と同じ。

両方は `structured_pair_edc`。

## 初期重み 0 と cap での実行体制（2026-09-30）

- `structured` の既定の初期重みを **0** に変更（手で決めた値を入れない）。重みが全部 0 の間は全キー同点 → 古い順に追い出す = LRU。学習は最初から常に続ける（切り替え判定なし）。旧来の分析の向きを入れた版は方策 `structured_prior`（`--learn-init prior`）。VLDB（分離配置・localhost・α0.1・1000本・seed 1）：初期重み 0 は 39.16%（prior 39.98%）。差は最初の2バッチだけ（1バッチ目 23.4% で LRU 25.1% より低い）で、3バッチ目以降は同じ
- `make_servers_conf.sh`：`dist/servers.conf`（cap11〜cap26）の先頭から KN 台をノード担当、KE 台をエッジ担当にした role 列つき servers.conf（既定 `adaptive/servers_cap.conf`）を作る。`SKIP` で先頭を飛ばせる
- `deploy.sh check|push|all`：servers.conf の全ホストへ SSH・python3・グラフ・コードの有無を確認／adaptive・core・core/multi/partition.py・グラフを rsync。`DRY_RUN=1` で送る内容だけ表示
- `run_cluster.sh` の既定 servers.conf は `adaptive/servers_cap.conf`
- 2026-09-30 時点で cap11・cap12 に SSH 可（Python 3.10.12、vldb.gr あり、adaptive は未配布）

## 実行環境（2026-09-30 確定・最新）

前のキャッシュ方式（`dist/`）と同じ流れ・同じ servers.conf・同じ出力形式の実行スクリプトを、**このフォルダに置いた**（`dist/` は変更前の状態に戻してある）。
以前の節に出てくる `make_servers_conf.sh`・`servers_cap.conf`・role 列つき servers.conf・`KE` は廃止した。

| ファイル | 役割 |
|---|---|
| `run_cluster.sh` | 実機。servers.conf（既定 `base/new-many-server/servers.conf`、"host id ip port"、行数 = 台数）の各ホストに SSH でサーバを起動し、手元でコントローラを動かす。`DRY_RUN=1` で表示のみ |
| `run_local.sh` | localhost に K プロセス。実機前の動作確認 |
| `deploy.sh check / push / all` | servers.conf の全ホストへ core/・core/multi/partition.py・adaptive/・グラフ（・PARTITION_DIR）を配布／確認 |
| `adaptive_policies.sh` | 上の2本が読む共通部品（方策名 → サーバ引数、配置の設定） |

- **既定は分離配置**（`PLACEMENT=split`）。先頭 `KN` 台がノード担当、残りがエッジ担当（`KN` 省略時は台数の半分）。`PLACEMENT=modulo` で従来の分割
- 方策は従来のもの（lru / arc / memo / proposed …）と学習キャッシュ（structured / structured_frozen / structured_oonly / structured_edc / structured_pair / structured_prior / learned …）を混ぜられる
- 従来の方策かつ `PLACEMENT=modulo` のときだけ core/ のサーバ・コントローラを使う（`dist/run_local.sh` と出力が完全一致することを karate で確認）。それ以外は adaptive/server.py と adaptive/controller.py
- 出力は `adaptive/results/`。`summary.tsv` は dist と同じ RESULT 行（集計は `dist/aggregate_stats.py` を共用）、加えて `curve_<方策>_*.csv/json`

手順（リポジトリルートから）:

```bash
bash base/new-many-server/gen_servers_conf.sh --count 2
GRAPH=vldb bash base/proposed_cache/adaptive/deploy.sh all
DRY_RUN=1 GRAPH=vldb bash base/proposed_cache/adaptive/run_cluster.sh
GRAPH=vldb WALKS=1000 ALPHA=0.1 SEED=1 POLICIES="lru arc proposed structured" bash base/proposed_cache/adaptive/run_cluster.sh
```

## 任意の台数で回す（2026-09-30）

- 台数 K = servers.conf の行数（localhost は `K`）。先頭 `KN` 台がノード担当、残り K−KN 台がエッジ担当（`KN` 省略時は K/2）。servers.conf は `bash base/new-many-server/gen_servers_conf.sh --count K` で作る
- localhost で確認済み：K=3（KN=1）/ 4（KN=2）/ 5（KN=2）で動作、全構成で `structured_frozen` = LRU、各キャッシュは反対の型1種類だけ、分離配置では認可は常にリモート（local_auth=0）
- `CAP_TOTAL`：全サーバ合計の容量。1台あたり CAP = CAP_TOTAL/K（台数を変えて公平に比べる用。CAP は1台あたりなので、指定しないと台数を増やすほど合計容量も増える）
- コントローラが `servers_<方策>_*.csv`（サーバ別・役割別のヒット率、判断の質、重み）を出す。`plot_curves.py` は重みを役割ごとに平均（太線）＋各サーバ（細線）で描く
- 注意：判断の質は「そのサーバへのアクセス W=1000 回」待ってから確定する。台数が増えると1台あたりのアクセスが減るので確定件数が減る → 台数が多いときは walk を増やすか `LEARN_ARGS="--learn-window 500"`

## 全グラフ × 関連研究の比較（2026-09-30、`results/allgraphs/`）

`run_all_graphs.sh` → `plot_compare.py`。localhost 実サーバ、ノード/エッジ分離配置（ノード1＋エッジ1）、cap 100/台、1000 walks、引き返し禁止。α=0.1 は seed 3 本、α=0.01 は seed 1 本。比較手法: LRU / ARC / LFU / LeCaR / CACHEUS（簡略版）/ 現行 proposed / O だけ / Belady（上限）。

- α=0.1：structured_prior が amazon・vldb・enron で最良（最良の比較手法 +1.1 / +2.0 / +2.1pt）、er_mid・ba_mid・slashdot は誤差内で同等
- α=0.01：structured が 6/6 で最良（+0.6〜+2.5pt）。Belady との差を 12〜48% 詰める（比較手法は 0〜9%）
- 重みの比（|w_O|=1）：O が主軸、R は O の 0.3〜0.5（α=0.01 で 0.5〜1.0）、H はほぼ 0〜わずかに負、D_cond はノードをキャッシュするサーバで正（次数が大きいほど追い出す向き）
- 注意：amazon0601 はサーバ1台で約 3.3 GB 使う（run_all_graphs.sh は1ジョブずつ回す）。|w_O| が 0 に近いと比が大きく振れる

## 実機で台数を指定してまとめて回す／コントローラもリモートで動かす（2026-09-30）

- `run_all_cluster.sh`：`COUNT=<台数>`（cap<HOST_START>.. から自動で servers.conf を作る。共有の servers.conf は触らない）× グラフ × α × seed を1本ずつ順に回す。`KN` でノード担当の台数、`DEPLOY=1` で最初に配布、終わったジョブは飛ばす
- `remote.sh {setup|check|start|status|fetch|stop}`：コントローラを `CTRL_HOST`（既定 cap11）で nohup 実行。手元は送る・始める・様子を見る・回収するだけなので、Mac を閉じても止まらない
- `SSH_USER=kate`：servers.conf の別名ではなく `kate@ip` で接続（CTRL_HOST に ~/.ssh/config の別名が無いとき）。`run_cluster.sh` / `deploy.sh` も対応
- 確認済み（偽の ssh で手元を「リモート」に見立てた通し試験）：setup → check → start（DEPLOY 込み）→ status → fetch（図まで）→ stop、環境変数（LEARN_ARGS）がサーバまで届く。**本物のクラスタ越しの接続・鍵認証は未確認**

## 足りない方策だけ回す・学習の飽和点を探す（2026-09-30）

- **RESUME=1（既定）**：`summary.tsv` に結果がある方策は飛ばす（ローカル・実機とも）。方策を足して同じコマンドを流せば足りない分だけ回る。`RESUME=0` で全部やり直し。walk 数の違う途中結果に再開しようとすると止まる（`walks_used.txt` で管理）
- **AUTO_WALKS=1**：先に `PROBE_POLICY`（既定 structured_prior）を最小 `WALKS`〜最大 `MAX_WALKS`（既定 10×WALKS）で、学習が飽和するまで回す（コントローラ `--until-converged`）。飽和した本数 W* を `walks_used.txt` に書き、残りの方策は全部 W* で回す（公平な比較）
- **飽和の判定**（C=5 バッチ、`CONV_ARGS` で変更）：実効的な重み（重み × 特徴量の標準偏差）の向きの cos ≥ 0.995、大きさの相対変化 ≤ 5%、直近 C バッチとその前 C バッチの区間ヒット率の差 ≤ 1pt。素の重みで判定すると、ばらつきの小さい項（D_cond）の重みが伸び続けても向きが揃って見え、早すぎる判定になった（karate で確認）ので実効的な重みにした
- AUTO_WALKS=1 で SEEDS 未指定なら seed を MAX_WALKS ずつ離す（walk 集合が重ならないように）
- 固定本数の古い結果が入った OUT_DIR で AUTO_WALKS=1 を始めると、古い summary.tsv は `summary.tsv.before_auto_walks` に退避して回し直す
- 確認（localhost）：karate は W*=3600、er_mid（α=0.1）は W*=1400 で飽和。残りの方策は同じアクセス数で回る。重みの比は実効的な重みで表示（servers csv の ratio_*、plot_curves）

## 重要：実機で全方策が同じ値になる不具合（2026-09-30 修正）

- 症状：`run_cluster.sh` の結果で、どの方策も最初の方策（LRU）と同じ値になる（`cluster_vldb_*` の RESULT 形式の結果がこれ。**使えない**）
- 原因：方策の切り替え時に `ssh host "pkill -f base/...split_remote...; pkill -f base/.../adaptive/server.py"` を実行していたが、Linux では SSH 先のシェル自身のコマンドラインにもパターンが一致し、1つ目の pkill でシェルが自分を落とす → 2つ目が走らず `adaptive/server.py` が生き残る → 新しいサーバはポートが使えず起動できないのに、古いサーバが health に答えて通ってしまう
- 修正：パターンの先頭1文字を `[ ]` で囲む（`[b]ase/...`、自分には一致しない）、消えるまで待つ、`ensure_ports_free`（起動前にポートが空いたか）、`verify_servers`（起動後、`/access_stats` の policy・capacity・処理済みアクセス 0 を確認し、違えば止める）。remote.sh の pgrep / pkill も同じ書き方に

## サーバにログインして直接打つ（CTRL_HOST=local）

`CTRL_HOST=local` で remote.sh はこのマシン自身をコントローラにする（ssh しない）。setup は不要、fetch は図を作るだけ、start / status / check / stop は Mac から打つときと同じ。cap11 では、setup で置いたリポジトリルート（ログイン直後のホーム）で実行する。

## walk 数を縦軸にした図（2026-09-30、`plot_walks.py`）

`python3 plot_walks.py results/allgraphs --graphs vldb amazon0601 email-enron-connected --alpha 0.1`（→ `walks_a0.1.png`）、`--graphs amazon0601 vldb --alpha 0.01`（→ `walks_a0.01.png`）。左 = 横 walk 数 × 縦 累積ヒット率（関連研究 5 手法 + 提案 2 種、赤点線 = Base：Belady〔上限〕）、中・右 = 横 walk 数 × 縦 重みの値そのもの（役割別、灰点線 = Σ|w|）。`--hit batch` でバッチ内ヒット率。

注意（重要）：分離配置では1つのキャッシュに1種類の型しか来ないので、ノードをキャッシュするサーバでは z_H と [node]·z_H が同じ値になり、**w_H と w_H_node は和しか決まらない**（個別の値は意味がなく、Σ|w| も膨らむ）。和はほぼ 0。分離配置では H_node の項は不要。
