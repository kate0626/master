## RTT フォルダの全体像
このフォルダは、キャッシュ性能をRTT（Round Trip Time）コストで評価する実験のコードと結果をまとめたもの。

## ディレクトリ構成

base/rtt/
├── plot_mixed_rtt.py              ← 不均一RTTモデルの描画
├── plot_hopwise_rtt.py            ← hopwise RTTモデルの描画
├── analyze_high_freq_nodes_uni.py ← 均一モデル：高頻度ノード分析
├── analyze_high_freq_nodes_hetero.py ← 不均一モデル：高頻度ノード分析
│
└── alpha0.01_walks_100_capa_100/
    ├── uni/       ← 均一RTTモデルの結果
    └── hetero/    ← 不均一RTTモデルの結果
        ├── mixed_rtt_*.png / mixed_rtt_summary.csv
        ├── hopwise/
        └── rtt_detail/

## 2つのモデル
## uni（均一モデル）
全リモート通信が同一RTTを持つと仮定。シンプルな基準ケース。

## hetero（不均一モデル）
通信がローカル/中距離/長距離の3種類に分かれ、それぞれ異なるRTTを支払う：

ローカル	中距離	長距離	E[RTT]
パターンA ローカル支配	70%	20%	10%	39 ms
パターンB 域内バランス	30%	50%	20%	73 ms
パターンC グローバル分散	10%	30%	60%	139 ms
RTT単位値はAzure実測値（近: 10ms, 中: 60ms, 遠: 200ms）をベースに設定。

評価対象のキャッシュポリシー
ポリシー	意味
none	キャッシュなし（全アクセスがリモート）
memo	理想キャッシュ（上限性能）
lru	LRU キャッシュ
arc	ARC キャッシュ
測定指標

sim_time = (auth_calls + hop_count) × E[RTT]   ← リモート通信コスト
total    = walk_time + sim_time
memo_ratio = policy の total / memo の total    ← 理想キャッシュとの比
CSVの数値から：

none の memo_ratio ≈ 1.36〜1.40（キャッシュなしは理想の約40%増し）
lru/arc の memo_ratio ≈ 1.06（理想から約6%増し）
hopwise サブフォルダ
hop数（BFS距離）ごとにRTTが収束していくモデル。hop数が増えるほどRTTが定常値 E[RTT_∞] に近づく、という挙動を分析している。収束速度パラメータ ρ（0.8または0.92）を変えたシナリオが含まれる。

rtt_detail サブフォルダ
高頻度アクセスノード（頻繁にキャッシュヒット/ミスするノード）に絞った詳細RTT分析。amazon/vldb × A1/A2/B1/B2/C1/C2 の各シナリオで個別に可視化している。

一言でまとめると：「キャッシュのヒット削減効果を、グラフ上のホップ数 × RTTコストという実際のネットワーク遅延に換算して定量評価する」実験フォルダ。