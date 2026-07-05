作成したファイル
ファイル	役割
generate_results_csv.py	vldb_nobt 配下を全走査して 2 CSV を再生成
run_all_plots.sh	CSV 生成 → 8 プロット を一括実行

## 実行コマンド
cd /Users/maiko/Documents/GitHub/master-progrem
① α=0.01
\
ALPHA_OVERRIDE=0.01 \
GRAPH_OVERRIDE=amazon0601 \
CACHE_CAPACITY_OVERRIDE=100 \
THETA_LIST_OVERRIDE="2" \
DELTA_LIST_OVERRIDE="1" \
LAMBDA_LIST_OVERRIDE="1.0" \
HOP_EXP_LIST_OVERRIDE="0.5 1.0 2.0" \
DEG_EXP_LIST_OVERRIDE="0.0 0.5 1.0" \
LEARNING_WALKS_LIST_OVERRIDE="0 20 40" \
zsh base/proposed_cache/sweep_ppr_params.sh
② α=0.05

ALPHA_OVERRIDE=0.05 \
GRAPH_OVERRIDE=amazon0601 \
CACHE_CAPACITY_OVERRIDE=100 \
THETA_LIST_OVERRIDE="2" \
DELTA_LIST_OVERRIDE="1" \
LAMBDA_LIST_OVERRIDE="1.0" \
HOP_EXP_LIST_OVERRIDE="0.5 1.0 2.0" \
DEG_EXP_LIST_OVERRIDE="0.0 0.5 1.0" \
LEARNING_WALKS_LIST_OVERRIDE="0 20 40" \
zsh base/proposed_cache/sweep_ppr_params.sh
③ α=0.1

ALPHA_OVERRIDE=0.1 \
GRAPH_OVERRIDE=amazon0601 \
CACHE_CAPACITY_OVERRIDE=100 \
THETA_LIST_OVERRIDE="2" \
DELTA_LIST_OVERRIDE="1" \
LAMBDA_LIST_OVERRIDE="1.0" \
HOP_EXP_LIST_OVERRIDE="0.5 1.0 2.0" \
DEG_EXP_LIST_OVERRIDE="0.0 0.5 1.0" \
LEARNING_WALKS_LIST_OVERRIDE="0 20 40" \
zsh base/proposed_cache/sweep_ppr_params.sh



## グラフ更新時のUpdate
### Step 1: amazon0601 用 CSV 生成
cd /Users/maiko/Documents/GitHub/master-progrem
python3 base/proposed_cache/generate_results_csv.py --graph amazon0601
→ results/all_amazon0601_nobt_settings.csv と results/all_amazon0601_nobt_results.csv が生成される

### Step 2: 8枚のプロットを一括生成
GRAPH=amazon0601 zsh base/proposed_cache/run_all_plots.sh
→ results/plots/amazon0601/ に 8 PNG が出力される

## Step 3: vldb との比較
ファイルを並べて比較するか、簡易の集計を出力：

## code
python3 - << 'EOF'
import csv, pathlib
BASE = pathlib.Path('/Users/maiko/Documents/GitHub/master-progrem/base/proposed_cache/results')

for graph in ['vldb', 'amazon0601']:
    s = list(csv.DictReader(open(BASE / f'all_{graph}_nobt_settings.csv')))
    r = list(csv.DictReader(open(BASE / f'all_{graph}_nobt_results.csv')))
    rmap = {x['run_id']:x for x in r}
    m = [{**a, **rmap[a['run_id']]} for a in s if a['run_id'] in rmap]
    print(f'\n=== {graph}_nobt ===')
    print(f'  全 ラン数: {len(m)}')
    # ppr_demand vs lru/arc を α 別に
    for alpha in ['0.01','0.05','0.1']:
        bl = {r['policy']: float(r['hit_rate']) for r in m
              if r['alpha']==alpha and r['policy'] in ('lru','arc','memo')}
        pprs = [float(r['hit_rate']) for r in m
                if r['alpha']==alpha and r['policy']=='ppr_demand']
        if not pprs: continue
        best = max(pprs)
        print(f'  α={alpha}: ppr_best={best:.3f}, lru={bl.get("lru","-"):},  arc={bl.get("arc","-"):}')
EOF


## 分析の観点（何を見るか）
観点	期待	否定された場合の意味
β=1〜2 が最適か	vldb と同じ	グラフ密度で最適 β が変わる
γ=0 が最良か	vldb と同じ	ハブが効くグラフもある（amazon は密だから可能性あり）
lw の α 依存性	α=0.05 で最大	グラフによって最適 α が変わる
ppr_demand vs lru	α=0.05/0.1 で勝つ	グラフ依存で勝てなくなる可能性
ベースライン水準	vldb と異なる	グラフ自体の局所性の違いを反映
結論判定ルール：

amazon でも同じパターンなら → **「α が支配的、グラフ非依存」**と一般化できる
パターンが崩れるなら → 「グラフ構造（密度、次数分布）も効く」 → 新たな分析軸が必要