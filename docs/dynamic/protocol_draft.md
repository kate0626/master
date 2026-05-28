# 動的二部グラフ認可プロトコル — 叩き台 v0

> 目的: 秒オーダーのACL変更下で、Strong safety を満たしつつ、既存BFS/Degree局所性を活かして失効伝播を局所化する。

---

## 1. システムモデル

### 1.1 グラフ
時刻付き二部グラフ:
$$G_t = (U_t, V_t, E_t, \pi_t)$$

- $U_t$: 参照元Doc集合（時刻 $t$）
- $V_t$: 参照先Doc集合
- $E_t \subseteq U_t \times V_t$: 参照エッジ
- $\pi_t: E_t \to \{\text{allow}, \text{deny}\}$: エッジACL

### 1.2 アクター
- **Policy Admin (PA)**: ACL更新者。`update(e, new_acl)` を発行
- **Querier (Q)**: クエリ発行者。`walk(u, depth)` で到達可能ノード集合を要求
- **Shard Server (S_i)**: グラフを分散保持。キャッシュを持つ
- **Coordinator (C)**: 順序付け担当（論理クロック発行）

### 1.3 タイムスタンプ
- グローバル論理クロック $T$（Lamport / HLC）を Coordinator が管理
- ACL更新と探索クエリの両方がタイムスタンプを取得

---

## 2. データ構造

### 2.1 エッジエントリ（サーバ側永続）
```
EdgeEntry {
  edge_id: (u, v),
  acl: allow | deny,
  version: T,              // この版を作った更新タイムスタンプ
  valid_from: T,           // 有効開始
  valid_until: T | ∞,      // 有効終了（次の更新が来るまで∞）
}
```
→ MVCC的に**版を蓄積**。最新版だけでなく旧版も短期間保持（in-flightクエリ用）

### 2.2 キャッシュエントリ
```
CacheEntry {
  edge_id: (u, v),
  allowed: bool,
  acl_version: T,          // このキャッシュが基にしたACL版
  bfs_dist: int,           // 既存 BFSDistanceLRUCache から
  degree_tier: int,        // 既存 DegreeAwareLRUCache から
  inserted_at: T,
}
```
→ 既存の `set_with_dist()` に `acl_version` フィールドを追加するだけで済む

### 2.3 失効通知
```
InvalidationMsg {
  edge_id: (u, v),
  old_version: T_old,
  new_version: T_new,
  propagation_bound: int,  // BFS距離 d
}
```

---

## 3. プロトコル

### 3.1 ACL更新フロー (`UPDATE`)
```
PA → C:           request_update(edge, new_acl)
C  → PA:          assign timestamp T_new
PA → S_owner:     write EdgeEntry(version=T_new, valid_from=T_new)
                  旧版の valid_until = T_new - 1 に設定
S_owner:          determine propagation bound d
                  (= 既存 cache-bfs-prefetch-depth と対称)
S_owner → S_*:    broadcast InvalidationMsg(edge, T_old, T_new, d)
S_i:              該当キャッシュエントリを以下の規則で処理:
                    if cache.acl_version < T_new
                       AND cache.bfs_dist <= d:
                       evict or mark stale
C:                update committed at T_new
```

**ポイント**:
- 失効は**該当エッジを含むキャッシュ**だけでなく、**BFS距離d以内のキャッシュ**も対象（参照経路上のキャッシュが古いACLを使い続けるのを防ぐ）
- 既存の `bfs_far_threshold=6` と同じ局所性パラメータを再利用可能

### 3.2 クエリフロー (`WALK`)
```
Q → C:            request_walk(u, depth)
C → Q:            assign read timestamp T_read
Q → S_owner(u):   walk(u, depth, T_read)
S_i:              for each edge encountered:
                    cache_hit = lookup(edge)
                    if cache_hit AND cache.acl_version >= T_read_floor:
                        use cache.allowed
                    else:
                        authoritative = read EdgeEntry where
                            valid_from <= T_read < valid_until
                        cache it with acl_version=T_read
                        use authoritative.acl
```

`T_read_floor`: クエリ開始時点で観測されている最新コミット済みACL版。これより古いキャッシュは使えない。

### 3.3 Strong Safety の核
**不変条件**: タイムスタンプ $T_{\text{revoke}}$ で剥奪されたエッジ $e$ について、$T_{\text{read}} > T_{\text{revoke}}$ を持つ任意のクエリは $e$ を `deny` として観測する。

**証明スケッチ**:
1. `UPDATE` 完了時、Coordinator は $T_{\text{revoke}}$ をコミット
2. クエリの $T_{\text{read}}$ は $T_{\text{revoke}}$ より厳密に大きい（Coordinator が単調発行）
3. キャッシュ判定で `cache.acl_version >= T_read_floor >= T_revoke` なエントリは存在しない（失効済み or 上書き済み）
4. よってクエリは authoritative read に降り、新版を取得

---

## 4. 性能の主張（既存局所性の再利用）

### 4.1 失効コスト
- naive: 全キャッシュをスキャン → $O(|cache|)$
- 提案: BFS距離 d 以内 + Degree tier フィルタ → $O(\sum_{v \in N_d(e)} \deg(v))$ で**ハブ次数に対して劣線形**を期待

### 4.2 既存戦略との対応
| 既存戦略 | 動的版での役割 |
|---|---|
| `BFSDistanceLRUCache` | 失効伝播範囲を BFS 距離で限定 |
| `DegreeAwareLRUCache` | ハブノードの失効を優先（影響範囲が広いため） |
| `prefetch_bfs_neighbors` | クエリ時の prefetch を継続。新版だけ取得 |

→ **静的版で「探索を速くする」ために導入した局所性が、動的版で「失効を速くする」役割を兼ねる**

### 4.3 並行制御コスト
- MVCC により読み手はロックフリー
- 書き手は該当エッジの最新版にだけ排他（行ロック相当）
- Coordinator は単純な単調カウンタ（HLC）でスケール

---

## 5. 評価で示すこと

### 5.1 安全性
- 並行ACL変更を意図的に注入する敵対的ワークロードで、漏洩ゼロを確認
- TLA+/Alloy でモデル検査（オプション）

### 5.2 性能
| 指標 | 比較対象 |
|---|---|
| 更新スループット | naive 全失効 vs 提案 |
| クエリレイテンシ劣化 | 静的版 vs 動的版 |
| キャッシュヒット率 | 更新負荷を変化させた時の保持率 |
| 失効伝播コスト | BFS距離 d を変えた時のトレードオフ |

### 5.3 大規模スケール
- ノード数 $10^6$〜$10^7$
- 更新レート 1〜1000 events/sec
- シャード数 1, 4, 16, 64 でスケール特性

---

## 6. 未解決事項（要議論）

1. **propagation_bound d** をどう決めるか？
   - 静的解析（既存BFSパラメータ流用）
   - 適応的（クエリ統計から自動調整）
2. **Coordinator の単一障害点**
   - 単一でOK（小さい）／Raft等で冗長化
3. **長時間クエリ**
   - クエリ開始時の $T_{\text{read}}$ を貫くと、長いクエリほど古いACLを見る可能性
   - → 上限時間を設定 or 中途で読み直し
4. **削除と再挿入**
   - エッジ削除後に同じ `edge_id` で再挿入されたら、`version` で区別すれば OK
5. **Coordinator なしの完全分散版**
   - HLC のみで近似的順序を維持する設計（簡略版として比較対象に使える）

---

## 7. 次のアクション
- [ ] §3 のプロトコルを擬似コードレベルで完全に書き下す
- [ ] §4.1 の失効コストを既存データセット（vldb.gr 等）で実測
- [ ] §5 の評価設計を具体化（ワークロードジェネレータの仕様）
- [ ] §3.3 の証明を形式的に詰める
