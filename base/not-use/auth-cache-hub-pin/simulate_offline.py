#!/usr/bin/env python3
"""
hub-pin-lru / LRU / Belady のヒット率をオフラインでシミュレートする。
SSH / 分散環境は不要。既存の `*_global_transition.json` だけを入力にする。

Usage:
  python3 simulate_offline.py \
    --transition-dir base/auth-baseline-cache/results/alpha0.1_walks_1000_capa_100/vldb/lru_100 \
    --capacity 100 \
    --pin-k 0,10,20,30,50,70,100 \
    --walks 1000 \
    --alpha 0.1 \
    --seed 42

`--transition-dir` には start=*_global_transition.json が並んでいる dir を指定。
`--pin-k` は単一の数値かカンマ区切り。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict, OrderedDict
from pathlib import Path


# ===========================================================================
# Cache policies
# ===========================================================================
class LRUCache:
    """OrderedDict ベースの普通の LRU."""
    def __init__(self, capacity: int):
        self.cap = capacity
        self.od: OrderedDict[str, int] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def access(self, node: str) -> bool:
        if node in self.od:
            self.od.move_to_end(node)
            self.hits += 1
            return True
        self.od[node] = 1
        if len(self.od) > self.cap:
            self.od.popitem(last=False)
        self.misses += 1
        return False


class LFUCache:
    """frequency-based: 最も access count が少ないものを evict。
       同 freq 内は LRU で tie-break。"""
    def __init__(self, capacity: int):
        self.cap = capacity
        self.freq: dict[str, int] = {}
        self.recency: OrderedDict[str, int] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def access(self, node: str) -> bool:
        if node in self.freq:
            self.freq[node] += 1
            self.recency.move_to_end(node)
            self.hits += 1
            return True
        # miss → evict if full
        if len(self.freq) >= self.cap:
            # 最も freq が小さく、それでも tie なら最古
            evict = min(self.recency.keys(),
                        key=lambda k: (self.freq[k],
                                       list(self.recency).index(k)))
            del self.freq[evict]
            del self.recency[evict]
        self.freq[node] = 1
        self.recency[node] = 1
        self.misses += 1
        return False


class TinyLFUCache:
    """Window-TinyLFU 風: 小さな LRU window で「最近来た」、本体は LFU。
       window から本体へ promote する時に「本体で evict されそうなノードと freq 比較」。
       簡易実装：window cap = cap // 16,  本体 = 残り。
    """
    def __init__(self, capacity: int):
        self.cap = capacity
        self.win_cap = max(1, capacity // 16)
        self.main_cap = capacity - self.win_cap
        self.win: OrderedDict[str, int] = OrderedDict()
        self.main_freq: dict[str, int] = {}
        self.main_recency: OrderedDict[str, int] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def access(self, node: str) -> bool:
        if node in self.main_freq:
            self.main_freq[node] += 1
            self.main_recency.move_to_end(node)
            self.hits += 1
            return True
        if node in self.win:
            # window hit
            self.win.move_to_end(node)
            self.win[node] = self.win.get(node, 1) + 1
            self.hits += 1
            return True
        # miss → push to window
        self.win[node] = 1
        self.misses += 1
        if len(self.win) > self.win_cap:
            # window から evict されるノードを main へ promote する
            evicted_node, evicted_freq = self.win.popitem(last=False)
            if len(self.main_freq) < self.main_cap:
                self.main_freq[evicted_node] = evicted_freq
                self.main_recency[evicted_node] = 1
            else:
                # main の最弱 (lowest freq) と比較
                victim = min(self.main_recency.keys(),
                             key=lambda k: self.main_freq[k])
                if evicted_freq > self.main_freq[victim]:
                    del self.main_freq[victim]
                    del self.main_recency[victim]
                    self.main_freq[evicted_node] = evicted_freq
                    self.main_recency[evicted_node] = 1
        return False


class HubPinLRU:
    """先頭 K 個を pinned (絶対 evict されない) として固定し、
       残り (cap - K) 枠を LRU で運用する。"""
    def __init__(self, capacity: int, pin_set: set[str]):
        self.pin = set(pin_set)
        self.cap = capacity
        self.lru_cap = max(0, capacity - len(self.pin))
        self.lru: OrderedDict[str, int] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.hits_pinned = 0
        self.hits_lru = 0

    def access(self, node: str) -> bool:
        if node in self.pin:
            self.hits_pinned += 1
            self.hits += 1
            return True
        if node in self.lru:
            self.lru.move_to_end(node)
            self.hits_lru += 1
            self.hits += 1
            return True
        # miss
        self.misses += 1
        if self.lru_cap > 0:
            self.lru[node] = 1
            if len(self.lru) > self.lru_cap:
                self.lru.popitem(last=False)
        return False


# ===========================================================================
# データロード
# ===========================================================================
def load_transition_data(path: Path) -> dict:
    """1 つの start=*_global_transition.json を読み込み:
         - access: {node_str: count}
         - transition: {src: {dst: count}}   (ノード→ノード遷移確率を作るため)
    に整形して返す。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    access = {k: v for k, v in raw["access"].items() if not k.startswith("edge_")}

    transition: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for key, count in raw["transition"].items():
        # 'src->edge_S_T' 形式
        m = re.match(r"^(.+?)->edge_(.+?)_(.+)$", key)
        if not m:
            continue
        src, a, b = m.group(1), m.group(2), m.group(3)
        # 無向辺 edge_a_b では src が a なら dst は b、src が b なら dst は a
        dst = b if src == a else a
        transition[src][dst] += count

    return {
        "access": access,
        "transition": dict(transition),
        "start_node": _infer_start_from_filename(path),
    }


def _infer_start_from_filename(p: Path) -> int:
    m = re.search(r"start=(\d+)_", p.name)
    return int(m.group(1)) if m else -1


# ===========================================================================
# Pin set の作り方
# ===========================================================================
def pin_by_access_freq(access: dict[str, int], k: int) -> set[str]:
    """同一 start_node の access 頻度で top-K を選ぶ（=Belady 上限の参考）。"""
    return {n for n, _ in sorted(access.items(), key=lambda x: -x[1])[:k]}


def pin_by_outgoing_degree(transition: dict[str, dict[str, int]], k: int) -> set[str]:
    """各ノードからの**異なる遷移先の数**で top-K を選ぶ
       → 実 graph の degree の代理。**他の start_node の transition も使えるので
       テスト start_node のデータを使わない正直なシミュレーションが可能。**"""
    out_deg: dict[str, int] = {}
    for src, dsts in transition.items():
        out_deg[src] = len(dsts)
    return {n for n, _ in sorted(out_deg.items(), key=lambda x: -x[1])[:k]}


def pin_by_global_pool(access_per_start: list[dict[str, int]],
                       exclude_idx: int, k: int) -> set[str]:
    """leave-one-out: テスト対象 start_node 以外の access 頻度を合計して top-K を選ぶ。
       → 「過去の walks から学んだ」を模した、最も現実的な評価。"""
    pool: dict[str, int] = defaultdict(int)
    for i, acc in enumerate(access_per_start):
        if i == exclude_idx:
            continue
        for n, c in acc.items():
            pool[n] += c
    return {n for n, _ in sorted(pool.items(), key=lambda x: -x[1])[:k]}


def simulate_warmup_freeze(start_node: str,
                           transition: dict[str, dict[str, int]],
                           walks: int, alpha: float, seed: int,
                           capacity: int, k: int, warmup_walks: int):
    """warmup_walks 回は普通の LRU、その後 top-K アクセスを pin して残りを LRU で続行。
       戻り値: (overall hit_rate, post-warmup hit_rate)
    """
    rng = random.Random(seed)
    access_count: dict[str, int] = defaultdict(int)

    # Phase 1: LRU warmup
    lru = LRUCache(capacity)
    for _ in range(warmup_walks):
        node = start_node
        lru.access(node); access_count[node] += 1
        while True:
            if rng.random() < alpha:
                break
            row = transition.get(node)
            if not row:
                break
            nxt = sample_neighbor(row, rng)
            if nxt is None:
                break
            node = nxt
            lru.access(node); access_count[node] += 1
    h0, m0 = lru.hits, lru.misses

    # Phase 2: 学習した top-K を pin、残り (cap - K) を LRU
    pin = {n for n, _ in sorted(access_count.items(),
                                 key=lambda x: -x[1])[:k]}
    hp = HubPinLRU(capacity, pin)
    # phase 1 で見たノードのうち pin 以外を LRU に乗せ直しても良いが、
    # 厳密性のため空から始める（warmup の効果は経験的に pin set だけ）
    for _ in range(walks - warmup_walks):
        node = start_node
        hp.access(node)
        while True:
            if rng.random() < alpha:
                break
            row = transition.get(node)
            if not row:
                break
            nxt = sample_neighbor(row, rng)
            if nxt is None:
                break
            node = nxt
            hp.access(node)
    post_total = hp.hits + hp.misses
    post_rate = hp.hits / post_total if post_total > 0 else 0.0
    overall_total = (h0 + m0) + post_total
    overall_rate = (h0 + hp.hits) / overall_total if overall_total > 0 else 0.0
    return overall_rate, post_rate


# ===========================================================================
# walk シミュレータ
# ===========================================================================
def sample_neighbor(transition_row: dict[str, int], rng: random.Random) -> str | None:
    if not transition_row:
        return None
    items = list(transition_row.items())
    total = sum(c for _, c in items)
    r = rng.uniform(0, total)
    cum = 0
    for v, c in items:
        cum += c
        if r <= cum:
            return v
    return items[-1][0]


def simulate_one_run(start_node: str, transition: dict[str, dict[str, int]],
                     walks: int, alpha: float, seed: int,
                     cache) -> None:
    """walks 本の RW を回し、各 step ごとに cache.access() を呼ぶ。"""
    rng = random.Random(seed)
    for _ in range(walks):
        node = start_node
        cache.access(node)
        while True:
            if rng.random() < alpha:
                break  # teleport / stop
            row = transition.get(node)
            if not row:
                break
            nxt = sample_neighbor(row, rng)
            if nxt is None:
                break
            node = nxt
            cache.access(node)


# ===========================================================================
# Belady (offline OPT) 上限
# ===========================================================================
def belady_upper_bound(access: dict[str, int], cap: int) -> float:
    """ノードごとの access 回数 c[v] が与えられたとき、cap 個のノードを最初から
       入れておけば取りに行くのは 1 回目だけ＝それ以降は全部 hit。
       上限ヒット率 = sum_{top-cap} (c[v] - 1) / sum(c[v])"""
    total = sum(access.values())
    if total == 0:
        return 0.0
    top = sorted(access.values(), reverse=True)[:cap]
    return sum(max(c - 1, 0) for c in top) / total


# ===========================================================================
# 既存 LRU 実測ログから hit_rate を読む
# ===========================================================================
def read_lru_actual_hitrate(log_path: Path, start_node: int) -> float | None:
    if not log_path.exists():
        return None
    cur = None
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"\[START_NODE\]\s+(\d+)", line)
        if m:
            cur = int(m.group(1))
            continue
        m = re.search(r"Auth cache hit:\s*(\d+),\s*miss:\s*(\d+)", line)
        if m and cur == start_node:
            h, ms = int(m.group(1)), int(m.group(2))
            return h / (h + ms) if (h + ms) > 0 else None
    return None


# ===========================================================================
# main
# ===========================================================================
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transition-dir", type=Path, required=True,
                    help="start=*_global_transition.json が並ぶ dir")
    ap.add_argument("--capacity", type=int, default=100)
    ap.add_argument("--pin-k", type=str, default="50",
                    help="pin 枠サイズ。カンマ区切りで sweep 可 (例: 0,10,30,50,100)")
    ap.add_argument("--walks", type=int, default=1000)
    ap.add_argument("--alpha", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pin-source", choices=["self", "out_degree", "loo_access", "warmup"],
                    default="warmup",
                    help="pin set の選び方:\n"
                         "  self       : 同 start_node の access 上位 (Belady 風・楽観上限)\n"
                         "  out_degree : 全 start の transition から degree 代理で top-K\n"
                         "  loo_access : leave-one-out: 他 start の access 合算 top-K (正直)\n"
                         "  warmup     : 最初の N walks は LRU、その後 top-K を pin して残りを LRU (現実的・推奨)")
    ap.add_argument("--warmup-walks", type=int, default=100,
                    help="--pin-source=warmup のときに LRU で warmup する walks 数")
    args = ap.parse_args()

    transition_dir: Path = args.transition_dir
    pin_ks = [int(x) for x in args.pin_k.split(",")]
    # log_path: 同 dir の *.log もしくはその親 dir 配下の lru_100/*.log
    log_path = None
    cands = []
    for c in transition_dir.glob("*.log"):
        if not c.name.endswith(".memory.log"):
            cands.append(c)
    if not cands:
        for c in transition_dir.parent.glob("*.log"):
            if not c.name.endswith(".memory.log"):
                cands.append(c)
    if cands:
        log_path = cands[0]

    # 全 start_node のデータをロード
    files = sorted(transition_dir.glob("start=*_global_transition.json"))
    if not files:
        print(f"[error] transition json not found in {transition_dir}", file=sys.stderr)
        sys.exit(1)

    runs = []
    for f in files:
        runs.append(load_transition_data(f))

    # 各 start_node について全 K でシミュレート
    print(f"\n=== offline simulator ===")
    print(f"transition_dir = {transition_dir}")
    print(f"capacity = {args.capacity},  walks = {args.walks},  α = {args.alpha},  seed = {args.seed}")
    print(f"pin_source = {args.pin_source}")
    print()
    header = f"{'sn':>3}  {'lru_actual':>10}  {'lru_sim':>9}  {'lfu':>7}  {'tinylfu':>9}  {'belady':>9}"
    for k in pin_ks:
        header += f"  {'pin='+str(k):>9}"
    print(header)
    print("-" * len(header))
    lfu_sims: list[float] = []
    tlfu_sims: list[float] = []

    aggregate: dict[int, list[float]] = {k: [] for k in pin_ks}
    lru_sims: list[float] = []

    for idx, run in enumerate(runs):
        sn = run["start_node"]
        access = run["access"]
        trans = run["transition"]
        if not access:
            continue

        # 実測 LRU
        lru_actual = read_lru_actual_hitrate(log_path, sn) if log_path else None

        # シミュレート LRU
        lru = LRUCache(args.capacity)
        simulate_one_run(str(sn), trans, args.walks, args.alpha,
                         args.seed, lru)
        lru_sim = lru.hits / (lru.hits + lru.misses) if (lru.hits + lru.misses) > 0 else 0.0
        lru_sims.append(lru_sim)

        # シミュレート LFU (本ループ内で計算後 aggregate に追加)
        lfu = LFUCache(args.capacity)
        simulate_one_run(str(sn), trans, args.walks, args.alpha,
                         args.seed, lfu)
        lfu_sim = lfu.hits / (lfu.hits + lfu.misses) if (lfu.hits + lfu.misses) > 0 else 0.0

        # シミュレート TinyLFU
        tlfu = TinyLFUCache(args.capacity)
        simulate_one_run(str(sn), trans, args.walks, args.alpha,
                         args.seed, tlfu)
        tlfu_sim = tlfu.hits / (tlfu.hits + tlfu.misses) if (tlfu.hits + tlfu.misses) > 0 else 0.0
        lfu_sims.append(lfu_sim)
        tlfu_sims.append(tlfu_sim)

        # Belady
        belady = belady_upper_bound(access, args.capacity)

        line = f"{sn:>3}  {lru_actual if lru_actual is not None else float('nan'):>10.3f}  " \
               f"{lru_sim:>9.3f}  {lfu_sim:>7.3f}  {tlfu_sim:>9.3f}  {belady:>9.3f}"

        # 各 K で hub-pin
        for k in pin_ks:
            if args.pin_source == "warmup":
                rate, _ = simulate_warmup_freeze(
                    str(sn), trans, args.walks, args.alpha, args.seed,
                    args.capacity, k, args.warmup_walks)
            else:
                if args.pin_source == "self":
                    pin = pin_by_access_freq(access, k)
                elif args.pin_source == "out_degree":
                    merged: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
                    for r in runs:
                        for src, dsts in r["transition"].items():
                            for dst, c in dsts.items():
                                merged[src][dst] += c
                    pin = pin_by_outgoing_degree(merged, k)
                else:  # loo_access
                    pin = pin_by_global_pool([r["access"] for r in runs], idx, k)

                hp = HubPinLRU(args.capacity, pin)
                simulate_one_run(str(sn), trans, args.walks, args.alpha,
                                 args.seed, hp)
                rate = hp.hits / (hp.hits + hp.misses) if (hp.hits + hp.misses) > 0 else 0.0
            aggregate[k].append(rate)
            line += f"  {rate:>9.3f}"

        print(line)

    # 平均
    print("-" * len(header))
    avg_lru  = sum(lru_sims)/len(lru_sims) if lru_sims else 0
    avg_lfu  = sum(lfu_sims)/len(lfu_sims) if lfu_sims else 0
    avg_tlfu = sum(tlfu_sims)/len(tlfu_sims) if tlfu_sims else 0
    line = f"{'avg':>3}  {'-':>10}  {avg_lru:>9.3f}  {avg_lfu:>7.3f}  {avg_tlfu:>9.3f}  {'-':>9}"
    for k in pin_ks:
        v = aggregate[k]
        line += f"  {sum(v)/len(v) if v else 0:>9.3f}"
    print(line)
    print()
    print("(lru_actual: 既存ログから読んだ実測値, lru_sim: 本シミュレータ内で同じ alpha/walks で再現)")
    print("(belady: top-K オフライン最適 = 上限値,  pin=K: 提案手法 (K=pin 枠サイズ))")


if __name__ == "__main__":
    main()
