"""The Norwegian regulated maximum-price register (DMP / Legemiddelverket).

`maxPrice` in the output means the *regulated maximum* a pharmacy may pay, which is
set by the medicines agency — not anything a supplier offers in a tender. The tender
annex therefore cannot supply it (its price column is what a bidder fills in), but the
agency publishes the register itself as a freely downloadable spreadsheet:

    https://www.dmp.no/offentlig-finansiering/pris-pa-legemidler/maksimalpris
        -> legemiddelpriser-<date>.xlsx   (~9 800 pack rows, no authentication)

The join is exact: the register is keyed by `Varenummer`, the same Norwegian item
number the tender annex lists as `VARENR`. That is what makes this a lookup rather
than an estimate — every price written to a row is the published figure for that
specific pack.

Two prices are published per pack and they are not interchangeable:
  * **AIP** (apotekets innkjøpspris) — maximum *pharmacy purchase* price
  * **AUP** (apotekets utsalgspris)  — maximum *retail* price, AIP plus margin and VAT
Hospital procurement is conducted in AIP — LIS notices state their scope "i maksimal
AIP" — so AIP is the figure used here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import openpyxl

from ..http import HttpClient
from ..normalise import clean_text, parse_number

logger = logging.getLogger(__name__)

REGISTER_PAGE: Final[str] = (
    "https://www.dmp.no/offentlig-finansiering/pris-pa-legemidler/maksimalpris"
)
# The published file carries its date in the name; it is resolved from the page so a
# newer edition is picked up without a code change.
_ASSET_PATTERN: Final[str] = r'href="(/contentassets/[^"]*legemiddelpriser-[^"]*\.xlsx)"'
_BASE: Final[str] = "https://www.dmp.no"

# Header names as published, mapped to fields. Matched on text, not position.
_COLUMNS: Final[dict[str, str]] = {
    "Varenummer": "item_number",
    "Handelsnavn": "product_name",
    "Innehaver": "marketing_authorisation_holder",
    "Virkestoff": "substance",
    "Styrke": "strength",
    "Maks AIP": "max_aip",
    "Maks AUP": "max_aup",
}


@dataclass(frozen=True, slots=True)
class RegulatedPrice:
    """The published maximum price for one pack."""

    item_number: str
    product_name: str | None
    marketing_authorisation_holder: str | None
    substance: str | None
    strength: str | None
    max_aip: float | None
    max_aup: float | None
    source_document: str


def _header_row(sheet: object) -> tuple[dict[str, int], int] | None:
    """Find the header row and map fields to columns.

    The file opens with a title row, so the header is not row 1.
    """
    for row_index in range(1, 12):
        mapping: dict[str, int] = {}
        for column in range(1, (sheet.max_column or 0) + 1):  # type: ignore[attr-defined]
            header = str(sheet.cell(row_index, column).value or "").strip()  # type: ignore[attr-defined]
            for prefix, field in _COLUMNS.items():
                if header.startswith(prefix):
                    mapping.setdefault(field, column)
        if "item_number" in mapping and "max_aip" in mapping:
            return mapping, row_index
    return None


def parse_price_register(path: Path) -> dict[str, RegulatedPrice]:
    """Parse the register into {varenummer: RegulatedPrice}."""
    try:
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop a run
        logger.warning("could not open price register %s: %s", path.name, exc)
        return {}

    sheet = workbook[workbook.sheetnames[0]]
    located = _header_row(sheet)
    if located is None:
        logger.warning("price register %s: expected headers not found", path.name)
        return {}
    columns, header_index = located

    prices: dict[str, RegulatedPrice] = {}
    for row in sheet.iter_rows(min_row=header_index + 1, values_only=True):
        if not row:
            continue

        def value(field: str) -> object:
            column = columns.get(field)
            return row[column - 1] if column and column <= len(row) else None

        item_number = clean_text(value("item_number"))
        if not item_number:
            continue

        prices[item_number] = RegulatedPrice(
            item_number=item_number,
            product_name=clean_text(value("product_name")),
            marketing_authorisation_holder=clean_text(
                value("marketing_authorisation_holder")
            ),
            substance=clean_text(value("substance")),
            strength=clean_text(value("strength")),
            max_aip=parse_number(value("max_aip")),
            max_aup=parse_number(value("max_aup")),
            source_document=path.name,
        )

    logger.info("parsed %d regulated prices from %s", len(prices), path.name)
    return prices


def download_price_register(client: HttpClient, destination: Path) -> Path | None:
    """Fetch the current register, resolving its dated filename from the page.

    Returns None rather than raising if the file cannot be retrieved, so the pipeline
    continues with `maxPrice` empty instead of failing.
    """
    import re

    destination.mkdir(parents=True, exist_ok=True)
    try:
        page = client.get_bytes(REGISTER_PAGE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not load the price register page: %s", exc)
        return None
    if not page:
        return None

    match = re.search(_ASSET_PATTERN, page.decode("utf-8", "replace"))
    if not match:
        logger.warning("no legemiddelpriser spreadsheet linked on %s", REGISTER_PAGE)
        return None

    url = f"{_BASE}{match.group(1)}"
    data = client.get_bytes(url)
    if not data or not data.startswith(b"PK"):
        logger.warning("price register download did not return a spreadsheet")
        return None

    target = destination / Path(match.group(1)).name
    target.write_bytes(data)
    logger.info("downloaded price register %s", target.name)
    return target


def load_price_register(
    client: HttpClient, cache_dir: Path
) -> dict[str, RegulatedPrice]:
    """Return the register, downloading it once and reusing the local copy after."""
    existing = sorted(cache_dir.glob("legemiddelpriser-*.xlsx"))
    path = existing[-1] if existing else download_price_register(client, cache_dir)
    return parse_price_register(path) if path else {}
