"""
方法①②③の walks jsonl(各 run が記録した per-hop 実行RTT "exec")から、厳密に描く。
  左 : 遷移ステップごとの実行RTT(walk平均, 累計でない)
  右 : hop ごとの累積 実行RTT(全 walk 連結)
※ オフライン再現ではなく、各方法の実サーバ実行で記録した exec をそのまま使う(厳密)。
方法→ファイル接尾辞: ①=noauth_none / ②=auth_none / ③=auth_lru
  python3 plot_cumrtt_methods.py --graph vldb --walks 100 --alpha 0.05 --dir above --cap 200
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "YuGothic", "Arial Unicode MS"):
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.family"] = [_f, "sans-serif"]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False


def load_exec(path: Path):
    """walks jsonl から per-walk の exec 配列リストを返す(walk順)。"""
    rows = [json.loads(l) for l in open(path)]
    rows.sort(key=lambda r: r.get("walk", 0))
    return [np.array(r.get("exec", []), dtype=float) for r in rows]


def step_mean(per_walk, min_walks):
    maxlen = max(len(a) for a in per_walk)
    M = np.full((len(per_walk), maxlen), np.nan)
    for i, a in enumerate(per_walk):
        M[i, :len(a)] = a
    counts = np.sum(~np.isnan(M), axis=0)
    g = np.nanmean(M, axis=0)
    thr = max(min_walks, int(0.05 * len(per_walk)))
    valid = counts >= thr
    Tcut = int(np.max(np.where(valid))) if valid.any() else maxlen - 1
    return np.arange(Tcut + 1), g[:Tcut + 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--walks", type=int, required=True)
    ap.add_argument("--alpha", required=True)
    ap.add_argument("--dir", default="above")
    ap.add_argument("--cap", type=int, default=200)
    ap.add_argument("--min-walks", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    base = f"{a.results}/walks_{a.graph}_walks{a.walks}_alpha{a.alpha}_{a.dir}"
    methods = [("①認可なし", f"{base}_noauth_none.jsonl", "#1D9E75"),
               ("②認可あり/cacheなし", f"{base}_auth_none.jsonl", "#D85A30"),
               (f"③認可あり/lru(cap={a.cap})", f"{base}_auth_lru.jsonl", "#185FA5")]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    print(f"[rtt-methods] {a.graph} walks={a.walks} alpha={a.alpha} dir={a.dir}")
    for label, path, col in methods:
        if not Path(path).exists():
            print(f"  [skip] not found: {path}")
            continue
        pw = load_exec(Path(path))
        t, g = step_mean(pw, a.min_walks)
        cum = np.cumsum(np.concatenate(pw))
        ax1.plot(t, g, color=col, lw=1.7, label=label)
        ax2.plot(np.arange(len(cum)), cum, color=col, lw=1.7, label=f"{label} 総={cum[-1]:,.0f}")
        print(f"  {label:<22} 総RTT={cum[-1]:,.1f}")

    ax1.set_title("遷移ステップごとの実行RTT(walk平均・累計でない)")
    ax1.set_xlabel("遷移ステップ t"); ax1.set_ylabel("実行RTT / ステップ")
    ax1.legend(fontsize=9); ax1.grid(alpha=0.25)
    ax2.set_title("累積 実行RTT(全 walk 連結)")
    ax2.set_xlabel("hop(通し番号)"); ax2.set_ylabel("累積 実行RTT")
    ax2.legend(fontsize=9); ax2.grid(alpha=0.25)

    out = a.out or f"{a.results}/rtt_methods_{a.graph}_w{a.walks}_a{a.alpha}_{a.dir}.png"
    fig.suptitle(f"方法別 実行RTT(厳密)  {a.graph} walks={a.walks} α={a.alpha} dir={a.dir}", y=1.02)
    fig.tight_layout(); fig.savefig(out, dpi=140, bbox_inches="tight")
    print(f"[rtt-methods] saved {Path(out).resolve()}")


if __name__ == "__main__":
    main()
