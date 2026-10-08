"""Turn an uploaded UTF-8 text file into a stored document and text units.

Offsets are character positions in the decoded text, so each unit's text is
exactly text[start_offset:end_offset]. token_count counts whitespace-separated
words until a model tokenizer is chosen.
"""

import os
from hashlib import sha256
from pathlib import Path

from aether.core.models import Document, TextUnit
from aether.storage.documents import DuplicateDocumentError, Neo4jDocumentStore

MAX_UNIT_CHARS = 2000


class UploadError(ValueError):
    """The upload is not a non-empty UTF-8 text document."""


def split_text(text: str, max_chars: int = MAX_UNIT_CHARS) -> list[tuple[int, int]]:
    """Return (start, end) spans, preferring paragraph, line, then word breaks."""
    spans = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            for separator in ("\n\n", "\n", " "):
                cut = text.rfind(separator, start + 1, end)
                if cut != -1:
                    end = cut + len(separator)
                    break
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    return spans


def save_original(directory: Path, content_hash: str, body: bytes) -> None:
    """Content-addressed and atomic, so retries rewrite the same file safely."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"{content_hash}.txt"
    if target.exists():
        return
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_bytes(body)
    os.replace(temporary, target)


def ingest(
    body: bytes, filename: str | None, store: Neo4jDocumentStore, originals: Path
) -> tuple[Document, bool]:
    """Return the document and whether it was newly created."""
    content_hash = sha256(body).hexdigest()
    existing = store.get_by_hash(content_hash)
    if existing is not None:
        return existing, False
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise UploadError("Document must be UTF-8 text") from None
    spans = split_text(text)
    if not spans:
        raise UploadError("Document has no text")

    document = Document(
        filename=filename,
        size_bytes=len(body),
        content_hash=content_hash,
        text_unit_count=len(spans),
    )
    units = [
        TextUnit(
            text=text[start:end],
            source_document_id=document.id,
            token_count=len(text[start:end].split()),
            media_type="text/plain",
            start_offset=start,
            end_offset=end,
            content_hash=sha256(text[start:end].encode("utf-8")).hexdigest(),
        )
        for start, end in spans
    ]
    save_original(originals, content_hash, body)
    try:
        store.create(document, units)
    except DuplicateDocumentError:
        # A concurrent upload of the same bytes won the race.
        winner = store.get_by_hash(content_hash)
        if winner is None:
            raise
        return winner, False
    return document, True
