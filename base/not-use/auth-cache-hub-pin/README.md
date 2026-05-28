# auth-cache-hub-pin

**目的**: baseline 実験で見えた「LRU → memo の hit rate ギャップ」を、定 capacity (=100) のまま埋める。
BFS prefetch (`auth-cache-bfs-degree`) が機能しなかった原因を踏まえて設計し直す。

---

## 1. baseline データで見えたこと

### 1-A. avg_len は α だけで決まる

| graph | α=0.01 | α=0.1 |
|---|---:|---:|
| amazon0601 | avg_len ≈ 33 | ≈ 7.7 |
| vldb | ≈ 104 | ≈ 8.7 |

これは `E[len] = 1/α` のとおり。**`walks` の数は walk length を変えない** (= 同じ length の walk を何本走らせるかが walks)。

### 1-B. auth_time はほぼ「miss × 1.4ms」

`Average authorization time per call` は全ケースで **1.3〜1.6 ms** で安定。総 lookups 数も policy 間でほぼ同じ（cache が hit する/しないだけで RW のステップ数自体は変わらないから）。

つまり:
```
auth_time ≈ miss_count × 1.4ms
walk_time ≈ auth_time + (cache 操作以外のオーバーヘッド)
```
**miss を減らせば直接 walk_time が縮む**。

### 1-C. LRU と memo のヒット率ギャップが大きい

| graph | α | walks | LRU hit | memo hit | gap |
|---|---:|---:|---:|---:|---:|
| amazon | 0.01 | 100 | 0.440 | 0.527 | +0.087 |
| amazon | 0.01 | 1000 | 0.440 | 0.527 | +0.087 |
| amazon | 0.1  | 1000 | 0.555 | **0.753** | **+0.198** |
| vldb   | 0.01 | 1000 | 0.450 | 0.533 | +0.083 |
| vldb   | 0.1  | 100  | 0.520 | 0.630 | +0.110 |
| vldb   | 0.1  | 1000 | 0.504 | **0.778** | **+0.274** |

walks=1000 で**特に乖離が大きい**。これは「累積アクセスが増えて memo は全部覚える / LRU は古いのを忘れる」差。
walks=100 では cache がまだ初期段階なので差が小さい。

つまり**長く回すほど LRU は memo に負ける**。実運用ではむしろ walks が多い側が重要。

### 1-D. Belady (Top-K オフライン最適) は memo より更に高い

各 start_node の access frequency から「もし上位 100 アクセスノードを最初から pin できたら」の hit rate を計算したところ:

| ケース | LRU 実測 | Belady (top-100) | 余地 |
|---|---:|---:|---:|
| vldb α=0.1 walks=1000 sn=0 | 0.525 | **0.793** | +0.268 |
| vldb α=0.1 walks=1000 sn=2 | 0.562 | **0.888** | +0.326 |
| amazon α=0.1 walks=1000 sn=2 | 0.765 | **0.973** | +0.208 |
| amazon α=0.1 walks=1000 sn=3 | 0.694 | **0.943** | +0.249 |

LRU は **20〜33 ポイント** の改善余地を残している。
**「上位アクセスノードを最初から cache に置く」だけで、LRU を memo より上に持っていける**。

---

## 2. 既存提案 (`auth-cache-bfs-degree`) が微妙な理由

`auth-cache-bfs-degree/plot_bfs_hitrate_breakdown.py` の結果から:

| graph | LRU baseline | BFS-prefetched ノードのヒット率 | non-prefetched のヒット率 |
|---|---:|---:|---:|
| amazon0601 | 0.440 | 0.438〜0.592 | 0.439〜0.440 |
| vldb | 0.450 | **0.258〜0.307** | 0.456 |

vldb で BFS prefetch したノードのヒット率は 0.26〜0.31 と **baseline 0.45 を下回る**。原因:

1. **BFS は start ノード近傍を取るが、RW は遠くまで歩く**
   α=0.01 では平均 100 steps 進むので start 近傍 (BFS 距離 ≤ 2〜6) はすぐ離脱領域になる。

2. **prefetch 対象が walk で参照されていない**
   `plot_bfs_cache_usage.py` 結果: vldb の `preflook_frac ≈ 0.035` — walk の auth 参照のうち BFS prefetch 領域に当たるのは 3.5% だけ。**残り 96.5% は prefetch 範囲外**。

3. **キャッシュは溢れていない**
   `end/cap ≈ 0.39` — capacity は 60% 余ってる。LRU の eviction pressure は弱く、「LRU が prefetch を追い出した」というより「prefetch 対象がそもそも参照されない」ことが本質。

**まとめ: 「start からの距離」は random walk のホット領域とは無相関**。BFS で集めた近傍は局所的な探索構造でしかなく、walks 全体で見ると hub には程遠い。

---

## 3. 提案手法: **hub-pin-lru**

### コア・アイデア

**事前計算した「グラフ全体での hub ノード上位 K 個」を cache の固定枠 (pinned slot) に最初から入れ、残り (capacity - K) 枠を LRU で運用する。**

なぜこれが効くのか:
- 無向 RW (with teleport α) の stationary distribution は π(v) ∝ degree(v) (PageRank-ish)
- 高 degree ノードは「どの start からも遅くとも O(1/α) ステップで踏まれる」
- pinned なので絶対に evict されない → memo が持つ「忘れない性」を局所的に再現

### 既存提案との違い

| 観点 | bfs-degree (現在) | hub-pin (提案) |
|---|---|---|
| 学習源 | start_node からの BFS 距離 | グラフ全体の degree (静的) |
| いつ計算 | walk 開始時に毎回 | 起動時に 1 回だけ |
| 何を入れる | start_node 近傍 | グラフ全体のハブ |
| LRU との関係 | 同じ LRU プールに混入 → evict されやすい | 別枠 (pinned) → 絶対残る |
| パラメータ | far × depth (× hub_threshold) | **K (pinned 枠サイズ) 1 つだけ** |
| α 依存 | α 小だと無効 | α 不問 (どの distance でも hub は踏まれる) |

### パラメータ

- `K` = pinned 枠のサイズ。デフォルト `K = capacity // 2 = 50`
- `K=100` (= capacity) はすべて pin → LRU 部分なし。"static-only" モード
- `K=0` は通常 LRU と同じ

### offline simulator で実際に検証した結果

`simulate_offline.py` を全 (alpha, walks, graph) で走らせ、`cap=100` (1 サーバ分の実効 cap) で評価:

| ケース | LRU sim | LFU | TinyLFU | hub-pin (K=50) | Belady 上限 |
|---|---:|---:|---:|---:|---:|
| vldb α=0.1 walks=1000 | 0.783 | **0.808** | 0.801 | 0.794 | 0.872 |
| amazon0601 α=0.1 walks=1000 | 0.792 | **0.803** | 0.797 | 0.791 | 0.892 |
| vldb α=0.01 walks=1000 | 0.742 | 0.751 | **0.761** | 0.757 | — |

判明したこと:
- **hub-pin (K=50) は LRU と同等か微増程度** (+0.01 以内)。理論的に最も効くはずだったが、cap=100 が working set 全体を持てる場合は LRU の自然な状態と pin が変わらない。
- **LFU が最も安定** に LRU を 0.01〜0.025 ポイント上回る。これは memo の「忘れない」性質を有限 cap で近似するから。
- **TinyLFU は LFU の妥協版**として有効。少し劣るが pure LFU と僅差。
- **Belady 上限 (≈0.87〜0.89)** までは LFU でもまだ 0.06〜0.08 ポイント空きがある。これは「PPR 上位を事前に知っていれば」の理論限界。

### 結論

「hub-pin (degree top-K を起動時固定)」は理論的に魅力だが**実測上はメリット小**。
**LFU / TinyLFU の方が安定して良い**。最も効果が大きいのは:

  hit_rate を LRU から LFU に換えるだけで +0.025 → auth_time が約 0.025 × 12,000 lookups × 1.4ms = **0.42s/start_node 短縮**

walks=1000 と長い実験では「LRU は古い hot entry を忘れる」のが致命的で、LFU が直接これを救う。

---

## 4. ファイル構成

| ファイル | 役割 |
|---|---|
| `README.md` | 本ファイル（設計理由・期待値） |
| `simulate_offline.py` | **ローカルで実行可能**な検証。既存の `*_global_transition.json` を読んで、LRU と hub-pin を replay 比較。SSH 不要。 |
| `hub_pin_cache.py` | `hub-pin-lru` 政策クラスの実装（`server.py` に drop-in） |
| `splits.sh` | 分散実行スクリプト（`auth-cache-bfs-degree/splits.sh` の K-sweep 版） |

---

## 5. 使い方

### ステップ A: ローカル検証（先にやる）

```bash
# 既存の transition.json を読んで、各 start_node について
#   LRU 実測 / hub-pin シミュ / Belady 上限 を表示
python3 base/auth-cache-hub-pin/simulate_offline.py \
  --graph dataset/Louvain/graph/vldb.gr \
  --transition-dir base/auth-baseline-cache/results/alpha0.1_walks_1000_capa_100/vldb/lru_100 \
  --capacity 100 --pin-k 50

# K を sweep して最適値を探す
python3 base/auth-cache-hub-pin/simulate_offline.py \
  --graph dataset/Louvain/graph/vldb.gr \
  --transition-dir base/auth-baseline-cache/results/alpha0.1_walks_1000_capa_100/vldb/lru_100 \
  --capacity 100 --pin-k 0,10,20,30,50,70,100
```

### ステップ B: 本番（分散環境）

`hub_pin_cache.py` を `base/auth-cache-bfs-degree/server.py` に統合し、`--cache-policy hub-pin-lru --pin-k 50` を渡せるようにしてから:

```bash
bash base/auth-cache-hub-pin/splits.sh
```
