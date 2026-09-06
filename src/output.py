"""Turning matched notices into CSV rows.

Two rules govern this module:

* a field is written only when a source actually supplied it. Nothing is inferred,
  defaulted or back-filled, because an invented value is worse than an empty cell.
* the file is written UTF-8 with Norwegian characters intact.

Rows are emitted per notice rather than per procurement: a cancellation and its
re-tender are separate events, and collapsing them would hide the lifecycle.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable, Sequence

from .matching.base import Match
from .matching.engine import detected_flag
from .models import SourceNotice, TenderRow
from .normalise import clean_text, parse_date

logger = logging.getLogger(__name__)


def build_row(notice: SourceNotice, match: Match) -> TenderRow:
    """Build one CSV row from a notice and the evidence tying it to a molecule.

    Pack-level fields (item number, strength, pack size, max price, historical
    volume) are left empty here. They exist only inside tender annexes hosted on the
    buyer's document portal, which requires a supplier account; the notice payloads
    that this pipeline can reach do not carry them.
    """
    return TenderRow(
        noticeId=notice.notice_id,
        tenderRef=clean_text(notice.tender_ref),
        title=clean_text(notice.title) or "",
        country="NO",
        buyer=clean_text(notice.buyer),
        productMolecule=match.molecule.inn_en,
        moleculeDetected=detected_flag(match),
        moleculeVariant=clean_text(match.variant),
        detectionMethod=match.method.value,
        atcCode=match.molecule.atc_code,
        itemNumber=None,
        productName=None,
        strength=None,
        packSize=None,
        supplier=clean_text(notice.awarded_supplier),
        maxPrice=None,
        packsSoldLast12m=None,
        estimatedValue=notice.estimated_value,
        awardedValue=notice.awarded_value,
        awardedSupplier=clean_text(notice.awarded_supplier),
        # No "NOK" fallback: a currency the source did not state would be an invented
        # value, even though NOK is overwhelmingly likely for a Norwegian tender.
        currency=notice.currency,
        noticeType=clean_text(notice.notice_type),
        status=notice.lifecycle.value,
        publicationDate=parse_date(notice.publication_date),
        contractStart=parse_date(notice.contract_start),
        procedureType=clean_text(notice.procedure_type),
        sourceDocument=clean_text(notice.title),
        sourceUrl=notice.source_url,
    )


def write_csv(rows: Sequence[TenderRow], path: Path) -> Path:
    """Write rows to a UTF-8 CSV with the column order defined by TenderRow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = TenderRow.column_names()

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_dict())

    logger.info("wrote %d rows to %s", len(rows), path)
    return path


def coverage_report(rows: Iterable[TenderRow]) -> dict[str, dict[str, int]]:
    """How many rows carry a value for each column, and per molecule.

    Reported at the end of a run so that gaps are stated rather than discovered by
    the reader. Knowing which fields the sources genuinely support is part of the
    result, not an afterthought.
    """
    rows = list(rows)
    filled: dict[str, int] = {column: 0 for column in TenderRow.column_names()}
    per_molecule: dict[str, int] = {}

    for row in rows:
        values = row.to_csv_dict()
        for column, value in values.items():
            if value != "":
                filled[column] += 1
        per_molecule[row.productMolecule] = per_molecule.get(row.productMolecule, 0) + 1

    return {
        "total_rows": {"rows": len(rows)},
        "filled_by_column": filled,
        "rows_by_molecule": per_molecule,
    }
