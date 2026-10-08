"""Run extraction over every text unit of one document.

Provider failures (outage, quota) stop the job: every later unit would fail too.
A malformed response skips only that unit. Re-running resumes where it stopped.
"""

import logging
from uuid import UUID

from aether.extraction.extract import ExtractionError
from aether.extraction.pipeline import Extractor
from aether.storage.text_units import Neo4jTextUnitStore

log = logging.getLogger("aether.extraction")
PAGE = 1000


def extract_document(extractor: Extractor, units: Neo4jTextUnitStore, document_id: UUID) -> None:
    offset = 0
    while page := units.list_by_document(document_id, limit=PAGE, offset=offset):
        for unit in page:
            try:
                extractor.run(unit)
            except ExtractionError:
                log.warning("extraction.unit_skipped text_unit_id=%s", unit.id)
        offset += PAGE
    log.info("extraction.document_done document_id=%s", document_id)
