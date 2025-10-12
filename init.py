"""
ブロックチェーン解析用：Metropolis-Hastings (MH) ランダムウォーク 実装（論文準拠版）

このファイルは、以下の主要機能を実装した Python スクリプトです。
  - 論文で提示されている Metropolis–Hastings ベースのランダムウォーク（2-hop 提案分布 a を含む）
  - 生成ウォークを用いた skip-gram (Word2Vec) 埋め込み学習
  - インクリメンタル更新（Unbiased Update の近似実装）：既存モデルに対する新規ウォークの追加学習（gensim の update_vocab/training を使用）
  - ベースライン（均一ランダムウォーク / node2vec(p=q=1) 相当）との比較
  - 埋め込みの下流評価（LR / SVM）

注記（重要）
  - この実装は論文の主要設計（2-hop 提案, a_m=0.5, walk_length=5, window=5, dim=64 等）に合わせていますが、
    細かな実験チューニングや論文中の微妙な実装差（提案分布の正確な確率定義、時系列性を扱う詳細なインクリメンタル手法）については
    論文本文を参照してパラメータ調整してください。
  - 本スクリプトはローカルで実行可能な形で提供します（外部 API への依存はありません）。

使い方（準備）
1) Python 3.8+ を推奨
2) 必要パッケージのインストール:
   pip install networkx gensim scikit-learn tqdm numpy scipy pandas
3) データ準備:
   - edge_list.csv : CSV columns: src,dst,timestamp (timestamp optional)
   - labels.csv    : CSV columns: node,label (label: 0/1)
   既に公開されている Elliptic / Ethereum dataset を使う場合は適宜前処理してください。

主なスクリプト機能
 - build_graph_from_csv(edge_csv)
 - generate_mh_walks(G, num_walks, walk_length, a_m=0.5, proposal_hops=2)
 - generate_uniform_walks(...)  # baseline
 - train_or_update_embeddings(model, walks, ...)
 - incremental_update(old_model, old_walks, new_edges, ...)
 - evaluate_embeddings(model, labels)

"""

from collections import defaultdict
import random
import argparse
import networkx as nx
import numpy as np
from tqdm import trange, tqdm
from gensim.models import Word2Vec
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, average_precision_score
import pandas as pd
import csv
import os

# ------------------------- データ読み込み -------------------------


def build_graph_from_csv(edge_csv, directed=True, timestamp_col=None):
    """
    edge_csv: path to CSV with columns: src,dst[,timestamp]
    Returns: networkx DiGraph (or Graph)
    """
    if directed:
        G = nx.DiGraph()
    else:
        G = nx.Graph()
    df = pd.read_csv(edge_csv)
    if "src" not in df.columns or "dst" not in df.columns:
        raise ValueError("edge csv must contain src and dst columns")
    for _, row in df.iterrows():
        u = str(row["src"])
        v = str(row["dst"])
        G.add_edge(u, v)
    return G


def load_labels(label_csv):
    df = pd.read_csv(label_csv)
    if "node" not in df.columns or "label" not in df.columns:
        raise ValueError("label csv must contain node and label columns")
    labels = {str(r["node"]): int(r["label"]) for _, r in df.iterrows()}
    return labels


# ------------------------- MH 提案分布補助 -------------------------


def get_k_hop_neighbors(G, node, k=2):
    """Return set of neighbors up to k-hop excluding the node itself."""
    visited = set([node])
    frontier = set([node])
    result = set()
    for _ in range(k):
        next_front = set()
        for u in frontier:
            if G.is_directed():
                nbrs = set(G.successors(u))
            else:
                nbrs = set(G.neighbors(u))
            for v in nbrs:
                if v not in visited:
                    next_front.add(v)
        result.update(next_front)
        visited.update(next_front)
        frontier = next_front
    result.discard(node)
    return result


# ------------------------- MH 受容確率 -------------------------


def compute_mh_acceptance(
    G, cur, cand, proposal_prob_cur_to_cand, proposal_prob_cand_to_cur, target_prob=None
):
    """
    General MH acceptance: alpha = min(1, (pi(cand) * q(cand->cur)) / (pi(cur) * q(cur->cand)))
    Here we optionally specify target_prob(v) function (pi). If None, assume pi proportional to degree (deg(v)+eps).
    proposal_prob_* are q probabilities used in proposal.
    """
    eps = 1e-9
    if target_prob is None:
        deg_cur = (G.out_degree(cur) if G.is_directed() else G.degree(cur)) + eps
        deg_cand = (G.out_degree(cand) if G.is_directed() else G.degree(cand)) + eps
        pi_cur = deg_cur
        pi_cand = deg_cand
    else:
        pi_cur = target_prob(cur)
        pi_cand = target_prob(cand)
    num = pi_cand * (proposal_prob_cand_to_cur + eps)
    den = pi_cur * (proposal_prob_cur_to_cand + eps)
    alpha = min(1.0, num / den)
    return alpha


# ------------------------- MH ウォーク生成（2-hop 提案分布含む） -------------------------


def generate_mh_walks(
    G, num_walks, walk_length, a_m=0.5, proposal_hops=2, seed=None, nodes_sample=None
):
    """
    Implements MH walks where proposal draws from a mixture: with probability a_m draw from 1-hop neighbors,
    with probability (1-a_m) draw uniformly from up to proposal_hops neighbors (excluding current).

    Roughly follows the paper's description: use 2-hop "a" proposal, and accept per MH acceptance.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    if nodes_sample is None:
        nodes = list(G.nodes())
    else:
        nodes = nodes_sample

    walks = []
    for _ in trange(num_walks, desc="MH walks"):
        start = random.choice(nodes)
        walk = [start]
        cur = start
        for _ in range(walk_length - 1):
            # Build candidate set: 1-hop and up to k-hop
            one_hop = list(G.successors(cur) if G.is_directed() else G.neighbors(cur))
            two_hop = (
                list(get_k_hop_neighbors(G, cur, k=proposal_hops))
                if proposal_hops >= 2
                else []
            )
            # Remove current from candidate sets
            one_hop = [v for v in one_hop if v != cur]
            two_hop = [v for v in two_hop if v != cur]

            # If no neighbors, break
            if len(one_hop) == 0 and len(two_hop) == 0:
                break

            # proposal: choose source set according to a_m
            if len(one_hop) > 0 and len(two_hop) > 0:
                if random.random() < a_m:
                    candidate = random.choice(one_hop)
                    # q(cur->candidate) = a_m * (1/|one_hop|)
                    q_cur_to_cand = a_m * (1.0 / len(one_hop))
                else:
                    candidate = random.choice(two_hop)
                    q_cur_to_cand = (1.0 - a_m) * (1.0 / len(two_hop))
            elif len(one_hop) > 0:
                candidate = random.choice(one_hop)
                q_cur_to_cand = 1.0 * (1.0 / len(one_hop))
            else:
                candidate = random.choice(two_hop)
                q_cur_to_cand = 1.0 * (1.0 / len(two_hop))

            # compute reverse proposal prob q(candidate->cur)
            # We approximate by constructing candidate's proposal sets similarly
            cand_one = list(
                G.successors(candidate) if G.is_directed() else G.neighbors(candidate)
            )
            cand_two = (
                list(get_k_hop_neighbors(G, candidate, k=proposal_hops))
                if proposal_hops >= 2
                else []
            )
            cand_one = [v for v in cand_one if v != candidate]
            cand_two = [v for v in cand_two if v != candidate]
            # attempt to compute probability that candidate would propose cur
            q_cand_to_cur = 0.0
            # if cur in candidate's one_hop
            if cur in cand_one:
                # probability that candidate chooses from one_hop times uniform prob
                if len(cand_one) > 0:
                    q_cand_to_cur += a_m * (1.0 / len(cand_one))
            if cur in cand_two:
                if len(cand_two) > 0:
                    q_cand_to_cur += (1.0 - a_m) * (1.0 / len(cand_two))
            # As fallback, if q_cand_to_cur remains 0, add small eps
            if q_cand_to_cur == 0:
                q_cand_to_cur = 1e-9

            # acceptance prob
            alpha = compute_mh_acceptance(
                G, cur, candidate, q_cur_to_cand, q_cand_to_cur, target_prob=None
            )
            if random.random() <= alpha:
                cur = candidate
            # else remain at cur
            walk.append(cur)
        walks.append([str(n) for n in walk])
    return walks


# ------------------------- 均一ウォーク（baseline） -------------------------


def generate_uniform_walks(G, num_walks, walk_length, seed=None):
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
    nodes = list(G.nodes())
    walks = []
    for _ in trange(num_walks, desc="Uniform walks"):
        start = random.choice(nodes)
        cur = start
        walk = [cur]
        for _ in range(walk_length - 1):
            nbrs = list(G.successors(cur) if G.is_directed() else G.neighbors(cur))
            if len(nbrs) == 0:
                break
            cur = random.choice(nbrs)
            walk.append(cur)
        walks.append([str(n) for n in walk])
    return walks


# ------------------------- 埋め込み学習（増分対応） -------------------------


def train_or_update_embeddings(
    existing_model, walks, dim=64, window=5, min_count=1, epochs=5, workers=4, seed=42
):
    """
    If existing_model is None: train new Word2Vec model.
    If existing_model is provided: update vocabulary and continue training (incremental update).
    """
    if existing_model is None:
        model = Word2Vec(
            sentences=walks,
            vector_size=dim,
            window=window,
            min_count=min_count,
            sg=1,
            workers=workers,
            epochs=epochs,
            seed=seed,
        )
    else:
        model = existing_model
        # update vocab with new walks
        model.build_vocab(walks, update=True)
        model.train(walks, total_examples=len(walks), epochs=epochs)
    return model


# ------------------------- 評価 -------------------------


def evaluate_embeddings(embedding_model, labels, method="lr"):
    X = []
    y = []
    for node, lab in labels.items():
        if str(node) in embedding_model.wv:
            X.append(embedding_model.wv[str(node)])
            y.append(lab)
    X = np.array(X)
    y = np.array(y)
    if len(X) == 0:
        raise ValueError("No labeled nodes found in embeddings")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    if method == "lr":
        clf = LogisticRegression(solver="saga", max_iter=2000)
    elif method == "svm":
        clf = SVC(kernel="rbf", C=10, gamma=0.4, probability=True)
    else:
        raise ValueError("Unknown method")
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    if hasattr(clf, "predict_proba"):
        y_score = clf.predict_proba(X_test)[:, 1]
    else:
        try:
            y_score = clf.decision_function(X_test)
        except Exception:
            y_score = None
    f1 = f1_score(y_test, y_pred, average="binary")
    ap = average_precision_score(y_test, y_score) if y_score is not None else None
    return {"f1": f1, "average_precision": ap}


# ------------------------- インクリメンタル更新用ラッパー -------------------------


def incremental_update(
    existing_model, existing_walks, G, new_edges_df, params, labels=None
):
    """
    existing_model: gensim Word2Vec model or None
    existing_walks: list of old walk lists (strings)
    G: existing networkx graph (will be modified in-place)
    new_edges_df: pandas DataFrame with src,dst
    params: dict with keys num_new_walks, walk_length, a_m, proposal_hops, etc.
    returns: updated_model, combined_walks, eval_results (if labels provided)
    """
    # add edges to graph
    for _, r in new_edges_df.iterrows():
        G.add_edge(str(r["src"]), str(r["dst"]))
    # generate new walks on updated graph
    new_walks = generate_mh_walks(
        G,
        num_walks=params.get("num_new_walks", 1000),
        walk_length=params.get("walk_length", 5),
        a_m=params.get("a_m", 0.5),
        proposal_hops=params.get("proposal_hops", 2),
        seed=params.get("seed", None),
    )
    # combine and update model
    combined_walks = existing_walks + new_walks
    model = train_or_update_embeddings(
        existing_model,
        new_walks,
        dim=params.get("dim", 64),
        window=params.get("window", 5),
        epochs=params.get("epochs", 5),
    )
    eval_res = None
    if labels is not None:
        eval_res = evaluate_embeddings(model, labels, method="lr")
    return model, combined_walks, eval_res


# ------------------------- メインフロー -------------------------


def run_full_experiment(
    edge_csv,
    label_csv=None,
    num_walks=50000,
    walk_length=5,
    dim=64,
    window=5,
    epochs=5,
    a_m=0.5,
    proposal_hops=2,
    seed=42,
):
    G = build_graph_from_csv(edge_csv, directed=True)
    print(f"Loaded graph: |V|={G.number_of_nodes()}, |E|={G.number_of_edges()}")

    print("Generating MH walks...")
    mh_walks = generate_mh_walks(
        G,
        num_walks=num_walks,
        walk_length=walk_length,
        a_m=a_m,
        proposal_hops=proposal_hops,
        seed=seed,
    )
    print("Generating uniform baseline walks...")
    uni_walks = generate_uniform_walks(
        G, num_walks=num_walks, walk_length=walk_length, seed=seed
    )

    print("Training MH embeddings...")
    mh_model = train_or_update_embeddings(
        None, mh_walks, dim=dim, window=window, epochs=epochs, seed=seed
    )
    print("Training uniform embeddings...")
    uni_model = train_or_update_embeddings(
        None, uni_walks, dim=dim, window=window, epochs=epochs, seed=seed
    )

    results = {}
    labels = None
    if label_csv is not None:
        labels = load_labels(label_csv)
        print("Evaluating MH embeddings...")
        results["mh_lr"] = evaluate_embeddings(mh_model, labels, method="lr")
        results["mh_svm"] = evaluate_embeddings(mh_model, labels, method="svm")
        print("Evaluating uniform embeddings...")
        results["uni_lr"] = evaluate_embeddings(uni_model, labels, method="lr")
        results["uni_svm"] = evaluate_embeddings(uni_model, labels, method="svm")

    return mh_model, uni_model, mh_walks, uni_walks, results


# ------------------------- CLI -------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge_csv", type=str, required=True)
    parser.add_argument("--label_csv", type=str, default=None)
    parser.add_argument("--num_walks", type=int, default=50000)
    parser.add_argument("--walk_length", type=int, default=5)
    parser.add_argument("--dim", type=int, default=64)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--a_m", type=float, default=0.5)
    parser.add_argument("--proposal_hops", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    mh_model, uni_model, mh_walks, uni_walks, results = run_full_experiment(
        edge_csv=args.edge_csv,
        label_csv=args.label_csv,
        num_walks=args.num_walks,
        walk_length=args.walk_length,
        dim=args.dim,
        window=args.window,
        epochs=args.epochs,
        a_m=args.a_m,
        proposal_hops=args.proposal_hops,
        seed=args.seed,
    )
    print("Results:")
    print(results)
