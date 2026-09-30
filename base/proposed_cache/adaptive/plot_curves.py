#!/usr/bin/env python3
# =============================================================================
# plot_curves.py — controller.py が出した curve_*.csv から学習曲線を描く
#
#   上段: バッチごとのヒット率（walk を重ねるほど上がるか）。方策ごとに1本
#   下段: learned の重み（server0 / node と edge）の推移
#
#   使い方:
#     python3 base/proposed_cache/adaptive/plot_curves.py <run_local/cluster の OUT_DIR>
#     → <OUT_DIR>/learning_curve.png
# =============================================================================
from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from matplotlib import font_manager  # noqa: E402

_AVAILABLE = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("Hiragino Sans", "Hiragino Maru Gothic Pro", "Yu Gothic", "Noto Sans CJK JP"):
    if _f in _AVAILABLE:
        matplotlib.rcParams["font.family"] = _f
        break

ORDER = ["lru", "learned_frozen", "arc", "proposed", "learned", "structured"]


def load(path: Path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    raw = "--raw" in sys.argv[1:]      # 既定は |w_O| を 1 とした比。--raw で元の重み
    if not args:
        raise SystemExit("usage: plot_curves.py <OUT_DIR> [--raw]")
    out_dir = Path(args[0])
    curves = {}
    for p in sorted(out_dir.glob("curve_*.csv")):
        rows = load(p)
        if rows:
            curves[rows[0]["tag"]] = rows
    if not curves:
        raise SystemExit(f"curve_*.csv がありません: {out_dir}")
    tags = sorted(curves, key=lambda t: ORDER.index(t) if t in ORDER else 99)

    # 重みの列 w_s<sid>_<型>_<項> を、方策 × サーバの役割ごとにまとめる。
    #   分離配置（node_servers 列あり）: sid < Kn はノード担当（キャッシュにはエッジ）、残りはエッジ担当
    #   （キャッシュにはノード）。同じ役割のサーバの重みを平均して太線、各サーバを細線で描く。
    #   モジュロ分割: 全サーバを1グループにする。
    panels = []
    for t in tags:
        rows = curves[t]
        # 比の表示は実効的な重み（e_s…、重み × 特徴量の標準偏差）があればそれを使う。--raw は素の重み
        pref = "e_s" if (not raw and any(c.startswith("e_s") for c in rows[0])) else "w_s"
        wcols = [c for c in rows[0] if c.startswith(pref) and not c.endswith("_b")]
        if not wcols:
            continue
        kn_raw = rows[0].get("node_servers", "")
        kn = int(kn_raw) if kn_raw not in ("", None) else None
        groups = {}
        for c in wcols:
            _, sidp, typ, term = c.split("_", 3)
            sid = int(sidp[1:])
            if kn is None:
                g = f"全サーバ / {typ}"
            else:
                g = ("ノード担当（キャッシュ=エッジ）" if sid < kn else "エッジ担当（キャッシュ=ノード）")
            groups.setdefault(g, {}).setdefault(term, []).append(c)
        for g, terms in sorted(groups.items()):
            allc = [c for cs in terms.values() for c in cs]
            # 学習を止めた方策・学習が起きなかった役割（重みが最初から最後まで動かない）は描かない
            if all(len({r[c] for r in rows}) <= 1 for c in allc):
                continue
            panels.append((t, g, terms))
    dq_tags = [t for t in tags if "batch_pair_acc" in curves[t][0]
               and any(r["batch_pair_acc"] not in ("", "nan") for r in curves[t])]
    n_dq = 2 if dq_tags else 0
    fig, axes = plt.subplots(1 + n_dq + len(panels), 1,
                             figsize=(8, 4 + 3 * (n_dq + len(panels))), squeeze=False)
    ax = axes[0][0]
    for t in tags:
        if t == "belady":      # belady の実行は LRU でアクセス列を記録しただけ（曲線は LRU と同じ）→ 描かない
            continue
        rows = curves[t]
        x = [int(r["walks_done"]) for r in rows]
        ax.plot(x, [float(r["batch_hit"]) for r in rows], marker="o", ms=3,
                lw=2.2 if t in ("learned", "structured") else 1.2, label=f"{t}（累積 {float(rows[-1]['cum_hit']):.1f}%）")
    summ = out_dir / "summary.tsv"
    if summ.exists():                       # Belady（最適解）は最終値だけを横線で示す
        for line in summ.read_text().splitlines():
            if line.startswith("RESULT\tbelady\t"):
                bv = float(line.split("overall=")[1].split("\t")[0])
                ax.axhline(bv, color="#e34948", ls=":", lw=1.2)
                ax.text(ax.get_xlim()[0], bv, f" Belady（全体）{bv:.1f}%", va="bottom", fontsize=8, color="#e34948")
    wfile = out_dir / "walks_used.txt"
    if wfile.exists():                      # AUTO_WALKS=1 で見つけた飽和点（残りの方策はこの本数で回る）
        w_star, how = wfile.read_text().split()[:2]
        ax.axvline(int(w_star), color="gray", ls="--", lw=1)
        ax.text(int(w_star), ax.get_ylim()[1], f" W*={w_star}（{'飽和' if how == 'converged' else '上限・未飽和' if how == 'not_converged' else '固定'}）",
                va="top", fontsize=8, color="gray")
    ax.set_xlabel("walk 本数（累積）")
    ax.set_ylabel("バッチ内ヒット率 [%]")
    ax.set_title("バッチごとのヒット率")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    # 追い出しの判断の質（学習キャッシュ系のみ。LRU は structured_frozen で代用）
    for j, (col, title) in enumerate((("batch_pair_acc", "順位の正しさ：追い出したキーが残したキーより後で使われた割合 [%]"),
                                      ("batch_regret", "後悔：追い出したキーが滞在時間内に戻ってきた割合 [%]（低いほど良い）")),
                                     start=1):
        if not n_dq:
            break
        ax = axes[j][0]
        for t in dq_tags:
            rows = curves[t]
            ax.plot([int(r["walks_done"]) for r in rows],
                    [float(r[col]) if r[col] not in ("", "nan") else float("nan") for r in rows],
                    marker="o", ms=3, label=t)
        ax.set_xlabel("walk 本数（累積）")
        ax.set_title(title, fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)

    def val(r, c):
        v = r.get(c)
        return float(v) if v not in ("", None) else float("nan")

    color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    def series(r, c, o_col):
        """重み（raw）または |w_O| を 1 とした比。"""
        v = val(r, c)
        if raw:
            return v
        o = abs(val(r, o_col)) if o_col else float("nan")
        return v / o if o > 1e-12 else float("nan")

    for k, (t, g, terms) in enumerate(panels, start=1 + n_dq):
        rows = curves[t]
        x = [int(r["walks_done"]) for r in rows]
        ax = axes[k][0]
        n_srv = 0
        # 各サーバの O の列（比の分母）: 同じサーバの w_O
        o_of = {}
        for c in terms.get("O", []):
            o_of[c.split("_", 3)[1]] = c
        for j, (term, cs) in enumerate(terms.items()):
            col = color_cycle[j % len(color_cycle)]
            n_srv = max(n_srv, len(cs))
            ser = [[series(r, c, o_of.get(c.split("_", 3)[1])) for r in rows] for c in cs]
            if len(cs) > 1:
                for y in ser:
                    ax.plot(x, y, color=col, lw=0.6, alpha=0.35)
            mean = [sum(y[i] for y in ser) / len(ser) for i in range(len(rows))]
            ax.plot(x, mean, color=col, lw=2, marker=".", label=f"w_{term}" + ("" if raw else " / |w_O|"))
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_xlabel("walk 本数（累積）")
        ax.set_ylabel("重み（z 値あたり）" if raw else "重みの比（|w_O| = 1）")
        ax.set_title(f"{t} の{'重み' if raw else '重みの比'}：{g}（{n_srv} 台の平均、細線 = 各サーバ）"
                     "  正 = 追い出しやすい", fontsize=9)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, ncol=5)

    fig.tight_layout()
    out = out_dir / "learning_curve.png"
    fig.savefig(out, dpi=150)
    print(f"[saved] {out}")


if __name__ == "__main__":
    main()
