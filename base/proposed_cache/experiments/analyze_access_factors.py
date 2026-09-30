#!/usr/bin/env python3
# =============================================================================
# analyze_access_factors.py
#
#   目的:
#     ランダムウォーク (RW) の「認可アクセス回数」に対して、キャッシュのスコア式
#     で使っている 4 要素が **それぞれ独立にどれだけ効いているか** を測る。
#
#       D  次数            (node = 隣接エッジ数 / edge = 両端点次数の平均)
#       H  視点からの hop 数 (二部グラフ上の BFS 距離。視点 = 始点ノード)
#       O  過去のアクセス回数 (観測窓 [0,t0) での出現回数)
#       R  過去に訪れた時刻   (最終アクセスからの経過 age = t0 - last_access)
#
#   視点 (始点) は複数・無作為に取れる (--num-starts)。各視点ごとに独立に集計し、
#   視点間のばらつき (mean ± sd) も出す。
#
#   2 種類の「関係」を測る:
#     (A) 静的な関係   : 構造量 (D,H) と **総アクセス回数** の関係
#     (B) 予測的な関係 : 観測窓で見える 4 要素 と **窓の後の将来アクセス回数** の関係
#                        ← キャッシュが本当に必要としているのはこちら
#
#   出力:
#     results/accessfactors_<label>_keys.csv      キーごとの特徴量と目的変数 (任意)
#     results/accessfactors_<label>_summary.csv   相関・カバレッジの視点別まとめ
#     figs/accessfactors_<label>_univariate.png   要素ごとの単変量応答
#     figs/accessfactors_<label>_pairs.png        2 要素ごとの交互作用ヒートマップ
#
#   アクセス列は本家 (core/virtual_server_sim_reuse_score.py) の歩行関数を
#   そのまま呼んで記録する (authorize_candidate をラップするだけ)。既存ファイルは
#   一切変更しない。
# =============================================================================
from __future__ import annotations

import argparse
import csv
import importlib.util
import math
import os
import random
import sys
from collections import Counter, deque, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:          # cap サーバ等で numpy が無い場合でも
    np = None                # --collect-only (収集だけ) は動くようにする

_THIS = Path(__file__).resolve()
_CORE = _THIS.parent.parent / "core"


def _load_sim():
    sys.path.insert(0, str(_CORE))
    spec = importlib.util.spec_from_file_location(
        "virtual_server_sim_reuse_score", _CORE / "virtual_server_sim_reuse_score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sim = _load_sim()
srv = sim.srv


# =============================================================================
# 1. アクセス列の記録
# =============================================================================
class AccessRecorder:
    """sim.authorize_candidate をラップして (start, entity, hop) を記録する。
    キャッシュは policy='none' なので判定ロジックには一切干渉しない。"""

    def __init__(self) -> None:
        self.starts: List[int] = []
        self.entities: List[Any] = []
        self.hops: List[int] = []
        self._orig = sim.authorize_candidate

    def __enter__(self):
        orig = self._orig
        rec = self

        def wrapped(cluster, server, start_node, target, hops_done, node_only):
            rec.starts.append(int(start_node))
            rec.entities.append(target)
            rec.hops.append(int(hops_done))
            return orig(cluster, server, start_node, target, hops_done, node_only)

        sim.authorize_candidate = wrapped
        return self

    def __exit__(self, *exc):
        sim.authorize_candidate = self._orig
        return False


def run_walks(cluster, starts: Sequence[int], walks: int, alpha: float,
              seed: int, max_hops: int, no_backtrack: bool
              ) -> Dict[int, List[Tuple[Any, int]]]:
    """視点ごとのアクセス列 {start: [(entity, hop), ...]} を返す。"""
    per_start: Dict[int, List[Tuple[Any, int]]] = {}
    for start in starts:
        with AccessRecorder() as rec:
            for i in range(walks):
                rng = random.Random(seed + i)          # 本家 /walk と同じ seed 規則
                sim.run_walk(cluster, int(start), alpha, rng, max_hops,
                             False, no_backtrack)
        per_start[int(start)] = list(zip(rec.entities, rec.hops))
    return per_start


# =============================================================================
# 2. 構造量 (次数・BFS hop)
# =============================================================================
def build_degree_map(cluster) -> Dict[Any, float]:
    """node: 隣接エッジ数 / edge: 両端点次数の平均。
    (本家の cluster.degree() は edge に対して常に 2 を返すので、edge の '次数' は
     端点次数で代表させる。これを使わないと edge 側の次数軸が定数になる。)"""
    nm = cluster.neighbor_map
    deg: Dict[Any, float] = {}
    for ent, nbrs in nm.items():
        if isinstance(ent, int):
            deg[ent] = float(len(nbrs))
    for ent, nbrs in nm.items():
        if not isinstance(ent, int):
            du = deg.get(nbrs[0], 1.0)
            dv = deg.get(nbrs[1], 1.0) if len(nbrs) > 1 else du
            deg[ent] = 0.5 * (du + dv)
    return deg


def bfs_distance(cluster, start: int, wanted: set) -> Dict[Any, int]:
    """二部グラフ (node <-> edge) 上の BFS 距離。wanted に含まれる実体だけ返す。"""
    nm = cluster.neighbor_map
    dist = {start: 0}
    out: Dict[Any, int] = {}
    if start in wanted:
        out[start] = 0
    remaining = len(wanted) - len(out)
    dq = deque([start])
    while dq and remaining > 0:
        cur = dq.popleft()
        d = dist[cur] + 1
        for nb in nm.get(cur, ()):
            if nb in dist:
                continue
            dist[nb] = d
            if nb in wanted:
                out[nb] = d
                remaining -= 1
            dq.append(nb)
    return out


# =============================================================================
# 3. 統計ユーティリティ
# =============================================================================
def rankdata(a: np.ndarray) -> np.ndarray:
    """平均順位 (tie は平均)。scipy 非依存。"""
    a = np.asarray(a, dtype=float)
    n = a.size
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    sa = a[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3:
        return float("nan")
    rx, ry = rankdata(x), rankdata(y)
    sx, sy = rx.std(), ry.std()
    if sx < 1e-12 or sy < 1e-12:
        return float("nan")
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))


def partial_spearman(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> float:
    """z を統制した x-y の順位偏相関 (順位に直してから線形残差)。"""
    if x.size < 4:
        return float("nan")
    rx, ry, rz = rankdata(x), rankdata(y), rankdata(z)
    if rz.std() < 1e-12:
        return spearman(x, y)

    def resid(r):
        b = np.polyfit(rz, r, 1)
        return r - (b[0] * rz + b[1])

    ex, ey = resid(rx), resid(ry)
    if ex.std() < 1e-12 or ey.std() < 1e-12:
        return float("nan")
    return float(((ex - ex.mean()) * (ey - ey.mean())).mean() / (ex.std() * ey.std()))


def topc_coverage(score: np.ndarray, y: np.ndarray, cap: int) -> float:
    """score 上位 cap 件が将来アクセスの何 % を覆うか (= 単一要素で作ったキャッシュの
    理論的な取り分。ヒット率そのものではないが、比較可能な上界の指標)。"""
    tot = y.sum()
    if tot <= 0 or score.size == 0:
        return float("nan")
    cap = min(cap, score.size)
    idx = np.argsort(-score, kind="mergesort")[:cap]
    return float(100.0 * y[idx].sum() / tot)


def binned_mean(x: np.ndarray, y: np.ndarray, nbins: int
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """x の分位ビンごとの y 平均 (値の重複が多い軸でも落ちないよう unique 境界)。"""
    qs = np.unique(np.quantile(x, np.linspace(0, 1, nbins + 1)))
    if qs.size < 2:
        return np.array([x.mean()]), np.array([y.mean()]), np.array([y.size])
    idx = np.clip(np.digitize(x, qs[1:-1], right=True), 0, qs.size - 2)
    xs, ys, ns = [], [], []
    for b in range(qs.size - 1):
        m = idx == b
        if m.sum() == 0:
            continue
        xs.append(float(x[m].mean()))
        ys.append(float(y[m].mean()))
        ns.append(int(m.sum()))
    return np.array(xs), np.array(ys), np.array(ns)


# =============================================================================
# 4. 特徴量テーブルの構築
# =============================================================================
FEATURES = [
    ("deg", "Degree D"),
    ("hop", "Hop from start H"),
    ("past", "Past access count O"),
    ("recency", "Recency (1/age) R"),
]


# hop の定義は 3 通り持つ (どれを使うかは --hop-metric で選ぶ)
#   hop          : 二部グラフ上の BFS 距離 (最短距離)
#   hop_walk     : 初めてアクセスされたときの **歩数** (RW が何ホップ目で踏んだか)
#   hop_walk_min : 全アクセス中の最小歩数
KEY_COLUMNS = ["start", "entity", "is_edge", "deg", "hop", "hop_walk",
               "hop_walk_min", "total", "past", "future", "age"]
HOP_METRICS = {"walk": "hop_walk", "walk_min": "hop_walk_min", "bfs": "hop"}


def compute_key_rows(cluster, access_seq: List[Tuple[Any, int]], start: int,
                     split: float, deg_map: Dict[Any, float],
                     owner_filter: Optional[int]
                     ) -> Tuple[List[List[Any]], Dict[str, int]]:
    """1 視点分のアクセス列 → キー (=この視点で触れた実体) ごとの行。
    **numpy を使わない** (cap サーバでの収集専用モードのため)。
    age は「観測窓内で一度も触れていない」場合 -1 (= 欠測) とする。"""
    n = len(access_seq)
    t0 = int(round(n * split))

    total: Counter = Counter()
    past: Counter = Counter()
    fut: Counter = Counter()
    last: Dict[Any, int] = {}
    first_hop: Dict[Any, int] = {}
    min_hop: Dict[Any, int] = {}
    for t, (ent, hop) in enumerate(access_seq):
        total[ent] += 1
        if ent not in first_hop:
            first_hop[ent] = hop
        if hop < min_hop.get(ent, 1 << 30):
            min_hop[ent] = hop
        if t < t0:
            past[ent] += 1
            last[ent] = t
        else:
            fut[ent] += 1

    ents = list(total.keys())
    if owner_filter is not None:
        ents = [e for e in ents if cluster.owner(e) == owner_filter]
    bfs = bfs_distance(cluster, start, set(ents))

    rows = [[start, str(e), 0 if isinstance(e, int) else 1,
             float(deg_map.get(e, 1.0)),
             float(bfs.get(e, first_hop.get(e, -1))),
             float(first_hop.get(e, -1)),
             float(min_hop.get(e, -1)),
             float(total[e]), float(past.get(e, 0)), float(fut.get(e, 0)),
             float(t0 - last[e]) if e in last else -1.0]
            for e in ents]
    return rows, {"t0": t0, "n_access": n}


def rows_to_table(rows: List[List[Any]], meta: Dict[str, int],
                  hop_metric: str = "walk", columns: List[str] = None
                  ) -> Dict[str, Any]:
    """compute_key_rows の行 → numpy テーブル (分析・作図側が使う)。"""
    col = {name: i for i, name in enumerate(columns or KEY_COLUMNS)}

    def arr(name, dtype=float):
        return np.array([r[col[name]] for r in rows], dtype=dtype)

    age = arr("age")
    age[age < 0] = np.nan                       # 窓内未観測 = 欠測
    rec = {
        "entity": np.array([r[col["entity"]] for r in rows], dtype=object),
        "is_edge": arr("is_edge") > 0.5,
        "deg": arr("deg"),
        # 分析で使う "hop" は --hop-metric が指すもの (既定 = 歩数)
        "hop": arr(HOP_METRICS.get(hop_metric, "hop_walk")
                   if HOP_METRICS.get(hop_metric, "hop_walk") in col else "hop"),
        "hop_bfs": arr("hop"),
        "hop_walk": arr("hop_walk"),
        "total": arr("total"),
        "past": arr("past"),
        "future": arr("future"),
        "age": age,
    }
    rec["recency"] = 1.0 / (1.0 + rec["age"])   # 新しいほど大 (単調変換)
    rec["t0"] = meta.get("t0", 0)
    rec["n_access"] = meta.get("n_access", 0)
    return rec


def build_table(cluster, access_seq, start, split, deg_map, owner_filter,
                hop_metric: str = "walk"):
    rows, meta = compute_key_rows(cluster, access_seq, start, split, deg_map,
                                  owner_filter)
    return rows_to_table(rows, meta, hop_metric)


def largest_component_nodes(cluster) -> List[int]:
    """最大連結成分に属するノード (二部グラフ上の BFS。純標準ライブラリ)。"""
    nm = cluster.neighbor_map
    seen: Dict[int, int] = {}
    comps: List[List[int]] = []
    for s0 in cluster.nodes:
        if s0 in seen:
            continue
        comp = [s0]
        seen[s0] = len(comps)
        dq = deque([s0])
        while dq:
            cur = dq.popleft()
            for nb in nm.get(cur, ()):
                if isinstance(nb, int):
                    if nb not in seen:
                        seen[nb] = len(comps)
                        comp.append(nb)
                        dq.append(nb)
                else:
                    for nb2 in nm.get(nb, ()):
                        if nb2 not in seen:
                            seen[nb2] = len(comps)
                            comp.append(nb2)
                            dq.append(nb2)
        comps.append(comp)
    return max(comps, key=len)


# =============================================================================
# 5. 1 視点分の分析
# =============================================================================
def analyse_start(rec: Dict[str, np.ndarray], cap: int, kind: str) -> Dict[str, float]:
    """kind: 'all' / 'node' / 'edge'"""
    if kind == "node":
        m = ~rec["is_edge"]
    elif kind == "edge":
        m = rec["is_edge"]
    else:
        m = np.ones_like(rec["is_edge"], dtype=bool)

    out: Dict[str, float] = {"kind": kind, "n_keys": int(m.sum())}
    if m.sum() < 5:
        return out

    # --- (A) 静的な関係: 構造量 vs 総アクセス回数 ---------------------------
    tot = rec["total"][m]
    out["rho_deg_total"] = spearman(rec["deg"][m], tot)
    out["rho_hop_total"] = spearman(rec["hop"][m], tot)

    # --- (B) 予測的な関係: 観測窓で見えた 4 要素 vs 将来アクセス回数 -------
    seen = m & (rec["past"] > 0)
    out["n_seen"] = int(seen.sum())
    y = rec["future"][seen]
    out["future_total"] = float(y.sum())
    if seen.sum() < 5 or y.sum() <= 0:
        return out

    feats = {
        "deg": rec["deg"][seen],
        "hop": rec["hop"][seen],
        "past": rec["past"][seen],
        "recency": rec["recency"][seen],
    }
    # 「大きいほど残すべき」向きに揃えてから上位 C を取る (hop だけ符号反転)
    direction = {"deg": +1.0, "hop": -1.0, "past": +1.0, "recency": +1.0}
    for name, x in feats.items():
        out[f"rho_{name}"] = spearman(x, y)
        out[f"cov{cap}_{name}"] = topc_coverage(direction[name] * x, y, cap)
    # 統制付き (O を統制 / 他要素を統制)
    for name, x in feats.items():
        if name != "past":
            out[f"rho_{name}|past"] = partial_spearman(x, y, feats["past"])
    out["rho_past|recency"] = partial_spearman(feats["past"], y, feats["recency"])
    out["rho_deg|hop"] = partial_spearman(feats["deg"], y, feats["hop"])
    out["rho_hop|deg"] = partial_spearman(feats["hop"], y, feats["deg"])

    # 参照値
    out[f"cov{cap}_oracle"] = topc_coverage(y, y, cap)
    out[f"cov{cap}_random"] = float(100.0 * min(cap, y.size) / y.size)
    # 要素間の相関 (冗長性)
    names = list(feats)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            out[f"xx_{names[i]}_{names[j]}"] = spearman(feats[names[i]], feats[names[j]])
    return out


# =============================================================================
# 6. 作図
# =============================================================================
def plot_univariate(pooled: Dict[str, np.ndarray], label: str, out_png: Path,
                    nbins: int, cap: int, summary_mean: Dict[str, float]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seen = pooled["past"] > 0
    y = pooled["future"][seen]
    fig, axes = plt.subplots(2, 4, figsize=(17, 7.2))

    # 上段: 4 要素 vs 将来アクセス回数 (node/edge 別)
    for k, (fname, flabel) in enumerate(FEATURES):
        ax = axes[0][k]
        for kind, color, mk in (("node", "#1f77b4", "o"), ("edge", "#d62728", "s")):
            sel = (pooled["is_edge"][seen] if kind == "edge"
                   else ~pooled["is_edge"][seen])
            if sel.sum() < 10:
                continue
            xs, ys, ns = binned_mean(pooled[fname][seen][sel], y[sel], nbins)
            ax.plot(xs, ys, marker=mk, color=color, label=f"{kind} (n={sel.sum():,})")
        if fname in ("deg", "past", "recency"):
            ax.set_xscale("log")
        ax.set_xlabel(flabel)
        ax.set_ylabel("mean future accesses")
        r = summary_mean.get(f"rho_{fname}", float("nan"))
        rp = summary_mean.get(f"rho_{fname}|past", float("nan"))
        sub = f"Spearman ρ={r:+.3f}"
        if not math.isnan(rp):
            sub += f"  (ρ|O={rp:+.3f})"
        ax.set_title(f"{flabel}\n{sub}", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)

    # 下段左2: 構造量 vs 総アクセス回数
    for k, (fname, flabel) in enumerate((("deg", "Degree D"), ("hop", "Hop H"))):
        ax = axes[1][k]
        for kind, color, mk in (("node", "#1f77b4", "o"), ("edge", "#d62728", "s")):
            sel = pooled["is_edge"] if kind == "edge" else ~pooled["is_edge"]
            if sel.sum() < 10:
                continue
            xs, ys, ns = binned_mean(pooled[fname][sel], pooled["total"][sel], nbins)
            ax.plot(xs, ys, marker=mk, color=color, label=kind)
        if fname == "deg":
            ax.set_xscale("log")
        ax.set_xlabel(flabel)
        ax.set_ylabel("mean TOTAL accesses")
        r = summary_mean.get(f"rho_{fname}_total", float("nan"))
        ax.set_title(f"{flabel} vs total accesses\nSpearman ρ={r:+.3f}", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)

    # 下段3: top-C カバレッジ
    ax = axes[1][2]
    names = [f[0] for f in FEATURES]
    vals = [summary_mean.get(f"cov{cap}_{n}", float("nan")) for n in names]
    ax.bar(range(len(names)), vals, color="#4c72b0")
    for ref, c, ls in ((summary_mean.get(f"cov{cap}_oracle", float("nan")), "k", "--"),
                       (summary_mean.get(f"cov{cap}_random", float("nan")), "gray", ":")):
        if not math.isnan(ref):
            ax.axhline(ref, color=c, ls=ls, lw=1.2)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([f[1].split()[0] for f in FEATURES], fontsize=9)
    ax.set_ylabel(f"% of future accesses in top-{cap}")
    ax.set_title(f"Single-factor top-{cap} coverage\n(dashed=oracle, dotted=random)",
                 fontsize=10)
    ax.grid(alpha=0.3, axis="y")

    # 下段4: 要素間の冗長性
    ax = axes[1][3]
    mat = np.full((4, 4), np.nan)
    for i in range(4):
        mat[i][i] = 1.0
        for j in range(i + 1, 4):
            v = summary_mean.get(f"xx_{names[i]}_{names[j]}", float("nan"))
            mat[i][j] = mat[j][i] = v
    im = ax.imshow(mat, vmin=-1, vmax=1, cmap="coolwarm")
    ax.set_xticks(range(4)); ax.set_yticks(range(4))
    lbl = [f[1].split()[0] for f in FEATURES]
    ax.set_xticklabels(lbl, fontsize=8); ax.set_yticklabels(lbl, fontsize=8)
    for i in range(4):
        for j in range(4):
            if not math.isnan(mat[i][j]):
                ax.text(j, i, f"{mat[i][j]:+.2f}", ha="center", va="center", fontsize=8)
    ax.set_title("Redundancy between factors\n(Spearman)", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046)

    fig.suptitle(f"Access-count factors — {label}", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def plot_pairs(pooled: Dict[str, np.ndarray], label: str, out_png: Path,
               nbins: int) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    seen = pooled["past"] > 0
    y = pooled["future"][seen]
    pairs = [(a, b) for i, (a, _) in enumerate(FEATURES)
             for (b, _) in FEATURES[i + 1:]]
    fig, axes = plt.subplots(2, 3, figsize=(16, 8.5))
    lut = dict(FEATURES)

    def bin_axis(x):
        q = np.unique(np.quantile(x, np.linspace(0, 1, nbins + 1)))
        idx = np.clip(np.digitize(x, q[1:-1], right=True), 0, max(q.size - 2, 0))
        # 各ビンの代表値 (中央値) をラベルにする
        labels = []
        for b in range(max(q.size - 1, 1)):
            m = idx == b
            v = float(np.median(x[m])) if m.sum() else float("nan")
            labels.append(f"{v:.3g}")
        return idx, max(q.size - 1, 1), labels

    for ax, (fa, fb) in zip(axes.ravel(), pairs):
        xa, xb = pooled[fa][seen], pooled[fb][seen]
        ia, na, la = bin_axis(xa)
        ib, nb, lb = bin_axis(xb)
        grid = np.full((nb, na), np.nan)
        cnt = np.zeros((nb, na), dtype=int)
        for i in range(na):
            for j in range(nb):
                m = (ia == i) & (ib == j)
                cnt[j][i] = int(m.sum())
                if m.sum() >= 5:
                    grid[j][i] = max(y[m].mean(), 1e-3)
        vmin = np.nanmin(grid) if np.isfinite(grid).any() else 1e-3
        vmax = np.nanmax(grid) if np.isfinite(grid).any() else 1.0
        im = ax.imshow(grid, origin="lower", aspect="auto", cmap="viridis",
                       norm=LogNorm(vmin=max(vmin, 1e-2), vmax=max(vmax, 1e-1)))
        for i in range(na):
            for j in range(nb):
                if np.isfinite(grid[j][i]):
                    ax.text(i, j, f"{grid[j][i]:.1f}", ha="center", va="center",
                            fontsize=7.5,
                            color="white" if grid[j][i] < vmax * 0.3 else "black")
        ax.set_xticks(range(na)); ax.set_xticklabels(la, fontsize=7.5)
        ax.set_yticks(range(nb)); ax.set_yticklabels(lb, fontsize=7.5)
        ax.set_xlabel(f"{lut[fa]}  (bin median)")
        ax.set_ylabel(f"{lut[fb]}  (bin median)")
        ax.set_title(f"mean future accesses:  {lut[fa]} x {lut[fb]}", fontsize=10)
        fig.colorbar(im, ax=ax, fraction=0.046)
    fig.suptitle(f"Pairwise interaction (cell = mean future access count) - {label}",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


# =============================================================================
# 7. CLI
# =============================================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="RW のアクセス回数と 4 要素 (次数/hop/過去アクセス/直近性) の関係を測る")
    p.add_argument("--edges", default=None,
                   help=".gr エッジリスト (--from-keys のときは不要)")
    p.add_argument("--label", default=None, help="出力ファイル名に使うラベル")
    p.add_argument("--alpha", type=float, default=0.1)
    p.add_argument("--walks", type=int, default=1000, help="視点あたりの RW 本数")
    p.add_argument("--num-starts", type=int, default=8, help="無作為に取る視点の数")
    p.add_argument("--start-nodes", type=int, nargs="+", default=None,
                   help="視点を明示指定 (--num-starts を上書き)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start-seed", type=int, default=12345, help="視点抽選の seed")
    p.add_argument("--max-hops", type=int, default=100000)
    p.add_argument("--no-backtrack", action="store_true")
    p.add_argument("--split", type=float, default=0.5,
                   help="観測窓の割合 (視点ごとのアクセス列の先頭 f を '過去' とする)")
    p.add_argument("--cap", type=int, default=100, help="top-C カバレッジの C")
    p.add_argument("--bins", type=int, default=8)
    p.add_argument("--server-count", type=int, default=2)
    p.add_argument("--partition-dir", default=None)
    p.add_argument("--owner-filter", type=int, default=None,
                   help="指定 server-id が所有する実体だけに絞る (例: cap11=0)")
    p.add_argument("--out-dir", default=None)
    p.add_argument("--fig-dir", default=None)
    p.add_argument("--dump-keys", action="store_true",
                   help="(非推奨) キー単位 CSV は常に出るようになったので無視される")
    p.add_argument("--collect-only", action="store_true",
                   help="RW を回してキー単位 CSV を書くだけで終了 (numpy 不要。"
                        "cap サーバ側で回す用)")
    p.add_argument("--from-keys", default=None,
                   help="--collect-only が出した CSV を読んで統計・作図だけ行う "
                        "(RW は回さない。手元で回す用)")
    p.add_argument("--no-figs", action="store_true", help="作図しない")
    p.add_argument("--hop-metric", choices=["walk", "walk_min", "bfs"],
                   default="walk",
                   help="H として使う hop の定義。walk=初回アクセス時の歩数 (既定), "
                        "walk_min=最小歩数, bfs=二部グラフ上の最短距離")
    p.add_argument("--largest-component", action="store_true",
                   help="視点を最大連結成分から抽選する (非連結グラフ向け)")
    return p.parse_args(argv)


def _meta_path(keys_csv: Path) -> Path:
    return keys_csv.with_suffix(".meta.json")


def collect(args, out_dir: Path, tag: str) -> Path:
    """RW を回してキー単位 CSV + meta JSON を書く (numpy 不要)。"""
    import json
    edges = srv.load_edge_list(Path(args.edges))
    cluster = sim.VirtualCluster(
        edges, args.server_count,
        Path(args.partition_dir) if args.partition_dir else None)
    cluster.build_servers(lambda: srv.build_authz_cache("none", 0), 1.0, 1.0)

    if args.start_nodes:
        starts = list(args.start_nodes)
    else:
        pool = (sorted(largest_component_nodes(cluster)) if args.largest_component
                else sorted(cluster.nodes))
        rng = random.Random(args.start_seed)
        starts = rng.sample(pool, min(args.num_starts, len(pool)))

    print("=" * 78)
    print(f"graph={args.edges}  nodes={len(cluster.nodes):,}  edges={len(edges):,}")
    print(f"alpha={args.alpha}  walks/start={args.walks}  starts={starts}")
    print(f"split={args.split}  cap={args.cap}  owner_filter={args.owner_filter}"
          f"  largest_component={args.largest_component}")
    print("=" * 78)

    per_start = run_walks(cluster, starts, args.walks, args.alpha, args.seed,
                          args.max_hops, args.no_backtrack)
    deg_map = build_degree_map(cluster)

    keys_csv = out_dir / f"accessfactors_{tag}_keys.csv"
    meta: Dict[str, Any] = {"per_start": {}, "config": {
        k: v for k, v in vars(args).items() if k != "start_nodes"},
        "starts": starts}
    with keys_csv.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(KEY_COLUMNS)
        for s in starts:
            rows, m = compute_key_rows(cluster, per_start[s], s, args.split,
                                       deg_map, args.owner_filter)
            w.writerows(rows)
            meta["per_start"][str(s)] = m
            seen = sum(1 for r in rows if r[KEY_COLUMNS.index("past")] > 0)
            print(f"  start={s:>9}: accesses={m['n_access']:>9,}  "
                  f"keys={len(rows):>8,}  seen_in_window={seen:>8,}")
    _meta_path(keys_csv).write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[saved] {keys_csv}")
    print(f"[saved] {_meta_path(keys_csv)}")
    return keys_csv


def load_keys(keys_csv: Path, hop_metric: str = "walk"
              ) -> Tuple[List[int], Dict[int, Dict[str, Any]]]:
    """--collect-only の出力を読み戻す。"""
    import json
    meta_p = _meta_path(keys_csv)
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {"per_start": {}}
    by_start: Dict[int, List[List[Any]]] = defaultdict(list)
    with keys_csv.open() as fh:
        rd = csv.reader(fh)
        header = next(rd)
        missing = [c for c in KEY_COLUMNS if c not in header and c != "hop_walk_min"]
        if missing:
            raise SystemExit(f"想定外のヘッダ (足りない列: {missing}): {header}")
        for r in rd:
            st_ = int(r[0])
            by_start[st_].append([st_, r[1], int(r[2])] + [float(x) for x in r[3:]])
    starts = list(by_start)
    recs = {s: rows_to_table(by_start[s], meta["per_start"].get(str(s), {}),
                             hop_metric, header)
            for s in starts}
    return starts, recs


def main(argv=None) -> None:
    args = parse_args(argv)
    base = _THIS.parent.parent
    out_dir = Path(args.out_dir) if args.out_dir else base / "results"
    fig_dir = (Path(args.fig_dir) if args.fig_dir
               else base / "figs" / "access_factors")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    if args.edges is None and args.from_keys is None:
        raise SystemExit("--edges か --from-keys のどちらかが要ります")
    label = args.label or Path(args.edges or args.from_keys).stem
    tag = f"{label}_a{args.alpha}_w{args.walks}_s{args.num_starts}"

    # ---- 収集フェーズ (cap サーバ側。numpy 不要) --------------------------
    if args.from_keys is None:
        keys_csv = collect(args, out_dir, tag)
        if args.collect_only:
            return
    else:
        keys_csv = Path(args.from_keys)
        print(f"[load] {keys_csv}")
    _hop_desc = {"walk": "初回アクセス時の歩数", "walk_min": "最小歩数",
                 "bfs": "二部グラフ上の BFS 距離"}[args.hop_metric]
    print(f"[hop] H の定義 = {args.hop_metric} ({_hop_desc})")

    # ---- 分析フェーズ (numpy 必須) ---------------------------------------
    if np is None:
        raise SystemExit("numpy が無いので分析できません。--collect-only で CSV だけ "
                         "作り、numpy のある環境で --from-keys を使ってください。")
    starts, recs = load_keys(keys_csv, args.hop_metric)

    rows: List[Dict[str, Any]] = []
    for s in starts:
        rec = recs[s]
        for kind in ("all", "node", "edge"):
            r = analyse_start(rec, args.cap, kind)
            r["start"] = s
            r["n_access"] = rec["n_access"]
            r["hop_metric"] = args.hop_metric
            rows.append(r)

    # ---- 視点をまたいだ集約 (pooled) ----
    pooled = {k: np.concatenate([recs[s][k] for s in starts])
              for k in ("is_edge", "deg", "hop", "hop_walk", "total", "past",
                        "future", "age", "recency")}
    for kind in ("all", "node", "edge"):
        r = analyse_start(pooled, args.cap, kind)
        r["start"] = "POOLED"
        r["n_access"] = int(sum(recs[s]["n_access"] for s in starts))
        rows.append(r)

    # ---- CSV ----
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    keys = ["start", "kind", "n_access", "n_keys", "n_seen"] + \
           [k for k in keys if k not in ("start", "kind", "n_access", "n_keys", "n_seen")]
    sum_csv = out_dir / f"accessfactors_{tag}_summary.csv"
    with sum_csv.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---- 画面出力 (視点間の mean ± sd) ----
    NON_NUMERIC = {"start", "kind", "hop_metric"}

    def agg(kind: str, key: str) -> Tuple[float, float]:
        if key in NON_NUMERIC:
            return float("nan"), float("nan")
        vals = [r[key] for r in rows
                if r["kind"] == kind and r["start"] != "POOLED"
                and key in r and not (isinstance(r[key], float) and math.isnan(r[key]))]
        if not vals:
            return float("nan"), float("nan")
        return float(np.mean(vals)), float(np.std(vals))

    pooled_row = {r["kind"]: r for r in rows if r["start"] == "POOLED"}
    print("\n### (A) 静的: 構造量 vs 総アクセス回数  [Spearman ρ, 視点間 mean±sd]")
    print(f"{'kind':<6}{'ρ(deg, total)':>22}{'ρ(hop, total)':>22}")
    for kind in ("all", "node", "edge"):
        m1, s1 = agg(kind, "rho_deg_total")
        m2, s2 = agg(kind, "rho_hop_total")
        print(f"{kind:<6}{m1:>+13.3f}±{s1:<7.3f}{m2:>+13.3f}±{s2:<7.3f}")

    print("\n### (B) 予測的: 観測窓の 4 要素 vs 将来アクセス回数  [Spearman ρ]")
    hdr = f"{'kind':<6}" + "".join(f"{f[0]:>20}" for f in FEATURES)
    print(hdr)
    for kind in ("all", "node", "edge"):
        line = f"{kind:<6}"
        for fname, _ in FEATURES:
            m, s = agg(kind, f"rho_{fname}")
            line += f"{m:>+12.3f}±{s:<6.3f}"
        print(line)

    print("\n### (B') O を統制した偏相関 (その要素が O と独立に持つ情報)")
    print(f"{'kind':<6}{'deg|O':>20}{'hop|O':>20}{'recency|O':>20}")
    for kind in ("all", "node", "edge"):
        line = f"{kind:<6}"
        for fname in ("deg", "hop", "recency"):
            m, s = agg(kind, f"rho_{fname}|past")
            line += f"{m:>+12.3f}±{s:<6.3f}"
        print(line)

    print(f"\n### (C) 単一要素で上位 {args.cap} 件を保持したときの将来アクセス捕捉率 [%]")
    names = [f[0] for f in FEATURES] + ["oracle", "random"]
    print(f"{'kind':<6}" + "".join(f"{n:>13}" for n in names))
    for kind in ("all", "node", "edge"):
        line = f"{kind:<6}"
        for n in names:
            m, _ = agg(kind, f"cov{args.cap}_{n}")
            line += f"{m:>13.1f}"
        print(line)

    print("\n### (D) 要素間の相関 (冗長性, pooled/all)")
    pr = pooled_row.get("all", {})
    fn = [f[0] for f in FEATURES]
    for i in range(len(fn)):
        for j in range(i + 1, len(fn)):
            v = pr.get(f"xx_{fn[i]}_{fn[j]}", float("nan"))
            print(f"   ρ({fn[i]:<8}, {fn[j]:<8}) = {v:+.3f}")

    mean_map: Dict[str, float] = {}
    for k in keys:
        if k in NON_NUMERIC:
            continue
        m, _ = agg("all", k)
        mean_map[k] = m
    if args.no_figs:
        print(f"\n[saved] {sum_csv}")
        return
    plot_univariate(pooled, f"{label}  (α={args.alpha}, {args.walks} walks × "
                            f"{len(starts)} starts)",
                    uni_dir / f"accessfactors_{tag}_univariate.png", args.bins,
                    args.cap, mean_map)
    plot_pairs(pooled, f"{label}  (α={args.alpha}, {args.walks} walks × "
                       f"{len(starts)} starts)",
               pair_dir / f"accessfactors_{tag}_pairs.png", min(args.bins, 6))
    print(f"\n[saved] {sum_csv}")
    print(f"[saved] {uni_dir / f'accessfactors_{tag}_univariate.png'}")
    print(f"[saved] {pair_dir / f'accessfactors_{tag}_pairs.png'}")


if __name__ == "__main__":
    main()
