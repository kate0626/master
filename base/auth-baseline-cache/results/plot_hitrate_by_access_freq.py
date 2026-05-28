#!/usr/bin/env python3
"""
cache_comparison_hitrate.png の情報を「ノード/エッジが walk 中に何回参照されたか」
で**バケット分解**して可視化する。

各 transition.json には以下のキーがある:
  - authorization_attempts: {node_id: lookup_count} (auth キャッシュへの引きの回数)
  - cache hit / cache miss: 合計値 (per-node の内訳はない)

ここから次を計算する:

  - access_count buckets: [1, 2, 3-5, 6-20, 21-100, 101+]
  - bucket 内ノード数, lookup 合計, **memo 上限 hits = Σ (c-1)**
  - **memo の場合** は実 hits ≈ Σ (c-1) なので per-bucket がそのまま分かる
  - **LRU/ARC** は per-node hits が記録されてないので
    "total achievable" vs "actual total" の比 (= 効率) を全 bucket 共通で示すしかない

出力:
  results/<alpha_walks_capa>/hitrate_by_freq_<graph>.png  (graph ごとに 1 図)
  - 上段: bucket ごとの「lookup の総量」と「memo (=Belady) で得られる hits の総量」
  - 下段: bucket ごとの effective hit_rate（= memo hits / lookups, つまり (c-1)/c の bucket 平均）
          に加えて、各 policy の TOTAL hit_rate を水平線で重ねる
        
python3 base/auth-baseline-cache/results/plot_hitrate_by_access_freq.py \
  --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100        

出力
hitrate_stacked_amazon0601.png


"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# 日本語フォント
_JP_FONTS = [
    "Hiragino Sans",
    "Hiragino Maru Gothic Pro",
    "AppleGothic",
    "Noto Sans CJK JP",
    "IPAGothic",
    "IPAPGothic",
    "TakaoGothic",
]
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break

# ---------------------------------------------------------------------------
# bucket 定義
# ---------------------------------------------------------------------------
BUCKETS = [
    (1, 1, "=1"),
    (2, 2, "=2"),
    (3, 5, "3-5"),
    (6, 20, "6-20"),
    (21, 100, "21-100"),
    (101, None, "101+"),
]


def bucket_of(c: int) -> int:
    for i, (lo, hi, _) in enumerate(BUCKETS):
        if hi is None:
            if c >= lo:
                return i
        elif lo <= c <= hi:
            return i
    return -1


POLICY_ORDER = ["none", "memo", "lru", "arc"]
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
}


# ---------------------------------------------------------------------------
# 1 つの transition.json を集計
# ---------------------------------------------------------------------------
def aggregate_one_json(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    attempts: dict[str, int] = d.get("authorization_attempts", {})
    cache_hit = int(d.get("cache hit", 0))
    cache_miss = int(d.get("cache miss", 0))
    # avg_length は JSON に直接書かれていない。
    # 各 walk ステップで非 edge ノードが 1 つ訪問されるので、
    #   avg_len ≈ (非 edge access の合計) / walks
    walks = max(1, int(d.get("controller", {}).get("walks", 1)))
    node_accesses = sum(
        v for k, v in d.get("access", {}).items() if not k.startswith("edge_")
    )
    avg_len = node_accesses / walks

    # bucket 集計
    n_nodes_b = [0] * len(BUCKETS)
    lookups_b = [0] * len(BUCKETS)
    memo_hits_b = [0] * len(BUCKETS)
    for v, c in attempts.items():
        if c <= 0:
            continue
        b = bucket_of(c)
        if b < 0:
            continue
        n_nodes_b[b] += 1
        lookups_b[b] += c
        memo_hits_b[b] += max(c - 1, 0)
    return {
        "n_nodes_b": n_nodes_b,
        "lookups_b": lookups_b,
        "memo_hits_b": memo_hits_b,
        "cache_hit": cache_hit,
        "cache_miss": cache_miss,
        "avg_len": avg_len,
    }


# ---------------------------------------------------------------------------
# 1 policy ぶん (Length=1 を除外して集約)
# ---------------------------------------------------------------------------
def aggregate_policy_dir(policy_dir: Path) -> dict | None:
    rows = []
    for jf in sorted(policy_dir.glob("start=*_global_transition.json")):
        try:
            r = aggregate_one_json(jf)
        except Exception:
            continue
        if r["avg_len"] > 1.001:
            rows.append(r)
    if not rows:
        return None

    nb = len(BUCKETS)
    agg = {
        "n_nodes_b": [sum(r["n_nodes_b"][i] for r in rows) for i in range(nb)],
        "lookups_b": [sum(r["lookups_b"][i] for r in rows) for i in range(nb)],
        "memo_hits_b": [sum(r["memo_hits_b"][i] for r in rows) for i in range(nb)],
        "cache_hit": sum(r["cache_hit"] for r in rows),
        "cache_miss": sum(r["cache_miss"] for r in rows),
        "n_starts": len(rows),
    }
    total_lookups = agg["cache_hit"] + agg["cache_miss"]
    agg["hit_rate"] = agg["cache_hit"] / total_lookups if total_lookups > 0 else 0.0
    return agg


# ---------------------------------------------------------------------------
# 描画
# ---------------------------------------------------------------------------
def plot_one_graph(graph: str, by_policy: dict, out_path: Path) -> None:
    labels = [b[2] for b in BUCKETS]
    x = np.arange(len(labels))
    width = 0.18

    # 上段: lookups と memo hits を bucket 別に棒で
    # 下段: bucket 別 memo hit_rate (= memo_hits/lookups) + 各 policy の TOTAL hit_rate 線
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), squeeze=False)
    ax_top = axes[0][0]
    ax_bot = axes[1][0]

    # 上段: lookups (帯) を policy ごとに描く (アクセス頻度分布)
    # アクセス頻度分布は seed=42 で policy 不問なはずだが、Length=1 除外で
    # わずかに変動するため、各 policy 平均を取る (ほぼ同じ)
    # シンプルに、いずれかの policy 1 つ (lru 優先) のアクセス分布を代表値にする
    rep = (
        by_policy.get("lru") or by_policy.get("memo") or next(iter(by_policy.values()))
    )
    lookups = rep["lookups_b"]
    memo_hits_total_bucket = rep["memo_hits_b"]  # bucket 別の memo upper hits

    ax_top.bar(x, lookups, color="#e0e0e0", edgecolor="#666", label="total lookups")
    ax_top.bar(
        x,
        memo_hits_total_bucket,
        color="#1f77b4",
        edgecolor="white",
        label="memo (=Belady) ヒット上限 = Σ(c-1)",
    )
    # 数値ラベル
    for xi, lk, mh in zip(x, lookups, memo_hits_total_bucket):
        ax_top.text(
            xi,
            lk + max(lookups) * 0.015,
            f"{lk}\n({mh})",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#333",
        )
    ax_top.set_xticks(x)
    ax_top.set_xticklabels(labels)
    ax_top.set_xlabel("ノード参照頻度 c (= authorization_attempts[v])", fontsize=10)
    ax_top.set_ylabel("lookup 数 (合計, n_starts 個ぶん)", fontsize=10)
    ax_top.set_title(
        f"{graph} — bucket 別 lookups と memo 上限 hits "
        "（数字は lookups/memo_hits）",
        fontsize=11,
        fontweight="bold",
    )
    ax_top.legend(loc="upper right", fontsize=9)
    ax_top.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax_top.set_axisbelow(True)

    # 下段: bucket 別 hit_rate
    # bucket b の hit_rate (memo) = memo_hits_b / lookups_b = (Σ c-1) / Σ c
    bucket_hr_memo = [
        (mh / lk) if lk > 0 else 0.0 for mh, lk in zip(memo_hits_total_bucket, lookups)
    ]
    bar_colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(BUCKETS)))
    ax_bot.bar(
        x,
        bucket_hr_memo,
        color=bar_colors,
        edgecolor="white",
        label="memo (Belady) bucket hit_rate = (c-1)/c の重み付き平均",
    )
    for xi, hr in zip(x, bucket_hr_memo):
        ax_bot.text(
            xi,
            hr + 0.02,
            f"{hr:.2f}",
            ha="center",
            va="bottom",
            fontsize=9,
            color="#1f4e79",
        )

    # 各 policy の TOTAL hit_rate を水平線で重ねる
    for policy in POLICY_ORDER:
        agg = by_policy.get(policy)
        if agg is None:
            continue
        ax_bot.axhline(
            agg["hit_rate"],
            color=POLICY_COLORS[policy],
            linestyle="--",
            linewidth=1.8,
            label=f"{policy} 全体 hit_rate = {agg['hit_rate']:.3f}",
        )

    ax_bot.set_ylim(0, 1.05)
    ax_bot.set_xticks(x)
    ax_bot.set_xticklabels(labels)
    ax_bot.set_xlabel("ノード参照頻度 c", fontsize=10)
    ax_bot.set_ylabel("hit_rate", fontsize=10)
    ax_bot.set_title(
        f"{graph} — bucket 別 memo hit_rate と policy 別の " "全体 hit_rate (水平線)",
        fontsize=11,
        fontweight="bold",
    )
    ax_bot.legend(loc="lower right", fontsize=9, framealpha=0.92)
    ax_bot.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax_bot.set_axisbelow(True)

    fig.suptitle(
        f"hit_rate breakdown by access frequency  ({out_path.parent.name})",
        fontsize=12,
        y=1.005,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# ノード数の度数分布（c 別にノードが何個あるか）
# ---------------------------------------------------------------------------
def plot_node_count_distribution(graph: str, by_policy: dict,
                                  out_path: Path) -> None:
    """各 bucket に **ノードが何個**あるかの棒グラフ。

    上段: bucket 別 n_nodes (棒) + 累積カバー率 (折れ線)
    下段: bucket 別 lookups (= n_nodes × 平均 c, 参考表示)

    policy 不変 (= walk の経路だけで決まる) なので lru を代表として使う。
    """
    rep = (by_policy.get("lru") or by_policy.get("memo")
           or next(iter(by_policy.values())))
    n_nodes_b = rep["n_nodes_b"]
    lookups_b = rep["lookups_b"]
    total_nodes = sum(n_nodes_b)
    total_lookups = sum(lookups_b)
    if total_nodes == 0:
        return

    labels = [b[2] for b in BUCKETS]
    x = np.arange(len(labels))

    fig, axes = plt.subplots(2, 1, figsize=(11, 8), squeeze=False)
    ax_top, ax_bot = axes[0][0], axes[1][0]

    # ===== 上段: ノード数 (色は viridis で頻度バケットを色分け) =====
    bucket_colors = plt.cm.viridis(np.linspace(0.15, 0.9, len(BUCKETS)))
    bars = ax_top.bar(x, n_nodes_b, color=bucket_colors,
                       edgecolor="white", linewidth=0.6)
    # 数値ラベル
    for xi, n, lk in zip(x, n_nodes_b, lookups_b):
        if n == 0: continue
        pct_n  = 100 * n / total_nodes
        ax_top.text(xi, n * 1.04,
                    f"{n:,}\n({pct_n:.1f}% of nodes)",
                    ha="center", va="bottom", fontsize=8.5, color="#222",
                    linespacing=1.15)

    # 累積カバー線 (累積ノード数 / 累積 lookups の両方)
    ax2 = ax_top.twinx()
    cum_n = np.cumsum(n_nodes_b) / total_nodes
    cum_l = np.cumsum(lookups_b) / total_lookups
    ax2.plot(x, cum_n, marker="o", color="#d62728", linewidth=1.6,
              label="累積ノード数比率")
    ax2.plot(x, cum_l, marker="s", color="#1f77b4", linewidth=1.6,
              label="累積 lookups 比率")
    ax2.set_ylim(0, 1.05)
    ax2.set_ylabel("累積比率", fontsize=10, color="#444")
    ax2.legend(loc="center right", fontsize=8.5, framealpha=0.92)
    for xi, cn, cl in zip(x, cum_n, cum_l):
        ax2.text(xi, cn + 0.04, f"{cn:.2f}", fontsize=7, color="#d62728",
                  ha="center")
        ax2.text(xi, cl - 0.06, f"{cl:.2f}", fontsize=7, color="#1f77b4",
                  ha="center")

    ax_top.set_xticks(x)
    ax_top.set_xticklabels(labels, fontsize=10)
    ax_top.set_xlabel("ノード参照回数 c (= authorization_attempts[v])",
                       fontsize=10)
    ax_top.set_ylabel("ノード数", fontsize=10)
    ax_top.set_yscale("log")
    ax_top.set_title(
        f"{graph} — 何回アクセスされたノードが何個あるか "
        f"(全 unique = {total_nodes:,} 個)",
        fontsize=11, fontweight="bold")
    ax_top.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
    ax_top.set_axisbelow(True)

    # ===== 下段: 参考に lookups 分布 (n_nodes × 平均 c) =====
    bars2 = ax_bot.bar(x, lookups_b, color=bucket_colors,
                        edgecolor="white", linewidth=0.6)
    for xi, n, lk in zip(x, n_nodes_b, lookups_b):
        if lk == 0:
            ax_bot.text(xi, 1, "0", ha="center", va="bottom",
                          fontsize=8, color="#666"); continue
        avg_c = lk / max(1, n)
        pct = 100 * lk / total_lookups
        ax_bot.text(xi, lk * 1.04,
                     f"{lk:,}\n({pct:.1f}% of lookups)\n平均c={avg_c:.1f}",
                     ha="center", va="bottom", fontsize=8, color="#222",
                     linespacing=1.15)
    ax_bot.set_xticks(x)
    ax_bot.set_xticklabels(labels, fontsize=10)
    ax_bot.set_xlabel("ノード参照回数 c", fontsize=10)
    ax_bot.set_ylabel("lookups 合計 (n_nodes × c)", fontsize=10)
    ax_bot.set_yscale("log")
    ax_bot.set_title(
        f"{graph} — bucket 別 lookups 合計 (参考)",
        fontsize=11, fontweight="bold")
    ax_bot.yaxis.grid(True, which="both", linestyle="--", alpha=0.3)
    ax_bot.set_axisbelow(True)

    fig.suptitle(
        f"アクセス頻度別ノード数分布 ({out_path.parent.name})",
        fontsize=12, y=1.005)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# policy × bucket 積み上げ棒（hit_rate の内訳）
# ---------------------------------------------------------------------------
def estimate_bucket_hits_per_policy(
    by_policy: dict, policy: str, lookups_b: list, memo_hits_b: list,
    sim_bucket_hits: dict | None = None,
) -> list:
    """各 bucket での「その policy が稼いだ hits」を推定する。

    sim_bucket_hits が与えられたら、その policy の per-bucket hits をそのまま使う
    (= 方式 A, sim 由来の per-bucket。actual total に合うようスケール済み)。
    無ければ方式 B (全 bucket 一様効率) を使う。
    """
    if sim_bucket_hits is not None and policy in sim_bucket_hits:
        return sim_bucket_hits[policy]

    agg = by_policy[policy]
    actual_hits = agg["cache_hit"]
    if policy == "memo":
        return list(memo_hits_b)
    if policy == "none":
        return [0] * len(lookups_b)
    belady_total = sum(memo_hits_b)
    if belady_total <= 0:
        return [0] * len(lookups_b)
    eff = min(1.0, actual_hits / belady_total)
    return [int(round(mh * eff)) for mh in memo_hits_b]


def _compute_lru_bucket_hits_via_sim(graph_dir: Path) -> list | None:
    """LRU を sim して per-bucket hits を返す (実機 actual_lru_total にスケール補正)。
    transition.json の不足 (sim walk が短くなる) を補うため、bucket 比率を保ったまま
    総和を実機 cache_hit に合わせる。"""
    import sys as _sys
    _here = Path(__file__).parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    from plot_capacity_and_lru_buckets import load_chain, simulate_lru, bucket_of

    lru_dir = graph_dir / "lru_100"
    if not lru_dir.is_dir():
        return None
    n_b = len(BUCKETS)
    sim_hits_b = [0] * n_b
    sim_total = 0
    actual_lru_h = 0
    for jf in sorted(lru_dir.glob("start=*_global_transition.json")):
        try:
            ch = load_chain(jf)
        except Exception:
            continue
        if ch["avg_len"] <= 1.001:
            continue
        sim = simulate_lru(ch, 100)
        for v, c in ch["attempts"].items():
            b = bucket_of(c)
            if b < 0: continue
            sim_hits_b[b] += sim["hits_per_node"].get(v, 0)
        sim_total += sim["total_hits"]
        actual_lru_h += ch["cache_hit"]
    if sim_total == 0:
        return None
    scale = actual_lru_h / sim_total
    return [int(round(h * scale)) for h in sim_hits_b]


def plot_stacked_contribution(graph: str, by_policy: dict, out_path: Path,
                                sim_bucket_hits: dict | None = None) -> None:
    """X 軸 = policy, Y 軸 = hit_rate (0..1).
    各バー = bucket 別の「contribution = bucket_hits / total_lookups」を積み上げ。
    memo は per-bucket 正確、他 policy は全 bucket 一様効率の近似 (注釈付き)。"""
    rep = (
        by_policy.get("lru") or by_policy.get("memo") or next(iter(by_policy.values()))
    )
    lookups_b = rep["lookups_b"]
    memo_hits_b = rep["memo_hits_b"]
    total_lookups = sum(lookups_b)
    if total_lookups <= 0:
        return

    policies = [p for p in POLICY_ORDER if p in by_policy]
    n_b = len(BUCKETS)
    bucket_colors = plt.cm.viridis(np.linspace(0.15, 0.9, n_b))
    bucket_labels = [b[2] for b in BUCKETS]

    fig, ax = plt.subplots(figsize=(9, 6))
    x_pos = np.arange(len(policies))
    bar_w = 0.6

    # 棒積み上げ
    bottoms = np.zeros(len(policies))
    for bi, (lo, hi, lab) in enumerate(BUCKETS):
        contrib = []
        for p in policies:
            hits_per_bucket = estimate_bucket_hits_per_policy(
                by_policy, p, lookups_b, memo_hits_b, sim_bucket_hits
            )
            contrib.append(hits_per_bucket[bi] / total_lookups)
        ax.bar(
            x_pos,
            contrib,
            bar_w,
            bottom=bottoms,
            color=bucket_colors[bi],
            edgecolor="white",
            linewidth=0.6,
            label=f"c={lab}",
        )
        # 0.02 以上の塊だけ数値ラベル
        for xi, h, b0 in zip(x_pos, contrib, bottoms):
            if h >= 0.02:
                ax.text(
                    xi,
                    b0 + h / 2,
                    f"{h*100:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if bi >= 2 else "black",
                )
        bottoms += np.array(contrib)

    # actual total hit_rate を棒の上に数字で
    for xi, p in zip(x_pos, policies):
        hr = by_policy[p]["hit_rate"]
        ax.text(
            xi,
            hr + 0.02,
            f"{hr:.3f}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    # 物理下限（c=1 ノード分の lookups は絶対 miss）の line
    physical_ceiling = 1.0 - (lookups_b[0] / total_lookups)
    ax.axhline(
        physical_ceiling,
        color="#d62728",
        linestyle="--",
        linewidth=1.2,
        alpha=0.7,
        label=f"理論上限 = 1 − (c=1 lookups / total) = {physical_ceiling:.3f}",
    )

    ax.set_xticks(x_pos)
    ax.set_xticklabels(policies, fontsize=11)
    ax.set_ylabel("hit_rate (= cache_hit / total_lookups)", fontsize=11)
    ax.set_ylim(0, max(1.05, physical_ceiling + 0.1))
    ax.set_title(
        f"{graph} — hit_rate を「ノード参照頻度 bucket」に分解した内訳\n"
        "(色 = bucket、棒の高さ = そのポリシーの actual hit_rate)",
        fontsize=11,
        fontweight="bold",
    )
    ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        fontsize=9,
        framealpha=0.92,
        title="参照頻度 c",
    )
    note = (
        "memo: per-bucket は厳密 (Σ(c-1))。\n"
        "lru/arc: per-node hits が無いため「全 bucket 同一効率 η=actual/belady」で近似。\n"
        "実際は高頻度 bucket ほど取りやすいので、本図の lru/arc 内訳は\n"
        "下位 bucket を過大評価している可能性がある。"
    )
    ax.text(
        1.02,
        0.02,
        note,
        transform=ax.transAxes,
        fontsize=8,
        verticalalignment="bottom",
        color="#555",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#f5f5f5", edgecolor="#cccccc"),
    )

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    global _ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="例: base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100",
    )
    ap.add_argument(
        "--with-lru-sim",
        action="store_true",
        help="LRU の per-bucket hits を sim から取り (実機 actual_lru_total にスケール補正)、"
             "hitrate_stacked_<graph>_lru_sim.png として書き出す",
    )
    args = ap.parse_args()
    _ARGS = args

    results_dir: Path = args.results_dir
    if not results_dir.is_dir():
        raise SystemExit(f"not a dir: {results_dir}")

    # results_dir/<graph>/<policy>_<cap>/start=*_global_transition.json を順に
    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name
        by_policy: dict[str, dict] = {}
        for pol_dir in graph_dir.iterdir():
            if not pol_dir.is_dir():
                continue
            m = re.match(r"^([a-z]+)_(\d+)$", pol_dir.name)
            if not m:
                continue
            policy = m.group(1)
            agg = aggregate_policy_dir(pol_dir)
            if agg is None:
                continue
            by_policy[policy] = agg

        if not by_policy:
            continue

        # コンソール表示
        print(f"\n=== {graph} ===")
        print(f"{'bucket':>8} | {'lookups':>9} {'memo_hits':>10} {'hit_rate':>9}")
        rep = by_policy.get("lru") or next(iter(by_policy.values()))
        for i, (_, _, lab) in enumerate(BUCKETS):
            lk = rep["lookups_b"][i]
            mh = rep["memo_hits_b"][i]
            hr = mh / lk if lk > 0 else 0
            print(f"{lab:>8} | {lk:>9} {mh:>10} {hr:>9.3f}")
        print(
            f"{'TOTAL':>8} | {sum(rep['lookups_b']):>9} {sum(rep['memo_hits_b']):>10}"
        )
        print(f"  policy actual hit_rate:")
        for p in POLICY_ORDER:
            if p in by_policy:
                print(
                    f"    {p:<6} {by_policy[p]['hit_rate']:.3f} "
                    f"(n_starts={by_policy[p]['n_starts']})"
                )

        out = results_dir / f"hitrate_by_freq_{graph}.png"
        plot_one_graph(graph, by_policy, out)

        # 追加: ノード数の度数分布 (何回アクセスされたノードが何個あるか)
        out_nc = results_dir / f"node_count_by_freq_{graph}.png"
        plot_node_count_distribution(graph, by_policy, out_nc)

        # 追加: policy × bucket 積み上げ棒
        # --with-lru-sim 時は LRU の per-bucket hits を sim から取って scale 補正で挿入
        sim_bucket_hits: dict | None = None
        if getattr(_ARGS, "with_lru_sim", False):
            lru_sim_b = _compute_lru_bucket_hits_via_sim(graph_dir)
            if lru_sim_b is not None:
                sim_bucket_hits = {"lru": lru_sim_b}
                print(f"  [sim] LRU per-bucket hits (scaled to actual): {lru_sim_b}")
        suffix = "_lru_sim" if sim_bucket_hits else ""
        out2 = results_dir / f"hitrate_stacked_{graph}{suffix}.png"
        plot_stacked_contribution(graph, by_policy, out2, sim_bucket_hits)


# argparse は module 側で 1 度だけパースする (グローバル参照用)
_ARGS = None


if __name__ == "__main__":
    main()
