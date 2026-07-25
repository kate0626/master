"""
methods_summary.csv から「横=方法, 縦=RTT実行時間」の棒グラフを描く。
各棒は (A)移動RTT + (B)認可RTT の積み上げ。総RTTを頂上に表示。
  python3 plot_methods_time.py --graph vldb --walks 100 --alpha 0.05 \
      --out results/methods_time_vldb_w100_a0.05.png
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

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

ORDER = ["①認可なし", "②認可あり/cacheなし"]   # ③は接頭辞一致で後ろに


def sort_key(m: str) -> tuple:
    for i, p in enumerate(ORDER):
        if m == p:
            return (i, m)
    return (len(ORDER), m)   # ③認可あり/... は最後、名前順


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="results/methods_summary.csv")
    ap.add_argument("--graph", required=True)
    ap.add_argument("--walks", type=int, required=True)
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    rows = {}
    with open(a.csv) as f:
        for r in csv.DictReader(f):
            if (r["graph"] == a.graph and int(r["walks"]) == a.walks
                    and abs(float(r["alpha"]) - a.alpha) < 1e-9):
                rows[r["method"]] = r        # 同 method は最新で上書き
    if not rows:
        raise SystemExit(f"no rows for graph={a.graph} walks={a.walks} alpha={a.alpha}")

    methods = sorted(rows, key=sort_key)
    move = [float(rows[m]["rtt_move"]) for m in methods]
    auth = [float(rows[m]["rtt_auth"]) for m in methods]
    total = [float(rows[m]["rtt_total"]) for m in methods]

    fig, ax = plt.subplots(figsize=(max(6, 1.8 * len(methods) + 3), 5))
    x = range(len(methods))
    b1 = ax.bar(x, move, color="#185FA5", label="(A) 移動 RTT(跨ぎ hop)")
    b2 = ax.bar(x, auth, bottom=move, color="#D85A30", label="(B) 認可 RTT(ミス時)")
    for i, t in enumerate(total):
        ax.text(i, t, f"{t:,.0f}", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(list(x)); ax.set_xticklabels(methods, fontsize=10)
    ax.set_ylabel("RTT 実行時間(合計)")
    ax.set_title(f"方法別 RTT 実行時間  ({a.graph}, walks={a.walks}, α={a.alpha})")
    ax.legend(fontsize=9); ax.grid(alpha=0.25, axis="y")
    ax.margins(y=0.15)

    out = a.out or f"results/methods_time_{a.graph}_w{a.walks}_a{a.alpha}.png"
    fig.tight_layout(); fig.savefig(out, dpi=140)
    print(f"[plot] methods={methods}")
    print(f"[plot] saved {Path(out).resolve()}")


if __name__ == "__main__":
    main()
