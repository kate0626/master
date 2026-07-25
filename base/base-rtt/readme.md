# base-rtt — RTT 設計モデル → RW 実行 → 軌跡一致確認

分散ランダムウォーク(RW)で **各ステップに発生する RTT(往復遅延)** を、
**対象 RW の歩数 T から先に「設計」し**、その設計どおりに RTT が現れるよう RW を走らせる実験フォルダ。

- 形状は **above / below / zigzag の 3 種のみ**(コミュニティ割り当て・キャッシュ実験は撤去済み)。
- 土台は `base/base`(**サーバ間 RW** の本体)。その `GraphShard`(node↔edge 二部展開)を流用する。

---

## 0. やること(3 行)

1. **設計**: 対象 RW の歩数 T で RTT モデル `g_t = μ + a·λ^t`(above/below/zigzag)を作る(T 歩で概ね収束)。
2. **焼き付け→実行**: その曲線を RW で 1 歩ずつ消費して各エンティティに RTT を固定し、別シードで RW を実行して各歩の RTT を記録。
3. **確認**: 実行 RW の平均 RTT 軌跡が、設計モデル `g_t` に概ね一致することを重ね描き + RMSE で確認。

> つまり「先に決めた RTT の時間変化(上から / 下から / ギザギザ)」を、実際の RW が体験する RTT の軌跡として再現する。

---

## 1. RTT 設計モデル `g_t = μ + a·λ^t`

体験 RTT の 1 歩ごとの期待値を直接パラメトライズする。ノブは 3 つ:

| ノブ | 引数 | 意味 |
|---|---|---|
| 振幅 | `--amp` | 初期の μ からのズレ幅 \|g_0 − μ\| |
| 向き | `--direction` | `above`(上から) / `below`(下から) / `zigzag`(毎歩交互) |
| 収束速度 | `--converge-steps N` | 約 N 歩で 99% 収束 → \|λ\| = 0.01^(1/N)(`--lam` 直接指定も可) |

- `above` : `μ + amp·λ^t` (λ>0) 上から単調に μ へ
- `below` : `μ − amp·λ^t` (λ>0) 下から単調に μ へ
- `zigzag`: `μ + amp·(−λ)^t` (λ<0) 毎歩 高低交互に減衰
- 3 つとも平均 μ は共通で、**形だけ違う**。

```sh
python3 rtt_model.py --demo               # 速度・振幅・向きを自分で決められることの一覧図
python3 rtt_model.py --direction zigzag --converge-steps 60 --amp 40
```

---

## 2. 設計 → 実行 → 一致確認(本体)

```sh
# 既定 = 実データ com-amazon
./run.sh

# 個別に(karate なら数秒の smoke test)
python3 design_and_run.py \
  --graph ../../dataset/Louvain/graph/com-amazon-connected.gr \
  --start 1 --mu 50 --amp 40 --steps 100 --walks 80 --seed 1 \
  --directions above below zigzag \
  --out results/design_run_amz.png
```

- `--steps T` が **「対象 RW の歩数 = 設計 horizon」**。T 歩で概ね収束する曲線を設計し、焼き付け→実行。
- 出力図は above/below/zigzag それぞれで
  **設計 `g_t`(黒破線) vs 実行 RW の平均 RTT(赤)** を重ね描き(薄い線は個々の walk)。
- amazon で **RMSE ≈ 3.5**(RTT 10〜90 に対し十分小)で一致を確認済み。

> zigzag が単一の場で再現できるのは、node↔edge の二部構造(偶数歩=ノード / 奇数歩=エッジ)が
> 高低交互を担うため。

### 注意点

- 距離場は開始点 S 依存。`above` 場は「近く=高RTT / 遠く=μ」なので μ 未満の領域が無く、
  S 以外から出すと μ 付近で平坦になる(= walk 非依存の大域サーバ配置ではない)。
- 大きなグラフでは被覆率が低く、未訪問エンティティは μ にフォールバックする分、
  実測 λ が設計値から少しズレる。

---

## 3. フォルダ構成

```
base/base-rtt/
├── readme.md            このファイル
├── run.sh               設計→実行→一致確認 を一括実行(既定 amazon)
├── rtt_model.py         RTT 設計モデル: g_t = μ + a·λ^t(above/below/zigzag)
├── paint_field.py       設計曲線 g_t を RW で1歩ずつ消費し各エンティティに焼き付ける(距離場)
├── design_and_run.py    設計(T歩)→RW実行しRTT組込み→実測軌跡が設計に一致するか(本体)
└── results/             生成物(図)。walk ログ *.jsonl は .gitignore

依存(サーバ間RWの本体・このフォルダの土台):
../base/split_remote_server.py   … GraphShard / load_edge_list を流用
```

処理の流れ:

```
① 設計   rtt_model.make_curve    g_t = μ + a·λ^t(converge_steps=T)
② 焼付   paint_field.paint       g_t を1歩ずつ消費し初回訪問で各エンティティに固定(未訪問=μ)
③ 実行   design_and_run          別シードでRW→各歩のRTTを記録→平均g_tと重ね描き+RMSE
```

---

## 4. 用語

- **RTT** … ステップごとの往復遅延。ここでは「そのエンティティに割り当てた仮想遅延」。
- **node-consistent** … RTT がエンティティの固定関数。同じ物を再訪すれば必ず同じ RTT。
- **g_t** … 開始ノードを固定したときの t 歩目の期待 RTT `E[r_{X_t}]`。
- **距離場** … 開始点 S からの到達の早さ(初回訪問時刻 ≒ 距離)で決まる RTT の割り当て。
