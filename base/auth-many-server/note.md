### データを作成したい時
規模が大きくて作れなくなったので、4月以降はDenyリストの作成に更新済み
実行方法

<!-- 実行ファイル -->
prepare_auth_data.sh

<!-- 実行コマンド -->
nohup   ./base/auth-many-server/prepare_auth_data.sh --graph com-friendster     --ng-ratio 0.3     --server-count 2     --partitioner-type node-edge-fixed     --mode deny-direct    --starts "388990" > run.log 2>&1 &
 
はい、その通りです。

## 出力されるもの・されないもの

```
出力される：
  ノードB: [1, 3, 5]   // 始点1,3,5からNGにされたことがある

出力されない：
  ノードC: []          // どの始点からもNGにされていない = エントリ自体なし
```

## コードで確認すると

```python
def to_serializable(table):
    return {str(k): sorted(...) for k, v in table.items()}
```

`buckets[sid]` は `defaultdict(set)` なので、**一度もNGに選ばれなかったエンティティはキー自体が存在しない** → 出力されません。

---

つまりファイルの意味は：

> **「このファイルに載っているエンティティ＝少なくとも1つの始点からアクセス拒否されたことがあるもの」**
> 
> **「載っていないエンティティ＝全始点からアクセス可能」**
