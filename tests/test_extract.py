import pytest

from aether.extraction.extract import ExtractionError, extract, parse

TEXT = "Ada Lovelace worked with Charles Babbage on the Analytical Engine in London."


def entity(name, excerpt, **overrides):
    return {
        "name": name, "type": "Person", "description": f"{name}.",
        "excerpt": excerpt, "confidence": 0.9, **overrides,
    }


def relationship(source, target, excerpt="worked with Charles Babbage", **overrides):
    return {
        "source": source, "target": target, "type": "worked with",
        "description": "Collaborators.", "excerpt": excerpt, "confidence": 0.8,
        **overrides,
    }


ADA = entity("Ada Lovelace", "Ada Lovelace")
BABBAGE = entity("Charles Babbage", "Charles Babbage")


def test_keeps_grounded_entities_and_relationships():
    result = parse({
        "entities": [ADA, BABBAGE],
        "relationships": [relationship("ada lovelace", "Charles  Babbage")],
    }, TEXT)
    assert [e.name for e in result.entities] == ["Ada Lovelace", "Charles Babbage"]
    assert result.entities[0].type == "person"
    assert result.relationships[0].type == "WORKED_WITH"


def test_drops_entities_whose_excerpt_is_not_in_the_text():
    invented = entity("Alan Turing", "Alan Turing visited")
    result = parse({"entities": [ADA, invented], "relationships": []}, TEXT)
    assert [e.name for e in result.entities] == ["Ada Lovelace"]


def test_excerpt_match_ignores_case_and_whitespace():
    loose = entity("Analytical Engine", "the  analytical\nengine", type="product")
    assert parse({"entities": [loose]}, TEXT).entities[0].name == "Analytical Engine"


def test_drops_relationships_with_unknown_ends_self_loops_or_bad_excerpts():
    result = parse({
        "entities": [ADA, BABBAGE],
        "relationships": [
            relationship("Ada Lovelace", "Alan Turing"),
            relationship("Ada Lovelace", "ada lovelace"),
            relationship("Ada Lovelace", "Charles Babbage", excerpt="invented"),
            relationship("Ada Lovelace", "Charles Babbage", type="!!"),
        ],
    }, TEXT)
    assert result.relationships == []


def test_drops_invalid_items_and_duplicate_names():
    result = parse({"entities": [
        ADA,
        entity("ADA LOVELACE", "Ada Lovelace"),
        entity("   ", "Ada Lovelace"),
        entity("Charles Babbage", "Charles Babbage", confidence=3),
        "not an object",
    ]}, TEXT)
    assert [e.name for e in result.entities] == ["Ada Lovelace"]


@pytest.mark.parametrize("raw", [{}, {"entities": "x"}, {"entities": [], "relationships": None}])
def test_rejects_malformed_response(raw):
    with pytest.raises(ExtractionError):
        parse(raw, TEXT)


def test_extract_wraps_text_as_untrusted_passage():
    class FakeClient:
        def chat_json(self, system, user):
            self.user = user
            return {"entities": [ADA], "relationships": []}

    client = FakeClient()
    assert extract(client, TEXT).entities[0].name == "Ada Lovelace"
    assert client.user == f"<passage>\n{TEXT}\n</passage>"


def test_excerpt_match_ignores_dash_and_quote_variants():
    text = "Irène Joliot-Curie said \"hello\"."
    variant = entity("Irène Joliot‑Curie", "Joliot‑Curie said “hello”")
    assert parse({"entities": [variant]}, text).entities[0].name == "Irène Joliot‑Curie"
