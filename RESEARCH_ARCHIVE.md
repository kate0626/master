# 研究アーカイブ：異なる管理主体下にあるグラフの分散ランダムウォークと認可キャッシュ

作成日：2026-09-26
対象：岸 真唯子（慶應義塾大学 金子研究室）の修士研究。IN研稿、論文ドラフト v1〜v6、実験リポジトリ、検討メモのすべて。
目的：このリポジトリや作業環境を参照できなくなった後でも、何を考え、何を測り、何が確定して何が未解決かを、この1ファイルだけで追えるようにする。

TAG：CloudeでまとめていたもののSummary

---

## 0. この文書の読み方

### 0.1 凡例
- **【確定】**：独立 seed などで再測し、数値が固まっているもの。
- **【旧】**：後の検証で訂正・撤回されたもの。対外的に引用しないこと。
- **【推測】**：資料から推定したもので、裏づけが不十分なもの。
- pt：ヒット率の差（パーセントポイント）。

### 0.2 どの資料が「正」か（最重要）

| 優先度 | 資料 | 日付 | 内容 |
|---|---|---|---|
| 1 | `master-progrem/base/proposed_cache/README.md` | 2026-09-21 | 最新の手法定義と索引 |
| 1 | `.../docs/LOO_ABLATION_RESULTS.md`（追記4まで） | 2026-09-08 | 単一プール案（案A）への確定と確定値 |
| 1 | `.../docs/ACCESS_FACTORS_RESULTS.md` | 2026-09-19〜21 | アクセス要因の相関分析と価値式の形の検討 |
| 2 | IN研稿 `IN_KishiMaiko.pdf`（Desktop版） | 2026-08-13 | 対外公表稿。**dual-pool・γ=1 の旧定義で、後に訂正が入っている**（§10） |
| 3 | `.../docs/README_SUMMARY.md`、abstract v1/v2 など | 2026-08 上旬 | dual-pool 期の下書き。9月の訂正は未反映 |
| 4 | `M_Research/paper_draft_v1〜v6.md` | ファイル日付 2025-07-26 | 学習項・二段階運用の旧系列。数値は検証前 |
| 参考 | `M_Research/resources/*.pdf` | 2022 | 前任者（滝沢 駿）の非同期RW研究。本研究の前史 |

> 対外的に数値を引用するときは、優先度1の値（§7.3）だけを使うこと。
> 本文中のすべての数値について、生成スクリプト・実行コマンド・結果フォルダ・検証状況を **付録A** にまとめた。再現しようとする前に、必ず **A-1（再現性に関する注意）** を読むこと。

---

## 1. エグゼクティブサマリー

- **問題**：複数の管理主体がそれぞれ秘匿して持つグラフを、横断してランダムウォーク（RW）で解析したい。RW が主体の境界を越えるたびに「そのノード／エッジに触れてよいか」という認可の問い合わせが発生し、主体間の往復通信（RTT）が実行時間を支配する。
- **着眼**：認可結果をキャッシュすれば RTT を削減できる。ただし LRU や ARC は「最後にいつ使ったか」しか見ない。そのため RW 特有の再訪パターン（同じ始点から出た別の walk が、長い間隔をあけて始点近傍を再訪する）を取りこぼす。
- **課題の実測**：理想上限（memo：容量無限）と LRU の差は、walk 数を増やすほど広がる。例として VLDB（α=0.1、容量100）では memo が 53.5→85.2% に伸びる一方、LRU は 42.9→46.0% でほぼ横ばい。
- **提案（最終形＝案A）**：LRU の最終アクセス時刻に、再利用価値のボーナスを足して追い出し順を決める。
  - `Priority(e) = last_access(e) + w · V(e)`
  - `V = O^θ · H^β · D^γ`（O＝過去のアクセス回数、H＝始点への近さ、D＝次数でノードのみ）
  - w=100、単一プール
- **確定結果**：独立 seed 5本、α=0.1、walk 1000本、容量100、始点0。
  - LRU に対して **+7.0pt**（VLDB 45.49→52.47、Amazon 45.05→52.10）。
  - ARC に対しては **VLDB +1.04、Amazon −0.26 で同等**。
- **効く条件**：α が大きく（≥0.05）、walk が始点近傍に集中するとき。始点を平均すると対 LRU の改善は +2.6〜3.2pt に縮み、α≤0.02 では LRU も超えない。
- **分かったこと**：改善のほぼすべて（84〜96%）は「近さ」と「過去の回数」で説明できる。**次数は独立の予測子にならない**。ノード／エッジのプール分離も効果はほぼゼロ。
- **未完了**：新手法での RTT 込み実行時間の再測定、ARC 土台版の独立 seed での再測、次数の減衰形 prior の実装、IN研稿の訂正。

---

## 2. 研究の系譜（前史 → 本研究）

### 2.1 前史：地理分散グラフ上の非同期 RW（滝沢 駿、2022）
出典：`resources/概要.pdf`（2022-01-20）、`resources/20220805_IN研.pdf`（2022-08-05）、`resources/tecrep.pdf`（信学技報。滝沢・金子）

- **問題**：グラフが地理的に分散していると、RTT とパケットロスが大きくなる。
  - KnightKing（BSP 同期型）などの既存エンジンは単一データセンター向けで、均等分割が前提。
  - 5台で RW 500万回を回した場合、理想環境では約0.5秒。RTT 200ms で約42秒、ロス率0.1%で約160秒まで伸びる。
- **提案①（概要.pdf）**：RWer（1本の walk）を1パケットとして UDP で送り、計算と通信を非同期に並行させる（C++ 実装。クラス名 ACCM → ARWS）。
  - 実測（500万回、ロス0）：RTT 0ms では KnightKing 0.56秒・提案 10.29秒。RTT 200ms では KnightKing 42.54秒・提案 14.02秒。**約60msで逆転**する。PDF 上は「日中間の RTT は約50〜100ms」。
- 注：「ロス率0.1%で約160秒」は RTT 100ms 固定の条件。p7 の「RTT 約50〜100ms」は PDF 上「日中間」と読める（初版の「日米間」は誤り）。
- **提案②（IN研 2022-08、tecrep）**：RWer 単位の非同期処理で、特定サーバに RWer が集中して輻輳する問題に対処する。RED を用いた自律的輻輳制御である。
  - RQ：キューで RED 破棄を行う。
  - ロス検知：起点ごとのリングバッファ。タイムアウト T＝平均帰還時間＋4×偏差（RFC 2988 準用）。
  - RG：ロスが3回連続で無ければ RWer の生成を加速する。
  - 結果：Tail Drop と比べて平均キュー長が 95.1→7.5 に下がり、追加実行も少ない。
- **その後の C++ 実装（src10〜src12、2023-05〜2024-01）**：他サーバのグラフ構造（隣接リスト・次数・所有IP）をキャッシュして越境通信を減らす方向に発展した。
  - LiveJournal を5台に分割（1頂点あたり RWer 10本）、RTT 100ms、ロス0.03%（グラフの表記「loss (%)」に従う）の条件で、KnightKing 85.8秒に対し、提案の再利用なし（AR_0）は 52.8秒、再利用あり（AR_6000）は 12.0秒（`output2/`）。
  - 【推測】AR_N の N は「1サーバあたりの自前の辺と再利用辺の合計（×10⁴本）」。AR_6000 は合計6,000万本を意味し、MAX_CACHE_SIZE を実行ごとに変えていたと考えられる。
  - 初版にあった「10分割」「ロス3%」「再利用辺6000」は誤読だったので訂正した。
  - 【推測】このキャッシュは「認可結果」ではなく「リモートのグラフ構造」を対象としており、現在の認可キャッシュ研究（Python 実装）とは別系統。

### 2.2 本研究への接続
前史の「越境通信がボトルネックになる」「キャッシュで越境を減らす」という問題意識を引き継いだ。そのうえで、次の二点へ論点を移した。
1. 越境するものを「グラフ構造の取得」から「**アクセス権の確認（認可）**」に置き換えた。
2. 「分散のための分割」ではなく、「**管理主体が異なり、互いに秘匿している**」ことを前提にした。

---

## 3. 背景と問題設定

### 3.1 動機
- RW は Personalized PageRank（PPR）、中心性、グラフ埋め込み（node2vec 等）の基礎演算である。実用エンジンでは、1クエリにつき多数の RWer を並列に走らせて近接性を推定する。
- 異なる主体のグラフを横断して解析すると、単一主体では得られない近接性が得られる。IN研稿では Netflix × Apple Music のような横断推薦を例に挙げている。
- 各主体はグラフを秘匿したいので、越境時には所有主体への認可問い合わせが要る。越境のたびに RTT がかかる。

### 3.2 前提・用語
- **グラフ** G=(V,E)。無向。deg(v)＝次数。
- **RW**：始点 s、終了確率 α、RWer 数 ω。各ステップで確率 α で終了し、確率 1−α でランダムな隣接先へ移る。
  - 平均経路長は 1/α（α=0.1 なら約10 hop、α=0.01 なら約100 hop）。
  - 始点から h hop 先に到達する確率は、概ね (1−α)^h で減衰する。
- **エンティティ**：ノードとエッジの両方が独立した認可対象である。実装では二部グラフ（ノード↔エッジ）に展開して扱う。したがって偶数ステップはノード、奇数ステップはエッジになる。
- **所有と認可**：
  - (a) エンティティはグラフ分割で各主体（サーバ）に排他的に所有される。
  - (b) 認可の単位は（始点 s, エンティティ e）の組。同一クエリ内では s が固定なので、結果は不変でありキャッシュできる。
  - (c) 所有主体は **拒否リスト（deny list）**＝「e へのアクセスを拒否する始点の集合」を持つ。許可が支配的な現実的設定では疎になり保持コストが小さいので、allow ではなく deny を採用した。
  - (d) テーブルは所有側、キャッシュは要求側に置く。
  - (e) 越境時にまず自分のキャッシュを見る。ミスしたときだけ所有主体に問い合わせ、RTT を払う。
- **キャッシュ容量**：重み単位（ノード=2、エッジ=3）。主条件は容量100。
- **memo**：容量無限のキャッシュ。ヒット率の理想上限として使う。
- **時間モデル**：実行時間 ≈ 遷移回数 × 2.27ms ＋ ミス回数 × RTT。
  - 2.27ms/遷移は、実プロトタイプの実測（α=0.01、100本、VLDB）から認可時間を差し引いた値。
  - 再計算すると、4始点の比の平均は 2.2753、合算した比は 2.2838 になる。2.27 は切り捨てた値（付録A-3）。

### 3.3 問題の帰着
認可付き分散 RW の高速化は、「将来再アクセスされる（s, e）の認可結果を、限られた容量にどう残すか」という**キャッシュ置換問題**に帰着する。削減時間は「削減したミス回数 × RTT」で与えられる。
- RTT が大きいほど、1回のヒットの価値が上がる。
- RTT が小さい環境では、キャッシュ導入の効果は限定的になる。

---

## 4. 関連研究

整理の観点は三つ。(i) RW の反復的な越境認可に対応しているか、(ii) グラフ構造を使うか、(iii) 分散環境の RTT を考慮するか。

### 4.1 グラフのアクセス制御

| 研究 | 要点 | 本研究との差 |
|---|---|---|
| RBAC（Sandhu+, IEEE Computer 1996） | ロール単位で権限を付与する。状態を持たず軽量 | 越境のたびにポリシーを再評価する必要がある。粒度が粗い。反復認可の償却という観点がない |
| グラフ構造化データ向け拡張認可ポリシー（Mohamed+, SN Comput. Sci. 2021） | 許可サブグラフ（認可ビュー）で任意粒度に制御する | 部分グラフを保持するためメモリが膨張する。反復認可の再利用という観点がない |

→ いずれも「同一エンティティへの繰り返し越境認可」を想定していない。本研究は認可結果の再利用でこのコストを償却する点で相補的である。

### 4.2 キャッシュ置換（汎用）

| 研究 | 要点 | 本研究との差 |
|---|---|---|
| LRU（Mattson+, IBM Syst. J. 1970） | 直近性（recency）だけで判断する | 構造も RTT も見ない。長い間隔での再訪を取りこぼす |
| ARC（Megiddo & Modha, FAST 2003） | recency と frequency を自己調整する | 構造も RTT も見ない。本研究の最強ベースライン |
| LFU / TinyLFU / LRU-K / GCLOCK / FIFO / Random | 古典的な方式 | 比較実装に含めた（§13.4） |
| GD / GDSF（GreedyDual 系） | コスト（RTT）×頻度で重み付けする | RTT を重みに使う先例。base-rtt で試した（§12） |
| OPT（Belady） | 未来を知る場合の上限 | 均一コストの前提での上限であり、RTT が不均一な場合の最適ではない |

### 4.3 グラフ構造を使うキャッシュ

| 研究 | 要点 | 本研究との差 |
|---|---|---|
| **CBGA**（Aksu+, IEEE IC2E 2015）※輪読済み | 分散グラフストア（HBase）で2ホップ先読みを行い、TTL = l/(s·d) で追い出す（l＝取得レイテンシ、s＝サイズ、d＝起点からのホップ距離）。Twitter で LRU 43%→68%、最大15.4倍高速化 | 最も近い先行研究。ただしワークロードは k-hop BFS で RW ではない。指数が固定で、各項のアブレーションがない。本研究のスコアは CBGA の TTL を一般化したものとみなせ、LOO/Shapley 分析が差別化点になる。「ヒット率より実時間で評価すべき」という主張は本研究の RTT 評価の根拠として引用できる |
| GRASP（Faldu+, HPCA 2020） | CPU の LLC で高次数頂点を優先的に保持する | 単一計算機内の話で、RTT や主体間通信の概念がない |
| SOOP（JCST 2021） | 容量から決めた次数閾値を超えるノードだけを格納する | 比較用に `soop_admission` として移植 |
| PaGraph（GNNSys 2021） | 高次数ノードの固定集合を GPU にキャッシュする | 比較用に `degree_static` として移植 |
| LAC（Korkmaz+, PVLDB 18） | 保存順序（BFS）上の距離で GCLOCK のカウンタを増減する | 比較用に `lac_layout` として移植 |
| GraSorw（Shao+, PVLDB 15） | 二次 RW 向けのブロック I/O エンジン | 置換方式ではないので比較対象から外した（回帰モデル部分のみ実装） |
| memcached（NSDI 2013）、Neo4j、TAO（ATC 2013）、Pregel（SIGMOD 2010）、Mondal & Deshpande（SIGMOD 2012） | 大規模グラフや KV の実システム | いずれも LRU か、キャッシュを持たない。レイテンシの差を区別しない |

### 4.4 RW エンジン・分散グラフ処理

| 研究 | 要点 |
|---|---|
| KnightKing（Yang+, SOSP 2019） | 分散 RW エンジン（BSP）。前史の比較対象 |
| Gluon-async | 非同期型の分散グラフ処理（前史の IN研スライドで比較） |
| Kudu（Chen & Qian, arXiv 2021） | キャッシュ容量をグラフの約10%とする根拠として引用（旧ドラフト） |
| DistGER（Fang+, PVLDB 2023） | 情報指向 RW による分散グラフ埋め込み（旧ドラフトで VLDB データの出典として引用） |
| PageRank（Page+, 1998） | RW 系指標の原典 |

### 4.5 空白（本研究の位置付け）
「RW の確率的な再訪構造（近さ・反復）」と「主体間の RTT」を同時に考慮する認可キャッシュは、知る限り存在しない。

---

## 5. 課題と原因分析（実測）

### 5.1 課題①：LRU は時刻しか見ないため、長い間隔での再訪を取りこぼす
- **仕組み**：同じ始点から出た別々の walk は始点近傍を何度も踏むが、再訪の間隔が長い。容量が逼迫していると、LRU は再訪の前に追い出してしまう。
- **実測**（α=0.1、容量100、seed 1,7、walk 100→10,000本）【確定】

| グラフ | memo（上限） | LRU | 差 |
|---|---|---|---|
| VLDB | 53.5 → 85.2% | 42.9 → 46.0% | +11 → +39pt |
| Amazon | 49.8 → 78.2% | 40.9 → 45.0% | +9 → +33pt |

- 容量を1,000に増やしても差は残る（walk 10,000本で VLDB +14.0pt、Amazon +15.9pt）。
  - memo の値は図の注記で確認できた。LRU の 71.2／62.3 は図に注記がなく、目視で整合を見ただけ。
- α=0.01 でも同じ傾向（VLDB memo 41.5→65.7% に対し LRU 34.0→35.3%）。ただし、この差を提案手法で埋められるのは α≥0.05 の場合だけ（§9）。
- 生成元：`figs/plot_challenge_memo_vs_lru.py`。`M_Research/graph_data/figA_*` などの古い図は別実装由来で数値が違うので使わないこと。

### 5.2 課題②：ノードとエッジで再利用の構造が違う
- 再利用 tier 別の平均（VLDB、α=0.01、キャッシュなしの実走トレース）【確定】

| 項目 | よく再利用されない → よく再利用される |
|---|---|
| ノードの始点 hop | 5.3 → 3.9 |
| ノードの次数 | 8 → 41（正の関係） |
| エッジの始点 hop | 4.5 → 2.5 |
| エッジの次数（端点） | 46 → 34（**逆の関係**） |

- 解釈：無向 RW は隣接先を一様に選ぶ。高次数の端点にぶら下がるエッジは 1/deg の確率でしか選ばれないので、再通過しにくい。
- 旧資料には「hop 126→7」という記述があるが、これは誤り（上の実測値に差し替え済み）。

### 5.3 予備観察（旧ドラフト v1〜v6）【旧：数値は未再検証】
- O1：始点に近いほど訪問数・ヒット数が多い。
- O2：同じ距離なら次数が高いほどアクセスされやすい（ただしグラフ依存）。
- O3：半数以下の RWer で、全 RWer の訪問集合の約半分を覆える。先行 walk の履歴は予測に使える。
- 一度以上アクセスされるエンティティは約47%で、残り約53%はキャッシュを汚染する。

---

## 6. 提案手法の変遷と最終形

### 6.1 変遷の全体像

| 時期 | 系列 | スコア | 運用 | 結末 |
|---|---|---|---|---|
| 〜2025-07 | 旧ドラフト v1〜v6 | `S = H·(1−α)^(β·hop)·deg^γ·(1+λ·lw)`（v1 は和と積が混在） | 二段階（1〜N本目で学習、N+1本目以降で学習項を反映）。純スコア順に追い出す | 数値検証が不十分。「最大約160秒削減」は旧手法の値で、条件も版ごとに食い違う（RTT 100ms／50ms） |
| 2026-07 | reuse_score | `V = O^θ·L^λ·H^β·D^γ(·R^ρ)`。各因子を [1,2) に正規化 | 追い出しの土台を landlord-L から last_access に変更。w=0 で厳密に LRU と一致 | walk 100本・seed 単発で測っていたため失敗（§11） |
| 2026-08 | **dual-pool**（IN研稿） | ノード：`Freq^θ·Close^β·Deg^γ`、エッジ：`Freq^θ·Close^β` | ノードとエッジで別プール（容量 40:60、w_node=100／w_edge=300） | LOO/Shapley でプール分離の寄与がほぼゼロと判明。実分割では有害 |
| 2026-08 | StructuralARC | ARC 土台で `position + w·V` が最小のものを追い出す | ARC 土台 | 対 ARC で +4.2/+1.8 と出たが、seed が相関しており**要再測** |
| **2026-09-08** | **案A（最終形）** | `V = O^θ·H^β·D^γ`（D はノードのみ） | **単一プール**、`last_access + w·V`、w=100 | 【確定】現行の定義 |
| 2026-09-21 | 検討中 | `V = O^θ·H^β·D^(γ/(1+O))`（ノードのみ）、ρ=0 | 同上 | 分析上は有望だが未実装 |

### 6.2 最終形（案A）の定義【確定】
```
Priority(e) = last_access(e) + w · V(e)       ← Priority が最小のものを追い出す
V(e)        = O(e)^θ · H(e)^β · D(e)^γ        （D はノードのみ。エッジは D=1）
O(e) = 1 + freq/(freq + C_F)                  過去のアクセス回数（飽和させる。C_F=5〜10）
H(e) = 1 + σ(z(近さ))                          始点への近さ（Welford 法で逐次標準化 → sigmoid）
D(e) = 1 + σ(z(次数))  または 1 + deg/(deg + C_D)
w = 100（両グラフ共通）、単一プール
```
- 各因子は [1,2) に収まるので、指数を0にすればその項は無効になる。**w=0 なら LRU と厳密に一致**する（配線の正しさの検証に使える）。
- 実サーバでの起動オプション：`--size-neutral --reuse-edge-no-deg --reuse-w 100`。
- 仮想サーバシミュレータと実分散実装の結果は VLDB で一致（Δ=0.00）。

### 6.3 設計理由
1. **LRU 土台＋ボーナス**：recency の情報を捨てない。純スコア順の追い出しは直近性を失って不利になる。RTT だけで重み付けした GD が LRU に負けたのと同じ教訓（§12）。
2. **積形**：「近くて、かつよく踏まれる」という AND を表現できる。和形では片方が大きいだけで救われてしまう。24/24条件で積形が勝った（§8.2）。
3. **次数はノードのみ**：エッジでは次数の効果が逆向きになる（§5.2）。エッジに次数を入れると −0.75／−0.54pt（`singlepool_*.csv`、w=100）。旧記載の「−0.68／−1.02」は CSV と一致しない。
4. **単一プール**：プール分離は Shapley 寄与が +0.12/+0.54pt で誤差程度。実分割（METIS）ではサーバが事実上「ノード担当」と「エッジ担当」に分かれるので、分離すると −5.77pt と有害。パラメータも4個から1個に減る。
5. **`/size` の補償**：占有量（ノード2・エッジ3）で割ると型に偏りが出るので打ち消す。これを入れないと VLDB で −2.38pt（旧記載は −2.3）。

### 6.4 近さ H の定義に関する注意
- 当初は二部グラフ上の BFS 最短距離を使っていたが、これは**サーバが持てないオラクル量**である。
- 実装が持っているのは歩数（`hops_done`）なので、分析の既定を歩数に変更した。すると12/12条件で H の効果が弱まった（Spearman ρ で 0.09〜0.26 低下）。
- IN研稿の「hop＝最短ホップ数」という記述は、実装と一致しない。

### 6.5 計算量
- 追加で要るのは、エントリごとのスコア、回数カウンタ、優先度付きキューだけ。
- ミス1回あたりの追加計算量は O(log C)。RTT（ミリ秒オーダ）に比べて無視できる。

---

## 7. 評価

### 7.1 実験条件

| 項目 | 値 |
|---|---|
| グラフ | VLDB（**317,080 ノード / 1,049,866 エッジ**。無向で各辺1行。SNAP com-DBLP と同じ値【推測：実体は com-DBLP】）、Amazon0601（403,394 ノード / 3,387,388 **有向**辺。無向として重複を除くと 2,443,408）。RW は無向として扱う |
| 分割 | K=2。モジュロ分割（ノードは n%2、エッジは (u+v)%2）。追加検証で METIS による実分割も使用 |
| RW | α=0.1（主条件）、0.01（拡散側）。walk 1000本。始点0（主条件）。バックトラックあり |
| 容量 | 100（重み単位。ノード=2、エッジ=3） |
| seed | 独立5本（`--disjoint`） |
| 比較 | LRU、ARC、memo（上限）。ほかに related_work で12方式を移植 |
| 実行環境 | 仮想サーバシミュレータ（単一プロセスで K 台を模擬）。実分散版は ab06/ab11 → cap11〜cap26 |

- ヒット率は「アクセス列 × 分割 × 方式」だけで決まり、ネットワークに依存しない。そのため仮想サーバで測り、RTT はモデルで換算する。

### 7.2 IN研稿の表1【旧：相関した seed・始点0の1点・dual-pool】

| | LRU | 提案 | memo |
|---|---|---|---|
| VLDB | 45.3 | 51.9（+6.6pt、相対+14.6%） | 74.7（※CSV の値は 74.84） |
| Amazon | 44.3 | 51.4（+7.1pt、相対+16.0%） | 64.0（※CSV の値は 64.33） |

- ※memo 列は、表1 の元 CSV（`loo_*_paperseed_raw.csv`）と一致しない。74.7 は w スイープ図（3 seed）や dist の seed42 単発（74.70）の値と一致するので、別の run の値が混ざったと推測される。

### 7.3 確定値（案A、独立 seed 5本、α=0.1、walk 1000本、容量100、始点0）【確定】

| | LRU | ARC | **提案** | memo | 提案−LRU | 提案−ARC |
|---|---|---|---|---|---|---|
| VLDB | 45.49 | 51.43 | **52.47** | 73.86 | **+6.98** | +1.04 |
| Amazon | 45.05 | 52.36 | **52.10** | 65.04 | **+7.04** | −0.26 |

### 7.4 始点依存性【確定・最重要】
- 提案が LRU に勝った始点は、VLDB 4/5、Amazon 6/10。
- **始点平均の改善は、対 LRU で +3.20／+2.56pt、対 ARC で −0.05／−0.67pt**。
- 論文の +6.6/+7.1 は、条件の良い始点1点の値だった。
- 効くかどうかは **LRU/memo 比（始点近傍の局所性の指標）** で説明できる（r=+0.909）。
  - r=+0.909 は、天井にあたる VLDB の start4 を除いた14点での値。15点すべてを入れると r=0.496 になる。
  - 0.6以上なら +4〜7pt。
  - 0.53以下なら −0.8〜−1.4pt。ただし例外が2点あり、Amazon の start7（0.490、+2.61）と start8（0.443、+0.20）はプラスだった。
  - 1に近い（天井）と +0.2pt。

### 7.5 RTT 込み実行時間（推定）
- 時間モデル：遷移回数 × 2.27ms ＋ ミス回数 × RTT。条件は α=0.1、1000本、容量100、seed 5本、RTT=50ms。

| | キャッシュなし | LRU | 提案 | memo |
|---|---|---|---|---|
| VLDB ミス回数 | 9,216 | 5,042 | 4,438 | 2,319 |
| VLDB 推定時間 | 482秒 | 273秒 | 243秒 | 137秒 |
| Amazon 推定時間 | 478秒 | 275秒 | 243秒 | 184秒 |

- **対 LRU で −30秒（−11.1%）／−32秒（−11.7%）**。
- 注意：
  - これは dual-pool 期のモデル推定であり、実ネットワークでの計測ではない。
  - 2.27ms の出所は α=0.01 の実測で、図の条件（α=0.1）とは異なる。
  - 旧ドラフトの「最大約160秒削減」は旧手法の値なので使わないこと。
- 前段の研究（`base/rtt`）：Azure 実測（10/60/200ms）に基づく RTT パターンで換算した。memo に対する比は、キャッシュなし ≈1.36〜1.40、LRU/ARC ≈1.06。

---

## 8. 要素別寄与とアクセス要因の分析

### 8.1 対照実験（LOO／Shapley／感度）【確定】
条件は §7.3 と同じ。数値は VLDB／Amazon の順。

- **LOO（案A、全体ヒット率）**：その要素を抜いたときの低下幅。
  - Base（LRU 土台）+11.70／+4.74
  - Close（近さ）+4.24／+3.24
  - Freq（回数）+3.26／+3.03
  - Deg（次数）+0.25／+0.87。内訳はノード +6.72、エッジ −5.58（VLDB）で相殺している。
- **Shapley 分解**（dual-pool 期。16セルで計算し、合計が総改善 +6.56／+7.05 と厳密に一致）
  - Close +3.38／+2.85
  - Freq +2.89／+3.08
  - Deg +0.17／+0.58
  - Pool +0.12／+0.54
  - → **改善の 96%／84% は Close と Freq で説明できる**。
- **次数は代替財**：単独で入れれば +0.68／+0.91 だが、Freq や Close と重なるため LOO では負になる。
- **プール分離**：単独では効果ゼロ。「全体を上げる機構」ではなく、「エッジが追い出され続けること（餓死）を防ぎ、型間の偏りを直す機構」と位置付けるのが正確。
- **パラメータ感度**（7軸）
  - 動作点は、どの軸でも最良値との差が +0.56pt 以内。
  - 効き幅が大きいのは容量配分（7.7〜10.3pt）と w（約7pt）の2軸だけ。
  - w は 25〜800 の全域で LRU を上回る。
  - 指数の最良値は θ=1.5、β=0.5、γ=0.5（γ=1 はすでに劣化する側）。
  - 3軸を同時に最良値へ動かしたときの +1.23／+0.72、γ=0 版の +0.86／+0.36、再調整版の始点平均 −0.07／−0.32 は、**結果ファイルが残っておらず未検証**（メモ上の値）。
  - **パラメータを再調整しても ARC を安定して超えることはできない**（負の結果）。
- **deny の影響**：結果に影響しない。論文の図は deny テーブルを読み込んでおらず、しかも start=0 を拒否するエンティティは0件だった。

### 8.2 アクセス要因の相関（キャッシュ方式に依存しない、生のアクセス統計）【確定】
- 設定：無作為8視点 × RW 1000本。観測窓の後の将来アクセス回数を目的変数とする。

**注意**
- results に残っている summary・interplay の CSV はすべて歩数版の H で、BFS 版の結果ファイルはない。BFS 版の値は keys.csv から `--hop-metric bfs` で再計算して確認した。
- Amazon の値は、9/19 に `--largest-component` 付きで再実行した際に上書きされている。現在の値は α=0.1 で D −0.053 / H −0.619 / O +0.597 / R +0.366。
- 下の MI・交互作用情報量（II）の値は、旧 Y ビン（6分位）と BFS 版の H で計算したもので、今のコードでは再現できない。Shapley 値は `--hop-metric bfs` を付ければ再現できる。

**単変量**（Spearman ρ、VLDB α=0.1、BFS 距離版）

| 要因 | ρ |
|---|---|
| H（hop） | −0.767 |
| O（過去回数） | +0.677 |
| R（直近性） | +0.476 |
| D（次数） | −0.127 |

- 歩数ベースの H では −0.535 に弱まる。α=0.01 では全体に値が半減する。
- 上位100件の捕捉率：O 単独 86.5%、H 単独 85.0%、oracle 90.4%。

**結論**
1. **主軸は H と O**。互いを統制しても残る。
2. **R は O とほぼ冗長**。→ R^ρ を独立項にせず、LRU 土台に任せる（ρ=0）。
3. **次数 D は独立した予測子ではない**。O で統制すると ≈0、hop で統制すると負になる。
   - ノードでは弱い正、エッジでは負。
   - node と edge をまとめて分析すると偽の相関が出る（Simpson 型）ので、**必ず層別すること**。
4. **6グラフでの追試**（次数分布の変動係数 CV が 0.32〜3.50 の er_mid / amazon / ba_mid / vldb / slashdot / enron）
   - 次数が効くのは「**CV が大きい** かつ **到達率が約15%以上**」の場合だけ（enron α=0.01 で ρ=+0.65）。
   - vldb と amazon は到達率が2.5%以下なので、CV に関係なく ρ≈0。つまり次数が効かないのは、グラフの性質というより「walk が始点近傍から出ていない」ため。
5. **式の形**
   - 積形が和形より良い（24/24条件、+0.6〜+26.7pt）。
   - 交互作用項は不要。
   - α=0.01 では O 単独が4要素版より良いことがある（多因子化による過適合）。
6. **次数の入れどころ＝挿入直後だけ**
   - O=1 の層（挿入直後で回数の情報がない）に限れば、次数上位が将来アクセスを 3.6〜4.0倍多く捕捉する（slashdot/enron α=0.01）。
   - 減衰形 `D^(γ/(1+O))` なら、α=0.01 で +0.3〜+3.3pt、α=0.1 でも実質無害。**ただし、この減衰形の実装も結果 CSV も見つかっておらず未検証（メモ上の値）**。
   - 次数を恒久項として入れた場合の悪化（−2.5〜−3.8pt）が出るのは slashdot（−2.48）と enron（−3.72）だけ。他のグラフはほぼ0（vldb −0.20、amazon +0.15）。
   - 設計の意味：**次数は「O が貯まるまでの暫定 prior」**として使う。
- 資料上の注意：ACCESS_FACTORS_RESULTS.md の末尾に「追記7」という見出しが2つあり、一方に撤回済みの「γ を適応的に切り替える」案が残っている。最終的な結論は追記8（減衰形）。

---

## 9. レジーム依存性と限界

### 9.1 効く条件（4つの軸）

| 軸 | 効く側 | 効かない側 |
|---|---|---|
| α（終了確率） | ≥0.05（局所。PPR 的な用途） | ≤0.02（拡散）。walk を10,000本に増やしても LRU を超えない |
| walk 数 | 500本以上（再訪が貯まる） | 100本程度 |
| 容量 | 逼迫している（≤100） | 大きい（memo に収束） |
| 始点 | LRU/memo ≳0.6（近傍が密） | ≲0.53 |

### 9.2 限界
1. ARC とはほぼ同等で、明確に上回るとは言えない（Amazon では −0.26pt）。
2. 実行時間はモデル推定であり、実ネットワークの揺らぎや輻輳は含まない。
3. K=2、30万〜40万ノード規模での結果。数億ノードや K>2 での挙動は未検証。
4. hop の定義（BFS か歩数か）で効果の大きさが変わる。実装で使えるのは歩数だけ。
5. キャッシュの漏洩や陳腐化によって認可を誤るリスク、TTL の設計は未評価。
6. 動的グラフ（構造の変化）には未対応。

---

## 10. 訂正履歴と、公表稿（IN研稿）の不整合一覧

**IN研稿（2026-08）を今後引用・改稿する際は、以下を必ず反映すること。**

| # | IN研稿・旧資料の記述 | 正しい内容 | 根拠 |
|---|---|---|---|
| 1 | 提案 = dual-pool | 単一プール（案A）に変更。プール分離の寄与はほぼゼロで、実分割では有害 | LOO 追記3・4 |
| 2 | 「β=γ=1 固定」 | γ=0（またはノードのみ・減衰形）。図8 だけは γ=0 で描かれていて本文と不整合 | LOO、感度、ACCESS 追記7・8 |
| 3 | 最大 +7.1pt 改善 | 始点1点の値。始点平均は +2.6〜3.2pt | 始点スイープ |
| 4 | 5 seed の誤差棒 | `seed+i` 規則のため walk 集合が99%重複し、誤差棒が2〜3倍過小。独立 seed で取り直し済み | LOO 追記1 |
| 5 | ARC との比較がない | ARC とは同等（+1.04／−0.26） | 追記4 |
| 6 | VLDB 2,856,664 / 13,511,731 | 実験で使ったのは 317,080 / 1,049,866 | vldb.gr |
| 7 | 「30% deny」 | 図は deny なしで生成されている。start=0 には deny が0件で、結果にも影響しない | LOO 追記1 |
| 8 | hop＝最短ホップ数 | 実装は歩数（hops_done）を使う。BFS はオラクル量 | ACCESS 追記3 |
| 9 | Pool の LOO +4.2 | 共有プール側の `/size` 型バイアスによる人工物で、正しくは ≈0 | LOO 追記3 |
| 10 | 対 ARC +4.2／+1.8（StructuralARC） | seed が相関していたので要再測 | LOO |
| 11 | 旧ドラフト「最大約160秒削減」 | 旧手法の値。条件も版で食い違う（100ms／50ms）。新手法では −30秒（−11%、推定） | exec-time 図 |
| 12 | abstract v1/v2「ARC を上回る」「ARC 土台が最強」 | 確定値では過大主張 | 追記4 |
| 13 | 「hop 126→7」 | ノード 5.3→3.9、エッジ 4.5→2.5 | 課題③ 図 |
| 14 | 以前の BFS 先読みの結論「距離はホット領域と無相関」 | α=0.01・先読み方式での結論であり、α=0.1 の相関分析とは条件が違う【推測】 | hub-pin README |
| 15 | IN研稿 表1 の memo 74.7 / 64.0 | 元 CSV では 74.84 / 64.33 | 付録A-4 |
| 16 | 「エッジに次数を入れると −0.68 / −1.02」 | −0.75 / −0.54 | 付録A-5 |
| 17 | r=+0.909 | 14点（VLDB start4 を除く）での値。15点すべてでは 0.496。しきい値にも例外が2点ある | 付録A-5 |
| 18 | Amazon0601 を「無向・約350万エッジ」 | 3,387,388 は有向辺の数。無向で重複を除くと 2,443,408 | 付録A-11 |
| 19 | 前史「LiveJournal 10分割・ロス3%」「日米間」 | 5台に分割・1頂点10 RWer、ロス0.03%、日中間 | 付録A-10 |
| 20 | アクセス要因の Amazon 値、interplay の MI・II | 上書き済み、または旧ビン設定の値 | 付録A-7 |

補足：`研究室資料/大学院/IN_KishiMaiko.pdf`（2025年）は、JWT トークンの再利用による認証高速化という**別の研究**の稿である（最大33.77%高速化）。Desktop 版（2026-08-13、dual-pool）と混同しないこと。【推測：同名ファイルが2つある】

---

## 11. 採用しなかったアプローチと理由

| アプローチ | 結果 | 理由・教訓 |
|---|---|---|
| BFS 先読み（auth-cache-bfs-degree） | 先読みしたノードのヒット率 0.26〜0.31（LRU は 0.45） | 先読み範囲への参照は3.5%しかなく、キャッシュの39%しか使われなかった。当てずっぽうの先読みは容量を浪費する |
| hub-pin（次数上位を固定枠に置く） | LRU と +0.015 以内で同等（旧記載の「±0.01」は README の表と一致しない） | 容量が作業集合をほぼ収容できる条件だった。LFU/TinyLFU の方が安定して +0.01〜0.025 |
| 旧 reuse_score（7月） | walk 10,000本で 18.8%（LRU 22.0% より下） | (1) 改善余地の小さい walk 100本で測っていた、(2) landlord-L 形は w に対してスケール不変で w が効かなかった、(3) `(1−α)^hop` が飽和した、(4) 学習項が lw=0 で無効だった、(5) seed 単発だった |
| 学習フェーズ（lw、二段階運用） | 単一 seed の段階から検証されず | オンラインの O が同じ情報を担う |
| dual-pool | Shapley ≈0、実分割で −5.77pt | §6.3 |
| 次数の恒久項（γ>0） | LOO・Shapley・相関・cov のすべてで否定された | 次数は O の代理にすぎない。挿入直後の prior としてのみ残す |
| R^ρ（直近性の独立項） | cov はほぼ不変 | O と冗長。LRU 土台が担う |
| 和形スコア、交互作用項 | 24条件すべてで積形に劣る／過適合 | — |
| RTT のみの重み付け（GD） | 高 RTT ノードは守れるが、総 RTT は LRU より悪化 | recency を失う。RTT は頻度と組み合わせて使う（GDSF は +2〜3%） |
| auth-subgraph-server | START_NODE=ALL で認可拒否が大量に出て、RW が進まなかった | 理由の記載なし【推測：allow/deny の反転】 |
| add-cache-out（E1） | not-use に移動 | 理由の記載なし |

---

## 12. RTT のモデル化（次段の研究の土台。スコアへの接続は未完）

- **動機**：RTT が主体ごとに違えば、高 RTT のエンティティを優先して残す価値がある。スコアに RTT を入れる前に、「どんな RTT の並びなら差が出るか」を制御できる必要がある。
- **SBM 上の3つのノブ**（`graph_data/SBM_rtt.py`）
  - 比率：どの RTT 帯に何割のエッジが向くか。
  - 順序：ラベルの空間配置。smooth なら lag-1 自己相関 +0.25、scrambled なら −0.41。
  - 滞在長：ブロックサイズ／ブロック内に留まる確率。
- **漸近モデル**（`graph_data/rtt_asymptotic.py`）
  - 走行平均 RTT は μ（定常分布で重み付けした平均）に収束する。収束の形はブロック連鎖の固有値で決まる。
    - λ∈(0,1) かつ a>0 → 上から収束
    - λ∈(0,1) かつ a<0 → 下から収束
    - λ<0 → ギザギザ
  - μ（直流成分）と形（k≥2 のモード）は独立に決まる。
- **実グラフでの実現**（`master-progrem/base/base-rtt/`）
  - `g_t = μ + a·λ^t` を設計し、RW の初回訪問時にその値を割り当てて凍結する（paint field）。
  - amazon で above/below/zigzag の3形状を再現した（RMSE ≈3.5。図でのみ確認）。zigzag はノード↔エッジの二部構造（偶奇）が2色塗り分けを与えるので、1つの場で実現できる。
  - 構造を無視したランダム割り当てでは、数ステップで μ に潰れて形が出ない。
- **キャッシュとの関係**
  - RTT の順序は、単一 walk ではヒット率を変えない（ヒット／ミスは軌跡で決まる）。変わるのは、複数 RWer が非同期に走り、かつ追い出し圧があるときだけ。
  - GDSF（RTT×頻度）は総 RTT で LRU を約2〜3%上回った。ただし、このスクリプト（cache_replay.py）は現存せず未検証。fit λ≈0.99 と lag-1 自己相関 +0.25／−0.41 も同様に、メモ上の値。差が小さいのは、Louvain 由来の RTT が空間的に固まっていて、識別する余地が少ないため。
- **現状**
  - multiserver 版では、各ステップの RTT を walk 内で運ぶところまで実装済み。
  - 実行時間を「移動 RTT（毎 hop）＋認可 RTT（リモートかつミス時）」に分解済み。キャッシュで認可分が約40〜44%減る。
  - **RTT を admit/evict のスコアに入れる部分は未実装**。差を出すには、高 RTT が散在する（hetero な）場が必要。

---

## 13. 実装・実験基盤

### 13.1 リポジトリ
- `M_Research`（このリポジトリ）
  - 前史の C++ 非同期 RW 実装（src〜src12。src12 が最新でキャッシュ付き）
  - 実験出力（output/、output2/）
  - グラフ生成（graph_data/）
  - 論文ドラフト v1〜v6
  - 輪読資料（rinko/）
  - 前任者の PDF（resources/）
- `master-progrem`（`~/Documents/GitHub/master-progrem`）：本研究の Python 実装と実験の本体。

### 13.2 master-progrem のディレクトリ

| ディレクトリ | 用途 | 状態 |
|---|---|---|
| `base/base` | 認可なしの分散 RW（HTTP、ノード／エッジ分散） | 基盤 |
| `base/auth-baseline-cache` | 最初期の memo 型キャッシュ。none/memo/lru/arc の比較と原因分析図 | 初期 |
| `base/auth-cache` | 移動のたびに認可するモデル＋キャッシュ | 旧 |
| `base/auth-many-server` | deny テーブルの生成、METIS 分割 | 現役（データ生成） |
| `base/new-many-server` | servers.conf で任意 K 台を動かす自己完結版 | 現役 |
| `base/proposed_cache` | **提案手法の本体**（core/dist/experiments/docs/figs/results/archive） | 現役・中心 |
| `base/related_work` | 比較方式12個の移植 | 現役 |
| `base/rtt` | ヒット率 → RTT コストへの換算評価 | 評価 |
| `base/base-rtt` | RTT 軌跡の設計と実現、multiserver | 次段の土台 |
| `base/sub-visit-count` | よく通るサブグラフだけ丁寧に認可する構想 | 構想 |
| `base/not-use/*` | hub-pin、add-cache-out、auth-subgraph-server など | 廃止 |

### 13.3 主要ファイル（proposed_cache）
- 実サーバ／コントローラ：`split_remote_server_reuse_score.py`、`split_controller_reuse_score.py`
- 仮想サーバシミュレータ：`virtual_server_sim_reuse_score.py`
  - 実サーバのコードを import して流用するので、キャッシュのロジックがずれない。
  - 検証済みの不変条件：K=1 ならリモート0、w=0 なら LRU と一致。
- 対照実験：`run_loo_ablation.py`、`run_shapley_decomposition.py`、`run_param_sensitivity.py`、`run_startnode_sweep.py`
- 相関分析：`experiments/analyze_access_factors.py`、`analyze_factor_interplay.py`、`analyze_score_form.py`、`analyze_degree_placement.py`
- 作図：`figs/plot_challenge_memo_vs_lru.py`（課題）、`plot_cause_structure.py`（原因）、`plot_loo_ablation.py`、`plot_shapley_decomposition.py`、`plot_startnode_dependence.py`、`plot_param_sensitivity.py`、`collect_time_stats.py` → `plot_hitrate_and_time.py`（実行時間）
- スイープ：`run_reuse_score_sweep_rtt.sh`
  - 環境変数：`ALPHA_OVERRIDE`、`GRAPH_OVERRIDE`、`THETA/LAMBDA/BETA/GAMMA/LW/RHO_SWEEP_OVERRIDE`

### 13.4 比較方式（related_work）
none / random / fifo / lru / lfu / lru_k / gclock / degree_static / soop_admission / lac_layout / proposed / opt、および arc / memo。
- 「原研究のシステムの再現」ではなく、「同じ RW と認可キーに適用した判断規則」を比べるもの。論文では「移植版」と明記すること。
- karate（K=2、30本）で、実 HTTP と仮想の結果が11方式すべて一致（テスト15件）。実クラスタでは未検証。

### 13.5 実行環境とデータ
- サーバ
  - 旧：ab06（10.58.60.6）、ab11（10.58.60.11）、port 3000
  - 新：cap11〜cap26（10.58.59.11〜26）、ユーザ kate、最大16台。手元から接続するには VPN が要る。
- グラフ：`dataset/Louvain/graph/*.gr`（1行1辺）
  - vldb、amazon0601、karate、com-amazon-connected、email-enron、soc-slashdot、er_mid/ba_mid（合成）など。
  - コミュニティ情報は `dataset/Louvain/community/*.cm`。
- deny テーブル：`node_to_starts_server{i}.json`
  - 中身は拒否リスト（名前に反して「拒否する始点」の一覧）。
  - allow 形式の密なデータを deny サーバに入れると認可が反転するので注意。
- METIS のエッジ ID に off-by-one バグがあり、修正済み（`max_node+idx+1` → `max_node+idx`）。
- 実行時の落とし穴
  - `NO_BACKTRACK` の既定値がスクリプトによって違う（dist は0、他は1）。VLDB の LRU で 45.4% と 21.1% ほど結果が変わる。
  - 古い PNG は `_deprecated_old_plots` に隔離されている。

---

## 14. 未完了・今後の課題

### 14.1 検証の積み残し
1. 新手法（案A）での RTT 込み実行時間の再計測（実分散で）。
2. StructuralARC（ARC 土台）の独立 seed での再測と、重み付き容量版での確認。
3. α=0.05／0.02 の副条件12セル、tuning 用と held-out 用に分けた seed での検証。
4. w∝容量という規則の確認（`run_w_analysis.py` はあるが結果が未整理）。
5. 次数の減衰形 `D^(γ/(1+O))` の実装、β を α に連動させる切り替え、log(deg) 化 → LRU/ARC との比較。
6. K>2、METIS 実分割、大規模グラフでの評価。

### 14.2 研究の発展方向
1. **ARC を明確に上回る**こと。ARC 土台に構造ボーナスを載せる方向が最有力（seed 相関ありの段階で +4.2/+1.8pt）。
2. **RTT をスコアに接続**する。高 RTT が散在する場を用意し、RTT×頻度で重み付けする（§12）。
3. **α・始点に応じた適応化**。LRU/memo 比が始点の局所性を示す指標になる（r=0.909）。
4. 認可キャッシュのセキュリティ（漏洩・陳腐化、TTL）の定量評価。
5. 動的グラフへの対応。

### 14.3 論文として書くときの推奨ストーリー（現時点のデータで言えること）
- **課題**：memo と LRU の差が walk 数とともに広がる（確定値あり）。
- **原因**：再訪は「近さ」と「過去回数」で予測でき、次数は独立の予測子にならない（相関分析、6グラフ）。
- **提案**：LRU 土台＋近さ×回数の積形ボーナス（単一プール）。
- **評価**：対 LRU +7.0pt（始点0）、始点平均 +2.6〜3.2pt。ARC とは同等。効く条件は α≥0.05 かつ LRU/memo≳0.6。
- **貢献として強いもの**：要素別寄与の厳密な分解（Shapley）と、「次数は効かない」「プール分離は効かない」という負の結果。どちらも、CBGA などの先行研究にはないアブレーションになる。

---

## 15. 用語集

| 用語 | 意味 |
|---|---|
| RW / RWer | ランダムウォーク／1本の walk（メッセージ） |
| α | 終了確率。平均経路長は 1/α |
| エンティティ | 認可対象になるノードまたはエッジ |
| 越境 | 他主体が所有するエンティティへ遷移すること |
| deny list | 所有主体が持つ「アクセスを拒否する始点」の集合 |
| memo | 容量無限のキャッシュ（理想上限） |
| O / Freq | そのエンティティの過去のアクセス回数 |
| H / Close | 始点への近さ（実装は歩数ベース） |
| D / Deg | 次数（ノードのみ） |
| R | 直近性（LRU 土台が担う） |
| L / lw | 学習 walk での訪問回数（旧系列） |
| w | LRU 時刻に対するボーナスの重み |
| LOO | leave-one-out（1要素ずつ抜いたときの低下幅） |
| Shapley 分解 | 全部分集合で各要素の平均的な寄与を出す。合計が総改善と厳密に一致する |
| cov@100 | スコア上位100件が将来アクセスを覆う割合 |
| LRU/memo 比 | 始点近傍の局所性の指標。0.6以上で提案が効く |
| レジーム | 効く・効かないを分ける条件（α、walk 数、容量、始点） |
| 案A | 2026-09-08 に確定した単一プール版の提案手法 |

---

## 16. 資料索引

### M_Research
- `paper_draft_v1.md`〜`v6.md`：旧系列の論文ドラフト（v6 が最新）
- `resources/概要.pdf`、`20220805_IN研.pdf`、`tecrep.pdf`：前任者の非同期 RW 研究
- `rinko/rinko_CBGA_IC2E2015.md`、`slide_figure_map.md`：CBGA の輪読資料
- `graph_data/`：SBM 生成器、RTT モデル（`SBM_rtt.py`、`rtt_asymptotic.py`）、グラフ分割・変換
- `src`〜`src12`：C++ 非同期 RW（src2〜9 は作業ツリーでは削除済みで、git 履歴にある）
- `output/`、`output2/`：KnightKing 比較、RTT・ロス・ポート数・パーティション数の実験結果

### master-progrem/base/proposed_cache
- `README.md`（最新の索引）、`RUNBOOK.md`（実行手順）
- `docs/LOO_ABLATION_RESULTS.md`、`docs/ACCESS_FACTORS_RESULTS.md`（確定結果）
- `docs/README_SUMMARY.md`、`abstract_v1/v2.md`、`PROPOSED_METHOD.md`、`RESEARCH_STORY.md`、`MOTIVATION.md`、`EXPERIMENT_SUMMARY.md`、`ADDITIONAL_EXPERIMENTS_PLAN.md`（経緯資料。数値は旧）
- `results/`、`figs/`

### その他
- `~/Desktop/IN_KishiMaiko.pdf`：IN研稿（2026-08、dual-pool 版）
- `master-progrem/rinko/0915.md`：CBGA の輪読原稿（13枚）

---

## 付録A：数値の出典トレース（実験コマンド・結果フォルダ・検証状況）

2026-09-26 に、本文中のすべての数値について、生成スクリプト・実行コマンド・結果ファイルを特定し、結果ファイルの値と突き合わせた。作業は読み取りのみで行い、実験の再実行はしていない。照合に使ったのは、CSV/JSON の再集計と、keys.csv を入力にした集計モードだけ。

### A-0. 前提と凡例

**パスの略記**
- `MP` = `/Users/maiko/Documents/GitHub/master-progrem`
- `PC` = `MP/base/proposed_cache`
- `RS` = `PC/results`
- `MR` = `/Users/maiko/github/M_Research`
- master-progrem のコマンドは、`MP`（リポジトリのルート）から実行する前提。

**検証状況の凡例**
- ✅ **検証済**：結果ファイルから再計算して一致した。
- 🟡 **図で確認**：CSV が残っておらず、図の注記や目視で確認した。
- ⚠️ **不一致**：実際の値を併記した（本文は訂正済み）。
- ❓ **未特定**：スクリプトまたは結果ファイルが見つからない。メモ上の値。

**コマンド欄の読み方**
- 「（履歴）」：`~/.zsh_history` に実際に実行した記録があるもの。
- 「（文書）」：RUNBOOK や README に記載されているもの。
- 「（推定）」：argparse の既定値や図の横軸から起こしたもの。
- `~/.zsh_history` には、実験コマンドがほとんど残っていない（最終更新 9/13。残っているのは作図コマンドと scp 程度）。そのため大半は（文書）か（推定）になる。

### A-1. 再現性に関する重大な注意（必読）

1. **作業ツリーから、多くのスクリプトが削除されている**。git の index には残っているので、次のコマンドで読める。
   ```bash
   git -C /Users/maiko/Documents/GitHub/master-progrem show :base/proposed_cache/<path>
   ```
   再実行する前に、次のコマンドで作業ツリーへ戻す必要がある。
   ```bash
   git -C /Users/maiko/Documents/GitHub/master-progrem restore base/proposed_cache/<path>
   ```
   index にしか残っていないファイル：
   - `core/virtual_server_dualpool.py`
   - `experiments/run_shapley_decomposition.py`
   - `experiments/run_startnode_sweep.py`
   - `experiments/run_singlepool_selection.py`
   - `figs/plot_proposed_win.py`
   - `figs/plot_sweeps.py`
   - `figs/plot_wsweep_dualpool.py`
2. **dual-pool 系の数値は、今の作業ツリーのままでは再現できない**。
   - 作業ツリーの `run_loo_ablation.py` は `--pool shared` しか受け付けず、`w_edge` の既定値も 100 に変わっている。index 版と HEAD 版（a0526b7）は `--pool split` に対応している。
   - `run_param_sensitivity.py` も shared 用に改変されている。
3. **作図スクリプトの多くは、数値を標準出力に出すだけで CSV を残さない**。対象は `plot_challenge_memo_vs_lru.py`、`plot_cause_structure.py`、`plot_sweeps.py`、`plot_wsweep_dualpool.py`。そのため、図の注記が唯一の保存値になっている。今後は `| tee results/xxx.log` で標準出力を残すこと。
4. **図の置き場所が変わっている**。`figs/*.png` は `figs/notuse/` に移動済みで、README_SUMMARY の画像リンクは切れている。
5. **seed の系列が2種類ある**。
   - LOO・感度・始点スイープ：`1 + k·(walks+1)`（`--disjoint`）→ 1, 1002, 2003, 3004, 4005
   - singlepool_selection：`1 + k·walks` → 1, 1001, 2001, …
   - このため、案Aの表の LRU 45.49 と、選定表の LRU 45.51 が少しずれる。
6. **論文用の seed {1,7,13,99,123} は `seed+i` 規則のため、walk 集合が99%重複している**。誤差棒は2〜3倍過小になる。
7. **`NO_BACKTRACK` の既定値がスクリプトによって違う**。`dist/run_local.sh` は冒頭コメントでは「既定0」だが、コード上の既定は1。
8. **Amazon のアクセス要因の結果は上書きされている**（9/19 07:26 に `--largest-component` 付きで再実行）。
9. **アクセス要因の summary・interplay の CSV はすべて歩数版の H**。BFS 版は残っていないので、keys.csv から `--hop-metric bfs` で再計算する。その際は out-dir を分けること。分けないと歩数版を上書きする。
10. **ディスク上に存在しないもの**：
    - LiveJournal のグラフ実体
    - `base/base-rtt` の旧スクリプト（`fit_asymptotic.py`、`record_walks.py`、`cache_replay.py`）
    - `auth-cache-bfs-degree` の入力ログ
    - related_work の results/（related_work 自体が git 未追跡）

---

### A-2. 課題①・②（§5）

| 数値 | スクリプト | コマンド | 結果パス | 検証 |
|---|---|---|---|---|
| VLDB memo 53.5→85.2 / LRU 42.9→46.0（α=0.1、cap=100、seed 1,7。walks 100/500/1000/5000/10000） | `PC/figs/plot_challenge_memo_vs_lru.py`（内部で `core/virtual_server_sim_reuse_score.run_policy` を呼ぶ） | （推定）`python3 base/proposed_cache/figs/plot_challenge_memo_vs_lru.py --edges dataset/Louvain/graph/vldb.gr --graph-name VLDB --xaxis walks --values 100 500 1000 5000 10000 --alpha 0.1 --cap 100` | `PC/figs/notuse/challenge_walks_vldb.png`（CSV なし） | 🟡 注記 53.5/68.4/74.7/82.4/85.2、42.9/45.3/45.3/45.6/46.0 |
| Amazon memo 49.8→78.2 / LRU 40.9→45.0 | 同上 | （文書：README_SUMMARY.md:395）同上で `--edges dataset/Louvain/graph/amazon0601.gr --graph-name Amazon` | `PC/figs/notuse/challenge_walks_amazon.png` | 🟡 |
| 容量図（walks 10,000）：VLDB 85.2 vs 71.2、Amazon 78.2 vs 62.3 | 同上 | （推定）`--xaxis cap --values 50 100 200 300 500 1000 --alpha 0.1`（`--memo-walks` の既定は 100 1000 10000） | `PC/figs/notuse/challenge_cap_{vldb,amazon}.png` | memo は 🟡。LRU は注記がなく目視のみ |
| α=0.01：VLDB memo 41.5→65.7 / LRU 34.0→35.3、Amazon 36.6→67.2 / 30.4→29.0 | 同上 | （推定）上に `--alpha 0.01 --out figs/challenge_walks_{vldb,amazon}_a001.png` を付けたもの | `PC/figs/notuse/challenge_{walks,cap}_{vldb,amazon}_a001.png` | 🟡（図タイトルに α の表記なし） |
| 旧系列：VLDB α=0.01 memo 31.3→60.6、LRU≈25、差 最大35.4pt | ❓ | ❓ | 図のみ：`MR/graph_data/fig1_hitrate_vs_walks.png` | 図とは一致（31.3/40.9/60.6、LRU 26.0/25.4/25.2）。生成元は未特定。**引用しないこと** |
| 旧系列（参考）：walks=10000 で memo 57%、LRU 22% | `PC/dist/splits_reuse_score.sh`（SSH クラスタ） | 未特定 | `RS/alpha0.01_walks_10000_capa_100/all_vldb_nobt_results.csv`（memo 0.5719、LRU 0.2200）。walks=100 側は `RS/alpha0.01_walks_100_capa_100/all_vldb_nobt_results.csv`（0.2802 / 0.2271） | ✅（`MR/graph_data/figA_walks_vldb.png` と同じ系列で、31.3 系列とは別物） |
| 原因：node hop 5.3→3.9、node deg 8→41、edge hop 4.5→2.5、edge deg 46→34 | `PC/figs/plot_cause_structure.py` | （推定）`--edges dataset/Louvain/graph/vldb.gr --trace <下記トレース> --start 0 --graph-name VLDB --alpha-label 0.01` | トレース：`RS/alpha0.01_walks_10000_capa_100/vldb_nobt/none_100_h-0/start=0_walks=10000_alpha=0.01_seed=42_none_100_h-0_per_walk_access.json`。図：`PC/figs/notuse/cause_structure_vldb_a001.png` | ✅ 再計算値 5.31→3.93、7.7→41.1、4.52→2.47、45.6→34.3 |
| 次数分布の山 5–8 → 33–64 | `PC/figs/plot_cause_distribution.py` | （推定）同じトレースを入力 | `PC/figs/notuse/cause_dist_vldb_a001_deg.png` | 🟡 |
| 旧相関：次数 +0.457、hop −0.302 | ❓ | ❓ | 記載は `PC/docs/RESEARCH_STORY.md:71-72` のみ | ⚠️ 同じトレースから Spearman で再計算すると +0.478 / −0.311 |

### A-3. 実行時間・RTT 換算（§3.2、§7.5）

| 数値 | スクリプト | コマンド | 結果パス | 検証 |
|---|---|---|---|---|
| 遷移数 VLDB 9,216 / Amazon 9,138。ミス回数 VLDB none 9,216・LRU 5,042・提案 4,438・memo 2,319 | `PC/figs/collect_time_stats.py` | （推定：既定値で実行）`python3 base/proposed_cache/figs/collect_time_stats.py`（α=0.1、walks=1000、cap=100、seed 1 7 13 99 123） | `RS/time_stats_paperseed.csv` | ✅ 9,216.2 / 9,138.0、5,042.4 / 4,437.6 / 2,319.0 |
| 推定時間 VLDB 482/273/243/137 s、Amazon 478/275/243/184 s、−30 s（−11.1%）/ −32 s（−11.7%） | `PC/figs/plot_hitrate_and_time.py`（hops×2.27ms＋misses×RTT、`--charge all-miss`） | （履歴）`python3 base/proposed_cache/figs/plot_hitrate_and_time.py --rtt 50 --layout overlay`（→ `hitrate_with_time.png`）。パネル版は `--rtt 50`（推定） | `PC/figs/notuse/hitrate_and_time.png`、`hitrate_with_time.png` | ✅ 481.7/273.0/242.8/136.9、477.6/275.1/242.9/183.7、−30.2（−11.08%）/ −32.2（−11.71%） |
| 2.27 ms/遷移 | `plot_hitrate_and_time.py` にハードコード（`--hop-ms 2.27`）。算出は手計算 | — | `MP/base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100/vldb/none_100/start=*_global_transition.json`（(walk_time_total − auth_time_total) / 遷移数） | ⚠️ start0〜3 の比の平均は 2.2753（2.4394 / 2.2114 / 2.2402 / 2.2099）。start4 は遷移数0で除外。合算した比は 2.2838 |
| RTT 3パターン（10/60/200ms 系。E[RTT] 39/73/139ms） | `MP/base/rtt/plot_mixed_rtt.py`（18〜20行に根拠） | （履歴）`python3 base/rtt/plot_mixed_rtt.py --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 --out-dir base/rtt/output --mc 50` | `MP/base/rtt/output/mixed_rtt_summary.csv` | ✅（自前の実測ではなく、Azure の公表値を丸めた設定値） |
| memo 比：none ≈1.36〜1.40、LRU/ARC ≈1.06 | 同上 | 同上 | 同上 | ✅ 1.3608〜1.4025、1.0554〜1.0652 |

### A-4. IN研稿 表1・案A 確定値（§7.2、§7.3）

| 数値 | スクリプト | コマンド | 結果パス | 検証 |
|---|---|---|---|---|
| 表1 VLDB LRU 45.29 / 提案 51.85 / memo 74.84 | `PC/experiments/run_loo_ablation.py`（index 版） | （文書：RUNBOOK §6）`python3 base/proposed_cache/experiments/run_loo_ablation.py --pool split --seeds 1 7 13 99 123 --edges dataset/Louvain/graph/vldb.gr ...`（α0.1、walks1000、start0 は既定値） | `RS/loo_VLDB_paperseed_raw.csv` | ✅ |
| 表1 Amazon 44.33 / 51.38 / 64.33 | 同上 | 同上（amazon0601.gr） | `RS/loo_Amazon_paperseed_raw.csv` | ✅ |
| IN稿の memo 表記 74.7 / 64.0 | — | — | — | ⚠️ CSV は 74.84 / 64.33。74.7 は w スイープ図、または dist の seed42 単発の値と推測。64.0 は出典不明 |
| w≈100 で約52%（+7.0） | `PC/figs/plot_wsweep_dualpool.py`（index 版。γ=0、seed 1,7,13、w_edge=3w） | （推定）`--edges dataset/Louvain/graph/vldb.gr --graph-name VLDB --alpha 0.1 --walks 1000` | `PC/figs/notuse/wsweep_dualpool_vldb_a0.1.png` | 🟡 52.3% |
| θ=0 で 48.4% | — | — | `RS/loo_VLDB_paperseed_raw.csv` の「−Freq」行が 48.41 | 一致候補（γ=1・5 seed の条件で、図8 とは条件が違う） |
| **案A** VLDB 45.49 / 51.43 / 52.47 / 73.86、Amazon 45.05 / 52.36 / 52.10 / 65.04、差 +6.98/+7.04、+1.04/−0.26 | `PC/experiments/run_loo_ablation.py`（`--pool shared`） | （文書：RUNBOOK §6、README）`python3 base/proposed_cache/experiments/run_loo_ablation.py --pool shared --w-node 100 --alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 --edges dataset/Louvain/graph/{vldb,amazon0601}.gr` | `RS/loo_singlepool_{VLDB,Amazon}{,_raw}.csv`（9/8。seed 1,1002,2003,3004,4005） | ✅ 6.9826 / 7.0449、1.0425 / −0.2638 |

### A-5. LOO・Shapley・感度・始点依存（§8.1、§7.4）

| 数値 | スクリプト / コマンド | 結果パス | 検証 |
|---|---|---|---|
| 案A LOO：Base +11.70/+4.74、Close +4.24/+3.24、Freq +3.26/+3.03、Deg +0.25/+0.87、Deg の内訳 node +6.72 / edge −5.58 | A-4 の案A と同じ | `RS/loo_singlepool_*.csv` | ✅ |
| /size 補償なしで VLDB −2.38 | `PC/experiments/run_singlepool_selection.py`（index 版）。（推定）`--alpha 0.1 --walks 1000 --start-nodes 0 --disjoint --n-seeds 5 --out-csv results/singlepool_VLDB.csv` | `RS/singlepool_VLDB.csv`（plain w100 50.11 − shared-hop w100 52.50） | ✅（旧記載 −2.3） |
| エッジに次数を入れると −0.75 / −0.54 | 同上（Amazon は `--w-edge 500` 付きと推定） | `RS/singlepool_{VLDB,Amazon}.csv`（w100 の degstar − hop） | ⚠️ 旧記載 −0.68 / −1.02 は、どの w でも一致しない |
| 案A vs dual-pool +0.06 / −0.49 | 同上 | 同上 | ✅ +0.064 / −0.487 |
| dual LOO：Base +7.39/+4.21、Close +3.46/+2.04、Freq +3.41/+2.43、Deg −0.48/−0.03、Pool −0.08/+0.16 | HEAD 版 `run_loo_ablation.py`（a0526b7、8/29）。（文書：RUNBOOK §1） | `RS/loo_{vldb,amazon}_a0.1.csv`、`RS/loo_{VLDB,Amazon}_a0.1_raw.csv` | ✅ |
| Pool 旧値 +4.23 / +4.02（修正前の実装） | 修正前のコード（上書き済み） | `RS/loo_VLDB_denyOnly_raw.csv`（52.39 − 48.16）、`RS/loo_amazon_a0.1_disjoint.csv`（delta 4.0195） | ✅（旧実装の残骸） |
| 実分割で Pool −5.77、提案 vs LRU −0.29 | （文書：RUNBOOK §5）`... --partition-dir base/auth-many-server/data/splits/vldb/0.3` | `RS/loo_VLDB_deny.csv`、`loo_VLDB_deny_raw.csv`、`loo_VLDB_partOnly_raw.csv`（RUNBOOK にある `loo_VLDB_partdeny_raw.csv` は実在しない） | ✅ −5.7654、−0.2866 |
| dual-pool の提案−ARC +0.96±0.91 / −0.10±0.32 | dual LOO と同じ | `RS/loo_{vldb,amazon}_a0.1.csv` の ARC 行 | ✅ |
| Shapley：Close +3.38/+2.85、Freq +2.89/+3.08、Deg +0.17/+0.58、Pool +0.12/+0.54、合計 +6.56/+7.05。Deg 単独 +0.68/+0.91。LOO の単純合計 +6.32/+4.71 | `PC/experiments/run_shapley_decomposition.py`（index 版）。（文書：RUNBOOK §2）`--seeds 1 7 13 99 123 --out-csv results/shapley_X.csv --out-raw results/shapley_X_raw.csv` | `RS/shapley_{VLDB,Amazon}{,_raw}.csv` | ✅ 合計 6.5624 / 7.0519 |
| 感度：容量配分 7.7〜10.3、w 7.1、C_F 0.6〜1.0、動作点との差 +0.00〜+0.56、OAT の和 +1.07 | `PC/experiments/run_param_sensitivity.py`（index 版＝split）。（文書：RUNBOOK §4）`--start-nodes 0 --n-seeds 5 --out-csv results/sens_X.csv` | `RS/sens_{VLDB,Amazon}.csv`（8/29） | ✅ 7.70/10.29、7.07/7.13、0.62/1.03、最大 0.56、0.32+0.19+0.56 |
| 同時最良 +1.23/+0.72、γ0 版 +0.86/+0.36、再調整版の始点平均 −0.07/−0.32 | ❓ | ❓（探した場所：PC 全体、results、archive、zsh_history） | ❓ メモ上の値 |
| 始点依存：VLDB 4/5・Amazon 6/10、平均 +3.20/+2.56、対 ARC −0.05/−0.67 | `PC/experiments/run_startnode_sweep.py`（index 版）。（文書：RUNBOOK §3）`--starts 0 1 2 3 4`（Amazon は 0〜9）`--alpha 0.1 --walks 1000 --n-seeds 5` | `RS/startsweep_{VLDB,Amazon}.csv` | ✅ |
| LRU/memo と r=+0.909 | 上の CSV から算出 | 同上 | ✅ n=14（VLDB start4 を除く）。全15点では 0.496 |

### A-6. ARC 土台・旧値・dist・旧 reuse_score（§6.1、§10）

| 数値 | スクリプト / コマンド | 結果パス | 検証 |
|---|---|---|---|
| StructuralARC 対 ARC +4.2 / +1.8（walks=10000 で +4.5 / +2.3） | `PC/figs/plot_sweeps.py`（index 版、seed 1,7）。（文書：README_SUMMARY）`--xaxis walks --values 100 500 1000 3000 10000 --alpha 0.1 --prop-base arc` | `PC/figs/notuse/sweep_walks_{vldb,amazon}_arcbase.png`（CSV なし） | 🟡 |
| README_SUMMARY 5-1 の ARC 50.4 / 51.1、提案 55.4 / 53.7 | 同上 | `PC/docs/README_SUMMARY.md` 276〜278行 | ⚠️ ARC 50.4 は不一致（CSV は 49.92）。+4.2/+1.8 は件数ベースの ARC（≈51.2/51.9）との差 |
| dual 旧値：VLDB 51.9 vs ARC 49.9 vs LRU 45.3 | `PC/figs/plot_proposed_win.py`（index 版） | `RS/loo_VLDB_paperseed_raw.csv`、`PC/figs/notuse/proposed_win_vldb_a0.1.png` | ✅ |
| dual 旧値：Amazon 52.0 vs 51.1 vs 44.3 | 同上。（推定）`--cap-node 43 --cap-edge 57 --w-edge 500` | `PC/figs/notuse/proposed_win_amazon_a0.1.png` | 🟡 |
| 実分散と仮想が一致（VLDB Δ=0.00） | 実分散：（推定）`GRAPH=vldb WALKS=1000 ALPHA=0.1 bash base/proposed_cache/dist/run_local.sh`。照合：`dist/verify_vs_virtual.py ... --seed 42 --w 100 --summary ...` | `PC/dist/results/vldb_K2_a0.1_w1000_cap100/summary.tsv`（lru 45.36 / arc 49.97 / memo 74.70 / proposed 51.75） | 実分散側は ✅。仮想側の値は残っておらず Δ は ❓ |
| NO_BACKTRACK の違いで LRU 45.4% vs 21.1% | — | 45.4 は上の summary.tsv | 45.4 は ✅、21.1 は ❓ |
| 旧 reuse_score：walks=10000 で 18.8% vs LRU 22.0% | `PC/dist/splits_reuse_score.sh` と `sweeps/generate_results_csv.py`（SSH クラスタ） | `RS/alpha0.01_walks_10000_capa_100/{all_vldb_nobt_*.csv, vldb_nobt/}` | LRU 22.00 は ✅。18.8 は一致候補（t0/l0/b0/g0 の4始点の単純平均が 18.77）。ただし LRU は start0 だけの値で、条件が揃っていない |
| 旧 reuse_score：α=0.01 VLDB +1.0 | 同上（walks100） | `RS/alpha0.01_walks_100_capa_100/vldb_nobt/all_vldb_nobt_results.csv` | ✅ LRU 22.71、最良 23.67（148構成中の最大、seed42 単発） |

### A-7. アクセス要因の相関（§8.2、§6.4）

**共通のコマンド**
- 収集（RW 実行。cap サーバでも動き、標準ライブラリのみで実行できる）：
  ```bash
  STARTS=8 bash base/proposed_cache/experiments/run_access_factors_graphs.sh
  ```
  中身は `--walks 1000 --num-starts 8 --cap 100 --split 0.5 --largest-component`。グラフの定義は `experiments/graphs_access_factors.conf`。今のスクリプトの既定は STARTS=32 なので、再現には `STARTS=8` が必要。
- 統計（keys.csv から）：
  ```bash
  python3 base/proposed_cache/experiments/analyze_access_factors.py --from-keys RS/accessfactors_<g>_a<α>_w1000_s8_keys.csv --label <g> --alpha <α> --walks 1000 --num-starts 8 --cap 100 --bins 8 --hop-metric {bfs|walk}
  ```
  その後 `aggregate_access_factors.py` を実行する。歩数版で一括して作り直すときは `bash base/proposed_cache/experiments/analyze_fetched_keys.sh results`。
- meta.json は seed=42、start_seed=12345、largest_component=True。
- 結果：`RS/accessfactors_<g>_a{0.1,0.01}_w1000_s8_{keys,summary}.csv`、`*_meta.json`。図：`PC/figs/access_factors/`。まとめ：`PC/docs/ACCESS_FACTORS_RESULTS.md`。

| 数値 | 結果パス | 検証 |
|---|---|---|
| VLDB α=0.1：D −0.127 / H −0.767 / O +0.677 / R +0.476 | `RS/accessfactors_vldb_a0.1_w1000_s8_*` | ✅（H は BFS で再計算。summary の H は歩数版で −0.535） |
| Amazon α=0.1：−0.029 / −0.610 / +0.567 / +0.346 | 当時のファイルは上書き済み | ⚠️ 現在の値は −0.053 / −0.619 / +0.597 / +0.366 |
| α=0.01：H −0.46〜−0.50、O +0.31 | `RS/*_a0.01_*` | VLDB（−0.499 / +0.314）は ✅。Amazon は現在 −0.477 / +0.331 |
| 偏相関：H\|O −0.43〜−0.61、O\|R +0.29〜+0.57、R\|O +0.13〜+0.17、D\|H −0.06〜−0.22 | 同上 | VLDB は ✅。現在の Amazon 値を入れると −0.44〜−0.61、+0.30〜+0.57、+0.12〜+0.17、−0.07〜−0.22 |
| top-100 捕捉：O 86.5 / H 85.0 / oracle 90.4% | vldb_a0.1 | ✅ 86.45 / 85.04（BFS）/ 90.37 |
| Amazon D 7.6% vs random 12.4% | amazon_a0.1_summary.csv | ⚠️ 現在は 7.08 vs 12.57 |
| 歩数版 H：ρ が 0.09〜0.26 弱化、top100 が 1.5〜17pt 低下、vldb −0.535、er_mid −0.067 など | `RS/accessfactors_*_summary.csv` | ✅ |
| 6グラフの CV：0.32 / 0.96 / 1.32 / 1.51 / 3.43 / 3.50 | .gr からインラインの python で算出（スクリプトは保存されていない） | ✅ 再計算で一致 |
| ρ(D) node（α=0.01）：+0.32 / +0.49 / +0.61 / +0.65 | 各 a0.01_summary.csv | ✅ |
| 到達率 ≳15% vs ≤2.5% | summary の n_keys ÷ 全ノード数 | ✅ 15.0〜96.0% と 2.5% |
| enron の U 字 2.1→14.6 | `PC/figs/access_factors/01_per_factor/accessfactors_enron_a0.01_w1000_s8_univariate.png` | ✅ 実値 2.18→14.57 |
| enron top100：O 32.2 / H 21.2 / D 4.6% | enron_a0.01 | ✅（H は BFS） |
| interplay：edge I(D;Y) 0.027→0.096、II +0.069、歩数版 II +0.019、O-R II −0.138（37%）、enron II −0.113 | `PC/experiments/analyze_factor_interplay.py`。（推定）`WHAT=interplay bash base/proposed_cache/experiments/run_factor_analysis_all.sh`（既定 HOP=walk） | `RS/interplay_*.csv`（36本、すべて歩数版） | ⚠️ 現在の CSV とは不一致（例：0.030 / +0.036 / −0.145 / −0.128）。旧 Y ビン（6分位）＋BFS で再計算すると一致。今のコードでは再現できない |
| interplay の Shapley：edge D 11.9%、node D −0.048（歩数版 −0.102）、all O 43 / R 30 / H 23 / D 4%、enron D 32.7% | 同上に `--hop-metric bfs` | 同上 | ✅（BFS で再実行すると一致。歩数版 −0.1016 は CSV で ✅） |
| 式の形：積>和 24/24（+0.6〜+26.7）、交互作用 −10.7〜+2.5、R 抜き −0.7〜+1.1、D 抜き −0.8〜+2.4、vldb node O 単独 +12.3 | `PC/experiments/analyze_score_form.py`。（推定）`--keys <keys.csv> --kind {node,edge}`（既定 hop=walk、cap=100、seed=0）。12 keys × 2 kinds のループスクリプトは未保存 | `RS/scoreform_*_{node,edge}.csv`（24本） | ✅ |
| 次数の置き場所：O=1層 14〜47%、slashdot ρ +0.398・40.0%、enron +0.393・36.4%、低次数優先 1.9% | `PC/experiments/analyze_degree_placement.py`。（推定）`--keys <keys.csv> --kind {node,edge}`（既定 cf=5、cd=10、hop=walk） | `RS/degplace_*.csv`（24本） | ✅ |
| 恒久項 α=0.01 で −2.5〜−3.8 | 同上 | 同上 | ⚠️ slashdot −2.48、enron −3.72 のみ。他はほぼ0 |
| 減衰形 +0.3〜+3.3、α=0.1 で −0.4〜−0.1 | ❓ 実装なし | ❓ | ❓ メモ上の値 |

### A-8. RTT モデル（§12）

パスの起点：`MP/base/base-rtt/`

| 数値 | スクリプト | コマンド | 結果パス | 検証 |
|---|---|---|---|---|
| amazon RMSE≈3.5（start=1、T=100、walks=80） | `single-server/design_and_run.py`（`run.sh` から起動） | （推定：run.sh の固定値）`--graph dataset/Louvain/graph/com-amazon-connected.gr --start 1 --mu 50 --amp 40 --steps 100 --walks 80 --seed 1 --directions above below zigzag` | `results/design_run_amz.png` | 🟡 |
| multiserver：above ① 617,801 / ② 919,220 / ③ 785,600 | `multiserver/run_multi_local.sh` → `split_{remote_server,controller}_rtt.py`、集計は `append_summary.py` | （履歴）`[NO_AUTH_OVERRIDE=1] CACHE_POLICY_OVERRIDE={none\|lru} GRAPH_OVERRIDE=vldb ALPHA_OVERRIDE=0.05 RW_WALKS_OVERRIDE=100 START_NODES_OVERRIDE="0" zsh run_multi_local.sh`。場の生成は（推定）`paint_field.py --mu 250 --amp 240 --start 0 --converge-steps 40` | `multiserver/results/methods_summary.csv` の**末尾9行**（それより前は旧仕様）と `walks_vldb_walks100_alpha0.05_{above,below,zigzag}_*.jsonl` | ✅ |
| below 395,199 / 597,780 / 517,400、zigzag 536,410 / 797,968 / 684,558 | 同上 | 同上 | 同上 | ✅ |
| キャッシュによる認可 RTT の削減 40〜44% | 同 CSV の rtt_auth 列 | — | 同上 | ✅ above 44.3%、below 39.7%、zigzag 43.4% |
| fit λ≈0.99 vs 設計値 0.955、GDSF +2〜3%、memo_ratio 1.22 vs 1.25 | 旧 `fit_asymptotic.py`、`cache_replay.py`（現存せず、git にもない） | ❓ | ❓ | ❓（0.955 = 0.01^(1/100) は計算上正しい） |
| lag-1 自己相関 +0.25 / −0.41 | `MR/graph_data/SBM_rtt.py`（docstring に記載があるだけ） | ❓ | ❓ | ❓ |

### A-9. 比較実装・廃止案・METIS（§11、§13）

| 数値 | スクリプト / コマンド | 結果パス | 検証 |
|---|---|---|---|
| karate K=2 walks=30 で11方式が一致 | `MP/base/related_work/run_local.sh`、`compare.py`、`verify_summary.py`。（推定）`GRAPH=karate K=2 WALKS=30 ALPHA=0.1 CAP=15 START=0 SEED=42 NO_BACKTRACK=1 bash base/related_work/run_local.sh` | results/ は存在しない（git 未追跡） | ❓ |
| テスト15件 | `python3 -m unittest discover -s base/related_work/tests -v` | — | 件数は ✅（通るかは未確認） |
| BFS 先読みのヒット率 0.26〜0.31、先読み範囲への参照 3.5%、キャッシュ使用 39% | `base/auth-cache-bfs-degree/plot_bfs_hitrate_breakdown.py`、`plot_bfs_cache_usage.py`（git HEAD にのみ残る。引数なし） | 入力のログが現存しない | ❓ |
| 比較対象の LRU 0.45 | — | `MP/base/auth-baseline-cache/results/alpha0.01_walks_1000_capa_100/vldb/lru_100/vldb.log` | ✅ 21,927/48,709 = 0.450 |
| hub-pin +0.015 以内、LFU/TinyLFU +0.01〜0.025、Belady 0.87〜0.89 | `MP/base/not-use/auth-cache-hub-pin/simulate_offline.py`（標準出力のみ）。（推定）`--transition-dir base/auth-baseline-cache/results/alpha0.1_walks_1000_capa_100/{vldb,amazon0601}/lru_100 --capacity 100 --pin-k 50`。README のコマンドは `--graph` がありエラーになる | 保存ファイルなし（出典は README の表） | README の表とは整合（Belady 0.872 / 0.892） |
| METIS のエッジ端点同居 55/77 → 77/77（karate K=3） | `bash base/new-many-server/partition.sh --graph karate --server-count 3`。修正箇所は `split_auth_tables.py:264`、`only_split_auth_tables.py:275` | `MP/base/new-many-server/data/metis/karate/bipartite.metis.part.3` | 77/77 は ✅。55 は末尾エッジの扱いで 55 か 56 になる |

### A-10. 前史（§2）

| 数値 | 出典 / データ | コマンド | 検証 |
|---|---|---|---|
| 理想約0.5秒、RTT 200ms で約42秒、ロス0.1%（RTT 100ms 固定）で約160秒 | `MR/resources/概要.pdf` p3。生データは `MR/output/KnightKing_5000000_RTT_{0,200}_loss_0.txt`、`KnightKing_5000000_RTT_100_loss_0.1.txt` | KnightKing の実行コマンドと、RTT・ロスの注入方法（tc netem 等）は ❓。描画は `MR/output/src/Knight_loss.py` など | ✅ 0.5615 / 42.54 / 160.29（各 n=5） |
| 約60msで逆転 | 概要.pdf p7。`KnightKing_5000000_RTT_*_loss_0.txt` と `basic_5000000_RTT_*_loss_0_time.txt` | 描画は `MR/output/src/Knight_basic_RTT.py`。basic は `start.cpp` が stdin で回数・ファイル名・RW 回数を受け取り、`../output/<名前>_time.txt` に追記する。ワーカーは `./a.out <グラフディレクトリ>` | ✅ 40ms では Kn 7.97 < basic 10.95、60ms では 11.67 > 11.27（補間すると約57.6ms） |
| KnightKing 0.56/11.67/20.95/42.54、basic 10.29/11.27/12.32/14.02 | 同上 | 同上 | ✅ |
| port 数 4 で 18.6秒、14 で 12.3秒 | `MR/output/hard_basic_5000000_port_{4,14}_time.txt`（n=10） | ポート数はコンパイル時定数 `SEND_RECV_PORT` を書き換えて再ビルド（推定）。描画は `port_time.py` | ✅ 18.62 / 12.29 |
| cache 26.1秒 vs normal 784.5秒 | `MR/output/cache_gen1000_time.txt`（n=5）、`normal_gen1000_time.txt`（**n=1**） | src10/11 期と推定。描画は `cache_time.py` | ✅ |
| output2：Kn 85.8、AR_0 52.8、AR_2000 38.6、AR_6000 12.0（LiveJournal、5台、1頂点10 RWer、RTT 100、ロス 0.03%） | `MR/output2/data/{Kn,AR_0,AR_2000,AR_6000}_LiveJournal_10_RTT_100_loss_0.03.txt`（n=5） | `src12/start.cpp`（stdin で分割数・回数・名前・RW 回数・待機時間を受け取る）。ファイルは後から手作業で改名したと推定。描画は `MR/output2/src/AR_cache.py` など | ✅ 85.769 / 52.793 / 38.593 / 11.984 |
| ロス0.1%：Kn 181.2 vs AR_0 52.2。RTT を振ると AR_0 は 48.8〜55.2、Kn は 3.6〜150.7 | `..._RTT_{0,50,100,150,200}_loss_{0.03,0.1}.txt` | 描画は `Kn_vs_AR_RTT.py`、`Kn_vs_AR_loss.py` | ✅ |
| SBM_3：Kn 97.6 → AR_400 19.8 | `Kn_SBM_3_1000_RTT_100_loss_0.03.txt`、`AR_400_SBM_3_1000_...`（n=3） | — | ✅（比較は KnightKing と AR_400。AR_0→AR_400 なら 57.2→19.8） |
| tecrep：キュー長 7.5 / 95.1、差分 5秒・313万回 / 20秒 / 1秒・12万回、表1 2.0〜14.6 vs 13.6〜26.1 | `MR/resources/tecrep.pdf` p5〜6、`20220805_IN研.pdf` p8〜10 | 当時のコードは git 383c14e（2022-07-24）の `src/random_walker_queue.hpp:26-28`（minth=5、maxth=100、maxp=0.1）、`src/random_walk.hpp:11`（α=0.2）。HEAD 版は値が変わっているので引用しないこと | PDF の記載のみ（生データは ❓） |
| src12 の定数 | `MR/src12/param.hpp:39`（MAX_CACHE_SIZE=50,000,000）、`:55`（VERTEX_SIZE=5,000,000） | — | ✅ |
| LiveJournal 3,997,962 / 34,681,189 | `MR/graph_data/README.md`（SNAP のヘッダを写したもの） | 実ファイルはない | ❓ |

### A-11. グラフ規模（§7.1）

| グラフ | ファイル | 数え方 | 結果 |
|---|---|---|---|
| VLDB | `MP/dataset/Louvain/graph/vldb.gr` | `wc -l`（ヘッダなし）。異なるノード ID の数。自己ループ・重複・逆向きの重複を検査 | 1,049,866 行、317,080 ノード（ID は 0〜425,956 で飛び番あり）。自己ループ・重複はいずれも0 ✅ |
| Amazon0601 | `MP/dataset/Louvain/graph/amazon0601.gr` | 同上 | 3,387,388 行（有向）、403,394 ノード。逆向きの辺を持つ有向辺が 1,887,960 本、無向として一意にすると 2,443,408 ✅ |
| 旧ドラフト・IN稿の「VLDB 約280万ノード・約1,350万エッジ」 | — | — | ⚠️ 実ファイルと一致しない |

### A-12. 旧ドラフト（§5.3、§6.1）

| 数値 | 記載箇所 | 検証 |
|---|---|---|
| 約47% / 53% | v1 23行、v2 23行、v3 15行、v4 27・67行、v5/v6 21・75行 | ❓ 元データは M_Research にも master-progrem の調査範囲にもない |
| 最大約160秒 | v1 37行（RTT 100ms）、v4 111行・v5 138行・v6 154行（50ms） | ❓（output/ の KnightKing RTT100・ロス0.1% の 160.29秒は別の実験で、一致は偶然と推測） |

### A-13. 追跡できなかった数値の扱い
- ❓ の数値は、論文・発表資料には**引用しないこと**。使うなら再実行し、結果を CSV で保存してからにする。
- 実際に打ったコマンドは、master-progrem 側の Claude Code セッション記録（`~/.claude/projects/` 配下）に残っている可能性がある。今回は調査していない。


---

## 付録B：参考文献
1. L. Page, S. Brin, R. Motwani, T. Winograd, "The PageRank Citation Ranking: Bringing Order to the Web," Stanford InfoLab, 1998.
2. K. Yang et al., "KnightKing: A Fast Distributed Graph Random Walk Engine," ACM SOSP, 2019.
3. R. L. Mattson et al., "Evaluation Techniques for Storage Hierarchies," IBM Systems Journal 9(2), 1970.
4. N. Megiddo, D. S. Modha, "ARC: A Self-Tuning, Low Overhead Replacement Cache," USENIX FAST, 2003.
5. R. S. Sandhu et al., "Role-Based Access Control Models," IEEE Computer 29(2), 1996.
6. A. Mohamed et al., "Extended Authorization Policy for Graph-Structured Data," SN Computer Science 2(5), 2021.
7. P. Faldu, J. Diamond, B. Grot, "Domain-Specialized Cache Management for Graph Analytics," IEEE HPCA, 2020.
8. J. Chen, X. Qian, "Kudu: An Efficient and Scalable Distributed Graph Pattern Mining Engine," arXiv:2105.03789, 2021.
9. J. Leskovec, L. A. Adamic, B. A. Huberman, "The Dynamics of Viral Marketing," ACM TWEB 1(1), 2007.（Amazon0601）
10. P. Fang et al., "Distributed Graph Embedding with Information-Oriented Random Walks," PVLDB 16(7), 2023.
11. H. Aksu, M. Canim, Y.-C. Chang, I. Korpeoglu, Ö. Ulusoy, "Graph Aware Caching Policy For Distributed Graph Stores," IEEE IC2E, pp.6–15, 2015.
12. SOOP, JCST, 2021.／PaGraph, GNNSys, 2021.／LAC, Korkmaz et al., PVLDB 18.／GraSorw, Shao et al., PVLDB 15.
13. R. Nishtala et al., "Scaling Memcache at Facebook," NSDI, 2013.／N. Bronson et al., "TAO," USENIX ATC, 2013.／G. Malewicz et al., "Pregel," SIGMOD, 2010.／J. Mondal, A. Deshpande, SIGMOD, 2012.
14. V. D. Blondel et al., "Fast unfolding of communities in large networks," J. Stat. Mech. P10008, 2008.（Louvain）
15. 滝沢 駿, 金子 晋丈, 「Random Walker 単位の非同期処理における Random Early Detection を用いた自律的輻輳制御機構」, 信学技報（IN研）, 2022.
