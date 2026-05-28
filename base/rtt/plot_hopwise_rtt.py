#!/usr/bin/env python3
"""
Phase 3: ホップ別不均一 RTT モデル

研究MTGで議論された「最初のホップは局所性が高い→ホップを重ねるとグローバル平均に収束」
という挙動を、ホップ k 依存の混合分布で表現する。

----------------------------------------------------------------------
モデル: 幾何収束 (geometric convergence)
----------------------------------------------------------------------
  w_k[c] = w_inf[c] + (w_0[c] - w_inf[c]) * rho^(k-1)
    c          : カテゴリ (local / regional / global)
    w_0        : 1 hop 目の混合比率 (局所性が反映される初期分布)
    w_inf      : 収束先 (グローバル平均, default = (1/3, 1/3, 1/3))
    rho        : 収束速度 (0 < rho < 1, 小さいほど早く収束)
    k          : ホップ番号 (1, 2, 3, ...)

  各 hop での期待 RTT:
    E[RTT_k] = Σ_c w_k[c] * RTT_c

  各 hop k での期待コスト (auth 含む):
    cost_k = (1 + miss_rate) × E[RTT_k]
    miss_rate = auth_calls / hop_count
    (キャッシュミス時に追加の auth 通信が同じ hop 位置で発生する想定)

  ウォーク中の累積 sim 時間:
    sim_total = (1 + miss_rate) × Σ_{k=1..N} E[RTT_k]

  → N が十分大きいと  E[RTT_k] → E[RTT_inf] = Σ_c w_inf[c] × RTT_c
    つまりホップが増えるほど "グローバル平均" に近づく。
  → miss_rate (= cache 効果) も sim_time に比例的に効く。

----------------------------------------------------------------------
6 シナリオ — Phase 2 の 3 パターンを w_∞ (収束先) として、各 2 種類の漸近パス
----------------------------------------------------------------------
  ★ 設計方針:
     Phase 2 で定めた 3 つの混合比率を「最終的に収束する分布 (w_∞)」とし、
     そこへ「どこからスタートして」「どのくらいの速度で」収束するか
     という 2 軸でシナリオを増やす。最終的に問いたいのは:
        「どの収束先・どの漸近パスのとき、何 hop 目までキャッシュすれば
         十分なのか?」

  ---- パターンA: 収束先 w_∞ = (近0.70 / 中0.20 / 遠0.10)  E[RTT_∞]=39ms ----
    A1. 国内深部 → A緩収束    (Deep-local → A, very slow)
        w_0 = (近0.95 / 中0.04 / 遠0.01), ρ = 0.92
        例: 国内地域コミュニティが徐々に外部接続を獲得していくケース
    A2. 国際スタート → A中速収束 (Global → A, moderate pullback)
        w_0 = (近0.10 / 中0.30 / 遠0.60), ρ = 0.80
        例: 海外発サービスが国内中心の利用形態に集約されるケース

  ---- パターンB: 収束先 w_∞ = (近0.30 / 中0.50 / 遠0.20)  E[RTT_∞]=73ms ----
    B1. 強局所 → B緩収束     (Local-anchored → B, very slow)
        w_0 = (近0.80 / 中0.15 / 遠0.05), ρ = 0.92
        例: 国内 EC が域内 (アジア圏) 展開で安定するケース
    B2. 強国際 → B中速収束   (Global-anchored → B, moderate)
        w_0 = (近0.05 / 中0.20 / 遠0.75), ρ = 0.80
        例: 国際サービスが域内に網を張って落ち着くケース

  ---- パターンC: 収束先 w_∞ = (近0.10 / 中0.30 / 遠0.60)  E[RTT_∞]=139ms ----
    C1. 国内発 → C緩グローバル化 (Local → C, very slow)
        w_0 = (近0.70 / 中0.20 / 遠0.10), ρ = 0.92
        例: 国内発サービスが国際 CDN 展開で globally-spread になるケース
    C2. 国際深部 → C中速微調整 (Deep-global → C, moderate)
        w_0 = (近0.02 / 中0.08 / 遠0.90), ρ = 0.80
        例: 完全国際分散サービスが域内サーバ追加で僅かに局所性を獲得するケース

  ※ ρ は 1 に近いほど緩やか。 ρ=0.92 で ε=5ms 飽和は約 25 hop、
    ρ=0.80 で約 15 hop が目安 (初期偏差の大きさにも依存)。

  ---- 文献根拠 (各パターンの収束先) ----
    A: Internet Society 2012 / Makaroff et al. — 国内ドメインの強い locality.
    B: TeleGeography intl bandwidth + Statista — 国内60-70%, 域内20-30%, 海外10-20%.
    C: arXiv:2110.05772 海底ケーブル研究 — 多くの国で Web 60%+ が国際路.

----------------------------------------------------------------------
出力 (--out-dir, default は base/rtt/output)
----------------------------------------------------------------------
  hopwise_weights_evolution.png   3シナリオの w_k 推移 (各カテゴリ割合)
  hopwise_ertt_convergence.png    E[RTT_k] vs hop_k の収束曲線
  hopwise_saturation_table.csv    ε-飽和ホップ数 (E[RTT_k] が E[RTT_inf] の±ε内に入るホップ)
  hopwise_cumulative_simtime.png  累積 sim_time vs hop_k (1 walk あたり)
  hopwise_total_time_all.png      実測データに適用した総処理時間バー (3シナリオ横並び)
  hopwise_diff_vs_memo_all.png    memo 基準の差分バー (3シナリオ横並び)
  hopwise_summary.csv             各値の表形式

実行例:
      # x 軸を更に拡げる(ρ=0.95 にしてもっと緩くしたいときなど)
python3 base/rtt/plot_hopwise_rtt.py \
    --results-dir base/auth-baseline-cache/results/alpha0.01_walks_100_capa_100 \
    --out-dir base/rtt/output \
    --max-hops-evol 80 --max-hops-conv 120 --max-hops-cum 200
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# macOS / Linux 日本語フォント
_JP_FONTS = [
    "Hiragino Sans",
    "Hiragino Maru Gothic Pro",
    "AppleGothic",
    "Noto Sans CJK JP",
    "IPAGothic",
    "IPAPGothic",
    "TakaoGothic",
]
_available = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for _f in _JP_FONTS:
    if _f in _available:
        matplotlib.rcParams["font.family"] = _f
        break

# ---------------------------------------------------------------------------
# RTT 単位 [ms] — Phase 1/2 と統一
# ---------------------------------------------------------------------------
RTT_CLASSES = {"local": 10, "regional": 60, "global": 200}
CATEGORY_ORDER = ["local", "regional", "global"]
CATEGORY_LABELS = {
    "local": "近 (10ms)",
    "regional": "中 (60ms)",
    "global": "遠 (200ms)",
}
CATEGORY_COLORS = {"local": "#2ca02c", "regional": "#ff7f0e", "global": "#d62728"}

# Phase 2 の 3 つの混合パターン (収束先 w_∞ として使う)
PHASE2_TARGETS = {
    "A": {"local": 0.70, "regional": 0.20, "global": 0.10},  # E[RTT]=39ms
    "B": {"local": 0.30, "regional": 0.50, "global": 0.20},  # E[RTT]=73ms
    "C": {"local": 0.10, "regional": 0.30, "global": 0.60},  # E[RTT]=139ms
}

# ---------------------------------------------------------------------------
# Phase 3 シナリオ — Phase 2 の各パターンを w_∞ として、2 種類の漸近パス
# ---------------------------------------------------------------------------
SCENARIOS = [
    # ===== Pattern A (target: Local-dominant 70/20/10) =====
    {
        "key": "A1",
        "label": "A1: 国内深部 → A緩収束 (下から, ρ=0.92)",
        "short": "A1 DeepLocal→A (slow)",
        "pattern": "A",
        "w0": {"local": 0.95, "regional": 0.04, "global": 0.01},
        "w_inf": PHASE2_TARGETS["A"],
        "rho": 0.92,
        "note": "国内地域コミュニティが徐々に外部接続を獲得",
        "color": "#1f77b4",
    },
    {
        "key": "A2",
        "label": "A2: 国際スタート → A中速収束 (上から, ρ=0.80)",
        "short": "A2 Global→A (mod)",
        "pattern": "A",
        "w0": {"local": 0.10, "regional": 0.30, "global": 0.60},
        "w_inf": PHASE2_TARGETS["A"],
        "rho": 0.80,
        "note": "海外発サービスが国内中心の利用に集約",
        "color": "#aec7e8",
    },
    # ===== Pattern B (target: Regional-balanced 30/50/20) =====
    {
        "key": "B1",
        "label": "B1: 強局所 → B緩収束 (下から, ρ=0.92)",
        "short": "B1 Local→B (slow)",
        "pattern": "B",
        "w0": {"local": 0.80, "regional": 0.15, "global": 0.05},
        "w_inf": PHASE2_TARGETS["B"],
        "rho": 0.92,
        "note": "国内 EC が域内 (アジア圏) 展開で安定",
        "color": "#ff7f0e",
    },
    {
        "key": "B2",
        "label": "B2: 強国際 → B中速収束 (上から, ρ=0.80)",
        "short": "B2 Global→B (mod)",
        "pattern": "B",
        "w0": {"local": 0.05, "regional": 0.20, "global": 0.75},
        "w_inf": PHASE2_TARGETS["B"],
        "rho": 0.80,
        "note": "国際サービスが域内に網を張って落ち着く",
        "color": "#ffbb78",
    },
    # ===== Pattern C (target: Globally-spread 10/30/60) =====
    {
        "key": "C1",
        "label": "C1: 国内発 → C緩グローバル化 (下から, ρ=0.92)",
        "short": "C1 Local→C (slow)",
        "pattern": "C",
        "w0": {"local": 0.70, "regional": 0.20, "global": 0.10},
        "w_inf": PHASE2_TARGETS["C"],
        "rho": 0.92,
        "note": "国内発が国際 CDN 展開で globally-spread 化",
        "color": "#2ca02c",
    },
    {
        "key": "C2",
        "label": "C2: 国際深部 → C中速微調整 (上から, ρ=0.80)",
        "short": "C2 DeepGlobal→C (mod)",
        "pattern": "C",
        "w0": {"local": 0.02, "regional": 0.08, "global": 0.90},
        "w_inf": PHASE2_TARGETS["C"],
        "rho": 0.80,
        "note": "国際分散サービスが域内サーバ追加で僅かに局所性獲得",
        "color": "#98df8a",
    },
]

# パターンごとに 2 シナリオずつ持つことを示すヘルパー
PATTERN_KEYS = ["A", "B", "C"]
PATTERN_LABELS = {
    "A": "Pattern A: Local-dominant (w_∞ = 近70/中20/遠10, E[RTT_∞]=39ms)",
    "B": "Pattern B: Regional-balanced (w_∞ = 近30/中50/遠20, E[RTT_∞]=73ms)",
    "C": "Pattern C: Globally-spread (w_∞ = 近10/中30/遠60, E[RTT_∞]=139ms)",
}

# ---------------------------------------------------------------------------
# ポリシー設定 (Phase 1/2 と統一)
# ---------------------------------------------------------------------------
POLICY_ORDER = ["none", "memo", "lru", "arc"]
POLICY_LABELS = {
    "none": "キャッシュなし",
    "memo": "メモ (無制限)",
    "lru": "LRU",
    "arc": "ARC",
}
POLICY_COLORS = {
    "none": "#7f7f7f",
    "memo": "#1f77b4",
    "lru": "#ff7f0e",
    "arc": "#2ca02c",
}
GRAPH_LABELS = {
    "karate": "Karate",
    "fb-caltech-connected": "FB-Caltech",
    "amazon0601": "Amazon0601",
    "vldb": "VLDB",
}


# ===========================================================================
# モデル本体
# ===========================================================================
def weights_at_hop(scenario: dict, k: int) -> dict:
    """ホップ k での混合分布 w_k = w_inf + (w_0 - w_inf) * rho^(k-1)"""
    rho = scenario["rho"]
    w0 = scenario["w0"]
    w_inf = scenario["w_inf"]
    decay = rho ** (k - 1)
    return {c: w_inf[c] + (w0[c] - w_inf[c]) * decay for c in CATEGORY_ORDER}


def expected_rtt_at_hop(scenario: dict, k: int) -> float:
    """E[RTT_k] [ms]"""
    w = weights_at_hop(scenario, k)
    return sum(w[c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)


def expected_rtt_inf(scenario: dict) -> float:
    """E[RTT_∞] [ms]"""
    return sum(scenario["w_inf"][c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)


def cumulative_sim_time_ms(scenario: dict, N_hops: int, normalize: bool = False) -> float:
    """1 walk で N_hops hop した時の累積 sim 時間 [ms] = Σ_{k=1..N} E[RTT_k]

    normalize=True の場合、ウォーク内の平均 E[RTT_k] が E[RTT_∞] (= Phase 2 の
    E[RTT_mix]) に一致するように再スケーリングする。これにより
        N hop の累積 sim_time_phase3 = N × E[RTT_∞] = Phase 2 の sim_time
    となり、Phase 2 との直接比較が可能になる。
    曲線の形状 (収束の上下方向や速度) は保持される。
    """
    # 等比級数による閉じた式: rho == 1 を除く一般式
    rho = scenario["rho"]
    w_inf_rtt = expected_rtt_inf(scenario)
    # Σ_{k=1..N} E[RTT_k] = N * E[RTT_inf] + Σ_c RTT_c (w_0[c] - w_inf[c]) Σ_{k=1..N} rho^(k-1)
    if abs(rho - 1.0) < 1e-12:
        geom_sum = N_hops
    else:
        geom_sum = (1.0 - rho**N_hops) / (1.0 - rho)
    bias_term = (
        sum(
            RTT_CLASSES[c] * (scenario["w0"][c] - scenario["w_inf"][c])
            for c in CATEGORY_ORDER
        )
        * geom_sum
    )
    raw = N_hops * w_inf_rtt + bias_term
    if normalize and raw > 0:
        # raw / N が ウォーク内 mean(E[RTT_k]) で、これを E[RTT_∞] に揃える
        target = N_hops * w_inf_rtt
        return target  # = N × E[RTT_∞] = Phase 2 と一致
    return raw


def normalization_factor(scenario: dict, N_hops: int) -> float:
    """scenario の N hop ウォークに対する、Phase 2 とマッチさせるためのスケール係数。
       E[RTT_k]_normalized(k) = E[RTT_k] × factor となり、Σ が N × E[RTT_∞] になる。"""
    raw = cumulative_sim_time_ms(scenario, N_hops, normalize=False)
    target = N_hops * expected_rtt_inf(scenario)
    return target / raw if raw > 0 else 1.0


def expected_rtt_at_hop_normalized(scenario: dict, k: int, N_hops: int) -> float:
    """正規化版 E[RTT_k]: ウォーク全体の平均が E[RTT_∞] と一致するようスケール。"""
    factor = normalization_factor(scenario, N_hops)
    return expected_rtt_at_hop(scenario, k) * factor


def saturation_hop(scenario: dict, epsilon_ms: float = 1.0, max_hops: int = 300) -> int:
    """E[RTT_k] が E[RTT_inf] の ±epsilon_ms 以内に入る最小の k."""
    rtt_inf = expected_rtt_inf(scenario)
    for k in range(1, max_hops + 1):
        if abs(expected_rtt_at_hop(scenario, k) - rtt_inf) <= epsilon_ms:
            return k
    return max_hops + 1


# ===========================================================================
# データ読み込み (Phase 1/2 と共通)
# ===========================================================================
def load_results(results_dir: Path) -> dict:
    data: dict = defaultdict(lambda: defaultdict(list))
    for graph_dir in sorted(results_dir.iterdir()):
        if not graph_dir.is_dir():
            continue
        graph = graph_dir.name
        for policy_dir in sorted(graph_dir.iterdir()):
            if not policy_dir.is_dir():
                continue
            parts = policy_dir.name.rsplit("_", 1)
            if len(parts) != 2:
                continue
            policy, cap_str = parts
            try:
                capacity = int(cap_str)
            except ValueError:
                continue
            for json_file in sorted(policy_dir.glob("*_global_transition.json")):
                try:
                    d = json.loads(json_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                ctrl = d.get("controller", {})
                start_node = ctrl.get("start_node", -1)
                walk_time = float(d.get("walk_time_total", 0.0))
                walk_calls = int(d.get("walk_calls", 0))
                transition_obj = d.get("transition", {})
                if isinstance(transition_obj, dict):
                    hop_count = int(
                        sum(
                            v
                            for v in transition_obj.values()
                            if isinstance(v, (int, float))
                        )
                    )
                else:
                    hop_count = int(transition_obj or 0)
                auth_calls = int(d.get("auth_calls", 0))
                if walk_time < 1.0:
                    continue
                avg_hops_per_walk = hop_count / walk_calls if walk_calls > 0 else 0
                # miss_rate: 各 hop で auth 確認が必要だった割合
                miss_rate = auth_calls / hop_count if hop_count > 0 else 0.0
                data[graph][policy].append(
                    {
                        "walk_time_total": walk_time,
                        "hop_count": hop_count,
                        "auth_calls": auth_calls,
                        "walk_calls": walk_calls,
                        "avg_hops_per_walk": avg_hops_per_walk,
                        "miss_rate": miss_rate,
                        "capacity": capacity,
                        "start_node": start_node,
                    }
                )
    return data


def aggregate(records: list, scenario: dict, normalize_to_phase2: bool = False) -> dict:
    """
    実測データに Phase 3 のホップ別 RTT モデルを適用。

    モデル:
      各 hop k で、データ取得通信 (E[RTT_k]) が常に発生する。
      cache miss 時はさらに auth 確認通信 (+ E[RTT_k]) が発生する。
      miss_rate = auth_calls / hop_count とすると、
        hop k の期待コスト = (1 + miss_rate) × E[RTT_k]
        1 walk あたり sim_time = (1 + miss_rate) × Σ_{k=1..N} E[RTT_k]
        ※ N = avg_hops_per_walk

      → miss_rate が大きい (cache 効果が薄い) ほど sim_time が増える。

    normalize_to_phase2=True:
      ウォーク内の累積 sim_time を N × E[RTT_∞] (= Phase 2 と同じ) に
      正規化する。形状(下から / 上からの漸近、ρ の緩急)は保持され、
      総量だけを Phase 2 と揃える。
    """
    if not records:
        return {
            "walk_per_start": 0.0,
            "sim_hop_per_start": 0.0,
            "total_per_start": 0.0,
            "walk_sum": 0.0,
            "sim_hop_sum": 0.0,
            "total_sum": 0.0,
            "hops_sum": 0,
            "auth_calls_sum": 0,
            "rt_count_sum": 0,
            "n_valid": 0,
            "avg_hops_per_walk": 0.0,
            "miss_rate": 0.0,
            "walks_sum": 0,
        }

    by_cap: dict = defaultdict(list)
    for r in records:
        by_cap[r["capacity"]].append(r)

    cap_walk_per_start = []
    cap_sim_per_start = []
    walk_sum = 0.0
    sim_sum = 0.0
    hops_sum = 0
    auth_calls_sum = 0
    walks_sum = 0
    n_valid = 0
    avg_hops_list = []
    miss_rate_list = []

    for cap_records in by_cap.values():
        n = len(cap_records)
        if n == 0:
            continue
        walk_total = sum(r["walk_time_total"] for r in cap_records)
        hops_total = sum(r["hop_count"] for r in cap_records)
        auth_total = sum(r.get("auth_calls", 0) for r in cap_records)
        walks_total = sum(r["walk_calls"] for r in cap_records)

        # 1 record (= 1 start) ごとに sim time を計算
        sim_total = 0.0
        for r in cap_records:
            if r["walk_calls"] <= 0:
                continue
            avg_h = r["avg_hops_per_walk"]
            miss_rate = r.get("miss_rate", 0.0)
            # avg_h は実数なので、整数部分まで E[RTT_k] を積算 + 端数調整
            N_int = int(avg_h)
            frac = avg_h - N_int
            cum = cumulative_sim_time_ms(scenario, N_int)
            if frac > 0:
                cum += frac * expected_rtt_at_hop(scenario, N_int + 1)
            # Phase 2 正規化: 累積を N × E[RTT_∞] に揃える
            if normalize_to_phase2 and avg_h > 0:
                target_cum = avg_h * expected_rtt_inf(scenario)
                if cum > 0:
                    cum = target_cum
            # (1 + miss_rate) で auth 通信分も加算
            cum_with_auth = cum * (1.0 + miss_rate)
            # 1 walk あたり → walk_calls 個分
            sim_total += cum_with_auth / 1000.0 * r["walk_calls"]
            avg_hops_list.append(avg_h)
            miss_rate_list.append(miss_rate)

        cap_walk_per_start.append(walk_total / n)
        cap_sim_per_start.append(sim_total / n)
        walk_sum += walk_total
        sim_sum += sim_total
        hops_sum += hops_total
        auth_calls_sum += auth_total
        walks_sum += walks_total
        n_valid += n

    walk_per_start = float(np.mean(cap_walk_per_start)) if cap_walk_per_start else 0.0
    sim_hop_per_start = float(np.mean(cap_sim_per_start)) if cap_sim_per_start else 0.0

    return {
        "walk_per_start": walk_per_start,
        "sim_hop_per_start": sim_hop_per_start,
        "total_per_start": walk_per_start + sim_hop_per_start,
        "walk_sum": walk_sum,
        "sim_hop_sum": sim_sum,
        "total_sum": walk_sum + sim_sum,
        "hops_sum": hops_sum,
        "auth_calls_sum": auth_calls_sum,
        "rt_count_sum": hops_sum + auth_calls_sum,
        "walks_sum": walks_sum,
        "avg_hops_per_walk": float(np.mean(avg_hops_list)) if avg_hops_list else 0.0,
        "miss_rate": float(np.mean(miss_rate_list)) if miss_rate_list else 0.0,
        "n_valid": n_valid,
    }


# ===========================================================================
# 描画: モデル特性のグラフ
# ===========================================================================
def plot_weights_evolution(out_path: Path, max_hops: int = 20):
    """6 シナリオの w_k 推移を 2×3 配置 (行=variant, 列=pattern A/B/C)"""
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5), sharex=True, sharey=True)
    hops = np.arange(1, max_hops + 1)
    # パターン別 × variant 別に並べる
    scen_by_pattern = {
        p: [s for s in SCENARIOS if s["pattern"] == p] for p in PATTERN_KEYS
    }
    for col, p in enumerate(PATTERN_KEYS):
        scs = scen_by_pattern[p]
        for row, sc in enumerate(scs):
            ax = axes[row][col]
            ws = {c: [weights_at_hop(sc, k)[c] for k in hops] for c in CATEGORY_ORDER}
            bottom = np.zeros(len(hops))
            for c in CATEGORY_ORDER:
                ax.fill_between(
                    hops,
                    bottom,
                    bottom + np.array(ws[c]),
                    color=CATEGORY_COLORS[c],
                    alpha=0.7,
                    label=CATEGORY_LABELS[c],
                )
                bottom += np.array(ws[c])
            ax.set_title(
                f"{sc['short']}  (ρ={sc['rho']})\n"
                f"w0=(L{sc['w0']['local']:.2f}/R{sc['w0']['regional']:.2f}/G{sc['w0']['global']:.2f})",
                fontsize=10,
            )
            if row == 1:
                ax.set_xlabel("ホップ番号 k", fontsize=10)
            if col == 0:
                ax.set_ylabel("混合比率 w_k[c]", fontsize=10)
            ax.set_xlim(1, max_hops)
            ax.set_ylim(0, 1)
            ax.grid(alpha=0.3)
    # パターン列ヘッダ
    for col, p in enumerate(PATTERN_KEYS):
        axes[0][col].annotate(
            PATTERN_LABELS[p],
            xy=(0.5, 1.18),
            xycoords="axes fraction",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )
    axes[0][-1].legend(loc="upper right", fontsize=8, title="カテゴリ")
    fig.suptitle(
        "Phase 3 — w_k の推移 (Phase 2 の各収束先 × 2 種類の漸近パス)",
        fontsize=13,
        y=1.04,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_ertt_convergence(out_path: Path, max_hops: int = 30, eps_ms: float = 1.0):
    """E[RTT_k] の収束曲線 (3 パターン横並び、各 2 線を重ね描き)"""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.0), sharey=False)
    hops = np.arange(1, max_hops + 1)
    scen_by_pattern = {
        p: [s for s in SCENARIOS if s["pattern"] == p] for p in PATTERN_KEYS
    }
    for ax, p in zip(axes, PATTERN_KEYS):
        for sc in scen_by_pattern[p]:
            ertts = [expected_rtt_at_hop(sc, k) for k in hops]
            rtt_inf = expected_rtt_inf(sc)
            sat = saturation_hop(sc, epsilon_ms=eps_ms, max_hops=max_hops)
            ax.plot(
                hops,
                ertts,
                marker="o",
                color=sc["color"],
                linewidth=2,
                markersize=4,
                label=f"{sc['short']} (E[RTT_1]={ertts[0]:.0f}, 飽和k≈{sat})",
            )
        # 共通の収束先
        rtt_inf = sum(PHASE2_TARGETS[p][c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)
        ax.axhline(
            rtt_inf,
            linestyle="--",
            color="black",
            alpha=0.6,
            linewidth=1,
            label=f"E[RTT_∞]={rtt_inf:.0f}ms (収束先)",
        )
        ax.set_xlabel("ホップ番号 k", fontsize=11)
        ax.set_title(PATTERN_LABELS[p], fontsize=10)
        ax.grid(linestyle="--", alpha=0.5)
        ax.legend(loc="best", fontsize=8)
        ax.set_xlim(1, max_hops)
    axes[0].set_ylabel("E[RTT_k]  [ms]", fontsize=11)
    fig.suptitle(
        f"Phase 3 — E[RTT_k] の Phase 2 収束先への漸近曲線  (ε={eps_ms:g}ms 飽和判定)",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_cumulative_simtime(out_path: Path, max_hops: int = 100):
    """累積 sim_time (1 walk あたり) vs hop_k — 3 パターン横並び (各 2 線)"""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.0), sharey=False)
    hops = np.arange(1, max_hops + 1)
    scen_by_pattern = {
        p: [s for s in SCENARIOS if s["pattern"] == p] for p in PATTERN_KEYS
    }
    for ax, p in zip(axes, PATTERN_KEYS):
        for sc in scen_by_pattern[p]:
            cum = [cumulative_sim_time_ms(sc, k) / 1000.0 for k in hops]
            ax.plot(hops, cum, color=sc["color"], linewidth=2, label=sc["short"])
        ax.set_xlabel("1 walk のホップ数 N", fontsize=11)
        ax.set_title(PATTERN_LABELS[p], fontsize=10)
        ax.grid(linestyle="--", alpha=0.5)
        ax.legend(loc="best", fontsize=9)
        ax.set_xlim(1, max_hops)
    axes[0].set_ylabel("累積 sim_time [s] / walk", fontsize=11)
    fig.suptitle(
        "Phase 3 — 1 walk あたりの累積シミュレート通信時間 (Σ_{k=1..N} E[RTT_k])",
        fontsize=13,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def save_saturation_table(out_path: Path):
    """ε-飽和ホップ数の表 CSV (6 シナリオ)

    研究的に最も興味深い列は eps=5ms 飽和点 = "ここまでキャッシュすれば
    十分" の目安。"""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "scenario",
                "pattern",
                "label",
                "w0_local",
                "w0_regional",
                "w0_global",
                "w_inf_local",
                "w_inf_regional",
                "w_inf_global",
                "rho",
                "E[RTT_1]_ms",
                "E[RTT_inf]_ms",
                "saturation_hop_eps=0.5ms",
                "saturation_hop_eps=1.0ms",
                "saturation_hop_eps=5.0ms",
                "recommended_cache_depth",
            ]
        )
        for sc in SCENARIOS:
            sat5 = saturation_hop(sc, 5.0)
            w.writerow(
                [
                    sc["key"],
                    sc["pattern"],
                    sc["label"],
                    f"{sc['w0']['local']:.3f}",
                    f"{sc['w0']['regional']:.3f}",
                    f"{sc['w0']['global']:.3f}",
                    f"{sc['w_inf']['local']:.3f}",
                    f"{sc['w_inf']['regional']:.3f}",
                    f"{sc['w_inf']['global']:.3f}",
                    sc["rho"],
                    f"{expected_rtt_at_hop(sc, 1):.3f}",
                    f"{expected_rtt_inf(sc):.3f}",
                    saturation_hop(sc, 0.5),
                    saturation_hop(sc, 1.0),
                    sat5,
                    sat5,  # 5ms 飽和点を推奨キャッシュ深度とする
                ]
            )
    print(f"[saved] {out_path}")


# ===========================================================================
# 描画: 実データへの適用
# ===========================================================================
def _annotate(ax, bars, vals, fmt="{:.1f}", fontsize=8):
    for bar, val in zip(bars, vals):
        if abs(val) < 1e-9:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.01,
            fmt.format(val),
            ha="center",
            va="bottom",
            fontsize=fontsize,
        )


def _draw_stacked(ax, graphs, walk_vals, sim_vals, bar_width=0.18):
    x = np.arange(len(graphs))
    n_p = len(POLICY_ORDER)
    for i, policy in enumerate(POLICY_ORDER):
        w = walk_vals.get(policy, [0.0] * len(graphs))
        a = sim_vals.get(policy, [0.0] * len(graphs))
        offset = (i - (n_p - 1) / 2) * bar_width
        ax.bar(
            x + offset,
            w,
            width=bar_width,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
        )
        bars_a = ax.bar(
            x + offset,
            a,
            width=bar_width,
            bottom=w,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
            hatch="///",
            alpha=0.85,
        )
        _annotate(ax, bars_a, [wi + ai for wi, ai in zip(w, a)], fmt="{:.1f}")
    return x


def _draw_diff(ax, graphs, diff_vals, bar_width=0.18):
    x = np.arange(len(graphs))
    n_p = len(POLICY_ORDER)
    for i, policy in enumerate(POLICY_ORDER):
        vals = diff_vals.get(policy, [0.0] * len(graphs))
        offset = (i - (n_p - 1) / 2) * bar_width
        bars = ax.bar(
            x + offset,
            vals,
            width=bar_width,
            color=POLICY_COLORS.get(policy),
            edgecolor="white",
            linewidth=0.5,
            label=POLICY_LABELS.get(policy, policy),
        )
        for bar, val in zip(bars, vals):
            if abs(val) < 1e-6:
                continue
            va = "bottom" if val >= 0 else "top"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{val:+.1f}",
                ha="center",
                va=va,
                fontsize=8,
            )
    ax.axhline(0, color="black", linewidth=0.8)
    return x


def _add_stack_legend(ax):
    from matplotlib.patches import Patch

    policy_handles = [
        Patch(facecolor=POLICY_COLORS[p], label=POLICY_LABELS[p]) for p in POLICY_ORDER
    ]
    section_handles = [
        Patch(facecolor="lightgray", label="walk_time"),
        Patch(
            facecolor="lightgray",
            hatch="///",
            label="sim_time = (1+miss_rate) × Σ_k E[RTT_k]",
        ),
    ]
    leg1 = ax.legend(
        handles=policy_handles, loc="upper left", fontsize=8, title="ポリシー"
    )
    ax.add_artist(leg1)
    ax.legend(handles=section_handles, loc="upper right", fontsize=8, title="内訳")


def plot_total_time_all(graphs, per_scenario, out_path, normalize_label=""):
    """6 シナリオを 2×3 配置 (行=variant, 列=pattern A/B/C)"""
    fig, axes = plt.subplots(
        2, 3, figsize=(max(14, len(graphs) * 4.2), 10), sharey=True
    )
    scen_by_pattern = {
        p: [s for s in SCENARIOS if s["pattern"] == p] for p in PATTERN_KEYS
    }

    all_max = 0.0
    for sc in SCENARIOS:
        v = per_scenario[sc["key"]]
        for p in POLICY_ORDER:
            for wi, ai in zip(v["walk"].get(p, []), v["sim"].get(p, [])):
                all_max = max(all_max, wi + ai)
    ylim_top = all_max * 1.15 if all_max > 0 else 1.0

    for col, p in enumerate(PATTERN_KEYS):
        scs = scen_by_pattern[p]
        for row, sc in enumerate(scs):
            ax = axes[row][col]
            v = per_scenario[sc["key"]]
            x = _draw_stacked(ax, graphs, v["walk"], v["sim"])
            ax.set_xticks(x)
            ax.set_xticklabels([GRAPH_LABELS.get(g, g) for g in graphs], fontsize=9)
            rtt_inf = expected_rtt_inf(sc)
            rtt_1 = expected_rtt_at_hop(sc, 1)
            ax.set_title(
                f"{sc['short']} (ρ={sc['rho']})\n"
                f"E[RTT_1]={rtt_1:.0f}ms → E[RTT_∞]={rtt_inf:.0f}ms",
                fontsize=10,
            )
            ax.set_ylim(0, ylim_top)
            ax.grid(axis="y", linestyle="--", alpha=0.4)
        # パターンの列ヘッダ
        axes[0][col].annotate(
            PATTERN_LABELS[p],
            xy=(0.5, 1.25),
            xycoords="axes fraction",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )
    axes[0][0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    axes[1][0].set_ylabel("総処理時間 [s] (per start_node 平均)", fontsize=11)
    _add_stack_legend(axes[0][-1])
    title = "Phase 3 ホップ別不均一RTT — Phase 2 収束先 × 2 種類の漸近パス (2×3, y軸共通)"
    if normalize_label:
        title += f"\n{normalize_label}"
    fig.suptitle(title, fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def plot_diff_all(graphs, diff_per_scenario, out_path, normalize_label=""):
    """6 シナリオの memo 差分を 2×3 配置"""
    fig, axes = plt.subplots(
        2, 3, figsize=(max(14, len(graphs) * 4.2), 10), sharey=True
    )
    scen_by_pattern = {
        p: [s for s in SCENARIOS if s["pattern"] == p] for p in PATTERN_KEYS
    }

    all_max, all_min = 0.0, 0.0
    for sc in SCENARIOS:
        v = diff_per_scenario[sc["key"]]
        for p in POLICY_ORDER:
            for val in v["diff"].get(p, []):
                all_max = max(all_max, val)
                all_min = min(all_min, val)
    pad = max(abs(all_max), abs(all_min)) * 0.18 if (all_max or all_min) else 1.0
    ylim_top = all_max + pad
    ylim_bot = all_min - pad if all_min < 0 else -pad

    for col, p in enumerate(PATTERN_KEYS):
        scs = scen_by_pattern[p]
        for row, sc in enumerate(scs):
            ax = axes[row][col]
            v = diff_per_scenario[sc["key"]]
            memo_totals = v.get("memo_totals", [0.0] * len(graphs))
            x = _draw_diff(ax, graphs, v["diff"])
            xlabels = [
                f"{GRAPH_LABELS.get(g, g)}\n(memo={mt:.1f}s)"
                for g, mt in zip(graphs, memo_totals)
            ]
            ax.set_xticks(x)
            ax.set_xticklabels(xlabels, fontsize=9)
            ax.set_title(f"{sc['short']} (ρ={sc['rho']})", fontsize=10)
            ax.grid(axis="y", linestyle="--", alpha=0.4)
            ax.set_ylim(ylim_bot, ylim_top)
        axes[0][col].annotate(
            PATTERN_LABELS[p],
            xy=(0.5, 1.20),
            xycoords="axes fraction",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )
    axes[0][0].set_ylabel("memo との差分 [s] (per start)", fontsize=11)
    axes[1][0].set_ylabel("memo との差分 [s] (per start)", fontsize=11)
    axes[0][-1].legend(loc="best", fontsize=8, title="ポリシー")
    title = "Phase 3 ホップ別不均一RTT — 同シナリオ内 memo 差分 (2×3, y軸共通)"
    if normalize_label:
        title += f"\n{normalize_label}"
    fig.suptitle(title, fontsize=13, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out_path}")


def save_summary_csv(graphs, per_scenario, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "scenario",
                "label",
                "E[RTT_1]_ms",
                "E[RTT_inf]_ms",
                "rho",
                "graph",
                "policy",
                "avg_hops_per_walk",
                "miss_rate",
                "walks_sum",
                "walk_per_start_s",
                "sim_time_per_start_s",
                "total_per_start_s",
                "walk_sum_s",
                "sim_time_sum_s",
                "total_sum_s",
                "hops_sum",
                "auth_calls_sum",
                "rt_count_sum",
                "n_valid",
                "memo_diff_per_start_s",
                "memo_ratio",
            ]
        )
        for sc in SCENARIOS:
            agg = per_scenario[sc["key"]]["agg"]
            for graph in graphs:
                base = agg[graph].get("memo", {})
                base_total = base.get("total_per_start", 0.0)
                for policy in POLICY_ORDER:
                    v = agg[graph].get(policy, {})
                    if not v or v.get("n_valid", 0) == 0:
                        continue
                    diff = v["total_per_start"] - base_total
                    ratio = v["total_per_start"] / base_total if base_total > 0 else 0
                    w.writerow(
                        [
                            sc["key"],
                            sc["label"],
                            f"{expected_rtt_at_hop(sc, 1):.2f}",
                            f"{expected_rtt_inf(sc):.2f}",
                            sc["rho"],
                            graph,
                            policy,
                            f"{v.get('avg_hops_per_walk', 0):.2f}",
                            f"{v.get('miss_rate', 0):.4f}",
                            v.get("walks_sum", 0),
                            f"{v['walk_per_start']:.4f}",
                            f"{v['sim_hop_per_start']:.4f}",
                            f"{v['total_per_start']:.4f}",
                            f"{v['walk_sum']:.3f}",
                            f"{v['sim_hop_sum']:.3f}",
                            f"{v['total_sum']:.3f}",
                            v["hops_sum"],
                            v.get("auth_calls_sum", 0),
                            v.get("rt_count_sum", 0),
                            v["n_valid"],
                            f"{diff:+.4f}",
                            f"{ratio:.4f}",
                        ]
                    )
    print(f"[saved] {out_path}")


# ===========================================================================
# main
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Phase 3: ホップ別不均一 RTT モデル — 3シナリオで分析・可視化"
    )
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=Path("base/rtt/output"))
    ap.add_argument(
        "--max-hops-evol",
        type=int,
        default=50,
        help="weights_evolution グラフの x 軸範囲 (default 50, 緩漸近に合わせ拡張)",
    )
    ap.add_argument(
        "--max-hops-conv",
        type=int,
        default=80,
        help="ertt_convergence グラフの x 軸範囲 (default 80, 緩漸近に合わせ拡張)",
    )
    ap.add_argument(
        "--max-hops-cum",
        type=int,
        default=150,
        help="cumulative simtime グラフの x 軸範囲 (default 150)",
    )
    ap.add_argument(
        "--normalize-to-phase2",
        action="store_true",
        help="ウォーク累積を N × E[RTT_∞] (= Phase 2 と同値) に正規化する。"
        "形状(緩急/上下)は保持しつつ総量を Phase 2 と一致させる。",
    )
    args = ap.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================
    # シナリオ概要表示
    # ========================================================
    print("=" * 86)
    print(f"{'Phase 3: ホップ別不均一 RTT モデル (Phase 2 収束先 × 2 漸近パス)':^86}")
    print("=" * 86)
    print(
        f"RTT 単位 [ms]: local={RTT_CLASSES['local']}, "
        f"regional={RTT_CLASSES['regional']}, global={RTT_CLASSES['global']}"
    )
    if args.normalize_to_phase2:
        print("** 正規化モード ON: 各シナリオの累積 sim_time を Phase 2 と一致させます (形状は保持) **\n")
    print()
    for p in PATTERN_KEYS:
        target = PHASE2_TARGETS[p]
        e_inf = sum(target[c] * RTT_CLASSES[c] for c in CATEGORY_ORDER)
        print(f"━━━ {PATTERN_LABELS[p]} ━━━")
        print(f"  ⇒ Phase 2 と同じ E[RTT_∞] = {e_inf:.1f}ms (= Pattern {p} の収束先)")
        for sc in SCENARIOS:
            if sc["pattern"] != p:
                continue
            e1 = expected_rtt_at_hop(sc, 1)
            sat1 = saturation_hop(sc, 1.0)
            sat5 = saturation_hop(sc, 5.0)
            print(f"  [{sc['key']}] {sc['label']}")
            print(f"      例: {sc['note']}")
            print(
                f"      w0=L{sc['w0']['local']:.2f}/R{sc['w0']['regional']:.2f}/G{sc['w0']['global']:.2f}, ρ={sc['rho']}"
            )
            print(
                f"      E[RTT_1]={e1:.1f}ms → E[RTT_∞]={e_inf:.1f}ms (✓ Phase 2 と一致)"
                f"  /  飽和k: ε=1ms→{sat1}, ε=5ms→{sat5} (推奨キャッシュ深度)"
            )
        print()

    # ========================================================
    # モデル特性のグラフ
    # ========================================================
    plot_weights_evolution(
        out_dir / "hopwise_weights_evolution.png", args.max_hops_evol
    )
    plot_ertt_convergence(out_dir / "hopwise_ertt_convergence.png", args.max_hops_conv)
    plot_cumulative_simtime(
        out_dir / "hopwise_cumulative_simtime.png", args.max_hops_cum
    )
    save_saturation_table(out_dir / "hopwise_saturation_table.csv")

    # ========================================================
    # 実データ読み込み + 適用
    # ========================================================
    data = load_results(args.results_dir)
    if not data:
        print(f"[ERROR] 結果ファイルが見つかりませんでした: {args.results_dir}")
        return

    graph_order = ["karate", "fb-caltech-connected", "amazon0601", "vldb"]
    graphs = [g for g in graph_order if g in data]
    for g in sorted(data.keys()):
        if g not in graphs:
            graphs.append(g)

    per_scenario: dict = {}
    diff_per_scenario: dict = {}

    for sc in SCENARIOS:
        agg: dict = {}
        for graph in graphs:
            agg[graph] = {}
            for policy in POLICY_ORDER:
                agg[graph][policy] = aggregate(
                    data[graph].get(policy, []),
                    sc,
                    normalize_to_phase2=args.normalize_to_phase2,
                )

        walk_vals = {
            p: [agg[g][p]["walk_per_start"] for g in graphs] for p in POLICY_ORDER
        }
        sim_vals = {
            p: [agg[g][p]["sim_hop_per_start"] for g in graphs] for p in POLICY_ORDER
        }
        per_scenario[sc["key"]] = {"walk": walk_vals, "sim": sim_vals, "agg": agg}

        # console summary
        print(f"\n===== {sc['label']} =====")
        print(
            f"  sim_time = (1 + miss_rate) × Σ_k E[RTT_k]  "
            f"(miss_rate = auth_calls / hop_count)"
        )
        print(
            f"{'グラフ':<14} {'ポリシー':<8} {'avg_hops':>9} {'miss_rate':>10} "
            f"{'walk/s':>9} {'simTime/s':>10} {'total/s':>10} "
            f"{'memo差/s':>10} {'memo比':>7} {'n':>3}"
        )
        print("-" * 115)
        for graph in graphs:
            base = agg[graph].get("memo", {})
            base_t = base.get("total_per_start", 0.0)
            for policy in POLICY_ORDER:
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    continue
                diff = v["total_per_start"] - base_t
                ratio = v["total_per_start"] / base_t if base_t > 0 else 0
                print(
                    f"{GRAPH_LABELS.get(graph, graph):<14} "
                    f"{policy:<8} "
                    f"{v['avg_hops_per_walk']:>9.2f} "
                    f"{v.get('miss_rate', 0):>10.3f} "
                    f"{v['walk_per_start']:>9.3f} "
                    f"{v['sim_hop_per_start']:>10.3f} "
                    f"{v['total_per_start']:>10.3f} "
                    f"{diff:>+10.3f} "
                    f"{ratio:>6.2f}x "
                    f"{v['n_valid']:>3d}"
                )

        # 差分
        diff_vals = {p: [] for p in POLICY_ORDER}
        memo_totals = []
        for graph in graphs:
            base = agg[graph].get("memo", {})
            base_t = base.get("total_per_start", 0.0)
            memo_totals.append(base_t)
            for policy in POLICY_ORDER:
                v = agg[graph].get(policy, {})
                if not v or v.get("n_valid", 0) == 0:
                    diff_vals[policy].append(0.0)
                else:
                    diff_vals[policy].append(v["total_per_start"] - base_t)
        diff_per_scenario[sc["key"]] = {"diff": diff_vals, "memo_totals": memo_totals}

    nlabel = (
        "[正規化モード] 各シナリオの累積 sim_time を N × E[RTT_∞] (= Phase 2 と一致) に揃え、形状のみ比較"
        if args.normalize_to_phase2 else ""
    )
    plot_total_time_all(
        graphs, per_scenario, out_dir / "hopwise_total_time_all.png",
        normalize_label=nlabel,
    )
    plot_diff_all(
        graphs, diff_per_scenario, out_dir / "hopwise_diff_vs_memo_all.png",
        normalize_label=nlabel,
    )
    save_summary_csv(graphs, per_scenario, out_dir / "hopwise_summary.csv")

    print(f"\n完了。出力先: {out_dir}")


if __name__ == "__main__":
    main()
