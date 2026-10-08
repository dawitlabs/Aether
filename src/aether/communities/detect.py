"""Leiden community detection over the entity graph.

Single level only. Parallel relationships between two entities add their
weights. Singletons are dropped: a one-entity community has nothing to summarize.
"""

import networkx as nx

from aether.core.models import Community
from aether.storage.communities import Neo4jCommunityStore

SEED = 42


def detect(nodes: list[str], edges: list[tuple[str, str, float]]) -> list[list[str]]:
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    for source, target, weight in edges:
        if source == target:
            continue
        previous = graph.get_edge_data(source, target, {"weight": 0.0})["weight"]
        graph.add_edge(source, target, weight=previous + weight)
    communities = nx.community.leiden_communities(
        graph, weight="weight", metric="modularity", seed=SEED
    )
    # Largest first, members sorted, so output order is stable across runs.
    return sorted(
        (sorted(c) for c in communities if len(c) > 1),
        key=lambda members: (-len(members), members),
    )


def rebuild(store: Neo4jCommunityStore) -> list[Community]:
    nodes, edges = store.entity_graph()
    communities = [Community(entity_ids=members) for members in detect(nodes, edges)]
    store.replace_all(communities)
    return communities
