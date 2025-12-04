# #!/usr/bin/env python3
# from __future__ import annotations


"""
ここだと横軸ノードID、縦軸PPRのグラフができる
"""
# import re
# from pathlib import Path
# from typing import Dict, List, Set

# import matplotlib.pyplot as plt
# import numpy as np


# # === 設定 ===
# FILES = [
#     ("base_1", "auth/test/PPR_100_0.1_global_transition.log"),
#     ("subgraph_1", "auth_subgraph/test/1_result_ng0_3_walks10_alpha0.01.log"),
#     ("subgraph_2", "auth_subgraph/test/2_result_ng0_3_walks10_alpha0.01.log"),
#     ("subgraph_4", "auth_subgraph/test/4_result_ng0_3_walks10_alpha0.01.log"),
#     ("subgraph_6", "auth_subgraph/test/6_result_ng0_3_walks10_alpha0.01.log"),
#     ("subgraph_8", "auth_subgraph/test/8_result_ng0_3_walks10_alpha0.01.log"),
#     ("subgraph_10", "auth_subgraph/test/10_result_ng0_3_walks10_alpha0.01.log"),
# ]

# EDGE_FILE = "./../dataset/Louvain/graph/karate.gr"  # エッジリスト
# TOP_K = None
# INCLUDE_EDGES = True


# # ------------------------------------------------------------
# # エッジリストから全ノードを取得
# # ------------------------------------------------------------
# def load_all_nodes(edge_path: Path) -> List[int]:
#     nodes: Set[int] = set()
#     with edge_path.open("r", encoding="utf-8") as f:
#         for line in f:
#             s = line.strip()
#             if not s:
#                 continue
#             parts = s.split()
#             if len(parts) != 2:
#                 continue
#             u, v = int(parts[0]), int(parts[1])
#             nodes.add(u)
#             nodes.add(v)
#     return sorted(nodes)


# # ------------------------------------------------------------
# # ログから PPR を取り出す
# # ------------------------------------------------------------
# def parse_ppr_from_log(path: Path) -> Dict[int, float]:
#     text = path.read_text(encoding="utf-8", errors="ignore").splitlines()

#     in_section = False
#     ppr_map: Dict[int, float] = {}

#     line_re = re.compile(r"^\s*(\S+):\s*PPR=([0-9.]+)")

#     for line in text:
#         if "[Controller] Top PPR entities:" in line:
#             in_section = True
#             continue
#         if not in_section:
#             continue

#         stripped = line.strip()
#         if not stripped or stripped.startswith("["):
#             break

#         m = line_re.match(line)
#         if not m:
#             continue

#         entity_id = m.group(1)
#         ppr_val = float(m.group(2))

#         if entity_id.startswith("edge_") and not INCLUDE_EDGES:
#             continue

#         try:
#             node_id = int(entity_id)
#         except ValueError:
#             continue

#         ppr_map[node_id] = ppr_val

#     if TOP_K is not None:
#         ppr_map = dict(
#             sorted(ppr_map.items(), key=lambda kv: kv[1], reverse=True)[:TOP_K]
#         )

#     return ppr_map


# # ------------------------------------------------------------
# # メイン処理
# # ------------------------------------------------------------
# def main():
#     # --- 全ノード読み込み ---
#     all_nodes = load_all_nodes(Path(EDGE_FILE))
#     N = len(all_nodes)
#     node_to_idx = {node: idx for idx, node in enumerate(all_nodes)}

#     print(f"Loaded {N} nodes from graph.\n")

#     # --- ファイル一覧表示 ---
#     print("Using files:")
#     for label, f in FILES:
#         print(f"  {label}: {f}")

#     # --- PPR の読み込み ---
#     all_runs: List[Dict[int, float]] = []
#     labels: List[str] = []

#     for label, fpath in FILES:
#         run_ppr = parse_ppr_from_log(Path(fpath))
#         all_runs.append(run_ppr)
#         labels.append(label)

#     # --- プロット ---
#     x = np.arange(N)
#     width = 0.8 / len(all_runs)

#     plt.figure(figsize=(14, 6))

#     for run_idx, (label, run_ppr) in enumerate(zip(labels, all_runs)):
#         y = [run_ppr.get(node, 0.0) for node in all_nodes]
#         x_shifted = x + (run_idx - len(all_runs) / 2) * width

#         plt.bar(x_shifted, y, width=width, label=label, alpha=0.7)

#     plt.xlabel("Node index (all graph nodes)")
#     plt.ylabel("PPR value")
#     plt.title("PPR per node (manual file selection, labeled)")
#     plt.legend()
#     plt.grid(axis="y", linestyle="--", alpha=0.3)
#     plt.xticks([])

#     plt.tight_layout()
#     plt.savefig("ppr_bar_manual_files.png", dpi=200)
#     plt.show()


# if __name__ == "__main__":
#     main()

#!/usr/bin/env python3
from __future__ import annotations
import re
from pathlib import Path
import json
import matplotlib.pyplot as plt
import numpy as np

"""
    こちらの方法では、ヒートマップで示す
    サブグラフなしのときとサブグラフの大きさ=1でPPRがほとんど一致したので、アルゴリズムの正当性は
    保証済みだと考えられる
    
"""

# 個別ファイル指定
log_files = {
    "base_1": "auth/test/PPR_100_0.1_global_transition.log",
    "subgraph_1": "auth_subgraph/test/1_result_ng0_3_walks10_alpha0.01.log",
    "subgraph_2": "auth_subgraph/test/2_result_ng0_3_walks10_alpha0.01.log",
    "subgraph_4": "auth_subgraph/test/4_result_ng0_3_walks10_alpha0.01.log",
    "subgraph_6": "auth_subgraph/test/6_result_ng0_3_walks10_alpha0.01.log",
    "subgraph_8": "auth_subgraph/test/8_result_ng0_3_walks10_alpha0.01.log",
    "subgraph_10": "auth_subgraph/test/10_result_ng0_3_walks10_alpha0.01.log",
}

EDGE_FILE = "./../dataset/Louvain/graph/karate.gr"

# PPR の行抽出用
LINE_RE = re.compile(r"^\s*(\S+):\s*PPR=([0-9.]+)")


def load_all_nodes(edge_path: Path):
    nodes = set()
    with edge_path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            u, v = map(int, parts)
            nodes.add(u)
            nodes.add(v)
    return sorted(nodes)


def parse_ppr(path: str):
    """ログファイルから PPR を dict[node] = value で取得"""
    ppr = {}
    in_section = False

    for line in Path(path).read_text(errors="ignore").splitlines():
        if "[Controller] Top PPR entities:" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if not line.strip() or line.startswith("["):
            break

        m = LINE_RE.match(line)
        if not m:
            continue

        key = m.group(1)
        value = float(m.group(2))

        # edge_xxx の場合は無視
        if key.startswith("edge_"):
            continue

        try:
            node = int(key)
        except:
            continue

        ppr[node] = value

    return ppr


def main():
    # 全ノードを共通化
    all_nodes = load_all_nodes(Path(EDGE_FILE))
    N = len(all_nodes)

    # (ノード数, ファイル数) の行列を準備
    heat_matrix = []

    # 説明ラベル
    labels = list(log_files.keys())

    for name, file_path in log_files.items():
        ppr = parse_ppr(file_path)

        # 全ノード分の縦ベクトル（欠けは 0）
        col = np.array([ppr.get(node, 0.0) for node in all_nodes])
        heat_matrix.append(col)

    # (ファイル数, ノード数)→転置して (ノード数, ファイル数)
    heat_matrix = np.array(heat_matrix).T

    # === 描画 ===
    plt.figure(figsize=(2 * len(log_files), 12))
    # plt.imshow(heat_matrix, aspect="auto", cmap="viridis")

    # ここから
    # --- カラースケール範囲を PPR の min/max に合わせる ---
    # 0 のノードを除外して min を計算し、色のダイナミクスを上げる
    non_zero = heat_matrix[heat_matrix > 0]
    if len(non_zero) > 0:
        ppr_min = non_zero.min()
    else:
        ppr_min = 0.0

    ppr_max = heat_matrix.max()

    plt.imshow(
        heat_matrix,
        aspect="auto",
        cmap="viridis",
        vmin=ppr_min,
        vmax=ppr_max,
    )
    # ここまで

    plt.colorbar(label="PPR value")

    # X 軸はファイル (列）
    plt.xticks(ticks=np.arange(len(labels)), labels=labels, rotation=45, ha="right")

    # Y 軸はノード（全部）
    plt.yticks(ticks=np.arange(N), labels=all_nodes)

    plt.xlabel("Log file")
    plt.ylabel("Node ID")
    plt.title("PPR heatmap comparison")

    plt.tight_layout()
    plt.savefig("ppr_heatmap_vertical_compare.png", dpi=200)
    plt.show()


if __name__ == "__main__":
    main()
