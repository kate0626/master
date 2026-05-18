# auth-cache-bfs-degree 結果考察

## 目的

`auth-cache-bfs-degree` の結果を見ると、`lru` を改良したはずの `bfs-lru`、`degree-lru`、`hybrid-lru` が、期待したほど時間短縮できていない。特に `degree-lru` と `hybrid-lru` は、`lru` より認可時間が長くなっている。ここでは、`baseline-cache` と `auth-cache-bfs-degree` の結果を参照しながら、その理由を整理する。

ただし重要なのは、`amazon0601` と `amazon0601-6`、`vldb` と `vldb-6` は同じものではなく、発展手法側の実験環境が異なる点である。そのため、

- `none` / `memo` の baseline 値は共通の参考値として扱う
- `bfs-lru` / `degree-lru` / `hybrid-lru` は、それぞれの実験環境の結果として分けて扱う

という整理で分析する必要がある。

比較対象として主に以下を参照した。

- `base/auth-cache-bfs-degree/results/alpha0.01_walks100_capa100/*/all_policies_summary.log`
- `base/auth-baseline-cache/results/alpha0.01_walks_1000_capa_100/*/all_policies_summary.log`
- `base/auth-baseline-cache/results/.../<policy>_100/*.log` の `Auth cache hit/miss`


## 1. 結果の整理

### amazon0601

baseline 側の共通参考値:

- `none`: 24.357s
- `memo`: 11.607s
- `lru`: 13.684s
- `memo hit_rate`: 0.527
- `lru hit_rate`: 0.440

`auth-cache-bfs-degree` 側で確認できる値:

- `bfs-lru`: 13.500s
- `hybrid-lru`: 16.518s

この節では、baseline の `none` / `memo` / `lru` を参考にしつつ、`amazon0601` 環境で実行された `bfs-lru` と `hybrid-lru` を評価する。少なくとも `bfs-lru` は `baseline lru` とほぼ同等、`hybrid-lru` は悪化している。


### amazon0601-6

認可時間 `authorization_time_sum`:

- `none`: 24.115s
- `lru`: 13.492s
- `bfs-lru`: 13.551s
- `degree-lru`: 16.542s
- `hybrid-lru`: 16.366s
- `baseline memo`: 11.607s

ヒット率:

- `lru`: 0.440
- `bfs-lru`: 0.440
- `degree-lru`: 0.350
- `hybrid-lru`: 0.331
- `baseline memo`: 0.527

ここでの `none` / `lru` は `amazon0601-6` 実験環境の結果であり、`memo` のみ baseline の共通参考値である。この条件では `bfs-lru` は `lru` とほぼ同じで、`degree-lru` と `hybrid-lru` は `lru` より悪化している。


### vldb

baseline 側の共通参考値:

- `none`: 69.146s
- `memo`: 31.926s
- `lru`: 37.525s
- `memo hit_rate`: 0.534
- `lru hit_rate`: 0.451

`auth-cache-bfs-degree` 側で確認できる値:

- `degree-lru`: 54.704s

`vldb` 環境で確認できる範囲では、`degree-lru` は `baseline lru` よりかなり遅い。したがって、少なくとも次数ベースの保護方針はこの環境では有効に働いていない。


### vldb-6

認可時間:

- `baseline none`: 69.146s
- `baseline lru`: 37.525s
- `bfs-lru`: 37.654s
- `hybrid-lru`: 54.593s
- `baseline memo`: 31.926s

ヒット率:

- `baseline lru`: 0.451
- `bfs-lru`: 0.451
- `hybrid-lru`: 0.205
- `baseline memo`: 0.534

ここでの `none` / `memo` / `lru` は baseline 共通参考値であり、発展手法側は `vldb-6` 環境の結果である。この条件では `bfs-lru` は `baseline lru` とほぼ同じだが、`hybrid-lru` は大きく悪化している。


### karate

認可時間:

- `none`: 99.928s
- `lru`: 11.196s
- `bfs-lru`: 11.135s
- `degree-lru`: 16.587s
- `hybrid-lru`: 19.217s

ヒット率:

- `lru`: 0.895
- `bfs-lru`: 0.895
- `degree-lru`: 0.843
- `hybrid-lru`: 0.818

`karate` では全体に局所性が強いためどの方式も効きやすいが、それでも `degree-lru` と `hybrid-lru` は `lru` に勝てていない。


## 2. なぜ時間が減っていないのか

### 2.1 `bfs-lru` は実質的に `lru` とほぼ同じ挙動になっている

`bfs-lru` は、walk 開始時だけ BFS によるプリフェッチを行い、そのときだけ正しい BFS 距離を使ってキャッシュ投入する。一方、通常のキャッシュミス後の投入では `bfs_dist=0` として `near` 側に入れている。

実装上も以下のようになっている。

- `BFSDistanceLRUCache.__setitem__()` は reactive cache-in を `bfs_dist=0` で処理
- `prefetch_bfs_neighbors()` は walk 開始時だけ BFS 深さ分を先読み
- `_authorize_candidate()` では `dist_map` にないものを `0` として投入

つまり、BFS 距離で差別化されるのはプリフェッチされた一部のエントリだけであり、実際の認可問い合わせの大半は `near` として扱われる。結果として eviction の優先順位は LRU と大差なくなり、ヒット率も `lru` と同じ水準に留まる。

そのため、`bfs-lru` は「悪化はしないが、大きくも改善しない」という結果になったと考えられる。


### 2.2 `degree-lru` は保護対象の選び方が workload と合っていない

`degree-lru` は、高次数ノードや hub-hub エッジを優先的に残し、low-low エッジを先に追い出す設計になっている。しかし、この実験のキャッシュキーは単なるノードやエッジではなく、`(start_node, entity)` である。

ここで重要なのは、

- 「グラフ全体で次数が高いか」

ではなく、

- 「ある始点に対して、その認可結果が再び参照されるか」

である。

高次数ノードは探索の中で現れやすい可能性はあるが、同じ始点から何度も同じ認可判定が再利用されるとは限らない。逆に、低次数でも始点近傍の局所構造に含まれるエンティティは何度も再利用されうる。

それにもかかわらず `degree-lru` はグローバルな次数だけで保護対象を決めるため、

- 実際には再利用されないハブ系エンティティを残す
- 実際には再利用される低次数エンティティを先に追い出す

というミスマッチが起こる。

この結果、少なくとも `amazon0601-6` と `vldb` の結果では `lru` よりヒット率が大きく低下し、認可時間も悪化したと考えられる。


### 2.3 `hybrid-lru` は BFS と degree の弱点を同時に持っている

`hybrid-lru` は BFS の near/far と degree Tier を組み合わせ、10 段階の slot に分けて管理している。設計意図としては細かく優先順位を制御したいのだが、容量 100 の小さなキャッシュではこれが逆効果になりやすい。

理由は以下の通り。

- `bfs-lru` と同様、正確な BFS 距離を持つのはプリフェッチ分が中心
- `degree-lru` と同様、次数ベースの保護方針が再利用性と一致しない
- さらに 10 スロットに分割することで、LRU の単純な適応性が弱まる

LRU は「最近使われたものを残す」という単純な戦略だが、この workload ではそれがかなり強い基準になっている。`hybrid-lru` はその単純さを崩した一方で、BFS 距離や次数から十分に強い予測利益を得られていない。そのため、追加した複雑さに見合う改善が起きず、むしろ悪化したと考えられる。


### 2.4 追加オーバーヘッドに見合う miss 削減が得られていない

`bfs-lru` では walk 開始時に毎回 BFS プリフェッチが走る。`degree-lru` と `hybrid-lru` では、各 cache-in のたびにノード次数や Tier/slot の計算が必要になる。

これらの追加処理は、ヒット率が十分に上がるなら回収できる。しかし実際には、

- `bfs-lru`: ヒット率は `lru` と同等
- `degree-lru`: ヒット率が低下
- `hybrid-lru`: さらに低下

という結果なので、追加した計算コストを取り返せていない。したがって「工夫したのに速くならない」のではなく、「工夫により増えたオーバーヘッドを、キャッシュ効率の改善で相殺できていない」と整理するのが正確である。


## 3. baseline memo と比較すると何が言えるか

`baseline memo` は容量制限付き LRU ではなく、実質的に無制限に近いキャッシュなので、eviction ミスが起きにくい。そのため、

- `amazon0601`: `memo auth=11.607s`, `hit_rate=0.527`
- `vldb`: `memo auth=31.926s`, `hit_rate=0.534`

となり、容量 100 の `lru` 系より有利である。

ただし、この `memo` は baseline の共通参考値であり、`amazon0601-6` や `vldb-6` の発展実験と完全に同一条件ではない。そのため、`memo` との差は厳密な優劣比較というより、「容量制約がない場合にはこれだけ再利用できる」という上限の目安として読むのが適切である。

したがって、今回の比較で最も重要なのは「memo に勝てなかった」ことではなく、

- 容量制約付きの条件下で
- `lru` より賢くしたはずの方式が
- 実際には再利用性をうまく捉えられなかった

という点である。


## 4. まとめ

今回の結果から言えることは次の通りである。

1. `amazon0601` と `amazon0601-6`、`vldb` と `vldb-6` は別実験環境なので、発展手法の値は分けて扱う必要がある。
2. `bfs-lru` は、BFS 距離を使う範囲がプリフェッチ分に限られており、確認できる範囲では `lru` とほぼ同じ挙動になった。
3. `degree-lru` は、グローバルな次数に基づいて保護対象を決めているが、この workload で重要なのは `(start_node, entity)` 単位の再利用性であり、評価軸がずれていた。
4. `hybrid-lru` は BFS と degree の両方の複雑さを持ち込んだが、その複雑さに見合うヒット率改善が得られず、むしろ悪化した。
5. したがって、時間が減らない主因は「工夫のオーバーヘッド」そのものよりも、「工夫した保護方針が実際の再利用パターンと噛み合わず、miss を十分減らせなかったこと」にある。


## 5. レポート用の短い記述例

本実験では、`amazon0601` と `amazon0601-6`、`vldb` と `vldb-6` を同一条件としては扱わず、baseline の `none` / `memo` を共通参考値、発展手法を各実験環境の結果として分けて解釈した。そのうえで、認可結果の再利用性は BFS 距離やノード次数そのものよりも、始点ごとの局所的な再訪パターンに強く支配されていたと考えられる。そのため、`degree-lru` と `hybrid-lru` は保護対象を適切に選べず、`lru` よりヒット率を下げてしまった。一方 `bfs-lru` は walk 開始時のプリフェッチでのみ距離情報を活用しており、通常の cache-in の多くは `near` 扱いとなるため、確認できる範囲では `lru` とほぼ同じ挙動に留まった。結果として、追加した分類・プリフェッチのコストに見合う miss 削減が得られず、認可時間の短縮にはつながらなかった。
