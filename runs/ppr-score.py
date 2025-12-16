#!/usr/bin/env python3
from __future__ import annotations

"""
visit_*.log の Failure rate per entity から

  - 精度指標（coverage または accuracy）
  - それを size / alpha / wr / hot に対して
    複数グループの折れ線として 1 枚のグラフに描画

を行うスクリプト。

★ GRAPH_FILE は 2パターン想定 ★

1. エッジリスト(テキスト)
   u v
   u v
   ...

   → ノード u,v と edge_u_v (昇順) から entity を構成

2. エンティティグラフ(JSON)
   {
     "0": [...],
     "1": [...],
     "edge_0_1": [...],
     ...
   }

   → JSON のキー (0,1,edge_0_1,...) をそのまま entity として使う

Accuracy の定義:
  - EXPERIMENT_GROUPS[0] の先頭ログを Base とみなす
  - その entity 分布 p_base と各ログの分布 p を比較し
        TV = 0.5 * sum |p_base - p|
        accuracy = 1 - TV
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Tuple, Set, Any

import numpy as np
import matplotlib.pyplot as plt

# ============================================================
# 設定
# ============================================================

# グラフ・エンティティの元ファイル
#   - エッジリスト: "karate.gr" など
#   - エンティティグラフ(JSON): "entity_graph.json" など
GRAPH = "fb-caltech-connected"
GRAPH_FILE = Path(
    "base/auth-many-server/fb-caltech/node_to_starts.json"
)  # ←ここを実際のファイルに合わせて変えてね


BASE_LOG = "runs/visit_count/test/B1/fb/fb-caltech-connected_size1_walks10_alpha0_1_wr0_hot1000.log"
# グループごとに「凡例ラベル + ログのリスト」を定義
EXPERIMENT_GROUPS: List[Tuple[str, List[str]]] = [
    # (
    #     "size=1",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size1_walks10_alpha0_1_wr0_1_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size1_walks10_alpha0_1_wr0_2_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size1_walks10_alpha0_1_wr0_3_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size1_walks10_alpha0_1_wr0_4_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size1_walks10_alpha0_1_wr0_5_hot30.log",
    #     ],
    # ),
    (
        "size=100",
        [
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_hot2.log",
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_1_hot2.log",
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_2_hot2.log",
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_3_hot2.log",
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_4_hot2.log",
            "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_5_hot2.log",
            # "runs/visit_count/test/fb-caltech-connected_size20_walks10_alpha0_1_wr0_6_hot70.log",
            # "runs/visit_count/test/fb-caltech-connected_size20_walks10_alpha0_1_wr0_7_hot300.log",
            # "runs/visit_count/test/fb-caltech-connected_size20_walks10_alpha0_1_wr0_8_hot300.log",
        ],
    ),
    # (
    #     "size=50",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_1_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_2_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_3_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_4_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size50_walks10_alpha0_1_wr0_5_hot30.log",
    #     ],
    # ),
    # (
    #     "size=100",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_1_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_2_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_3_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_4_hot30.log",
    #         "runs/visit_count/test/fb-caltech-connected_size100_walks10_alpha0_1_wr0_5_hot30.log",
    #     ],
    # ),
    # (
    #     "size=150",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_1_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_2_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_3_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_4_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_5_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_6_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_7_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size150_walks10_alpha0_1_wr0_8_hot3.log",
    #     ],
    # ),
    # (
    #     "size=200",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_1_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_2_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_3_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_4_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_5_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_6_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_7_hot300.log",
    #         "runs/visit_count/test/fb-caltech-connected_size200_walks10_alpha0_1_wr0_8_hot300.log",
    #     ],
    # ),
    # (
    #     "size=250",
    #     [
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_1_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_2_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_3_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_4_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_5_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_6_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_7_hot3.log",
    #         "runs/visit_count/test/fb-caltech-connected_size250_walks10_alpha0_1_wr0_8_hot3.log",
    #     ],
    # ),
]

# 横軸: "size" / "alpha" / "wr" / "hot"
X_MODE = "wr"

# 縦軸: "coverage" / "accuracy"
Y_MODE = "accuracy"  # まず coverage で確認して、その後 accuracy に切り替えると良い

# Failure rate per entity の行をパース
FAIL_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_]+):\s*(\d+)/(\d+)\s+failures")
# group(1) = entity (nodeID or edge_x_y)
# group(2) = failures (使ってない)
# group(3) = attempts

# ファイル名のパラメータ抽出
# 例: visit_fb-caltech-connected_size20_walks1_alpha0_01_wr0_2_hot2.log
FNAME_RE = re.compile(
    r".*?_size(?P<size>\d+)_walks(?P<walks>\d+)_alpha(?P<alpha>[0-9_]+)_wr(?P<wr>[0-9_]+)_hot(?P<hot>\d+)\.log"
)

# ============================================================
# グラフ・エッジ or エンティティグラフから entity 空間を作る
# ============================================================


def load_entities_from_graph(path: Path) -> List[str]:
    """
    GRAPH_FILE の中身に応じて entity 一覧を作る。

    1) JSON っぽい場合:
        {
          "0": [...],
          "edge_31_33": [...],
          ...
        }
       → キーをそのまま entity として使う

    2) それ以外: エッジリストとみなす
        u v
        u v
       → ノード u,v と edge_u_v (昇順) を entity として使う
    """
    text = path.read_text(encoding="utf-8", errors="ignore").lstrip()
    entities: List[str] = []

    # --- JSON 形式かどうか判定 ---
    is_json = False
    if text.startswith("{") or text.startswith("[") or path.suffix in {".json", ".js"}:
        try:
            data: Any = json.loads(text)
            # dict 形式: {"0": [...], "edge_31_33": [...], ...}
            if isinstance(data, dict):
                entities = sorted(data.keys())
                is_json = True
            # list 形式などは今回は想定しない（必要なら拡張）
        except json.JSONDecodeError:
            is_json = False  # パース失敗したら通常のテキスト扱い

    if is_json:
        print(f"[INFO] GRAPH_FILE を JSON エンティティグラフとして解釈: {path}")
        print(f"[INFO] #entities (JSON keys) = {len(entities)}")
        return entities

    # --- ここからは従来どおり「エッジリスト」として扱う ---
    print(f"[INFO] GRAPH_FILE をエッジリストとして解釈: {path}")

    nodes: Set[str] = set()
    edges: Set[str] = set()

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) != 2:
            continue
        u, v = parts[0], parts[1]
        nodes.add(u)
        nodes.add(v)

        # 無向エッジ想定: u,v を昇順にして edge ID を作成
        try:
            a, b = sorted((int(u), int(v)))
            edge_id = f"edge_{a}_{b}"
        except ValueError:
            a, b = sorted((u, v))
            edge_id = f"edge_{a}_{b}"
        edges.add(edge_id)

    entities = sorted(nodes | edges)
    print(
        f"[INFO] #nodes = {len(nodes)}, #edges = {len(edges)}, #entities = {len(entities)}"
    )
    return entities


# ============================================================
# Failure log 解析: entity -> attempts
# ============================================================


def parse_attempts_from_log(path: Path) -> Dict[str, int]:
    attempts: Dict[str, int] = {}
    in_section = False

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "[Controller] Failure rate per entity" in line:
            in_section = True
            continue
        if not in_section:
            continue

        m = FAIL_LINE_RE.match(line)
        if not m:
            continue

        entity = m.group(1)  # "28" or "edge_1_14" など
        total_attempts = int(m.group(3))
        attempts[entity] = total_attempts

    return attempts


# ============================================================
# カバー率
# ============================================================


def compute_coverage(attempts: Dict[str, int], entities: List[str]) -> float:
    """
    カバー率 (Coverage):

      - 分母: GRAPH_FILE から作った全 entity
              (ノード + edge_u_v もしくは JSON のキー)
      - 分子: Failure セクションに一度でも登場した entity
              (失敗率 0% でも attempts > 0 なら訪問済み)

    attempts に存在しない entity は「一度も訪れていない」とみなす。
    """
    if not entities:
        return 0.0

    visited_count = 0
    for e in entities:
        if attempts.get(e, 0) > 0:
            visited_count += 1

    return visited_count / len(entities)


# ============================================================
# Accuracy 用：分布ベクトル & TV
# ============================================================


def attempts_to_distribution(
    attempts: Dict[str, int],
    entities: List[str],
) -> np.ndarray:
    """
    attempts を entities の順に並べて正規化し、確率ベクトルを返す。
    """
    arr = np.array([attempts.get(e, 0) for e in entities], dtype=float)
    s = arr.sum()
    print(f"[DEBUG] attempts_to_distribution: sum = {s}")
    if s == 0:
        return arr
    print(f"[DEBUG] attempts_to_distribution: normalized")
    print(f"[DEBUG]  sample values: {arr[:10]} ...")
    print(f"[DEBUG]  sample normalized: {(arr / s)[:10]} ...")
    return arr / s


def total_variation(p: np.ndarray, q: np.ndarray) -> float:
    return 0.5 * np.sum(np.abs(p - q))


# ============================================================
# ファイル名から (size, alpha, wr, hot) を抽出
# ============================================================


def parse_params_from_filename(path: Path) -> Tuple[int, float, float, float]:
    m = FNAME_RE.search(path.name)
    print(f"[DEBUG] parse_params_from_filename: parsing {path.name}")
    print(m)
    if not m:
        raise ValueError(f"Unexpected filename format: {path.name}")

    size = int(m.group("size"))
    alpha = float(m.group("alpha").replace("_", "."))
    wr = float(m.group("wr").replace("_", "."))
    hot = float(m.group("hot"))

    return size, alpha, wr, hot


# ============================================================
# デバッグ用: entities と attempts の対応状況を出力
# ============================================================


def debug_inspect(entities: List[str], attempts: Dict[str, int], label: str) -> None:
    print(f"\n[DEBUG] === {label} ===")
    print(f"  #entities (from GRAPH_FILE): {len(entities)}")
    print(f"  #attempt entries (from log): {len(attempts)}")

    print("  sample entities (first 10):", entities[:10])
    attempts_keys = list(attempts.keys())
    print("  sample attempts keys (first 10):", attempts_keys[:10])

    ent_set = set(entities)
    att_set = set(attempts.keys())
    inter = ent_set & att_set
    print(f"  #intersection(entities ∩ attempts): {len(inter)}")
    print("  sample intersection (first 10):", list(sorted(inter))[:10])


# ============================================================
# メイン
# ============================================================


def main() -> None:
    # ---- GRAPH_FILE から entity 空間を作る ----
    entities = load_entities_from_graph(GRAPH_FILE)
    print(f"Total entities = {len(entities)}\n")

    # グループごとに attempts & params を格納
    group_labels: List[str] = []
    group_attempts: List[List[Dict[str, int]]] = []
    group_params: List[List[Tuple[int, float, float, float]]] = []

    for group_label, file_list in EXPERIMENT_GROUPS:
        attempts_per_group: List[Dict[str, int]] = []
        params_per_group: List[Tuple[int, float, float, float]] = []

        for f in file_list:
            path = Path(f)
            attempts = parse_attempts_from_log(path)
            attempts_per_group.append(attempts)

            size, alpha, wr, hot = parse_params_from_filename(path)
            params_per_group.append((size, alpha, wr, hot))

        group_labels.append(group_label)
        group_attempts.append(attempts_per_group)
        group_params.append(params_per_group)

    # ---- デバッグ: 最初のログで entity の噛み合いを見る ----
    first_attempts = BASE_LOG and parse_attempts_from_log(Path(BASE_LOG))
    # group_attempts[0][0]
    debug_inspect(entities, first_attempts, "FIRST GROUP / FIRST LOG")

    # ---- accuracy 用の base を決める ----
    # base_attempts = group_attempts[0][0]
    base_attempts = first_attempts
    base_dist = attempts_to_distribution(base_attempts, entities)

    # ---- グループごとの (x, metric) を計算 ----
    all_series_x: List[List[float]] = []
    all_series_y: List[List[float]] = []

    print(f"\nX_MODE = {X_MODE}, Y_MODE = {Y_MODE}\n")

    for g_label, attempts_list, params_list in zip(
        group_labels, group_attempts, group_params
    ):
        xs: List[float] = []
        ys: List[float] = []

        print(f"=== Group: {g_label} ===")

        for attempts, (size, alpha, wr, hot) in zip(attempts_list, params_list):
            # X 軸
            if X_MODE == "size":
                x_val = float(size)
            elif X_MODE == "alpha":
                x_val = alpha
            elif X_MODE == "wr":
                x_val = wr
            elif X_MODE == "hot":
                x_val = hot
            else:
                raise ValueError("X_MODE must be 'size', 'alpha', 'wr', or 'hot'")

            # Y 軸
            if Y_MODE == "coverage":
                metric = compute_coverage(attempts, entities)
            elif Y_MODE == "accuracy":
                dist = attempts_to_distribution(attempts, entities)
                tv = total_variation(base_dist, dist)
                metric = 1.0 - tv
                if metric < 0.0:
                    metric = 0.0
                if metric > 1.0:
                    metric = 1.0
            else:
                raise ValueError("Y_MODE must be 'coverage' or 'accuracy'")

            xs.append(x_val)
            ys.append(metric)

            if Y_MODE == "coverage":
                print(f"x={x_val}: coverage={metric:.4f} ({metric*100:.2f} %)")
            else:
                print(f"x={x_val}: accuracy={metric:.4f}")

        pairs = sorted(zip(xs, ys), key=lambda t: t[0])
        xs_sorted = [p[0] for p in pairs]
        ys_sorted = [p[1] for p in pairs]

        all_series_x.append(xs_sorted)
        all_series_y.append(ys_sorted)
        print()

    # ---- プロット ----
    if Y_MODE == "coverage":
        y_label = "Coverage"
    else:
        y_label = "Accuracy"

    plt.figure(figsize=(10, 6))
    for g_label, xs, ys in zip(group_labels, all_series_x, all_series_y):
        plt.plot(xs, ys, marker="o", label=g_label)

    plt.xlabel(X_MODE.upper())
    plt.ylabel(y_label)
    plt.title(
        f"{GRAPH}_{y_label} vs {X_MODE.upper()} " f"(base = first log of first group)"
    )
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend()
    plt.tight_layout()
    out_name = f"{GRAPH}_{Y_MODE}_vs_{X_MODE}_multi.png"
    plt.savefig(out_name, dpi=200)
    plt.show()
    print(f"Saved plot to {out_name}")


if __name__ == "__main__":
    main()
