from aether.communities.detect import detect

EDGES = [
    ("a", "b", 1.0), ("b", "c", 1.0), ("a", "c", 0.5),
    ("d", "e", 1.0), ("e", "f", 1.0), ("d", "f", 0.5),
    ("c", "d", 0.1),
]


def test_finds_dense_groups_and_drops_singletons():
    assert detect(list("abcdefg"), EDGES) == [["a", "b", "c"], ["d", "e", "f"]]


def test_is_deterministic_and_sums_parallel_edges():
    nodes = list("abcdefg")
    assert detect(nodes, EDGES) == detect(nodes, list(reversed(EDGES)))
    # Many weak c-d relationships together outweigh the groups' internal edges.
    heavy = EDGES + [("c", "d", 1.0)] * 5 + [("a", "a", 9.0)]
    assert any({"c", "d"} <= set(group) for group in detect(nodes, heavy))


def test_empty_graph_has_no_communities():
    assert detect([], []) == []
    assert detect(["a", "b"], []) == []
