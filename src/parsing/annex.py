"""Parse LIS tender annexes (Prisskjema price forms) into pack-level detail.

These spreadsheets are the only place pack-level data exists: item number
(varenummer), product name, strength, pack size, supplier and historical volume.
They are attachments on the buyer's document portal and require a supplier account,
so the pipeline reads them from a local directory rather than fetching them.

What the format actually is, which changes what can be claimed from it:

* the sheet is a **blank bidding template**. `TILBUDT GIP` (the supplier's offered
  price) is empty in all 993 rows, because suppliers fill it in when they bid. So
  these files carry volumes but **no prices** - `maxPrice` cannot be sourced here.
* `PAKNINGER <year>` is historical consumption in packs, which is the volume figure
  the notice text refers to as "historisk forbruk de siste 12 måneder".
* rows carry an ATC code, which is what ties a pack to a molecule. Matching on ATC
  rather than on product name is what makes brand packs (Inlyta, Afinitor) resolve to
  the right substance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator

import openpyxl

from ..normalise import clean_text, parse_number

logger = logging.getLogger(__name__)

# The price sheet among the workbook's several sheets.
_PRICE_SHEET: Final[str] = "Prisskjema"

# Header names as they appear in the annex, mapped to the field they populate.
# Matching is done on the header text rather than on a column index, so a reordered
# or extended sheet still parses.
_COLUMNS: Final[dict[str, str]] = {
    "VARENR": "item_number",
    "VARENAVN": "product_name",
    "STYRKE": "strength",
    "ENHET": "pack_size",
    "LEVERANDØR": "supplier",
    "ATC": "atc_code",
    "TILBUDT GIP": "offered_price",
}

# "PAKNINGER 2021" - the year moves between tender rounds, so it is matched by prefix.
_VOLUME_HEADER: Final[re.Pattern[str]] = re.compile(r"^PAKNINGER\b", re.IGNORECASE)

# Excel error literals that must not be mistaken for data.
_ERROR_VALUES: Final[frozenset[str]] = frozenset(
    {"#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}
)


@dataclass(slots=True)
class AnnexPack:
    """One pack line from a tender annex."""

    item_number: str | None
    product_name: str | None
    strength: str | None
    pack_size: str | None
    supplier: str | None
    atc_code: str | None
    packs_last_12m: float | None
    offered_price: float | None
    source_document: str


def _cell_text(value: object) -> str | None:
    """Clean a cell, treating Excel error literals as absent rather than as text."""
    text = clean_text(value)
    if text is None or text.upper() in _ERROR_VALUES:
        return None
    return text


def _header_map(sheet: object) -> tuple[dict[str, int], int] | None:
    """Locate the header row and map field names to column indices.

    The header is not always the first row (these workbooks carry a banner row), so
    the first rows are scanned for one containing the mandatory VARENR column.
    """
    for row_index in range(1, 11):
        headers = {
            str(sheet.cell(row_index, col).value or "").strip(): col  # type: ignore[attr-defined]
            for col in range(1, sheet.max_column + 1)  # type: ignore[attr-defined]
        }
        if "VARENR" not in headers:
            continue

        mapping: dict[str, int] = {}
        for header, column in headers.items():
            for prefix, field in _COLUMNS.items():
                if header.upper().startswith(prefix):
                    mapping.setdefault(field, column)
            if _VOLUME_HEADER.match(header):
                mapping.setdefault("packs_last_12m", column)
        return mapping, row_index
    return None


def parse_annex(path: Path) -> list[AnnexPack]:
    """Read every pack line from one annex workbook."""
    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop a run
        logger.warning("could not open annex %s: %s", path.name, exc)
        return []

    if _PRICE_SHEET not in workbook.sheetnames:
        logger.warning("annex %s has no %r sheet", path.name, _PRICE_SHEET)
        return []

    sheet = workbook[_PRICE_SHEET]
    located = _header_map(sheet)
    if located is None:
        logger.warning("annex %s: no VARENR header found", path.name)
        return []
    columns, header_row = located

    packs: list[AnnexPack] = []
    for row in range(header_row + 1, sheet.max_row + 1):
        def value(field: str) -> object:
            column = columns.get(field)
            return sheet.cell(row, column).value if column else None

        item_number = _cell_text(value("item_number"))
        atc_code = _cell_text(value("atc_code"))
        if not item_number and not atc_code:
            continue  # blank or separator row

        packs.append(
            AnnexPack(
                item_number=item_number,
                product_name=_cell_text(value("product_name")),
                strength=_cell_text(value("strength")),
                pack_size=_cell_text(value("pack_size")),
                supplier=_cell_text(value("supplier")),
                atc_code=atc_code,
                packs_last_12m=parse_number(_cell_text(value("packs_last_12m"))),
                offered_price=parse_number(_cell_text(value("offered_price"))),
                source_document=path.name,
            )
        )

    logger.info("parsed %d pack lines from %s", len(packs), path.name)
    return packs


def load_annexes(directory: Path) -> list[AnnexPack]:
    """Parse every annex workbook in a directory, if the directory exists.

    Missing annexes are normal: they require a supplier account, so the pipeline must
    produce a complete run without them.
    """
    if not directory.is_dir():
        logger.info("no annex directory at %s - pack-level columns stay empty", directory)
        return []

    packs: list[AnnexPack] = []
    for path in sorted(directory.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue  # Excel lock file
        packs.extend(parse_annex(path))

    if not packs:
        logger.info("no annex pack lines found in %s", directory)
    return packs


def packs_for_atc(packs: list[AnnexPack], atc_code: str) -> Iterator[AnnexPack]:
    """Pack lines whose ATC code identifies the given substance.

    Exact match only. ATC codes are reassigned between revisions - L01EX07 denoted
    axitinib historically but denotes cabozantinib in current data - so prefix or
    fuzzy matching here would silently attribute one manufacturer's packs to another.
    """
    wanted = atc_code.strip().upper()
    for pack in packs:
        if pack.atc_code and pack.atc_code.strip().upper() == wanted:
            yield pack
