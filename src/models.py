"""The output row and the intermediate records that feed it.

`TenderRow` defines the CSV contract in exactly one place: field order here is the
column order in output.csv, so adding a column is a one-line change.

Every optional field defaults to None and is written as an empty cell. That is
deliberate: the brief treats an empty cell as information and an invented value as a
defect, so nothing in this module ever substitutes a placeholder for a missing value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Any


class DetectionMethod(StrEnum):
    """How a molecule was tied to a notice.

    Recorded per row so a reviewer can audit *why* we believe a row is about a given
    molecule, and so we can report coverage per method rather than as one number.
    """

    NAME_NORWEGIAN = "name-norwegian"
    NAME_ENGLISH = "name-english"
    BRAND_NAME = "brand-name"
    ATC_CODE = "atc-code"
    CPV_CODE = "cpv-code"
    THERAPEUTIC_BUNDLE = "therapeutic-area-bundle"
    # The portal's own full-text index matched the term inside the notice document,
    # in a part the API's metadata fields do not expose.
    SOURCE_FULL_TEXT = "source-full-text"


class NoticeLifecycle(StrEnum):
    """Where a notice sits in a procurement's life.

    One purchase commonly produces several notices; keeping the stage explicit lets
    the analysis distinguish a cancelled tender from a live one.
    """

    PRIOR_INFORMATION = "prior-information"
    COMPETITION = "competition"
    AWARD = "award"
    CANCELLED = "cancelled"
    DIRECT_AWARD_INTENT = "direct-award-intent"
    OTHER = "other"


@dataclass(slots=True)
class SourceNotice:
    """A notice as retrieved from a portal, before molecule matching.

    Keeping this separate from `TenderRow` means a new portal only has to produce
    this shape; it never needs to know about the CSV.
    """

    notice_id: str
    title: str
    source_url: str
    source_name: str
    buyer: str | None = None
    description: str | None = None
    tender_ref: str | None = None
    cpv_codes: tuple[str, ...] = ()
    notice_type: str | None = None
    lifecycle: NoticeLifecycle = NoticeLifecycle.OTHER
    status: str | None = None
    publication_date: str | None = None
    contract_start: str | None = None
    procedure_type: str | None = None
    estimated_value: float | None = None
    awarded_value: float | None = None
    awarded_supplier: str | None = None
    currency: str | None = None
    documents_url: str | None = None
    # The search term that retrieved this notice. When a portal's full-text index
    # matches a term inside the notice document, that is evidence the metadata
    # fields do not carry, so it is kept rather than discarded.
    matched_query: str | None = None
    # Identifiers used to recognise that two notices describe one procurement.
    procurement_key: str | None = None
    related_notice_ref: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class TenderRow:
    """One CSV row: a molecule seen in a notice, at pack level where available.

    Field order defines column order in the output file.
    """

    noticeId: str
    tenderRef: str | None
    title: str
    country: str
    buyer: str | None
    productMolecule: str
    moleculeDetected: bool
    moleculeVariant: str | None
    detectionMethod: str
    atcCode: str | None
    itemNumber: str | None
    productName: str | None
    strength: str | None
    packSize: str | None
    supplier: str | None
    maxPrice: float | None
    packsSoldLast12m: float | None
    estimatedValue: float | None
    awardedValue: float | None
    awardedSupplier: str | None
    currency: str | None
    noticeType: str | None
    status: str | None
    publicationDate: str | None
    contractStart: str | None
    procedureType: str | None
    sourceDocument: str | None
    sourceUrl: str

    @classmethod
    def column_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_csv_dict(self) -> dict[str, str]:
        """Render to strings, writing None as an empty cell rather than 'None'."""
        out: dict[str, str] = {}
        for key, value in asdict(self).items():
            if value is None:
                out[key] = ""
            elif isinstance(value, bool):
                out[key] = "true" if value else "false"
            else:
                out[key] = str(value)
        return out
