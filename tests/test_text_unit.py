from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether.core.models import TextUnit


def test_mismatched_content_hash_is_rejected():
    with pytest.raises(
        ValidationError,
        match="content_hash must match text",
    ):
        TextUnit(
            text="Nix",
            source_document_id=uuid4(),
            token_count=1,
            media_type="text/plain",
            content_hash=sha256(b"different text").hexdigest(),
        )


def test_valid_offsets_are_accepted():
    text = "Nix"

    unit = TextUnit(
        text=text,
        source_document_id=uuid4(),
        token_count=1,
        media_type="text/plain",
        content_hash=sha256(text.encode("utf-8")).hexdigest(),
        start_offset=9,
        end_offset=12,
    )

    assert unit.start_offset == 9
    assert unit.end_offset == 12
    assert unit.text == text


@pytest.mark.parametrize(
    "start,end",
    [(12, 9), (9, 9)],
)
def test_invalid_offset_ranges_are_rejected(start, end):
    text = "Nix"

    with pytest.raises(
        ValidationError, match="end_offset must be greater than start_offset"
    ):
        TextUnit(
            text=text,
            source_document_id=uuid4(),
            token_count=1,
            media_type="text/plain",
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
            start_offset=start,
            end_offset=end,
        )


@pytest.mark.parametrize(
    "start,end",
    [(None, None), (None, 12), (9, None)],
)
def test_unknown_offsets_are_accepted(start, end):
    text = "Nix"

    unit = TextUnit(
        text=text,
        source_document_id=uuid4(),
        token_count=1,
        media_type="text/plain",
        content_hash=sha256(text.encode("utf-8")).hexdigest(),
        start_offset=start,
        end_offset=end,
    )

    assert unit.start_offset == start
    assert unit.end_offset == end
    assert unit.text == text
