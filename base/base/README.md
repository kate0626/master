## 概要
- 完全に認可のないモデルを考える
- エッジとノードをバラバラに配置したのちに、普通にRWを同じ条件で行うことを考える


[重要]
- 勝手にプロセスが終了されないので、都度治っているのかを確認すること！



python3 base/base/split_remote_server.py \
  --server-id 0 \
  --server-count 2 \
  --edges dataset/Louvain/graph/amazon0601.gr \
  --host 10.58.60.6 \
  --port 3000 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --node-to-starts-file base/auth-many-server/data/splits/amazon0601/0.3/node_to_starts_server0.json \
  --owned-hints-only

python3 base/base/split_remote_server.py \
  --server-id 1 \
  --server-count 2 \
  --edges dataset/Louvain/graph/amazon0601.gr \
  --host 10.58.60.11 \
  --port 3000 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --node-to-starts-file base/auth-many-server/data/splits/amazon0601/0.3/node_to_starts_server1.json \
  --owned-hints-only

// スタートノードは１からしか始まらないので注意
 
 python3 base/base/split_controller.py \
  --servers 2 \
  --server-endpoints 10.58.60.6:3000 10.58.60.11:3000 \
  --start-node 1 --walks 10 --alpha 0.1 --seed 42 \


  
  --node-to-starts-file base/auth-many-server/data/test/node_to_starts_0.0.json
  