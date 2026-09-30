#!/usr/bin/env python3
# =============================================================================
# learned_cache.py — 重みをオンライン学習する加算スコアの認可キャッシュ（方式A / F1）
#
#   スコア（F1 = 素の値の加算。各特徴量はオンライン標準化した z 値）:
#
#       ŷ(e) = b + w_H·z(hop) + w_O·z(freq) + w_D·z(deg) + w_R·z(age)
#
#       ŷ = 「次にアクセスされるまでの距離」の予測（log1p、キャッシュ時計単位）
#       退去 = ŷ が最大のエントリ（= いちばん先まで使われないと予測したもの）
#
#   学習（LRB 型の教師あり・オンライン）:
#     1. 退去のたびに、犠牲者 + 無作為に選んだ k 個の候補の特徴量を記録する
#        （＝判断した時点の特徴量。age が伸びた状態で学習するため）
#     2. そのキーが次にアクセスされた時点で y = log1p(経過時計) を確定
#        W を過ぎても来なければ打ち切りとして y = log1p(2W)
#     3. 忘却係数付き RLS でサーバ別・型（node/edge）別の重みを更新
#
#   「顕著に訪問回数が多いキーのミスほど強く効かせる」:
#     退去したのに W 以内に再アクセスされた（＝自分の退去が起こしたミス）サンプルは
#     重み c = max(1, freq / 平均freq) で RLS に入れる（--miss-weight rel_freq）。
#
#   正しさのアンカー:
#     初期重み w_R=1, 他=0 かつ学習を止める（learn=False）と厳密に LRU と一致する
#     （z は age に単調、同点は last_access の古い方を退去）。
# =============================================================================
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

Key = Tuple[int, str]

FEATURES = ("H", "O", "D", "R")          # 距離 / 観測回数 / 次数 / 経過時間
DIM = 1 + len(FEATURES)                  # 先頭はバイアス
TYPES = ("node", "edge")


@dataclass
class LearnerConfig:
    learn: bool = True           # False で重みを固定（アンカー検証用）
    init: str = "auto"           # "auto": 方策の既定（learned=lru / structured=prior）
                                 # "lru": 経過時間だけ / "zero": 全0 / "prior": 分析の向き / "b,..." 直接指定
    forget: float = 0.999        # RLS 忘却係数 λ_f（実効記憶 ≈ 1/(1−λ_f) サンプル）
    p0: float = 1.0              # RLS 初期共分散（初期重みの弱さ）
    window: int = 1000           # memory window W（キャッシュ時計）
    sample_k: int = 8            # 退去1回あたり記録する非犠牲者の候補数
    miss_weight: str = "rel_freq"  # "rel_freq" / "none"
    features: str = "H,O,D,R"    # 使う特徴量（診断用に絞れる。外した特徴量は z=0 に固定）
    target: str = "dist"         # "dist": y = log1p(次回アクセスまでの距離)
                                 # "reuse": y = 0（滞在時間 h 以内に再利用された）/ 1（されなかった）
                                 #          h = 追い出し時の age の指数移動平均（≈ キャッシュに居られる時間）
                                 # "pairwise": 追い出しのたびに（追い出したキー, 残したキー）の組を作り、
                                 #          実際に後で使われた方のスコアが高くなるよう重みを直す（RankNet 型）
    pair_lr: float = 0.05        # pairwise の学習率（AdaGrad で特徴量ごとに自動で縮む）
    shared_model: bool = False   # True で node / edge が1つの重みを共有する（型ごとのバイアス差を無くす）
    log_features: str = ""       # log1p してから標準化する特徴量（例 "O" / "O,R"）。空 = 素の値（F1 そのまま）
    freq_scope: str = "cache"    # O の数え方。"cache": キャッシュに入ってからの回数（退去で 0 に戻る）
                                 #             "total": このサーバでの全アクセス回数（退去しても消さない）
                                 #             "edc":   減衰させた回数。1回のアクセスは半減期ごとに 1/2 の重みになる
                                 #                      （LRB の EDC と同じ。昔の常連が居座らない）
    decay_halflife: float = 1000.0  # edc の半減期（キャッシュ時計）
    hop_agg: str = "min"         # キーの hop を "min"（既存 bump_prior と同じ）か "last" で持つ
    z_clip: float = 3.0          # z 値の上限（裾の長い freq/age で RLS が発散するのを防ぐ）
    c_max: float = 10.0          # ミス重み c の上限
    trace_every: int = 200       # 重み履歴を記録する間隔（更新回数）
    seed: int = 0


def _init_weights(mode: str) -> List[float]:
    #          b    H     O     D    R
    if mode == "zero":
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    if mode == "prior":
        return [0.0, 0.5, -0.5, 0.0, 1.0]
    if "," in mode:                       # "b,H,O,D,R" を直接指定（診断用）
        w = [float(v) for v in mode.split(",")]
        if len(w) != DIM:
            raise ValueError(f"初期重みは {DIM} 個（b,H,O,D,R）: {mode}")
        return w
    return [0.0, 0.0, 0.0, 0.0, 1.0]     # lru / auto


class RunningNorm:
    """Welford。z は更新せずに引ける（スコア計算で分布を汚さない）。"""

    __slots__ = ("n", "mean", "m2")

    def __init__(self) -> None:
        self.n = 0.0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, x: float) -> None:
        self.n += 1.0
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)

    def z(self, x: float) -> float:
        if self.n < 2:
            return 0.0
        var = self.m2 / (self.n - 1.0)
        if var <= 1e-12:
            return 0.0
        return (x - self.mean) / math.sqrt(var)


class RLS:
    """重み付き・忘却係数付きの逐次最小二乗（d は小さいので素の Python で十分）。"""

    def __init__(self, w0: List[float], p0: float, forget: float) -> None:
        self.w = list(w0)
        self.d = len(w0)
        self.p0 = p0
        self._reset_P()
        self.lam = forget
        self.p_cap = p0 * 100.0  # 励起不足で P が発散するのを防ぐ上限（trace/d）
        self.updates = 0
        self.abs_err_sum = 0.0

    def _reset_P(self) -> None:
        self.P = [[self.p0 if i == j else 0.0 for j in range(self.d)] for i in range(self.d)]
        self.resets = getattr(self, "resets", 0) + 1

    def predict(self, x: List[float]) -> float:
        return sum(wi * xi for wi, xi in zip(self.w, x))

    def pair_update(self, dx: List[float], lr: float, c: float = 1.0) -> None:
        """RankNet 型: s(遠い) − s(近い) = w·dx を大きくする方向に1歩。

        損失 log(1 + exp(−w·dx)) の勾配は −σ(−w·dx)·dx。AdaGrad で特徴量ごとに歩幅を縮める。
        dx の先頭（バイアス）は常に 0 なので b は動かない（順位には関係しない）。
        """
        m = sum(wi * xi for wi, xi in zip(self.w, dx))
        g = c * (1.0 / (1.0 + math.exp(m)) if m > -30 else 1.0)   # σ(−m)
        G = getattr(self, "G", None)
        if G is None:
            G = self.G = [0.0] * self.d
        for i in range(1, self.d):
            gi = g * dx[i]
            if gi == 0.0:
                continue
            G[i] += gi * gi
            self.w[i] += lr * gi / (math.sqrt(G[i]) + 1e-8)
        self.updates += 1
        self.abs_err_sum += g            # pairwise では「外した度合い」σ(−m) を記録

    def update(self, x: List[float], y: float, c: float = 1.0) -> None:
        d, P = self.d, self.P
        Px = [sum(P[i][j] * x[j] for j in range(d)) for i in range(d)]
        xPx = sum(x[i] * Px[i] for i in range(d))
        if xPx < 0.0:            # 丸め誤差で P が正定値でなくなった → 初期化し直す
            self._reset_P()
            Px = [v * self.p0 for v in x]
            xPx = sum(v * v for v in x) * self.p0
        denom = self.lam + c * xPx
        k = [c * v / denom for v in Px]
        e = y - self.predict(x)
        self.abs_err_sum += abs(e)
        self.updates += 1
        for i in range(d):
            self.w[i] += k[i] * e
        for i in range(d):
            row = P[i]
            for j in range(d):
                row[j] -= k[i] * Px[j]
        # 対称化（非対称の丸め誤差が積もると発散する）
        trace = 0.0
        for i in range(d):
            for j in range(i + 1, d):
                m = 0.5 * (P[i][j] + P[j][i])
                P[i][j] = P[j][i] = m
            if P[i][i] <= 0.0:
                self._reset_P()
                return
            trace += P[i][i]
        if trace < self.p_cap * d:
            inv = 1.0 / self.lam
            for row in P:
                for j in range(d):
                    row[j] *= inv


class _Snap:
    __slots__ = ("key", "t", "x", "typ", "victim", "freq", "done", "h", "event", "gap")

    def __init__(self, key: Key, t: int, x: List[float], typ: str,
                 victim: bool, freq: float) -> None:
        self.key = key
        self.t = t
        self.x = x
        self.typ = typ
        self.victim = victim
        self.freq = freq
        self.done = False
        self.h = 0.0
        self.event: Optional[int] = None   # どの追い出し判断で記録したか
        self.gap: Optional[float] = None   # 実際の次回アクセスまでの距離（W 超えは inf）


def entity_type(key: Key) -> str:
    return "edge" if key[1].startswith("edge_") else "node"


class LearnedLinearCache:
    """PPRBoundedCache と同じ mapping 風インタフェース（in / [] / []=）。

    仮想サーバの authorize_candidate から、アクセス直前に set_context(key, hop, deg)
    が呼ばれる前提（adaptive/sim.py が差し込む）。
    """

    # --- 式を差し替えるときに上書きする部分（structured_cache.py 参照）-------------
    term_names: Tuple[str, ...] = FEATURES   # 重みの名前（先頭のバイアス b は含めない）
    norm_names: Tuple[str, ...] = FEATURES   # 標準化の基準を持つ量の名前
    policy_name = "learned"

    def shared_model(self) -> bool:
        return self.cfg.shared_model

    def terms_for(self, typ: str) -> Tuple[str, ...]:
        """型ごとの項の名前（node / edge で式を分けるときに上書きする）。"""
        return self.term_names

    def _initial_weights(self) -> List[float]:
        return _init_weights(self.cfg.init)

    def _features(self, key: Key, update_norms: bool) -> List[float]:
        """特徴ベクトル x（先頭 1.0 = バイアス）。update_norms=True のとき標準化の基準も更新する。"""
        typ = entity_type(key)
        tv = self._tf(self._raw(key))
        if update_norms:
            nm = self.norms[typ]
            for f, v in zip(FEATURES, tv):
                nm[f].add(v)
        return self._x(typ, tv)

    def __init__(self, capacity: int, sizer, cfg: LearnerConfig) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.sizer = sizer
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.clock = 0
        self.store: Dict[Key, bool] = {}
        self.weights: Dict[Key, int] = {}
        self.last_access: Dict[Key, int] = {}
        self.freq: Dict[Key, int] = {}
        self.total_count: Dict[Key, int] = {}   # freq_scope="total" 用。退去しても消さない
        self.hop: Dict[Key, int] = {}
        self.deg: Dict[Key, float] = {}
        self.total_weight = 0
        self._ctx: Optional[Tuple[Key, int, float]] = None
        if self.shared_model():
            one = RLS(self._initial_weights(), cfg.p0, cfg.forget)
            self.models = {t: one for t in TYPES}
        else:
            self.models = {t: RLS(self._initial_weights(), cfg.p0, cfg.forget) for t in TYPES}
        # z の基準はサーバ内で node / edge 共通にする。追い出しは型をまたいで ŷ を比べるので、
        # 型ごとに基準が違うと同じ age でも z が変わり、比較がずれる（固定重み LRU が LRU と一致しなくなる）。
        # モデル（重み）は型ごとのまま。共通基準との差は型ごとの重みとバイアスが吸収する。
        shared = {f: RunningNorm() for f in self.norm_names}
        self._active = {f.strip() for f in cfg.features.split(",") if f.strip()}
        self._h = 0.0                     # target="reuse" の滞在時間 h（追い出し時 age の EWMA）
        self._freq_mean = RunningNorm()   # ミス重み用の「素の回数」の平均（O を log にしても変わらない）
        self._log_set = {f.strip() for f in cfg.log_features.split(",") if f.strip()}
        bad = self._log_set - set(FEATURES)
        if bad:
            raise ValueError(f"log_features に未知の特徴量: {bad}（使えるのは {FEATURES}）")
        self.norms = {t: shared for t in TYPES}
        self.pending: Dict[Key, List[_Snap]] = {}
        self.queue: Deque[_Snap] = deque()
        self.history: List[Dict[str, Any]] = []
        self.insertions = 0
        self.evictions = 0
        self.rejected_too_large = 0
        self.samples = {"hit_label": 0, "miss_label": 0, "censored": 0}
        self._edc: Dict[Key, List[float]] = {}     # freq_scope="edc": key -> [値, 最終更新時刻]
        self._events: Dict[int, Dict[str, Any]] = {}
        self._next_event = 0
        # 追い出しの判断の質（関連研究に倣った指標。すべて累積カウンタ）
        #   good   : 追い出したキーの次回アクセスが境界 h より先（LRB の good decision）
        #   regret : 追い出したキーが h 以内に戻ってきた（LeCaR / CACHEUS の後悔）
        #   pairs / pairs_correct : （追い出したキー, 残したキー）の組のうち、
        #            追い出した方が本当に後で使われた（または両方来なかった以外で同順）割合
        self._xstat: Dict[int, List[RunningNorm]] = {}   # id(モデル) -> 特徴ごとの Welford
        self.decision = {"decisions": 0, "good": 0, "regret": 0, "pairs": 0, "pairs_correct": 0}

    # --- 文脈（hop / 次数）----------------------------------------------------
    def set_context(self, key: Key, hop: int, deg: float) -> None:
        self._ctx = (key, int(hop), float(deg))

    def _apply_context(self, key: Key) -> None:
        if self._ctx is None or self._ctx[0] != key:
            return
        _, hop, deg = self._ctx
        self._ctx = None
        cur = self.hop.get(key)
        if cur is None or self.cfg.hop_agg == "last" or hop < cur:
            self.hop[key] = hop
        self.deg[key] = deg

    # --- 特徴量 ----------------------------------------------------------------
    def _raw(self, key: Key) -> Tuple[float, float, float, float]:
        o = self._freq_value(key)
        return (float(self.hop.get(key, 0)), o,
                float(self.deg.get(key, 1.0)),
                float(self.clock - self.last_access.get(key, self.clock)))

    def _freq_value(self, key: Key) -> float:
        """O の値（freq_scope に応じて）。"""
        scope = self.cfg.freq_scope
        if scope == "total":
            return float(self.total_count.get(key, 1))
        if scope == "edc":
            e = self._edc.get(key)
            if e is None:
                return 1.0
            return e[0] * 0.5 ** ((self.clock - e[1]) / self.cfg.decay_halflife)
        return float(self.freq.get(key, 1))

    def _count_access(self, key: Key) -> None:
        self.total_count[key] = self.total_count.get(key, 0) + 1
        e = self._edc.get(key)
        if e is None:
            self._edc[key] = [1.0, float(self.clock)]
        else:
            e[0] = e[0] * 0.5 ** ((self.clock - e[1]) / self.cfg.decay_halflife) + 1.0
            e[1] = float(self.clock)

    def _tf(self, raw) -> Tuple[float, ...]:
        """裾の長い特徴量を log1p で圧縮する（標準化の前）。

        回数や経過時間は裾が長く、素のまま z にすると標準偏差が大きくなり、
        追い出しが集中する低い値どうし（回数 1〜3 回など）の差が z でほぼ消える。
        """
        logs = self._log_set
        if not logs:
            return tuple(raw)
        return tuple(math.log1p(max(0.0, v)) if f in logs else v
                     for f, v in zip(FEATURES, raw))

    def _x(self, typ: str, raw) -> List[float]:
        nm = self.norms[typ]
        zc = self.cfg.z_clip
        act = self._active
        return [1.0] + [max(-zc, min(zc, nm[f].z(v))) if f in act else 0.0
                        for f, v in zip(FEATURES, raw)]

    def predict(self, key: Key) -> float:
        return self.models[entity_type(key)].predict(self._features(key, False))

    # --- ラベル確定 ------------------------------------------------------------
    def _emit(self, s: _Snap, y: float, c: float) -> None:
        s.done = True
        if not self.cfg.learn:
            return
        m = self.models[s.typ]
        m.update(s.x, y, c)
        if m.updates % self.cfg.trace_every == 0:
            self.history.append({"clock": self.clock, "type": s.typ,
                                 "updates": m.updates,
                                 **{n: round(v, 5) for n, v in zip(("b",) + self.terms_for(s.typ), m.w)}})

    def _resolve(self, key: Key) -> None:
        snaps = self.pending.pop(key, None)
        if not snaps:
            return
        for s in snaps:
            if s.done:
                continue
            if s.victim:
                self.samples["miss_label"] += 1
            else:
                self.samples["hit_label"] += 1
            self._on_label(s, float(self.clock - s.t))

    def _miss_c(self, s: _Snap) -> float:
        """追い出したのに戻ってきたサンプルの重み（訪問回数が多いほど強く効かせる）。"""
        if self.cfg.miss_weight != "rel_freq":
            return 1.0
        mean_f = self._freq_mean.mean or 1.0
        return min(self.cfg.c_max, max(1.0, s.freq / mean_f))

    def _on_label(self, s: _Snap, gap: float) -> None:
        """ラベル（実際の次回アクセスまでの距離。W 超えは inf）が確定したとき。"""
        s.gap = gap
        tgt = self.cfg.target
        if tgt == "pairwise":
            s.done = True
        else:
            if gap == math.inf:
                y = 1.0 if tgt == "reuse" else math.log1p(2 * self.cfg.window)
                c = 1.0
            else:
                y = (0.0 if gap <= s.h else 1.0) if tgt == "reuse" else math.log1p(gap)
                c = self._miss_c(s) if s.victim else 1.0
            self._emit(s, y, c)
        ev = self._events.get(s.event) if s.event is not None else None
        if ev is not None:
            ev["open"] -= 1
            if ev["open"] == 0:
                self._finalize_event(self._events.pop(s.event))

    def _finalize_event(self, ev: Dict[str, Any]) -> None:
        """1回の追い出し判断の全サンプルのラベルが揃った: 判断の質を数え、pairwise なら学習する。"""
        v: _Snap = ev["victim"]
        d = self.decision
        d["decisions"] += 1
        if v.gap > v.h:
            d["good"] += 1
        else:
            d["regret"] += 1
        pair = self.cfg.target == "pairwise" and self.cfg.learn
        for o in ev["others"]:
            if v.gap == math.inf and o.gap == math.inf:
                continue                       # 両方来なかった: どちらを追い出しても同じ
            d["pairs"] += 1
            if v.gap >= o.gap:
                d["pairs_correct"] += 1
            if not pair or v.gap == o.gap:
                continue
            far, near = (v, o) if v.gap > o.gap else (o, v)
            m = self.models[far.typ]
            if m is not self.models[near.typ] or len(far.x) != len(near.x):
                continue                       # 型ごとに別の重み（structured_split）なら型をまたぐ組は使わない
            c = self._miss_c(v) if near is v else 1.0   # 追い出した方が先に戻った = 高くついた誤り
            m.pair_update([a - b for a, b in zip(far.x, near.x)], self.cfg.pair_lr, c)
            if m.updates % self.cfg.trace_every == 0:
                self.history.append({"clock": self.clock, "type": far.typ, "updates": m.updates,
                                     **{n: round(x, 5) for n, x in
                                        zip(("b",) + self.terms_for(far.typ), m.w)}})

    def _expire(self) -> None:
        W = self.cfg.window
        q = self.queue
        while q and self.clock - q[0].t > W:
            s = q.popleft()
            if s.done:
                continue
            self.samples["censored"] += 1
            self._on_label(s, math.inf)
            lst = self.pending.get(s.key)
            if lst is not None:
                lst[:] = [o for o in lst if not o.done]
                if not lst:
                    del self.pending[s.key]

    def _snapshot(self, key: Key, victim: bool, event: Optional[int] = None) -> _Snap:
        typ = entity_type(key)
        freq = self._raw(key)[1]
        self._freq_mean.add(freq)
        s = _Snap(key, self.clock, self._features(key, True), typ, victim, freq)
        # 学習に入れた特徴ベクトルのばらつき（モデルごと）。実効的な重み = 重み × 標準偏差 に使う
        xs = self._xstat.setdefault(id(self.models[typ]), [RunningNorm() for _ in s.x])
        for nm_i, v in zip(xs, s.x):
            nm_i.add(v)
        s.h = min(float(self.cfg.window), self._h)
        s.event = event
        self.pending.setdefault(key, []).append(s)
        self.queue.append(s)
        return s

    # --- mapping インタフェース -------------------------------------------------
    def __contains__(self, key: Key) -> bool:
        return key in self.store

    def __getitem__(self, key: Key) -> bool:
        self.clock += 1
        self._apply_context(key)
        self.freq[key] = self.freq.get(key, 1) + 1
        self._count_access(key)
        self.last_access[key] = self.clock
        self._resolve(key)
        self._expire()
        return self.store[key]

    def __setitem__(self, key: Key, value: bool) -> None:
        value = bool(value)
        w = self.sizer.entry_weight(key, value)
        if w > self.capacity:
            self.rejected_too_large += 1
            return
        self.clock += 1
        self._apply_context(key)
        self._count_access(key)
        self._resolve(key)          # 退去済みキーの再来 = 自分の退去が起こしたミス
        self._expire()
        self.last_access[key] = self.clock
        if key in self.store:
            self.total_weight += w - self.weights.get(key, 0)
            self.store[key] = value
            self.weights[key] = w
        else:
            self.store[key] = value
            self.freq[key] = 1
            self.weights[key] = w
            self.total_weight += w
            self.insertions += 1
        self._evict_until_fit()

    def __len__(self) -> int:
        return len(self.store)

    def _evict_until_fit(self) -> None:
        while self.total_weight > self.capacity and self.store:
            la = self.last_access
            victim = max(self.store, key=lambda k: (self.predict(k), -la.get(k, 0)))
            # 学習を止めていても記録はする（z の基準 = 平均・標準偏差を更新するため。
            # 止めると全キーの z が 0 のまま → 固定重みを与えても LRU に縮退してしまう）
            age_v = self.clock - la.get(victim, self.clock)
            self._h = age_v if self._h == 0.0 else 0.99 * self._h + 0.01 * age_v
            others = [k for k in self.store if k != victim]
            if len(others) > self.cfg.sample_k:
                others = self.rng.sample(others, self.cfg.sample_k)
            eid = self._next_event
            self._next_event += 1
            vs = self._snapshot(victim, victim=True, event=eid)
            os_ = [self._snapshot(k, victim=False, event=eid) for k in others]
            self._events[eid] = {"victim": vs, "others": os_, "open": 1 + len(os_)}
            self.total_weight -= self.weights.pop(victim, 0)
            self.store.pop(victim, None)
            self.freq.pop(victim, None)
            self.last_access.pop(victim, None)
            self.hop.pop(victim, None)
            self.evictions += 1

    # --- 集計 ------------------------------------------------------------------
    def current_weight(self) -> int:
        return self.total_weight

    def max_weight(self) -> Optional[int]:
        return self.capacity

    def storage_objects(self) -> Dict[str, Any]:
        return {"store": self.store}

    def model_summary(self) -> Dict[str, Any]:
        out = {}
        items = [("all", self.models[TYPES[0]])] if self.shared_model() else self.models.items()
        for t, m in items:
            names = ("b",) + self.terms_for("node" if t == "all" else t)
            d = {"updates": m.updates, "resets": m.resets - 1,
                 "mae": (m.abs_err_sum / m.updates) if m.updates else None,
                 **{n: v for n, v in zip(names, m.w)}}
            # 実効的な重み = 重み × その特徴量の標準偏差（追い出しの判断に実際にどれだけ効いているか）。
            #   z 値の項は標準偏差 ≈ 1 なので重みとほぼ同じ。積の項（D_cond など）はばらつきが小さいので、
            #   重みが大きくても実効的には小さい。
            xs = self._xstat.get(id(m))
            if xs:
                for i, n in enumerate(names):
                    if i == 0 or i >= len(xs):
                        continue
                    sd = math.sqrt(xs[i].m2 / (xs[i].n - 1)) if xs[i].n > 1 else 0.0
                    d[f"eff_{n}"] = m.w[i] * sd
            out[t] = d
        return out

    def stats(self) -> Dict[str, Any]:
        return {"policy": self.policy_name, "term_names": list(self.term_names),
                "term_names_by_type": {t: list(self.terms_for(t)) for t in TYPES},
                "entries": len(self.store),
                "current_weight": self.total_weight, "max_weight": self.capacity,
                "insertions": self.insertions, "evictions": self.evictions,
                "samples": dict(self.samples), "decision": dict(self.decision),
                "models": self.model_summary()}


# =============================================================================
# 自己検証
# =============================================================================
def _selftest() -> None:
    # RLS が既知の線形関係を復元できる
    rng = random.Random(1)
    true = [0.5, 1.0, -2.0, 0.0, 0.7]
    m = RLS([0.0] * DIM, p0=1.0, forget=1.0)
    for _ in range(3000):
        x = [1.0] + [rng.gauss(0, 1) for _ in range(DIM - 1)]
        m.update(x, sum(a * b for a, b in zip(true, x)) + rng.gauss(0, 0.1))
    assert all(abs(a - b) < 0.05 for a, b in zip(m.w, true)), m.w

    # 重み c が大きいサンプルほど強く引っ張る
    ma = RLS([0.0] * DIM, 1.0, 1.0)
    mb = RLS([0.0] * DIM, 1.0, 1.0)
    x = [1.0, 1.0, 0.0, 0.0, 0.0]
    ma.update(x, 1.0, c=1.0)
    mb.update(x, 1.0, c=5.0)
    assert mb.w[1] > ma.w[1]
    print("learned_cache.py selftest OK")


if __name__ == "__main__":
    _selftest()
