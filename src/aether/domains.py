"""Domain packs: a fixed corpus with sources and golden questions.

    domains/<slug>/pack.json     name, description, license, retrieved, sources
    domains/<slug>/corpus/*.txt  the documents, one per source
    domains/<slug>/golden.jsonl  evaluation questions (see evaluation.Golden)
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from aether.evaluation import Golden

# Packs live in the repository, next to src/; this assumes a source checkout.
DOMAINS = Path(__file__).resolve().parents[2] / "domains"


class Source(BaseModel):
    file: str = Field(pattern=r"^[a-z0-9-]+\.txt$")
    title: str = Field(min_length=1)
    url: str | None = None
    revision: int | None = None


class Pack(BaseModel):
    name: str = Field(min_length=1)
    description: str
    license: str = Field(min_length=1)
    retrieved: str
    sources: list[Source] = Field(min_length=1)
    root: Path
    goldens: list[Golden]

    @model_validator(mode="after")
    def files_exist_and_goldens_cite_them(self) -> "Pack":
        files = {s.file for s in self.sources}
        on_disk = {p.name for p in (self.root / "corpus").glob("*.txt")}
        if files != on_disk:
            raise ValueError(f"sources and corpus differ: {sorted(files ^ on_disk)}")
        for golden in self.goldens:
            unknown = set(golden.documents) - files
            if unknown:
                raise ValueError(f"{golden.question!r} cites unknown files {sorted(unknown)}")
        return self

    def corpus(self) -> list[Path]:
        return [self.root / "corpus" / s.file for s in self.sources]


def load_pack(slug: str) -> Pack:
    root = DOMAINS / slug
    goldens = [Golden.model_validate_json(line)
               for line in (root / "golden.jsonl").read_text().splitlines() if line.strip()]
    return Pack(**json.loads((root / "pack.json").read_text()), root=root, goldens=goldens)


def available() -> list[str]:
    return sorted(p.parent.name for p in DOMAINS.glob("*/pack.json"))
