"""Global graph metadata, computed before partitioning without changing IDs."""
import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from time import perf_counter
from .upstream import server


def prepare(path, ordering="bfs"):
    begin = perf_counter()
    edges = server.load_edge_list(Path(path))
    adjacency = {}
    degree = Counter()
    for u, v in edges:
        e = server.make_edge_id(u, v)
        uk, vk = f"node:{u}", f"node:{v}"
        degree[uk] += 1
        degree[vk] += 1
        adjacency.setdefault(uk, set()).add(e)
        adjacency.setdefault(vk, set()).add(e)
        adjacency.setdefault(e, set()).update((uk, vk))
    ordered = []
    seen = set()
    for root in sorted(adjacency):
        if root in seen:
            continue
        queue = deque([root])
        seen.add(root)
        while queue:
            key = queue.popleft()
            ordered.append(key)
            if ordering == "bfs":
                for neighbor in sorted(adjacency[key]):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
    if ordering not in ("bfs", "id"):
        raise ValueError("ordering must be bfs or id")
    return dict(schema=1, graph_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                ordering=ordering, degree=dict(degree),
                layout={key: i for i, key in enumerate(ordered)},
                preprocessing_seconds=perf_counter() - begin)


def load_or_prepare(edges, metadata=None, ordering="bfs"):
    if not metadata:
        return prepare(edges, ordering)
    data = json.loads(Path(metadata).read_text())
    digest = hashlib.sha256(Path(edges).read_bytes()).hexdigest()
    if data.get("schema") != 1 or data.get("graph_sha256") != digest:
        raise ValueError("preprocessing metadata does not match graph/schema")
    return data
