from uuid import uuid4

from aether.evaluation import Golden, score, summarize
from aether.query import Answer, Citation

DOC_A, DOC_B = uuid4(), uuid4()
NAMES = {DOC_A: "a.txt", DOC_B: "b.txt"}


def answer(text, *documents):
    return Answer(
        answer=text, entity_ids=[], community_ids=[],
        citations=[Citation(text_unit_id=uuid4(), document_id=d, quote="q") for d in documents],
    )


def test_scores_keywords_and_citation_precision():
    golden = Golden(question="q", mode="local", keywords=["Warsaw", "Paris"], documents=["a.txt"])
    result = score(golden, answer("Born in warsaw.", DOC_A, DOC_B), NAMES)
    assert result.keyword_recall == 0.5
    assert result.citation_precision == 0.5
    assert (result.abstained, result.abstention_correct) == (False, True)


def test_uncited_answer_to_answerable_question_is_a_wrong_abstention():
    golden = Golden(question="q", mode="local", keywords=["x"], documents=["a.txt"])
    result = score(golden, answer("x"), NAMES)
    assert result.citation_precision is None
    assert (result.abstained, result.abstention_correct) == (True, False)


def test_unanswerable_question_scores_only_abstention():
    golden = Golden(question="q", mode="hybrid", keywords=[], documents=[], answerable=False)
    declined = score(golden, answer("Not in the material."), NAMES)
    cited = score(golden, answer("Tokyo.", DOC_A), NAMES)
    assert (declined.keyword_recall, declined.citation_precision) == (None, None)
    assert declined.abstention_correct and not cited.abstention_correct


def test_summary_averages_present_values():
    golden = Golden(question="q", mode="local", keywords=["x"], documents=["a.txt"])
    unanswerable = Golden(question="u", mode="local", keywords=[], documents=[], answerable=False)
    scores = [score(golden, answer("x", DOC_A), NAMES), score(unanswerable, answer("no"), NAMES)]
    assert summarize(scores) == {
        "keyword_recall": 1.0, "citation_precision": 1.0, "abstention_accuracy": 1.0,
    }
