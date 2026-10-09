from aether.communities.detect import detect
from aether.communities.reports import parse_report, prompt
from aether.storage.communities import CommunityContext

UNIT = "7d2b1c7e-0000-4000-8000-000000000001"
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



def test_report_keeps_only_supplied_citations():
    raw = {"title": " Curies ", "summary": "Physicists.", "findings": [
        {"text": "They found radium.", "text_unit_ids": [UNIT, "not-supplied", UNIT]},
        {"text": "Invented.", "text_unit_ids": ["not-supplied"]},
        {"text": "", "text_unit_ids": [UNIT]},
        "junk",
    ]}
    title, summary, findings = parse_report(raw, {UNIT})
    assert (title, summary) == ("Curies", "Physicists.")
    assert [(f.text, [str(i) for i in f.text_unit_ids]) for f in findings] == [
        ("They found radium.", [UNIT])
    ]


def test_report_needs_title_and_summary():
    assert parse_report({"title": "x", "summary": " "}, set()) is None
    assert parse_report({"summary": "x"}, set()) is None
    assert parse_report({"title": "t", "summary": "s"}, set()) == ("t", "s", [])


def test_prompt_marks_passages_with_ids():
    context = CommunityContext([{"name": "A"}], [], [{"id": UNIT, "text": "Hi."}])
    assert f'<passage id="{UNIT}">\nHi.\n</passage>' in prompt(context)
