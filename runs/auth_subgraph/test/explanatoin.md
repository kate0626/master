## ログの見方に関する解説

この JSON は START_NODE=ALL で 100 本ずつランダムウォークを回した結果なので、**各始点ノードで実質「開始ノードを 100 回記録しただけ」**になっています。access["n"] は「そのノードが経路に現れた回数」で、各ノードのウォークが 1 ステップも進めず即終了しているため、開始ノード分の 100 回だけが積算され、0〜33 までがそろって 100 になっています。

歩けなかった原因は authorization_denied が大量に出ている点から明らかで、node_to_starts.json の設定や subgraph_index.json の許可範囲の影響で、開始ノード以外のノード／エッジが「この始点からはアクセス不可」と判定されていると考えられます。controller も authorization_denied を 2500（=100 walks × 平均 25 試行等）と報告しており、許可が通らず遷移できていません。

エッジ ID（edge_u_v）は、ウォーカーがノード→エッジ→ノードと二部グラフを進んだときにのみ訪問回数にカウントされます。今回は隣接遷移に入る前に拒否されているため、エッジ側にほとんどアクセスが記録されません。

### 対処案
base/auth-many-server/node_to_starts.json（またはサブグラフ用のもの）で、始点から許されたノード／エッジが十分に含まれているか確認し、必要なら create_json_table.py で再生成してください。
テストとして、単発で controller を --start-node 1 など固定して走らせ、[Controller] Failure rate per entity と authorization_denied が 0 近くになるまで認可設定を調整します。
それでも歩かない場合は remote_server.py 側で _is_entity_authorized と _evaluate_group_access のログ（コメントアウトされた print を活用）を一時的に有効化して、どの段階で拒否されているかを調べてください。
これらを改善すれば、Shell 経由でもノードごとの訪問回数やエッジ利用履歴がばらけた PPR 結果になります。