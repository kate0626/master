# 関連研究・汎用キャッシュの比較

`proposed_cache` と並列に置く比較実装です。**RW、分割、認可判定、HTTP通信、
コントローラは提案手法の実装を共有**し、各サーバが独立した認可キャッシュを持ちます。
手法の差分は `methods/`、容量管理と前処理・接続は `common/` に分離しました。

## 構成

```text
base/
├── proposed_cache/
│   ├── core/cache_extensions.py       # 共通のキャッシュ登録口
│   ├── core/split_remote_server_reuse_score.py  # 全方式共通の実RW
│   └── dist/                         # 共通の起動・制御・集計
└── related_work/
    ├── common/
    │   ├── upstream.py               # 提案コードを参照する唯一のimport窓口
    │   ├── cache.py                  # 格納・重み・容量・統計
    │   ├── preprocess.py             # 分割前の次数・保存順序メタデータ
    │   └── registry.py               # 方式名 → ファクトリ
    ├── methods/
    │   ├── generic.py                # FIFO, Random, LFU, LRU-K, GCLOCK
    │   ├── degree.py                 # 次数静的選択、SOOPの閾値選択の移植
    │   ├── lac.py                    # LACの置換判断の移植
    │   ├── opt.py                    # オフラインOPT（均一サイズ限定）
    │   └── grasorw.py                # GraSorwの読込みコスト学習部品
    ├── prepare.py                    # 前処理成果物を保存
    ├── compare.py                    # 同条件の仮想分散比較、CSV/JSON
    ├── server.py                     # 登録後、既存HTTPサーバを起動
    ├── run_local.sh                  # 既存ランナーでlocalhost Kプロセス
    ├── run_cluster.sh                # 既存ランナーでSSHクラスタ
    ├── verify_summary.py             # 欠測と実通信・仮想の不一致を検出
    ├── train_grasorw.py              # 実測校正CSVからブロック別モデル学習
    └── tests/test_methods.py
```

## 実装範囲と方式名

| CLI名 | 差分 | 原研究との関係 |
|---|---|---|
| `none` | キャッシュなし | 共通RWのキャッシュを無効化。KnightKing本体ではない |
| `random` | 独立乱数で退去 | RWの乱数は消費しない。サーバごとにseedを分離 |
| `fifo` | 挿入順に退去 | ヒットでは順番を変えない |
| `lru` | 最近使われていない順 | 提案実装にあるLRUをそのまま利用 |
| `lfu` | 最少頻度、同頻度ならLRU | 頻度は在籍期間のみ。退去時に履歴を破棄 |
| `lru_k` | K番目の参照が最古のもの | **resident-history版**。K未満は先に退去、最初の参照順で比較 |
| `gclock` | 円環走査と参照カウンタ | LRU-Kとは別アルゴリズム。既定K=2 |
| `degree_static` | 高次数ノードの固定集合のみ格納 | PaGraphに着想を得た認可キャッシュ版。初回の認可後に格納 |
| `soop_admission` | 容量から求めた次数閾値より大きいノードのみ格納 | SOOPの格納判断を移植。隣接リスト・全台複製・分散KVは移植していない |
| `lac_layout` | 保存順序の距離でGCLOCKカウンタを増減 | LAC Algorithms 1–4の認可エントリ版。RTT重みではない |
| `proposed` | 既存の単一プール再利用スコア | `reuse_score`, size-neutral, edge-no-deg, w=100 |
| `opt` | 次の利用が最も遠いものを除く | 仮想実験のみ。全方式を同一サイズにした場合のミス数下限 |

`arc` と容量無制限の `memo` も任意指定できます。`memo` は同容量比較に含めないでください。

SOOP / PaGraph / LAC の**システム全体の再現ではなく、同じRW・認可キーに適用した
判断規則の比較**です。論文や図では上記の移植版名を使ってください。
`degree_static` と `soop_admission` はノードだけを選び、エッジ認可をキャッシュしません。
現在のグラフ読込みは無向なので、入次数・出次数の代わりに無向次数を使います。
SOOP版は同次数のグループをまとめて選ぶため、境界グループを収容できなければ空きが残ります。
複数始点の全キーを容量内に予約し、ALLOWを前提にした先読みは行いません。

### GraSorwの扱い

GraSorwはLRUのような置換方式ではなく、二次RWのブロックI/Oエンジンです。
`methods/grasorw.py` に §5.2 の回帰モデルとfull/on-demand選択を実装しました。
**ブロックI/O、bucket管理、bi-blockスケジューラとの接続は未実装**です。
現在の認可RPCにはこれらがないため、`compare.py --policies grasorw` は受け付けません。
単なるLFU等をGraSorwと名付けた数値を出さないための区別です。

実測の校正データ（学習用、評価用とは分離）を用意した場合：

```bash
# CSV列: block,mode,walks,vertices,seconds
# mode は full または on_demand。seconds は読込み＋実行の実測時間。
python3 base/related_work/train_grasorw.py \
  --calibration-csv calibration.csv --out base/related_work/results/loading_models.json
```

## 実行

リポジトリルートから実行します。標準ライブラリだけで動作します。

```bash
# 仮想K台：全オンライン方式＋提案方式。同じ始点・walk seed・容量・分割。
python3 base/related_work/compare.py \
  --edges dataset/Louvain/graph/karate.gr \
  --server-count 2 --alpha 0.1 --walks 100 --cache-capacity 15 \
  --start-nodes 0 --seeds 42 --no-backtrack \
  --out-dir base/related_work/results/karate

# localhostで実際にK個のHTTPサーバを起動。
# NO_BACKTRACKは共通シェルランナーの既定が1なので、明示して揃える。
GRAPH=karate K=2 WALKS=100 ALPHA=0.1 CAP=15 START=0 SEED=42 NO_BACKTRACK=1 \
  bash base/related_work/run_local.sh

# 実機：提案手法と同じservers.conf、グラフ、認可シャードを配置してから実行。
SERVERS_CONF=base/proposed_cache/dist/servers.conf \
REMOTE_REPO_DIR=/path/to/master-progrem GRAPH=vldb WALKS=1000 CAP=100 \
START=0 SEED=42 NO_BACKTRACK=1 \
  bash base/related_work/run_cluster.sh
```

`POLICIES="none lru soop_admission lac_layout proposed"` で絞れます。
実機へのデプロイは自動実行しません。各マシンには `related_work/` と更新した
`proposed_cache/` の両方が必要です。SSH側は既存ランナーと同じプロセス管理です。
別の実験と同時に起動しないでください。

既存の認可付き分割は、仮想実験では `--partition-dir PATH`、シェルランナーでは
`PARTITION_DIR=PATH` を指定します。無指定は既存のmodulo所有・denyなしの設定です。
比較対象は `proposed_cache/core/` + `dist/` の型混在配置です。
`core/multi/` のノード/エッジ別所有配置には、今回のランナーは対応していません。

サーバ単体の追加指定（残りの引数は既存サーバと共通）：

```bash
python3 base/related_work/server.py --help
# --method-starts 0 4 8  （省略時は環境変数START、なければ0）
# --method-metadata metadata.json
# --method-seed 42 --method-k 2 --method-ordering bfs
```

シェルランナー経由のキャッシュ用seedは42、Kは2、順序はBFSです。
`SEED` はRW用seedです。これらを変える実機実験は `server.py` を直接起動してください。

### OPTとの比較

```bash
python3 base/related_work/compare.py \
  --edges dataset/Louvain/graph/karate.gr --server-count 2 \
  --alpha 0.1 --walks 100 --cache-capacity 15 --capacity-mode entries \
  --no-backtrack --policies none lru lfu gclock lac_layout proposed opt \
  --out-dir base/related_work/results/karate_opt
```

通常は既存の重み単位（node=2, edge=3）です。`entries` は**全方式**の重みを1にします。
同じRWを先に1回走らせ、各サーバの将来アクセス列をOPTだけに与えます。
格納拒否を行う方式も比較対象にあるので、OPTも新規項目のbypassを許します。
保証するのは均一サイズ・均一ミスコストでの総ヒット数の上限であり、
**不均一RTTの通信時間やリモート認可回数の最適値ではありません**。
重み付き容量のままOPTを指定するとエラーにします。

## 分割前の処理と比較条件

```bash
python3 base/related_work/prepare.py \
  --edges dataset/Louvain/graph/vldb.gr --ordering bfs \
  --out base/related_work/results/vldb_metadata.json
```

次数と二部グラフのBFS保存順序を全体グラフから作り、その後各サーバのポリシーに渡します。
成果物は `--metadata`（仮想）/ `--method-metadata`（実サーバ）で再利用できます。
グラフのSHA-256を検査するため、違うデータセットの成果物は混用できません。
元のIDや隣接順、所有先を変更しません。LACだけのためにownerまで変わる交絡を避けます。
順序処理自体の効果は `--ordering id` と `--ordering bfs` を別実験として比較できます。
独自の分割を比較する場合も全方式に同一の認可シャードを与えてください。

各方式で始点、walk数、seed、停止確率、no-backtrack、キー粒度、サーバ数、分割を固定します。
サーバ別アクセス列のハッシュも全方式で照合します。OPT以外はアクセス列全体を保存しません。
複数seedで独立な繰返しを行う場合、各seedはwalk数以上離してください
（例：walks=100なら `--seeds 42 142 242`）。
キーは `(start, entity)` のfullモードです。ノードとエッジの認可を混同するnode_onlyは受け付けません。
実装内の提案方式は現在のコードにある**hop距離**を使います。
この追加で提案方式をRTT重みへ変更してはいません。RTT条件は既存の実機環境と揃えてください。

`results.csv` / `results.json` には各seedのヒット率、local/remote認可回数、前処理時間、
キャッシュ統計を保存します。`manifest.json` に実験引数、グラフ・分割のハッシュを保存します。
仮想実験の `simulator_seconds` はPython処理時間で、分散RWの実測時間ではありません。
HTTP実験のCSV/JSON・ログは共通コントローラが出力します。
前処理は測定対象RWの前に完了し、エンドツーエンド比較では別途加算してください。
`prepare.py` の時間にはメタデータ用のグラフ読込みも含まれます。

## 検証

```bash
python3 -m unittest discover -s base/related_work/tests -v

python3 base/related_work/verify_summary.py \
  --summary base/related_work/results/local_karate_K2_cap15/summary.tsv \
  --policies none random fifo lru lfu lru_k gclock degree_static soop_admission lac_layout proposed \
  --servers 2 --expected-json base/related_work/results/karate/results.json
```

実装時にkarate、K=2、walks=30、alpha=.1、cap=15、seed=42、no-backtrackで
11方式すべてのアクセス数・ヒット率・local/remote認可回数が実HTTPと仮想で一致しました。
15件のテストにはDENY保持、容量上限、複数始点・3サーバでのアクセス列一致、
OPTと全探索の照合、LACの終了性、GraSorw回帰を含みます。
実機SSHクラスタの実行は未検証です。karateの結果は動作確認用で、優劣の研究結果ではありません。

## 原典

- [SOOP, §4.3](https://jcst.ict.ac.cn/fileup/1000-9000/PDF/2021-5-3-1234.pdf)
- [LAC, §3.4 / Algorithms 1–4](https://www.vldb.org/pvldb/vol18/p2859-korkmaz.pdf)
- [GraSorw, §5.2](https://www.vldb.org/pvldb/vol15/p1619-shao.pdf)
- [PaGraph著者ポスター](https://gnnsys.github.io/posters/GNNSys21_poster_8.pdf)

新方式は `WeightedCache` を継承して `admit / victim / touch` 等の差分だけ実装し、
`common/registry.py` に登録します。RWや通信処理の複製は不要です。
