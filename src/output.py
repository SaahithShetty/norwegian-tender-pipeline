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
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .matching.base import Match
from .matching.engine import detected_flag
from .models import SourceNotice, TenderRow
from .normalise import clean_text, parse_date
from .parsing.annex import AnnexPack
from .parsing.price_register import RegulatedPrice

logger = logging.getLogger(__name__)


def build_row(notice: SourceNotice, match: Match) -> TenderRow:
    """Build one CSV row from a notice and the evidence tying it to a molecule.

    Pack-level fields are left empty here because a notice does not carry them: they
    come from the tender annex and the price register, and `build_pack_rows` fills
    them in for the notices those documents cover.
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
        # Doffin writes "Open", TED writes "open" for the same procedure. Casing is
        # normalised so the column is consistent across sources, as the brief asks.
        procedureType=_normalise_procedure(notice.procedure_type),
        sourceDocument=clean_text(notice.title),
        sourceUrl=notice.source_url,
    )


def _normalise_procedure(value: str | None) -> str | None:
    """Lower-case the procedure type so the two portals agree.

    Only casing is changed. TED also emits bare numeric codes for older notices
    (procedure-type "9"); those are passed through untouched rather than guessed at,
    since mapping a code to a name without the codelist would be inventing a value.
    """
    text = clean_text(value)
    return text.lower() if text else None


def build_pack_rows(
    notice: SourceNotice,
    match: Match,
    packs: Sequence[AnnexPack],
    prices: Mapping[str, RegulatedPrice] | None = None,
) -> list[TenderRow]:
    """Expand one notice into a row per pack, using annex detail.

    The brief asks for one row per molecule per pack "where pack-level detail
    exists", falling back to one row per notice otherwise. This is the former case.

    `maxPrice` never comes from the annex, whose price column is what a *supplier
    offers* when bidding — a different quantity from the buyer's regulated ceiling.
    It comes from the medicines agency's published maximum-price register instead,
    joined on the Norwegian item number, so the figure written is the official one
    for that exact pack. A pack absent from the register (delisted, for example)
    keeps an empty `maxPrice` rather than an inferred one.
    """
    base = build_row(notice, match)
    prices = prices or {}
    rows: list[TenderRow] = []
    for pack in packs:
        regulated = prices.get(pack.item_number or "")
        row = replace(
            base,
            itemNumber=pack.item_number,
            productName=pack.product_name,
            strength=pack.strength,
            packSize=pack.pack_size,
            supplier=pack.supplier,
            packsSoldLast12m=pack.packs_last_12m,
            maxPrice=regulated.max_aip if regulated else None,
            # Hospital tenders are denominated in AIP, and the register publishes in
            # NOK, so a pack with a regulated price has a known currency.
            currency=base.currency or ("NOK" if regulated else None),
            sourceDocument=_pack_provenance(pack, regulated),
        )
        rows.append(row)
    return rows


def _pack_provenance(pack: AnnexPack, price: RegulatedPrice | None) -> str:
    """Name every document a pack row draws on, so a value can be traced back."""
    if price is None:
        return pack.source_document
    return f"{pack.source_document}; {price.source_document}"


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
