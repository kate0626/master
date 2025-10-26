import networkx as nx
import community
import sys
import matplotlib.cm as cm
import matplotlib.pyplot as plt
from collections import defaultdict
import time


def louvain(graph):
    partition = community.best_partition(graph)
    # print(partition)
    communities = defaultdict(set)
    for nd in partition:
        c = partition[nd]
        communities[c].add(nd)
    comms = []
    for c in communities:
        comms.append(communities[c])
    print(nx.algorithms.community.quality.modularity(graph, comms))


def read_graph():
    # filepath = "graph/com-amazon.gr"
    # filepath = "graph/fb-pages-food.gr"
    filepath = sys.argv[1]
    graph = nx.Graph()
    f = open(filepath, "r")
    lines = f.readlines()
    for line in lines:
        if '#' in line:
            continue
        left = int(line.split()[0])
        right = int(line.split()[1])
        if left == right:
            continue
        graph.add_edge(left, right)
        graph.add_edge(right, left)
    f.close()
    return graph


if __name__ == "__main__":
    graph = read_graph()
    start = time.time()
    louvain(graph)
    end = time.time()
    print(end - start, "s")
