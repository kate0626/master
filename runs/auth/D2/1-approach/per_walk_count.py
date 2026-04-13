# !/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Start 固定の per_walk_access.json を入力として、

- ファイル名から start ノードを自動抽出
- 始点 entity は集計・描画から除外
- RW を K 本まで実行した時点の累積訪問数を計算
- 上位から足して「割合 p%」を占める entity を選択
- 縦棒グラフ（線形スケール）を 1 枚出力（Kとratioが違えば別の図）

追加（Hot基準決めのための比較）:
- K_LIST の最大（例: 100）を “最終(近似)真値” とみなし
- 各K・各ratioについて、以下を出力
  - Jaccard / Precision / Recall（Top集合の一致度）
  - Coverage(final)（K時点のTop集合が最終訪問をどれだけカバーするか）
  - L1(p)（正規化分布のL1距離）
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set

import matplotlib.pyplot as plt

# ============================================================
# ★ 設定（ここだけ触ればよい）
# ============================================================

GRAPH = "fb-caltech-connected"

# 入力ファイル（Start 固定）
INPUT_FILE = Path(f"./{GRAPH}/start=1_walks=100_alpha=0.1_seed=42_per_walk_access.json")

# 出力先ディレクトリ
OUT_DIR = Path(f"runs/auth/D2/1-approach/{GRAPH}")

# RW の離散点（図を作りたい K）
## 例えば、10Rwers時点における訪問ノードのばらつきを見ることができる
K_LIST = [10, 20, 30, 50, 100]

# 上位 entity を決める割合（複数）
TOP_RATIOS = [0.5]  # 好きに増減OK

# ============================================================
# IO
# ============================================================


def load_per_walk_access(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if isinstance(obj, dict) and "per_walk_access" in obj:
        return obj["per_walk_access"]
    if isinstance(obj, list):
        return obj

    raise ValueError("Unexpected JSON format")


# ============================================================
# start ノード抽出
# ============================================================

START_RE = re.compile(r"start=(\d+)")


def extract_start_node_from_filename(path: Path) -> str:
    """
    ファイル名から start=◯ を取り出す。
    返り値は entity と同じ文字列型 ("1" など)
    """
    m = START_RE.search(path.name)
    if not m:
        raise ValueError(f"Cannot find start= in filename: {path.name}")
    return m.group(1)


# ============================================================
# 集計
# ============================================================


def extract_access(walk: dict, start_entity: str) -> Dict[str, int]:
    """
    始点 entity は除外して access を返す
    """
    access = walk.get("access", {})
    out: Dict[str, int] = {}
    for k, v in access.items():
        k = str(k)
        if k == start_entity:
            continue  # ★ 始点を除外
        try:
            out[k] = int(v)
        except Exception:
            out[k] = 0
    return out


def cumulative_counter_until(
    per_walk: List[dict],
    K: int,
    start_entity: str,
) -> Counter:
    """
    RW を 1..K まで実行したときの累積訪問回数（始点除外）
    """
    counter = Counter()
    per_walk_sorted = sorted(per_walk, key=lambda w: int(w.get("walk_index", 0)))

    for walk in per_walk_sorted[:K]:
        counter.update(extract_access(walk, start_entity))

    return counter


def select_top_by_ratio(counter: Counter, ratio: float):
    """
    上位から足していって ratio を超えるまでの entity を返す
    """
    total = sum(counter.values())
    threshold = total * ratio

    selected = []
    acc = 0
    for entity, count in counter.most_common():
        selected.append((entity, count))
        acc += count
        if acc >= threshold:
            break

    return selected


# ============================================================
# 追加：比較指標（100RWer = 最終）関連
# ============================================================


def selected_set(counter: Counter, ratio: float) -> Set[str]:
    return {e for e, _ in select_top_by_ratio(counter, ratio)}


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def precision(pred: Set[str], gold: Set[str]) -> float:
    return 0.0 if not pred else len(pred & gold) / len(pred)


def recall(pred: Set[str], gold: Set[str]) -> float:
    return 0.0 if not gold else len(pred & gold) / len(gold)


def coverage_on_final(sel: Set[str], counter_final: Counter) -> float:
    total = sum(counter_final.values())
    if total == 0:
        return 0.0
    return sum(counter_final.get(e, 0) for e in sel) / total


def l1_distance(counter_a: Counter, counter_b: Counter) -> float:
    """
    正規化して確率分布としての L1距離（0が一致、最大2）
    """
    total_a = sum(counter_a.values())
    total_b = sum(counter_b.values())
    if total_a == 0 or total_b == 0:
        return 2.0

    keys = set(counter_a.keys()) | set(counter_b.keys())
    s = 0.0
    for k in keys:
        pa = counter_a.get(k, 0) / total_a
        pb = counter_b.get(k, 0) / total_b
        s += abs(pa - pb)
    return s


# ============================================================
# 描画
# 青色の棒グラフ、訪問回数がどの程度に達するのかのグラフ
# ============================================================


def plot_vertical_bar(
    entities: List[str],
    counts: List[int],
    title: str,
    out_path: Path,
):
    plt.figure(figsize=(max(6, len(entities) * 0.4), 6))
    plt.bar(entities, counts)
    plt.ylabel("Cumulative visit count")
    plt.xlabel("Entity (sorted by visits, start excluded)")
    plt.title(title)
    plt.xticks(rotation=90)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


# ============================================================
# main
# ============================================================


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    per_walk = load_per_walk_access(INPUT_FILE)

    start_entity = extract_start_node_from_filename(INPUT_FILE)
    print(f"[INFO] excluded start entity = {start_entity}")

    base_label = INPUT_FILE.stem.replace("_per_walk_access", "")

    # --- ★追加：Kごとのcounterを先に全部作る（最大Kを最終(近似)真値として比較に使う） ---
    counters_by_k: Dict[int, Counter] = {}
    for K in K_LIST:
        counters_by_k[K] = cumulative_counter_until(
            per_walk,
            K,
            start_entity=start_entity,
        )

    K_FINAL = max(K_LIST)
    counter_final = counters_by_k[K_FINAL]
    print(f"[INFO] K_FINAL (as final reference) = {K_FINAL}")
    # -------------------------------------------------------------------------------

    for K in K_LIST:
        counter = counters_by_k[K]

        for ratio in TOP_RATIOS:
            selected = select_top_by_ratio(counter, ratio)

            if not selected:
                print(f"[WARN] no entities selected for K={K}, ratio={ratio}")
                continue

            # --- ★追加：100(=K_FINAL) と比較指標を出す ---
            S_k = {e for e, _ in selected}
            S_final = selected_set(counter_final, ratio)

            jac = jaccard(S_k, S_final)
            pre = precision(S_k, S_final)
            rec = recall(S_k, S_final)
            cov = coverage_on_final(S_k, counter_final)
            l1 = l1_distance(counter, counter_final)

            print(
                f"[METRIC] K={K:>3} ratio={ratio:.2f} | "
                f"|S_k|={len(S_k):>4} | "
                f"Jaccard={jac:.3f} Prec={pre:.3f} Rec={rec:.3f} "
                f"Coverage(final)={cov:.3f} L1(p)={l1:.3f}"
            )
            # ---------------------------------------------

            entities = [e for e, _ in selected]
            counts = [c for _, c in selected]

            title = (
                f"{base_label} | RW={K} | "
                f"Top={int(ratio*100)}% | start excluded ({start_entity})"
            )

            out_path = OUT_DIR / f"{base_label}_RW{K}_top{int(ratio*100)}_no_start.png"

            plot_vertical_bar(
                entities,
                counts,
                title,
                out_path,
            )

            print(f"[OK] saved: {out_path}")


if __name__ == "__main__":
    main()


# ここまで青棒、ここからjaccardを出すためのCSVを出す 一旦こちらは使用しない
# -------------------------------------------------------------------------------------------------------------------------------------
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# from __future__ import annotations

# import csv
# import json
# import re
# from collections import Counter
# from pathlib import Path
# from typing import Dict, List, Set, Tuple

# import matplotlib.pyplot as plt

# # ============================================================
# # ★ 設定（ここだけ触ればよい）
# # ============================================================

# GRAPH = "karate"

# # 入力ファイル（Start 固定）
# # INPUT_FILE = Path(
# #     f"./0.3/{GRAPH}/start=1_walks=100_alpha=0.1_seed=42_per_walk_access.json"
# # )

# BASE_DIR = Path(f"./0.3/{GRAPH}")

# try:
#     INPUT_FILE = next(BASE_DIR.glob("start=*.json"))
#     print(INPUT_FILE)
# except StopIteration:
#     raise FileNotFoundError(f"No file starting with 'start=' found in {BASE_DIR}")

# # 出力先ディレクトリ
# OUT_DIR = Path(f"./0.3/{GRAPH}/")

# # RW の離散点（図を作りたい K）
# K_LIST = [10, 50, 100]

# # 上位 entity を決める割合（複数）
# TOP_RATIOS = [0.5]  # 好きに増減OK

# # メトリクスCSV（全K×ratioを1つにまとめる）
# CSV_PATH = OUT_DIR / "metrics_summary.csv"


# # CSV_PATH = f"runs/auth/D2/1-approach/{GRAPH}/metrics_summary.csv"


# # ============================================================
# # IO
# # ============================================================


# def load_per_walk_access(path: Path) -> List[dict]:
#     with path.open("r", encoding="utf-8") as f:
#         obj = json.load(f)

#     if isinstance(obj, dict) and "per_walk_access" in obj:
#         return obj["per_walk_access"]
#     if isinstance(obj, list):
#         return obj

#     raise ValueError("Unexpected JSON format")


# # ============================================================
# # start ノード抽出
# # ============================================================

# START_RE = re.compile(r"start=(\d+)")


# def extract_start_node_from_filename(path: Path) -> str:
#     m = START_RE.search(path.name)
#     if not m:
#         raise ValueError(f"Cannot find start= in filename: {path.name}")
#     return m.group(1)


# # ============================================================
# # 集計
# # ============================================================


# def extract_access(walk: dict, start_entity: str) -> Dict[str, int]:
#     access = walk.get("access", {})
#     out: Dict[str, int] = {}
#     for k, v in access.items():
#         k = str(k)
#         if k == start_entity:
#             continue  # ★ 始点を除外
#         try:
#             out[k] = int(v)
#         except Exception:
#             out[k] = 0
#     return out


# def cumulative_counter_until(
#     per_walk: List[dict], K: int, start_entity: str
# ) -> Counter:
#     counter = Counter()
#     per_walk_sorted = sorted(per_walk, key=lambda w: int(w.get("walk_index", 0)))
#     for walk in per_walk_sorted[:K]:
#         counter.update(extract_access(walk, start_entity))
#     return counter


# def select_top_by_ratio(counter: Counter, ratio: float) -> List[Tuple[str, int]]:
#     total = sum(counter.values())
#     threshold = total * ratio

#     selected: List[Tuple[str, int]] = []
#     acc = 0
#     for entity, count in counter.most_common():
#         selected.append((entity, count))
#         acc += count
#         if acc >= threshold:
#             break
#     return selected


# # ============================================================
# # メトリクス（100RWer = 最終）関連
# # ============================================================


# def selected_set(counter: Counter, ratio: float) -> Set[str]:
#     return {e for e, _ in select_top_by_ratio(counter, ratio)}


# def jaccard(a: Set[str], b: Set[str]) -> float:
#     if not a and not b:
#         return 1.0
#     if not a or not b:
#         return 0.0
#     return len(a & b) / len(a | b)


# def precision(pred: Set[str], gold: Set[str]) -> float:
#     return 0.0 if not pred else len(pred & gold) / len(pred)


# def recall(pred: Set[str], gold: Set[str]) -> float:
#     return 0.0 if not gold else len(pred & gold) / len(gold)


# def coverage_on_final(sel: Set[str], counter_final: Counter) -> float:
#     total = sum(counter_final.values())
#     if total == 0:
#         return 0.0
#     return sum(counter_final.get(e, 0) for e in sel) / total


# def l1_distance(counter_a: Counter, counter_b: Counter) -> float:
#     total_a = sum(counter_a.values())
#     total_b = sum(counter_b.values())
#     if total_a == 0 or total_b == 0:
#         return 2.0
#     keys = set(counter_a.keys()) | set(counter_b.keys())
#     s = 0.0
#     for k in keys:
#         pa = counter_a.get(k, 0) / total_a
#         pb = counter_b.get(k, 0) / total_b
#         s += abs(pa - pb)
#     return s


# # ============================================================
# # 描画（★図の下にメトリクス文字列を埋め込む）
# # ============================================================


# def plot_vertical_bar(
#     entities: List[str],
#     counts: List[int],
#     title: str,
#     footer_text: str,
#     out_path: Path,
# ):
#     # 図を大きめにし、下部にテキスト領域を確保
#     fig = plt.figure(figsize=(max(6, len(entities) * 0.4), 6.8))
#     ax = fig.add_subplot(111)

#     ax.bar(entities, counts)
#     ax.set_ylabel("Cumulative visit count")
#     ax.set_xlabel("Entity (sorted by visits, start excluded)")
#     ax.set_title(title)
#     ax.tick_params(axis="x", labelrotation=90)

#     # ★フッター（図の下にメトリクスを表示）
#     # 改行したい場合は footer_text に "\n" を入れてOK
#     fig.text(
#         0.5,
#         0.01,
#         footer_text,
#         ha="center",
#         va="bottom",
#         fontsize=8,
#     )

#     # 下にテキストが入る分、rectで余白を残す
#     fig.tight_layout(rect=[0, 0.05, 1, 1])

#     fig.savefig(out_path, dpi=200)
#     plt.close(fig)


# # ============================================================
# # main
# # ============================================================


# def main():
#     OUT_DIR.mkdir(parents=True, exist_ok=True)

#     per_walk = load_per_walk_access(INPUT_FILE)
#     start_entity = extract_start_node_from_filename(INPUT_FILE)
#     print(f"[INFO] excluded start entity = {start_entity}")

#     base_label = INPUT_FILE.stem.replace("_per_walk_access", "")

#     # Kごとのcounterを先に全部作る
#     counters_by_k: Dict[int, Counter] = {}
#     for K in K_LIST:
#         counters_by_k[K] = cumulative_counter_until(
#             per_walk, K, start_entity=start_entity
#         )

#     K_FINAL = max(K_LIST)
#     counter_final = counters_by_k[K_FINAL]
#     print(f"[INFO] K_FINAL (as final reference) = {K_FINAL}")

#     # --- ★CSVにまとめて保存するため、全行を貯める ---
#     csv_rows: List[Dict[str, object]] = []

#     for K in K_LIST:
#         counter = counters_by_k[K]

#         for ratio in TOP_RATIOS:
#             selected = select_top_by_ratio(counter, ratio)
#             if not selected:
#                 print(f"[WARN] no entities selected for K={K}, ratio={ratio}")
#                 continue

#             S_k = {e for e, _ in selected}
#             S_final = selected_set(counter_final, ratio)

#             jac = jaccard(S_k, S_final)
#             pre = precision(S_k, S_final)
#             rec = recall(S_k, S_final)
#             cov = coverage_on_final(S_k, counter_final)
#             l1 = l1_distance(counter, counter_final)

#             metric_line = (
#                 f"K={K} ratio={ratio:.2f} | "
#                 f"|S_k|={len(S_k)} | "
#                 f"Jaccard={jac:.3f} Prec={pre:.3f} Rec={rec:.3f} "
#                 f"Coverage(final)={cov:.3f} L1(p)={l1:.3f}"
#             )

#             print(f"[METRIC] {metric_line}")

#             entities = [e for e, _ in selected]
#             counts = [c for _, c in selected]

#             title = (
#                 f"{base_label} | RW={K} | "
#                 f"Top={int(ratio*100)}% | start excluded ({start_entity})"
#             )

#             out_path = OUT_DIR / f"{base_label}_RW{K}_top{int(ratio*100)}_no_start.png"

#             # ★図の下にメトリクス埋め込み
#             plot_vertical_bar(
#                 entities=entities,
#                 counts=counts,
#                 title=title,
#                 footer_text=metric_line,  # ここが図の下に入る
#                 out_path=out_path,
#             )

#             print(f"[OK] saved: {out_path}")

#             # ★CSV行を追加
#             csv_rows.append(
#                 {
#                     "graph": GRAPH,
#                     "input_file": str(INPUT_FILE),
#                     "start_entity": start_entity,
#                     "K_final": K_FINAL,
#                     "K": K,
#                     "ratio": ratio,
#                     "Sk_size": len(S_k),
#                     "Sfinal_size": len(S_final),
#                     "jaccard": jac,
#                     "precision": pre,
#                     "recall": rec,
#                     "coverage_on_final": cov,
#                     "l1_distance": l1,
#                     "png_path": str(out_path),
#                 }
#             )

#     # --- ★CSV書き出し（全K×ratioを1ファイル） ---
#     if csv_rows:
#         fieldnames = list(csv_rows[0].keys())
#         with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
#             w = csv.DictWriter(f, fieldnames=fieldnames)
#             w.writeheader()
#             w.writerows(csv_rows)

#         print(f"[OK] saved CSV: {CSV_PATH}")
#     else:
#         print("[WARN] no csv rows; CSV not written.")


# if __name__ == "__main__":
#     main()
