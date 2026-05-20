#!/usr/bin/env python3
"""
auth-cache-bfs-degree の実験結果を可視化する。

- グラフの種類ごとに別ファイルで出力
- 縦軸: 認可時間 (authorization_time_sum / start_nodes)
- 横軸: キャッシュポリシー（baseline memo/lru も棒として含む）
- lru が bfs-degree 結果にない場合は baseline から名前前方一致で補完
- 棒グラフ上にNONE比の削減率をアノテーション
- 図の下にmemoとの定量比較テキストを表示
"""

import re
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

BFS_DEGREE_RESULTS = Path(
    "./../auth-cache-bfs-degree/results/alpha0.01_walks100_capa100"
)
# baseline のフォルダ候補（alpha=0.01 を優先して探す）
BASELINE_ROOTS = [
    Path("./../auth-baseline-cache/results/alpha0.01_walks_1000_capa_100"),
    Path("base/auth-baseline-cache/results"),  # karate など直下に置かれているケース
]
NUM_START_NODES = 5

POLICY_ORDER = ["none", "lru", "bfs-lru", "degree-lru", "hybrid-lru"]
POLICY_COLORS = {
    "none": "#b0b0b0",
    "lru": "#6baed6",
    "bfs-lru": "#2171b5",
    "degree-lru": "#41ab5d",
    "hybrid-lru": "#d62728",
    "memo": "#f4a460",
}


# ---------------------------------------------------------------------------
# パーサ
# ---------------------------------------------------------------------------


def parse_summary_log(path: Path) -> dict:
    """all_policies_summary.log → {policy: {"ctrl": s, "auth": s}}"""
    data = {}
    pat = re.compile(
        r"\[SUMMARY\] policy=(\S+) "
        r"controller_duration_sum=([\d.]+)s "
        r"authorization_time_sum=([\d.]+)s"
    )
    with open(path) as f:
        for line in f:
            m = pat.search(line)
            if m:
                data[m.group(1)] = {
                    "ctrl": float(m.group(2)),
                    "auth": float(m.group(3)),
                }
    return data


def parse_log_sum(log_path: Path) -> dict:
    """*.log から Total authorization/walk time 行を集計する。
    walk_time < 1s のスタートノード（Avg length=1.0 の外れ値）は除外し、
    有効なスタートノード数で割った平均を返す。
    """
    pat_start = re.compile(r"\[START_NODE\]")
    pat_auth  = re.compile(r"Total authorization time \(sum over all servers\):\s+([\d.]+)")
    pat_ctrl  = re.compile(r"Total walk time \(sum over all servers\):\s+([\d.]+)")

    # スタートノードごとに (ctrl, auth) を収集
    runs: list[tuple[float, float]] = []
    cur_ctrl = cur_auth = None

    with open(log_path) as f:
        for line in f:
            if pat_start.search(line):
                if cur_ctrl is not None:
                    runs.append((cur_ctrl, cur_auth or 0.0))
                cur_ctrl = cur_auth = None
                continue
            m = pat_ctrl.search(line)
            if m and cur_ctrl is None:
                cur_ctrl = float(m.group(1))
            m = pat_auth.search(line)
            if m and cur_auth is None:
                cur_auth = float(m.group(1))

    if cur_ctrl is not None:
        runs.append((cur_ctrl, cur_auth or 0.0))

    # walk_time >= 1s のスタートノードのみ使用
    valid = [(c, a) for c, a in runs if c >= 1.0]
    if not valid:
        return {}

    n = len(valid)
    return {"ctrl": sum(c for c, _ in valid), "auth": sum(a for _, a in valid), "n": n}


def _baseline_graph_name(graph: str) -> str:
    """bfs-degree のグラフ名から baseline 側の対応グラフ名を求める。

    ルール: 末尾の "-<数字>" を除去し、baseline 側に同名フォルダがあればそれを返す。
    なければ先頭3文字以上が一致する baseline フォルダを探す。
    """
    # まず末尾の "-数字" を除去した名前を試みる
    stripped = re.sub(r"-\d+$", "", graph)

    for root in BASELINE_ROOTS:
        if not root.is_dir():
            continue
        # 完全一致（stripped）
        if (root / stripped).is_dir():
            return stripped
        # 先頭3文字以上前方一致するフォルダを探す
        prefix = stripped[:3]
        for candidate in root.iterdir():
            if candidate.is_dir() and candidate.name.startswith(prefix):
                return candidate.name

    return stripped


def _find_local_policy_data(graph: str, policy: str) -> dict | None:
    """同じ実験結果の base/<policy>_100/<graph>.log からデータを取得する。"""
    log_path = BFS_DEGREE_RESULTS / graph / "base" / f"{policy}_100" / f"{graph}.log"
    if log_path.exists():
        return parse_log_sum(log_path)
    return None


def _find_baseline_policy_data(graph: str, policy: str) -> dict | None:
    """baseline フォルダから指定ポリシーの時間データを取得する。

    1. summary log に policy が含まれていればそれを使う
    2. なければ <policy>_100/<graph>.log をパースして集計する
    """
    base_name = _baseline_graph_name(graph)

    for root in BASELINE_ROOTS:
        base_dir = root / base_name
        if not base_dir.is_dir():
            continue

        # サマリーから取得
        summary = base_dir / "all_policies_summary.log"
        if summary.exists():
            d = parse_summary_log(summary)
            if policy in d:
                return d[policy]

        # ログファイルから集計
        log_path = base_dir / f"{policy}_100" / f"{base_name}.log"
        if log_path.exists():
            result = parse_log_sum(log_path)
            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# 1グラフ分の描画
# ---------------------------------------------------------------------------


def plot_graph(graph: str, data: dict, out_dir: Path) -> None:
    # 全ポリシーを bfs-degree 結果 → なければ baseline の順で補完
    resolved = dict(data)  # bfs-degree 側の生データ
    from_baseline: dict[str, bool] = {}

    for policy in POLICY_ORDER:
        if policy in resolved:
            from_baseline[policy] = False
        else:
            local = _find_local_policy_data(graph, policy)
            if local:
                resolved[policy] = local
                from_baseline[policy] = False
            else:
                bl = _find_baseline_policy_data(graph, policy)
                if bl:
                    resolved[policy] = bl
                    from_baseline[policy] = True

    # memo は常に baseline から取得（bfs-degree では実行しない）
    memo_data = _find_baseline_policy_data(graph, "memo")
    if memo_data:
        from_baseline["memo"] = True

    def _per(d: dict) -> float:
        """合計値を有効スタートノード数で割って1ノードあたりの平均を返す。"""
        return d["ctrl"] / d.get("n", NUM_START_NODES)

    policies = [p for p in POLICY_ORDER if p in resolved]
    memo_per = _per(memo_data) if memo_data else None

    # memo を末尾の棒として追加
    all_labels = policies + (["memo"] if memo_per is not None else [])
    all_values = [_per(resolved[p]) for p in policies]
    if memo_per is not None:
        all_values.append(memo_per)

    none_per = _per(resolved["none"]) if "none" in resolved else None

    fig = plt.figure(figsize=(8, 7.5))
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.2], hspace=0.45)
    ax = fig.add_subplot(gs[0])
    ax_txt = fig.add_subplot(gs[1])
    ax_txt.axis("off")

    x = np.arange(len(all_labels))
    colors = [POLICY_COLORS.get(lbl, "#888888") for lbl in all_labels]

    def _hatch(lbl):
        return "..." if from_baseline.get(lbl, False) else ""

    bars = []
    for xi, (val, col, lbl) in enumerate(zip(all_values, colors, all_labels)):
        b = ax.bar(
            xi,
            val,
            color=col,
            width=0.62,
            edgecolor="white",
            linewidth=0.6,
            hatch=_hatch(lbl),
        )
        bars.append(b[0])

    # アノテーション（none 比削減率）
    y_pad = max(all_values) * 0.015
    for bar, label, val in zip(bars, all_labels, all_values):
        top = bar.get_height()
        if label == "none":
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + y_pad,
                "base",
                ha="center",
                va="bottom",
                fontsize=8.5,
                color="#666666",
            )
        elif label == "memo":
            txt = (
                f"[memo(b)]\n-{(none_per - val)/none_per*100:.1f}%"
                if none_per
                else "[memo(b)]"
            )
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + y_pad,
                txt,
                ha="center",
                va="bottom",
                fontsize=7.5,
                color="#8B4513",
                style="italic",
            )
        elif none_per is not None:
            red = (none_per - val) / none_per * 100
            suffix = "(b)" if from_baseline.get(label, False) else ""
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + y_pad,
                f"{'v' if red > 0 else '^'}{abs(red):.1f}%{suffix}",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#1a7a1a" if red > 0 else "#cc0000",
                fontweight="bold",
            )

    # タイトル・軸
    ax.set_title(graph, fontsize=13, fontweight="bold", pad=8)
    ax.set_xticks(x)
    tick_labels = [
        f"{lbl}(b)" if from_baseline.get(lbl, False) else lbl for lbl in all_labels
    ]
    ax.set_xticklabels(tick_labels, rotation=30, ha="right", fontsize=9.5)
    ax.set_ylabel("Walk time / start_node (s)", fontsize=9.5)
    ax.yaxis.grid(True, linestyle="--", alpha=0.35, color="#cccccc")
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # 凡例注記: baseline から補完したポリシーを列挙
    baseline_labels = [lbl for lbl in all_labels if from_baseline.get(lbl, False)]
    if baseline_labels:
        note = "(b): from baseline — " + ", ".join(baseline_labels)
        ax.text(
            0.98,
            0.98,
            note,
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=7.5,
            color="#555555",
            style="italic",
        )

    # 下部テキスト: memo 比較
    lines = []
    if memo_per and none_per:
        memo_red = (none_per - memo_per) / none_per * 100
        lines.append(f"[baseline memo(b)] {memo_per:.2f}s  (vs none: -{memo_red:.1f}%)")
        lines.append("-" * 52)
        for p in policies:
            if p == "none":
                continue
            p_per = _per(resolved[p])
            p_red = (none_per - p_per) / none_per * 100
            gap = (p_per - memo_per) / memo_per * 100
            b_mark = "(b)" if from_baseline.get(p, False) else "   "
            lines.append(
                f"  {p:<12}{b_mark}  "
                f"vs none:{'v' if p_red>0 else '^'}{abs(p_red):.1f}%   "
                f"vs memo:{'+' if gap>0 else ''}{gap:.1f}%"
            )
    elif memo_per and not none_per:
        lines.append(f"[baseline memo(b)] {memo_per:.2f}s  (no 'none' data to compare)")
    else:
        lines.append("(no baseline memo data)")

    ax_txt.text(
        0.5,
        0.97,
        "\n".join(lines),
        transform=ax_txt.transAxes,
        ha="center",
        va="top",
        fontsize=8.5,
        fontfamily="monospace",
        color="#222222",
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor="#f7f7f7",
            edgecolor="#bbbbbb",
            alpha=0.9,
        ),
    )

    fig.suptitle(
        "Cache Strategy — Walk Time\n"
        "(auth-cache-bfs-degree, a=0.01, walks=100, cap=100)",
        fontsize=11,
        y=1.01,
    )

    out_path = out_dir / f"time_{graph}.png"
    plt.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    baseline_list = ", ".join(baseline_labels) if baseline_labels else "none"
    print(f"Saved → {out_path}  [from_baseline: {baseline_list}]")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def _find_summary_log(graph_dir: Path) -> Path | None:
    """サマリーログをフラット配置 or base/ サブディレクトリから探す。"""
    direct = graph_dir / "all_policies_summary.log"
    if direct.exists():
        return direct
    nested = graph_dir / "base" / "all_policies_summary.log"
    if nested.exists():
        return nested
    return None


def main():
    entries = []
    for p in BFS_DEGREE_RESULTS.iterdir():
        if not p.is_dir():
            continue
        summary = _find_summary_log(p)
        if summary:
            entries.append((p.name, summary))
    entries.sort()

    for graph, summary_path in entries:
        data = parse_summary_log(summary_path)
        plot_graph(graph, data, BFS_DEGREE_RESULTS)


if __name__ == "__main__":
    main()
