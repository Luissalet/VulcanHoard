"""Close selected separation edges on a locally damaged triangulated shell."""

from collections import Counter, defaultdict
from heapq import heappop, heappush

import numpy as np


def connect_ends(edges, border, vertices):
    """Assign the extra boundary of the patch along the existing local mesh."""
    endpoints = {vertex for vertex, degree in Counter(edges.ravel()).items() if degree % 2}
    if not endpoints:
        return []
    graph = defaultdict(list)
    for (a, b), count in border:
        distance = float(np.linalg.norm(vertices[a] - vertices[b]))
        # Prefer free boundary, then already branching edges, then the intact skin.
        cost = max(distance, 1e-8) * {1: 1, 2: 24}.get(count, 4)
        graph[a].append((b, cost))
        graph[b].append((a, cost))

    routes = set()
    while endpoints:
        start = min(endpoints)
        queue, distances, parent = [(0.0, start)], {start: 0.0}, {}
        while queue:
            distance, node = heappop(queue)
            if distance != distances[node]:
                continue
            if node != start and node in endpoints:
                endpoints.difference_update((start, node))
                while node != start:
                    previous = parent[node]
                    edge = (min(previous, node), max(previous, node))
                    routes.symmetric_difference_update((edge,))
                    node = previous
                break
            for other, cost in graph[node]:
                candidate = distance + cost
                if candidate < distances.get(other, float("inf")):
                    distances[other], parent[other] = candidate, node
                    heappush(queue, (candidate, other))
        else:
            raise ValueError("A cut ends outside the local repair surface. / Un corte termina fuera de la zona de reparación.")
    return sorted(routes)


def cycles(edges):
    """Decompose an even-degree edge graph into simple, edge-disjoint rings."""
    graph = defaultdict(dict)
    for a, b in edges:
        graph[a][b] = None
        graph[b][a] = None
    for start in graph:
        trail, positions = [start], {start: 0}
        while graph[trail[-1]]:
            a = trail[-1]
            b = next(iter(graph[a]))
            del graph[a][b], graph[b][a]
            if b in positions:
                index = positions[b]
                yield trail[index:]
                for node in trail[index + 1:]:
                    del positions[node]
                del trail[index + 1:]
            else:
                positions[b] = len(trail)
                trail.append(b)
        if len(trail) != 1:
            raise ValueError("The repair boundary is not closed. / El contorno de la reparación sigue abierto.")
