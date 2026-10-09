"""Scoring for golden-question evaluation runs.

A question abstains when its answer carries no citations: the query prompt
tells the model to cite nothing when the material does not answer.
"""

from uuid import UUID

from pydantic import BaseModel, Field

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
    keyword_recall: float | None
    citation_precision: float | None
    abstained: bool
    abstention_correct: bool


def score(golden: Golden, answer: Answer, filenames: dict[UUID, str]) -> Score:
    """filenames maps document IDs to corpus filenames."""
    text = (answer.answer or "").casefold()
    recall = (
        sum(k.casefold() in text for k in golden.keywords) / len(golden.keywords)
        if golden.answerable and golden.keywords else None
    )
    cited = [filenames.get(c.document_id) for c in answer.citations]
    precision = (
        sum(name in golden.documents for name in cited) / len(cited)
        if golden.answerable and cited else None
    )
    abstained = not answer.citations
    return Score(
        question=golden.question,
        mode=golden.mode,
        keyword_recall=recall,
        citation_precision=precision,
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
    }
