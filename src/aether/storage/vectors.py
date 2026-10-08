"""LanceDB embedding index; derived data that Neo4j can always rebuild.

One table per embedding model and dimension count, so vectors from different
models never mix. Writes are upserts keyed by source ID: retrying a failed or
interrupted write is safe. To rebuild, delete the index directory and re-upsert.
"""

import math
import re
from pathlib import Path
from typing import Literal, NamedTuple
from uuid import UUID

import lancedb
import pyarrow as pa

SourceKind = Literal["text_unit", "entity"]
MODEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class IndexMismatchError(ValueError):
    """An existing table does not match the requested model or dimensions."""


class Match(NamedTuple):
    source_id: UUID
    source_kind: SourceKind
    distance: float


class LanceVectorIndex:
    def __init__(self, path: Path, *, model: str, dimensions: int) -> None:
        if not MODEL_PATTERN.fullmatch(model):
            raise ValueError("model must be lowercase letters, digits, '.', '_' or '-'")
        if type(dimensions) is not int or not 1 <= dimensions <= 8192:
            raise ValueError("dimensions must be an integer between 1 and 8192")
        self._dimensions = dimensions
        schema = pa.schema([
            pa.field("source_id", pa.string(), nullable=False),
            pa.field("source_kind", pa.string(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dimensions), nullable=False),
        ])
        db = lancedb.connect(path)
        self._table = db.create_table(f"{model}__{dimensions}", schema=schema, exist_ok=True)
        if self._table.schema != schema:
            raise IndexMismatchError(f"Table {model}__{dimensions} has an unexpected schema")

    def _check(self, vector: list[float]) -> list[float]:
        if len(vector) != self._dimensions:
            raise ValueError(f"vector must have {self._dimensions} dimensions, got {len(vector)}")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("vector values must be finite")
        return vector

    def upsert(self, source_id: UUID, source_kind: SourceKind, vector: list[float]) -> None:
        if source_kind not in ("text_unit", "entity"):
            raise ValueError(f"unknown source kind {source_kind!r}")
        row = {"source_id": str(source_id), "source_kind": source_kind, "vector": self._check(vector)}
        (
            self._table.merge_insert("source_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )

    def get(self, source_id: UUID) -> list[float] | None:
        # str(UUID) is always hex and dashes, so it is safe inside the filter.
        rows = self._table.search().where(f"source_id = '{source_id}'").limit(1).to_list()
        return [float(value) for value in rows[0]["vector"]] if rows else None

    def delete(self, source_id: UUID) -> None:
        self._table.delete(f"source_id = '{source_id}'")

    def search(self, vector: list[float], *, limit: int = 10) -> list[Match]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        rows = self._table.search(self._check(vector)).limit(limit).to_list()
        return [Match(UUID(r["source_id"]), r["source_kind"], r["_distance"]) for r in rows]
