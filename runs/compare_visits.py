#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple, Any, Optional, List


def load_access_counts(path: Path) -> Dict[int, int]:
    """
    JSON ログから access (訪問回数) を取り出し、ノードID(int)のみ返す。
    edge_x_x などのエッジエントリは除外する。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = data.get("access", {})
    out: Dict[int, int] = {}
    for k, v in counts.items():
        if isinstance(k, str) and k.startswith("edge_"):
            continue
        try:
            node = int(k)
        except Exception:
            continue
        try:
            out[node] = int(v)
        except Exception:
            continue
    return out


def compute_ratios(
    base_counts: Dict[int, int], target_counts: Dict[int, int]
) -> Dict[str, Any]:
    """
    base を分母、target を分子としてノードごとの ratio を計算。
    戻り値に per-node ratio と集計値を含める。
    """
    ratio_sum = 0.0
    abs_offset_sum = 0.0
    compared = 0
    zero_base = 0
    per_node_ratio: Dict[int, float] = {}

    all_nodes = set(base_counts.keys()) | set(target_counts.keys())
    for node in sorted(all_nodes):
        base = base_counts.get(node, 0)
        target = target_counts.get(node, 0)
        if base <= 0:
            if target > 0:
                zero_base += 1
            continue
        ratio = target / base
        per_node_ratio[node] = ratio
        ratio_sum += ratio
        abs_offset_sum += abs(ratio - 1.0)
        compared += 1

    summary = {
        "sum_ratio": ratio_sum,
        "sum_abs_offset": abs_offset_sum,
        "compared_nodes": compared,
        "zero_base_nodes": zero_base,
        "mean_ratio": (ratio_sum / compared) if compared else None,
        "mean_abs_offset": (abs_offset_sum / compared) if compared else None,
    }
    return {"per_node_ratio": per_node_ratio, "summary": summary}


def save_json(
    base_path: Path,
    target_path: Path,
    base_counts: Dict[int, int],
    target_counts: Dict[int, int],
    ratios: Dict[int, float],
    summary: Dict[str, Any],
    out_path: Path,
) -> None:
    payload = {
        "base_file": str(base_path),
        "target_file": str(target_path),
        "base_access": base_counts,
        "target_access": target_counts,
        "ratio_per_node": {str(k): v for k, v in ratios.items()},
        "summary": summary,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote summary JSON to {out_path}")


def plot_ratios(
    ratios: Dict[int, float],
    out_path: Path,
    title: Optional[str] = None,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed; skip plotting.")
        return

    if not ratios:
        print("No ratios to plot (empty). Skipping figure.")
        return

    nodes = sorted(ratios.keys())
    values = [ratios[n] for n in nodes]

    plt.figure(figsize=(12, 4))
    plt.scatter(nodes, values, s=20, alpha=0.7, label="target/base")
    plt.axhline(1.0, color="red", linestyle="--", linewidth=1, label="ratio=1")
    plt.xlabel("Node ID")
    plt.ylabel("Visit ratio (target / base)")
    plt.yscale("log") if any(v > 10 for v in values) else None
    if title:
        plt.title(title)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    print(f"Saved ratio plot to {out_path}")
    plt.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ノードごとに (target / base) の比率を計算して合計する比較ツール"
    )
    parser.add_argument("--base", help="基準とする JSON ログ (access を分母にする)")
    parser.add_argument("--target", help="比較対象の JSON ログ (access を分子にする)")
    parser.add_argument("--out-json", help="結果サマリを JSON で保存するパス")
    parser.add_argument("--plot", help="比率の散布図を保存するパス (例: ratio.png)")
    args = parser.parse_args()

    # ここで事前設定をまとめておける
    PRESET_COMPARISONS: List[Dict[str, str]] = [
        {
            "base": "100_0.01_global_transition.json",
            "target": "[testing]100_0.01_global_transition.json",
            "out_json": "runs/preset_compare_100_0.01.json",
            "plot": "runs/preset_compare_100_0.01.png",
        },
        # 追加したい場合はこの形式で辞書を足す
        # {"base": "...", "target": "...", "out_json": "...", "plot": "..."},
    ]

    def run_one(base_path: Path, target_path: Path, out_json: Optional[str], plot: Optional[str]) -> None:
        if not base_path.exists():
            print(f"[Skip] Base file not found: {base_path}")
            return
        if not target_path.exists():
            print(f"[Skip] Target file not found: {target_path}")
            return

        base_counts = load_access_counts(base_path)
        target_counts = load_access_counts(target_path)

        result = compute_ratios(base_counts, target_counts)
        ratios = result["per_node_ratio"]
        summary = result["summary"]

        compared = summary["compared_nodes"]
        zero_base = summary["zero_base_nodes"]
        mean_ratio = summary["mean_ratio"]
        mean_abs = summary["mean_abs_offset"]

        print(f"Compared nodes       : {compared}")
        print(f"Base=0 but target>0  : {zero_base} (除外済み)")
        if compared == 0:
            print("No comparable nodes (base access is zero everywhere).")
            return
        print(f"Sum of ratios        : {summary['sum_ratio']:.6f}")
        print(f"Mean ratio           : {mean_ratio:.6f}" if mean_ratio is not None else "Mean ratio           : N/A")
        print(f"Sum |ratio-1|        : {summary['sum_abs_offset']:.6f}")
        print(f"Mean |ratio-1|       : {mean_abs:.6f}" if mean_abs is not None else "Mean |ratio-1|       : N/A")

        if out_json:
            save_json(base_path, target_path, base_counts, target_counts, ratios, summary, Path(out_json))

        if plot:
            title = f"target/base visits ({target_path.name} / {base_path.name})"
            plot_ratios(ratios, Path(plot), title=title)

    # 単発指定があればそれを優先
    if args.base and args.target:
        run_one(Path(args.base), Path(args.target), args.out_json, args.plot)
        return

    # 未指定ならプリセットを一括実行
    if not PRESET_COMPARISONS:
        raise SystemExit("No base/target specified and PRESET_COMPARISONS is empty.")

    for entry in PRESET_COMPARISONS:
        run_one(
            Path(entry["base"]),
            Path(entry["target"]),
            entry.get("out_json"),
            entry.get("plot"),
        )


if __name__ == "__main__":
    main()
