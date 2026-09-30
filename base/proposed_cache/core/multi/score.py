#!/usr/bin/env python3
# =============================================================================
# score.py — 提案手法のスコア式。**式の差し替え点はこのファイルだけ**
#
#   既存実装では指数の適用が2箇所に分かれていて読みづらかった:
#       θ         → PPRBoundedCache._score()
#       β, γ      → PeerWalker._hop_prior()
#   ここに集約して、式の全体が1ファイルで読めるようにする。
#
# -----------------------------------------------------------------------------
#   退去優先度   H(e) = last_access(e) + w · V(e)          （大きいほど残す）
#   価値         V(e) = O(e)^θ · H_(e)^β · D(e)^γ
#
#       O  = 1 + freq / (freq + C_F)      観測項（オンライン頻度）
#       H_ = 1 + sigmoid(z_hop)           距離項（始点に近いほど大）
#       D  = 1 + sigmoid(z_deg)           次数項（高次数ほど大）
#       各因子 ∈ [1, 2)
#
#   既定は θ = β = γ = 1、w = 100。
#
#   ★ 次数項 D は **キャッシュするエンティティがノードのときだけ**使う。
#     エッジ再訪に対して次数は逆効果（高次数端点ほど 1/deg で選ばれにくく
#     再通過しない）という実測に基づく: エッジに次数を入れると overall が
#     VLDB −0.68 / Amazon −1.02 pt 悪化する。
#
#     ⚠️ 注意（型分離配置での非自明な帰結）:
#       キャッシュに入るのは「そのサーバが所有するエンティティ」ではなく
#       「そのサーバが認可を問い合わせた候補」= 現在地の隣接 = **反対の型**。
#         ノード担当サーバ（ノードを所有）→ 候補はエッジ → **エッジをキャッシュする**
#         エッジ担当サーバ（エッジを所有）→ 候補はノード → **ノードをキャッシュする**
#       したがって use_degree はサーバの役割ではなく、**エンティティの型**で
#       決めなければならない。役割で決めると逆になる。
#
#   ★ 学習項 L = 1 + learn/(learn + C_L) は持たない。
#     λ=0 かつ learn=0 で常に 1 の死んだ項だったので 2026-09-08 に削除した。
#     学習フェーズは legacy ppr_demand 専用の機能。
#
#   z 値は Welford によるオンライン標準化（分布が regime でずれても追従する）。
#   絶対式 (1−α)^hop や deg/(deg+C) は遠距離で全員 ≈1 に潰れるため使わない。
# =============================================================================
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional


# =============================================================================
# パラメータ
# =============================================================================
@dataclass
class ScoreParams:
    """提案手法のパラメータ。既定値が採用済みの動作点。"""

    w: float = 100.0        # 重み。H = last_access + w·V。w=0 で厳密に LRU
    theta: float = 1.0      # θ: 観測項 O の指数
    beta: float = 1.0       # β: 距離項 H_ の指数
    gamma: float = 1.0      # γ: 次数項 D の指数
    cf: float = 5.0         # C_F: 頻度の飽和点
    edge_use_degree: bool = False  # エッジに次数項を与えるか。既定 False（実測で悪化）

    def use_degree(self, is_edge: bool) -> bool:
        """このエンティティに次数項を与えるか。**型**で決める（役割ではない）。"""
        return self.edge_use_degree if is_edge else True

    def describe(self) -> str:
        node = f"O^{self.theta:g} · H^{self.beta:g} · D^{self.gamma:g}"
        edge = f"O^{self.theta:g} · H^{self.beta:g}"
        if self.edge_use_degree:
            edge += f" · D^{self.gamma:g}"
        return (f"V(node) = {node} / V(edge) = {edge}"
                f"   (w={self.w:g}, C_F={self.cf:g})")


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


# =============================================================================
# オンライン標準化 (Welford)
# =============================================================================
@dataclass
class RunningNorm:
    """特徴量ごとの (n, mean, M2) を持ち、z 値を返しながら分布を更新する。

    サーバ1台ごとに1インスタンス。既存実装の PeerWalker._reuse_norm_z と同一挙動
    （n<2 または分散≈0 のとき 0 を返す = sigmoid 0.5 = 中立 1.5）。
    """

    stats: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def z(self, name: str, x: float, invert: bool) -> float:
        s = self.stats.get(name)
        if s is None:
            s = {"n": 0.0, "mean": 0.0, "M2": 0.0}
            self.stats[name] = s
        s["n"] += 1.0
        delta = x - s["mean"]
        s["mean"] += delta / s["n"]
        s["M2"] += delta * (x - s["mean"])
        if s["n"] < 2:
            return 0.0
        var = s["M2"] / (s["n"] - 1.0)
        if var <= 1e-12:
            return 0.0
        z = (x - s["mean"]) / math.sqrt(var)
        return -z if invert else z


# =============================================================================
# 各因子
# =============================================================================
def observation_term(freq: float, p: ScoreParams) -> float:
    """O = 1 + freq/(freq + C_F) ∈ [1, 2)"""
    denom = freq + p.cf
    if denom <= 0:
        return 1.0
    return 1.0 + freq / denom


def distance_term(hop: int, norm: RunningNorm) -> float:
    """H_ = 1 + sigmoid(z_hop)。hop が小さい（始点に近い）ほど 2 に近づく。"""
    z = norm.z("hop", float(max(0, int(hop))), invert=True)
    return 1.0 + sigmoid(z)


def degree_term(degree: float, norm: RunningNorm) -> float:
    """D = 1 + sigmoid(z_deg)。高次数ほど 2 に近づく。"""
    z = norm.z("deg", float(degree), invert=False)
    return 1.0 + sigmoid(z)


# =============================================================================
# 構造 prior と価値
# =============================================================================
def structural_prior(hop: int, degree: float, norm: RunningNorm,
                     p: ScoreParams, is_edge: bool) -> float:
    """挿入時に固定する構造項 = H_^β · D^γ（エッジは H_^β のみ）。

    次数を使わないときは norm["deg"] を **参照も更新もしない**。
    エッジで deg を観測すると次数の分布が汚れて、ノード側の z 値がずれるため。
    既存実装の --reuse-edge-no-deg と同じ設計。
    """
    h = distance_term(hop, norm) ** p.beta
    if not p.use_degree(is_edge):
        return h
    d = degree_term(degree, norm) ** p.gamma
    return h * d


def value(freq: float, prior: float, p: ScoreParams) -> float:
    """V(e) = O^θ · prior     （prior = H_^β · D^γ、挿入時に固定した値）"""
    return (observation_term(freq, p) ** p.theta) * prior


def priority(last_access: float, freq: float, prior: float,
             p: ScoreParams) -> float:
    """退去優先度 H(e) = last_access + w · V(e)。大きいほど残す。

    ★ `/size`（占有量）で割らない。割ると V=1（スコア全 OFF）でも
      node に +w/2 / edge に +w/3 の定数差が付き、型バイアスが残る。
      割らない形にすると w=0 が厳密に LRU と一致する（分解のアンカー）。
      実測でも補償なしは VLDB で 2.3 pt 損する。
    """
    return last_access + p.w * value(freq, prior, p)


# =============================================================================
# 自己検証
# =============================================================================
def _selftest() -> None:
    p = ScoreParams()
    n = RunningNorm()

    # 各因子が [1,2) に入る
    for f in (0, 1, 5, 100, 10000):
        o = observation_term(f, p)
        assert 1.0 <= o < 2.0, (f, o)
    for i in range(50):
        h = distance_term(i % 7, n)
        assert 1.0 <= h < 2.0, h

    # w=0 は純 LRU（last_access のみ）
    p0 = ScoreParams(w=0.0)
    assert priority(123.0, 9, 1.7, p0) == 123.0

    # エッジでは次数の分布を汚さない / ノードでは使う
    n1 = RunningNorm()
    structural_prior(1, 999, n1, ScoreParams(), is_edge=True)
    assert "deg" not in n1.stats, "エッジで deg を観測してはいけない"
    n2 = RunningNorm()
    structural_prior(1, 999, n2, ScoreParams(), is_edge=False)
    assert "deg" in n2.stats
    # 明示的に有効化すればエッジでも使える（アブレーション用）
    n3 = RunningNorm()
    structural_prior(1, 999, n3, ScoreParams(edge_use_degree=True), is_edge=True)
    assert "deg" in n3.stats

    # 近い方が価値が高い（同じ freq・同じ分布）
    na = RunningNorm()
    for hop in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
        na.z("hop", float(hop), invert=True)   # 分布を温める
    near = structural_prior(0, 10, na, ScoreParams(), is_edge=True)
    far = structural_prior(9, 10, na, ScoreParams(), is_edge=True)
    assert near > far, (near, far)

    print("score.py selftest OK")
    print("  ", p.describe())


if __name__ == "__main__":
    _selftest()
