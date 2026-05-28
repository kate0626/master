# 関連研究サーベイ — 動的グラフ認可

> このページは Notion にそのまま貼り付けて使えるフォーマットで書いてあります。
> 各論文の `Status` / `Relevance` プロパティを Notion 側で設定すると DB として運用できます。

---

## 📚 論文DBテンプレート（プロパティ）

| プロパティ名 | 型 | 値の例 |
|---|---|---|
| Title | Title | 論文タイトル |
| Authors | Text | 著者 |
| Year | Number | 2019 |
| Venue | Select | USENIX ATC / VLDB / SIGMOD / arXiv 等 |
| Category | Multi-select | Authorization / Dynamic Graph / Cache / IVM / RAG |
| Relevance | Select | Core / Background / Eval Baseline |
| Status | Select | Not Started / Reading / Read / Cited |
| 修論章 | Multi-select | Ch1 / Ch2 / Ch4 / Ch6 |
| URL | URL | リンク |

---

## 🔵 Core（直接的に研究の柱になる論文）

### 1. Zanzibar: Google's Consistent, Global Authorization System

- **著者**: Pang et al., Google
- **年**: 2019
- **場所**: USENIX ATC '19
- **URL**: https://www.usenix.org/system/files/atc19-pang.pdf
- **カテゴリ**: Authorization / Consistency
- **修論章**: Ch1（②既存手法）, Ch2（関連研究）

**概要**:
Googleの全社認可システム。ReBAC（Relationship-based Access Control）を採用し、グラフ構造でACLを表現。**Zookie** という不透明な consistency token によって、クエリ実行時に「コンテンツのバージョンと少なくとも同程度に新しい ACL スナップショット」を強制する。トリリオン規模のACLで p95 < 10ms、可用性 99.999% を達成。

**本研究との関係**:
- ✅ 認可をグラフとして扱う先行例
- ✅ Consistency token のアイデアは本研究の `acl_version` の直接の祖先
- ❌ **エッジ単位 ACL は持たない**（ノード関係しか持たない）
- ❌ 探索キャッシュの局所性については論じられていない

**修論での使い方**: Ch1 ②で「Zanzibarでも参照エッジ自体への独立ACLは扱えない」と切る根拠。Ch2で consistency model の比較対象。

---

### 2. Temporal Graph Networks for Deep Learning on Dynamic Graphs (TGN)

- **著者**: Rossi, Chamberlain, Frasca, Eynard, Monti, Bronstein
- **年**: 2020
- **場所**: ICML Workshop / arXiv:2006.10637
- **URL**: https://arxiv.org/abs/2006.10637
- **カテゴリ**: Dynamic Graph
- **修論章**: Ch2

**概要**:
連続時刻動的グラフ（CTDG）を **時刻付きイベント列** として表現。**Memory module** で各ノードの過去履歴を圧縮表現し、graph-based operator と組み合わせる。多くの既存手法（JODIE, TGAT, DyRep 等）が TGN の特殊ケースとして表現できる統一フレームワーク。

**本研究との関係**:
- ✅ 「動的グラフをイベント列として扱う」考え方
- ❌ GNN なので**学習が前提**。本研究は学習ではなく**認可決定の正確性**が目的
- ✅ ただし、failure case（メモリの古さによる予測劣化）は本研究の「キャッシュ古さによる漏洩」と類似構造

**修論での使い方**: Ch2 で「動的グラフ研究の主流は学習タスクだが、本研究は**確定的な認可決定**を扱う点が異なる」とポジショニング。

---

### 3. Graph Structured Views and Their Incremental Maintenance

- **著者**: Zhuge, Garcia-Molina
- **年**: 1998（Stanford TR）
- **場所**: ICDE
- **URL**: https://www.semanticscholar.org/paper/Graph-structured-views-and-their-incremental-Zhuge-Garcia-Molina/
- **カテゴリ**: IVM / Graph DB
- **修論章**: Ch2, Ch4

**概要**:
グラフ構造ビューの形式定義と差分更新アルゴリズム。ベースグラフが変化したときに、materialized view を最小限の作業で同期する手法を提案。

**本研究との関係**:
- ✅ キャッシュ = materialized view と捉えると、本研究の失効伝播は IVM の特殊例
- ✅ 差分更新の局所性議論が直接参考になる
- ❌ 認可制約は考慮していない

**修論での使い方**: Ch4 で「失効伝播はグラフビューの IVM 問題として定式化できる」と位置づけ、自分の手法を IVM の文脈で正当化する。

---

### 4. MV4PG: Materialized Views for Property Graphs

- **著者**: 2024年の最近論文
- **年**: 2024
- **場所**: arXiv:2411.18847
- **URL**: https://arxiv.org/pdf/2411.18847
- **カテゴリ**: IVM / Graph DB
- **修論章**: Ch2, Ch6

**概要**:
プロパティグラフ上のmaterialized view（作成・維持・最適化）を提案。**可変長エッジを含むview**の効率的な維持手法を含む。

**本研究との関係**:
- ✅ 最新の IVM 手法で、本研究の評価ベースラインに使える
- ✅ プロパティグラフ＝認可情報を含むエッジに自然に拡張できる
- ❌ 認可は扱っていない

**修論での使い方**: Ch6 評価で「IVM手法を認可文脈に適用したベースライン」として比較。

---

### 5. Partial Update: Efficient Materialized View Maintenance in a Distributed Graph Database

- **著者**: Cho, Averbukh et al., LinkedIn
- **年**: 2018
- **場所**: SIGMOD
- **URL**: https://www.researchgate.net/publication/324538688
- **カテゴリ**: IVM / Distributed Graph DB
- **修論章**: Ch4, Ch6

**概要**:
LinkedInの分散グラフDB（LIquid）における部分更新による IVM。削除なしの設定で eventual consistency を保証し、本番で **保守時間とネットワーク帯域を50%削減**。

**本研究との関係**:
- ✅ 既存実装が**分散シャード型**なので直接的に近い設計
- ❌ Eventual consistency なので本研究（Strong safety）とは異なる
- ✅ 比較対象として「Strong safety だとどれだけコストが増えるか」を示せる

**修論での使い方**: Ch6 で性能ベースライン。Ch1 で「既存IVMは Eventual だが本研究は Strong」と差別化。

---

## 🟢 Background（背景・対比に使う論文）

### 6. TAO: Facebook's Distributed Data Store for the Social Graph

- **年**: 2013
- **場所**: USENIX ATC
- **カテゴリ**: Distributed Graph / Cache
- **URL**: 公開資料多数

**概要**:
Meta の社会グラフを支える分散キャッシュ。**invalidation message を replication stream に乗せる**ことで一貫性を担保。

**本研究との関係**:
- ✅ 大規模グラフの分散キャッシュ + 失効通知の実例
- ✅ 既存実装の controller / shard アーキとよく似ている
- ❌ 認可ではなく一般データ
- ❌ Consistency は best-effort

**修論での使い方**: Ch2 で「大規模分散グラフキャッシュの実用例」として参照。

---

### 7. Secure Retrieval-Augmented Generation: Preventing Data Leakage with Provenance and Policy Enforcement

- **年**: 2024 or 2025
- **場所**: Computer Fraud and Security
- **URL**: https://computerfraudsecurity.com/index.php/journal/article/view/976
- **カテゴリ**: RAG / Authorization
- **修論章**: Ch1

**概要**:
RAG における provenance（出自）追跡と policy enforcement を組み合わせて漏洩を防ぐアプローチ。

**本研究との関係**:
- ✅ ①の恐怖（RAG漏洩）の最近の議論として引用可
- ❌ ノード単位の制御に留まりエッジ単位 ACL は扱わない

**修論での使い方**: Ch1 ①の動機付け。「最近の研究でもエッジ認可は未解決」と示す。

---

### 8. Privacy-Aware RAG: Secure and Isolated Knowledge Retrieval

- **年**: 2025
- **場所**: arXiv:2503.15548
- **URL**: https://arxiv.org/pdf/2503.15548
- **カテゴリ**: RAG / Authorization
- **修論章**: Ch1, Ch2

**概要**:
RAG における プライバシー対応の知識検索分離。

**本研究との関係**:
- ✅ RAG 認可の最新文脈
- ✅ ①の問題設定の裏付け

**修論での使い方**: Ch1 で最新の動機付け論文として引用。

---

### 9. Enterprise AI Must Enforce Participant-Aware Access Control

- **年**: 2025
- **場所**: arXiv:2509.14608
- **URL**: https://arxiv.org/pdf/2509.14608
- **カテゴリ**: RAG / Authorization
- **修論章**: Ch1

**概要**:
エンタープライズAIにおける participant-aware なアクセス制御の必要性を主張。

**本研究との関係**:
- ✅ 「動的にアクセス制御を効かせる必要性」の社会的根拠
- ❌ 具体的なメカニズムは抽象

**修論での使い方**: Ch1 で「業界的にも必要性が認知されている」と示す。

---

### 10. A Simple and Practical Concurrent Non-blocking Unbounded Graph with Reachability Queries

- **年**: 2018
- **場所**: arXiv:1809.00896
- **カテゴリ**: Concurrent Graph / Reachability
- **URL**: https://arxiv.org/pdf/1809.00896
- **修論章**: Ch4

**概要**:
ロックフリーな並行動的グラフ実装。ノード/エッジの追加削除は lock-free、lookup は wait-free、reachability query は obstruction-free。

**本研究との関係**:
- ✅ 本研究のMVCC設計の代替アプローチ。比較に使える
- ✅ Reachability query をどう並行制御するかの実装ヒント
- ❌ 認可制約はない

**修論での使い方**: Ch4 で「並行制御の選択肢として lock-free か MVCC か」を議論する際に引用。

---

### 11. Incremental View Maintenance for Property Graph Queries

- **年**: 2018
- **場所**: GRADES-NDA / ResearchGate
- **URL**: https://www.researchgate.net/publication/325374100
- **カテゴリ**: IVM / Property Graph
- **修論章**: Ch2

**概要**:
プロパティグラフクエリに対する IVM。

**本研究との関係**:
- ✅ Property graph 上の IVM の標準的議論
- ❌ ACL文脈なし

---

## 🟡 Eval Baseline（評価で比較対象に使える論文・システム）

### 12. StreamTGN: A GPU-Efficient Serving System for Streaming Temporal Graph Neural Networks

- **年**: 2026（arXiv:2603.21090）
- **URL**: https://arxiv.org/abs/2603.21090
- **カテゴリ**: Streaming Graph / System
- **修論章**: Ch6

**概要**:
ストリーミング動的グラフのGPU効率サービングシステム。**動的グラフ更新の局所性**を活用して推論効率を改善。

**本研究との関係**:
- ✅ 「動的グラフ更新の局所性活用」という核アイデアが共通
- ✅ システム設計の参考になる
- ❌ GNN推論なので評価指標は異なる

**修論での使い方**: Ch6 で「局所性活用の他例」として参照。直接の比較は難しいがアーキ設計の参考。

---

### 13. Access Control Verification in Knowledge Graphs by Utilizing Dynamic Node-based Access Control Caches

- **形式**: USPTO 特許
- **URL**: https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12373588
- **カテゴリ**: Authorization / Graph Cache
- **修論章**: Ch2

**概要**:
ナレッジグラフでアクセス制御検証時に **動的なノードベースACLキャッシュ** を使う手法。

**本研究との関係**:
- ⚠️ **既存特許で最も近い**。ノードベースキャッシュ
- 差別化ポイント: 本研究は**エッジベース** + **BFS/Degree局所性活用**

**修論での使い方**: Ch2 で「最も近い既存技術」として明示的に位置づけ、差別化を強調する。

---

## 🗂️ カテゴリ別マトリクス

| 観点 | Zanzibar | TAO | TGN | MV4PG | Partial Update | 本研究 |
|---|---|---|---|---|---|---|
| 認可対応 | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| エッジ単位ACL | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| 動的更新 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Strong consistency | ✅ | ❌ | - | ❌ | ❌ | ✅ |
| グラフ局所性活用 | ❌ | 部分 | ✅ | ✅ | ✅ | ✅ |
| 分散シャード | ✅ | ✅ | ❌ | - | ✅ | ✅ |
| 二部グラフ特化 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

→ 本研究のオリジナリティは右端の列。**全ての ✅ を同時に満たすのは本研究のみ**として論文で主張できる。

---

## 📝 読書計画（優先順）

1. **Week 1**: Zanzibar 精読 → §2.2 Zookie の詳細を理解、本研究の `acl_version` と対比メモを作る
2. **Week 1**: Partial Update 精読 → 失効プロトコルの具体実装を比較
3. **Week 2**: TGN ざっと → 「学習 vs 認可」の境界線を明確化
4. **Week 2**: MV4PG → IVM 手法の最新ベースライン
5. **Week 3**: 残り（背景・特許）

---

## 🔍 さらに探したい論文（TODO）

- [ ] **Streaming graph systems**: GraphOne (FAST'19), Aspen (PLDI'19), Differential Dataflow
- [ ] **Bipartite graph algorithms**: 動的二部マッチング、二部到達可能性
- [ ] **Authorization linearizability**: Caelus, OPAL 等のポリシー一貫性
- [ ] **Time-versioned graphs**: TGraph, T-GQL 等のクエリ言語

---

## Sources（一次情報）

- [Zanzibar (USENIX ATC '19)](https://www.usenix.org/system/files/atc19-pang.pdf)
- [TGN paper (arXiv:2006.10637)](https://arxiv.org/abs/2006.10637)
- [MV4PG (arXiv:2411.18847)](https://arxiv.org/pdf/2411.18847)
- [Partial Update at LinkedIn](https://www.researchgate.net/publication/324538688)
- [Concurrent Graph Reachability (arXiv:1809.00896)](https://arxiv.org/pdf/1809.00896)
- [Privacy-Aware RAG (arXiv:2503.15548)](https://arxiv.org/pdf/2503.15548)
- [Participant-Aware Access Control (arXiv:2509.14608)](https://arxiv.org/pdf/2509.14608)
- [Secure RAG with Provenance](https://computerfraudsecurity.com/index.php/journal/article/view/976)
- [StreamTGN (arXiv:2603.21090)](https://arxiv.org/abs/2603.21090)
- [TAO at Facebook](https://engineeringatscale.substack.com/p/tao-metas-scalable-architecture-powering)
- [Knowledge Graph ACL Cache (USPTO)](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12373588)
