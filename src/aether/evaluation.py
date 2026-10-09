"""Scoring for golden-question evaluation runs.

A question abstains when its answer carries no citations: the query prompt
tells the model to cite nothing when the material does not answer.
A keyword may list alternatives as "a|b"; matching uses core.text.name_key. Citations into documents outside
the pack (the graph is shared) are reported as foreign, not scored.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from aether.core.text import name_key
from aether.query import Answer, Mode


class Golden(BaseModel):
    question: str = Field(min_length=1)
    mode: Mode
    keywords: list[str]
    documents: list[str]
    answerable: bool = True


class Score(BaseModel):
    question: str
    mode: Mode
    answer: str | None
    keyword_recall: float | None
    citation_precision: float | None
    citations: int
    foreign_citations: int
    abstained: bool
    abstention_correct: bool


def score(golden: Golden, answer: Answer, filenames: dict[UUID, str]) -> Score:
    """filenames maps document IDs to corpus filenames."""
    text = name_key(answer.answer or "")
    recall = (
        sum(any(name_key(alt) in text for alt in k.split("|")) for k in golden.keywords)
        / len(golden.keywords)
        if golden.answerable and golden.keywords else None
    )
    named = [filenames.get(c.document_id) for c in answer.citations]
    cited = [name for name in named if name is not None]
    precision = (
        sum(name in golden.documents for name in cited) / len(cited)
        if golden.answerable and cited else None
    )
    abstained = not answer.citations
    return Score(
        question=golden.question,
        mode=golden.mode,
        answer=answer.answer,
        keyword_recall=recall,
        citation_precision=precision,
        citations=len(named),
        foreign_citations=len(named) - len(cited),
        abstained=abstained,
        abstention_correct=abstained != golden.answerable,
    )


def summarize(scores: list[Score]) -> dict[str, float | None]:
    def mean(values: list[float | None]) -> float | None:
        present = [v for v in values if v is not None]
        return round(sum(present) / len(present), 3) if present else None

    return {
        "keyword_recall": mean([s.keyword_recall for s in scores]),
        "citation_precision": mean([s.citation_precision for s in scores]),
        "abstention_accuracy": mean([float(s.abstention_correct) for s in scores]),
        "foreign_citation_share": mean(
            [sum(s.foreign_citations for s in scores) / total]
            if (total := sum(s.citations for s in scores)) else []
        ),
    }
