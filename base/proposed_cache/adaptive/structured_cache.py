#!/usr/bin/env python3
# =============================================================================
# structured_cache.py — 分析で分かった4要素の性質を「項の形」に入れた学習キャッシュ
#
#   方策名: structured（F1 の learned は learned_cache.py にそのまま残してある）
#
#   分析結果（access-factor 分析）→ 式への反映:
#     H 距離     : 小さい方が有利。特に α=0.1 のノードで一貫        → w_H·z_H + w_Hn·[node]·z_H
#     O 回数     : 大きい方が非常に安定して有利（主軸）              → w_O·z_O
#     R 直近性   : O より弱いが、小さい（最近）方が有利             → w_R·z_R
#     D 次数     : α=0.01 で条件付き。ノード限定・O が小さいときの補助・
#                  H が大きいほど効く                                → w_D·[node]·z_D·(1/O)·σ(z_H)
#
#   スコア（大きいほど追い出す。重みはすべてオンライン学習）:
#
#       s = b + w_O·z_O + w_R·z_R + w_H·z_H + w_Hn·[node]·z_H
#             + w_D·[node]·z_D·(1/O)·σ(z_H)
#
#       z_O = z(log(1+O))    O = そのサーバでの全アクセス回数（退去しても消さない）
#       z_R = z(log(1+age))  age = 最後に使われてから経過したアクセス数
#       z_H = z(hop)         hop = 始点からの歩数（観測した最小値）
#       z_D = z(log(1+deg))  ノードの次数（基準もノードだけで作る）
#       1/O   : O が小さいほど 1 に近い（O が貯まると次数の出番が消える）
#       σ(z_H): H が大きいほど 1 に近い（遠いところほど次数が効く）
#       [node]: ノードなら 1、エッジなら 0
#       z(·)  : サーバ内の平均と標準偏差で標準化（node/edge 共通の基準）、±z_clip で打ち切り
#
#   学習の設定（learned の診断で良かった組み合わせに固定）:
#     - 重みは node / edge で1組（型ごとに別だとバイアス差で型をまたぐ比較がゆがむ）
#     - 目的 y = 0（滞在時間 h 以内に再利用された）/ 1（されなかった）
#     - O は全アクセス回数、回数と経過時間と次数は log(1+x) してから標準化
#
#   初期重み: 既定は全部 0（手で決めた値を入れない）。
#       重みが全部 0 の間は全キーが同点 → 同点は last_access の古い方を追い出すので、
#       学習が進むまでは自然に LRU と同じ動きになる。学習は最初から常に続ける（切り替え判定なし）。
#       分析の向きを初期値に入れる版は --learn-init prior（b=0, O=−1.0, R=+0.3, H=+0.5, H_node=+0.25, D=0）
# =============================================================================
from __future__ import annotations

import math
from dataclasses import replace
from typing import List, Tuple

from learned_cache import (Key, LearnedLinearCache, LearnerConfig, entity_type)

TERMS: Tuple[str, ...] = ("O", "R", "H", "H_node", "D_cond")
PRIOR_WEIGHTS = [0.0, -1.0, 0.3, 0.5, 0.25, 0.0]     # b, O, R, H, H_node, D_cond


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class StructuredLearnedCache(LearnedLinearCache):
    term_names = TERMS
    norm_names = ("logO", "logR", "H", "logD")
    policy_name = "structured"

    def __init__(self, capacity: int, sizer, cfg: LearnerConfig) -> None:
        # 目的: 既定は reuse（0/1）。pairwise を指定したときだけ組単位の学習にする
        # O の数え方: 既定は全アクセス回数。edc を指定したときだけ減衰させた回数にする
        cfg = replace(cfg,
                      freq_scope="edc" if cfg.freq_scope == "edc" else "total",
                      target="pairwise" if cfg.target == "pairwise" else "reuse",
                      shared_model=True, features="H,O,D,R", log_features="")
        super().__init__(capacity, sizer, cfg)

    def shared_model(self) -> bool:
        return True

    def _initial_weights(self) -> List[float]:
        mode = self.cfg.init
        if "," in mode:
            w = [float(v) for v in mode.split(",")]
            if len(w) != 1 + len(TERMS):
                raise ValueError(f"structured の初期重みは {1 + len(TERMS)} 個"
                                 f"（b,{','.join(TERMS)}）: {mode}")
            return w
        if mode == "zero":
            return [0.0] * (1 + len(TERMS))
        if mode == "lru":                       # 経過時間だけ = LRU（配線の検証用）
            return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        if mode == "prior":
            return list(PRIOR_WEIGHTS)
        return [0.0] * (1 + len(TERMS))         # 既定 auto = 全部 0（学習が進むまでは LRU）

    def _features(self, key: Key, update_norms: bool) -> List[float]:
        is_node = entity_type(key) == "node"
        o = self._freq_value(key)
        age = float(self.clock - self.last_access.get(key, self.clock))
        hop = float(self.hop.get(key, 0))
        deg = float(self.deg.get(key, 1.0))
        q = {"logO": math.log1p(o), "logR": math.log1p(max(0.0, age)), "H": hop,
             "logD": math.log1p(deg)}
        nm = self.norms["node"]                 # 基準は node / edge 共通（同じ辞書）
        if update_norms:
            for n in ("logO", "logR", "H"):
                nm[n].add(q[n])
            if is_node:                         # 次数の基準はノードだけで作る
                nm["logD"].add(q["logD"])
        zc = self.cfg.z_clip

        def z(n: str) -> float:
            return max(-zc, min(zc, nm[n].z(q[n])))

        zO, zR, zH = z("logO"), z("logR"), z("H")
        node = 1.0 if is_node else 0.0
        d_cond = node * z("logD") * (1.0 / max(1.0, o)) * _sigmoid(zH) if is_node else 0.0
        return [1.0, zO, zR, zH, node * zH, d_cond]


# =============================================================================
# structured_split — ノードとエッジで式と重みを分ける版
#
#   ノード: s_n = b_n + w_O·z_O + w_R·z_R + w_H·z_H + w_D·z_D·(1/O)·σ(z_H)
#   エッジ: s_e = b_e + w_O'·z_O + w_R'·z_R + w_H'·z_H          （次数は使わない）
#
#   z の基準（平均・標準偏差）はサーバ内で共通のまま（型をまたいで s を比べるため）。
#   目的は両方とも「滞在時間 h 以内に再利用されるか」の 0/1 なので、s は同じ尺度になる。
#   初期重み: ノード b=0, O=−1.0, R=+0.3, H=+0.75（旧 H + H_node）, D=0
#             エッジ b=0, O=−1.0, R=+0.3, H=+0.5
# =============================================================================
NODE_TERMS: Tuple[str, ...] = ("O", "R", "H", "D_cond")
EDGE_TERMS: Tuple[str, ...] = ("O", "R", "H")
SPLIT_PRIOR = {"node": [0.0, -1.0, 0.3, 0.75, 0.0], "edge": [0.0, -1.0, 0.3, 0.5]}


class StructuredSplitCache(StructuredLearnedCache):
    term_names = NODE_TERMS                  # 見出し用（エッジは D_cond なし）
    policy_name = "structured_split"

    def __init__(self, capacity: int, sizer, cfg: LearnerConfig) -> None:
        super().__init__(capacity, sizer, cfg)
        from learned_cache import RLS
        self.models = {t: RLS(self._init_for(t), self.cfg.p0, self.cfg.forget)
                       for t in ("node", "edge")}

    def shared_model(self) -> bool:
        return False

    def terms_for(self, typ: str) -> Tuple[str, ...]:
        return EDGE_TERMS if typ == "edge" else NODE_TERMS

    def _init_for(self, typ: str) -> List[float]:
        n = 1 + len(self.terms_for(typ))
        mode = self.cfg.init
        if mode == "zero":
            return [0.0] * n
        if mode == "lru":                        # 経過時間だけ = LRU（配線の検証用）
            return [0.0, 0.0, 1.0] + [0.0] * (n - 3)
        if "," in mode:
            raise ValueError("structured_split は型ごとに次元が違うので、初期重みの直接指定は未対応")
        if mode == "prior":
            return list(SPLIT_PRIOR[typ])
        return [0.0] * n                         # 既定 auto = 全部 0

    def _features(self, key: Key, update_norms: bool) -> List[float]:
        x = super()._features(key, update_norms)   # [1, zO, zR, zH, H_node, D_cond]
        if entity_type(key) == "edge":
            return x[:4]
        return [x[0], x[1], x[2], x[3], x[5]]


def _selftest() -> None:
    import split_remote_server_reuse_score as srv   # noqa: F401  (sizer)
    sizer = srv.DecisionCacheSizer()
    c = StructuredLearnedCache(20, sizer, LearnerConfig())
    assert c.models["node"] is c.models["edge"], "重みは node/edge で共有"
    assert c.models["node"].w == [0.0] * (1 + len(TERMS)), "既定の初期重みは 0"
    assert StructuredLearnedCache(20, sizer, LearnerConfig(init="prior")).models["node"].w == PRIOR_WEIGHTS
    # エッジでは D_cond と H_node が 0
    k_edge = (0, "edge_1_2")
    c.set_context(k_edge, 3, 10.0)
    c[k_edge] = True
    x = c._features(k_edge, False)
    assert x[4] == 0.0 and x[5] == 0.0, x
    sp = StructuredSplitCache(20, sizer, LearnerConfig())
    assert sp.models["node"] is not sp.models["edge"]
    assert len(sp.models["node"].w) == 5 and len(sp.models["edge"].w) == 4
    sp.set_context(k_edge, 3, 10.0)
    sp[k_edge] = True
    assert len(sp._features(k_edge, False)) == 4
    # edc: 半減期ごとに 1/2 に減る
    e = StructuredLearnedCache(20, sizer, LearnerConfig(freq_scope="edc", decay_halflife=10.0))
    k = (0, "node:1")
    e.set_context(k, 1, 3.0)
    e[k] = True
    e.clock += 10
    assert abs(e._freq_value(k) - 0.5) < 1e-9, e._freq_value(k)
    print("structured_cache.py selftest OK")


if __name__ == "__main__":
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:0] = [here, os.path.join(os.path.dirname(here), "core")]
    _selftest()
