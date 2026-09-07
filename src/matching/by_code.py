"""Match a molecule by classification code rather than by name.

Two different codes, doing two different jobs:

* **ATC** identifies the substance itself (L04AX04 *is* lenalidomide), so an ATC hit
  is strong evidence even when the molecule is never named. Norwegian notices do cite
  ATC codes, and TED indexes them in full text.
* **CPV** identifies the *category* ("pharmaceutical products"), so a CPV hit is not
  evidence of any particular molecule. It is used to scope a search and, here, to
  corroborate a weak textual hit — never to assert a molecule on its own.

Axitinib is the case that motivates the third matcher: it has no single-molecule
tender in Norway, and is procured inside bundled oncology framework agreements whose
notices name no molecules at all.
"""

from __future__ import annotations

import re
from typing import Final

from ..config import CPV_PHARMACEUTICAL, Molecule
from ..models import DetectionMethod, SourceNotice
from ..naming import normalise_for_search, norwegian_variants
from .base import Match, searchable_text

# Predecessor ATC codes, searched alongside the current one so that notices published
# before a reclassification still match.
#
# Deliberately empty for axitinib. It is often said to have moved from L01EX07 to
# L01EK01, and this map originally carried that alias - but the LIS 2207 price annex
# shows L01EX07 now denotes *cabozantinib* (Cabometyx, Cometriq). The code was
# reassigned, not retired, so searching it would attribute another manufacturer's
# packs to axitinib. Verified against real supplier data rather than assumed.
_RETIRED_ATC: Final[dict[str, tuple[str, ...]]] = {
    "L01EG02": ("L01XE10",),  # everolimus, reclassified in the 2021 revision
}


class AtcCodeMatcher:
    """Detects a molecule by its ATC classification code appearing in the notice."""

    method = DetectionMethod.ATC_CODE

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        haystack = searchable_text(notice).upper()
        if not haystack:
            return None

        candidates = (molecule.atc_code, *_RETIRED_ATC.get(molecule.atc_code, ()))
        for code in candidates:
            # Word-bounded so L01EK01 does not match inside a longer identifier.
            if re.search(rf"\b{re.escape(code)}\b", haystack):
                return Match(
                    molecule=molecule,
                    method=DetectionMethod.ATC_CODE,
                    variant=code,
                    confidence=0.98,
                )
        return None


class TherapeuticBundleMatcher:
    """Flags framework agreements that plausibly *contain* a molecule.

    Sykehusinnkjop procures many oncology molecules through a single bundled tender
    (for example "LIS 2207 Onkologi", ~3 200 MNOK/year) whose notice names no
    individual substance; the molecule list lives in an attached requirement
    specification.

    A hit here is explicitly *not* proof the molecule is in scope. Rows produced from
    it are written with moleculeDetected=false and this detection method, so the
    inference is visible in the output instead of being hidden inside a true/false.
    """

    method = DetectionMethod.THERAPEUTIC_BUNDLE

    def __init__(self, *, require_pharma_cpv: bool = True) -> None:
        self._require_pharma_cpv = require_pharma_cpv

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        area = molecule.therapeutic_area
        if not area:
            return None

        haystack = normalise_for_search(searchable_text(notice))
        if normalise_for_search(area) not in haystack:
            return None

        # A therapeutic-area word alone is too weak; require the notice to also be
        # classified as pharmaceutical procurement.
        if self._require_pharma_cpv and not self._is_pharmaceutical(notice):
            return None

        return Match(
            molecule=molecule,
            method=DetectionMethod.THERAPEUTIC_BUNDLE,
            variant=area,
            confidence=0.35,
        )

    @staticmethod
    def _is_pharmaceutical(notice: SourceNotice) -> bool:
        return any(code.startswith(CPV_PHARMACEUTICAL[:4]) for code in notice.cpv_codes)


class SourceFullTextMatcher:
    """Trusts the portal's own full-text index when our fields cannot see the term.

    TED returns a generic OJ headline ("Norway-Vadso: Pharmaceutical products") for
    pre-eForms notices, with no descriptive title or description in the API response.
    A notice can therefore be a genuine hit - TED's full-text search found the
    molecule inside the notice document - while carrying no readable evidence of it.

    Dropping those rows loses real data: three Norwegian paliperidone notices worth
    7.4M and 14.7M NOK were being discarded this way. Rather than infer, this matcher
    records the portal's finding as what it is: TED matched the term in the document,
    and the exact term is written to `moleculeVariant` so the claim is auditable.

    It fires only when the notice text genuinely carries no molecule name, so it never
    overrides a direct match.
    """

    method = DetectionMethod.SOURCE_FULL_TEXT

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        query = (notice.matched_query or "").strip()
        if not query:
            return None

        # The query has to identify *this* molecule: its name in either spelling, or
        # its ATC code. A CPV sweep or therapeutic-area search says nothing about
        # which substance a notice concerns.
        normalised = normalise_for_search(query)
        identifies = normalised in {
            normalise_for_search(variant) for variant in norwegian_variants(molecule.inn_en)
        } or query.upper() == molecule.atc_code.upper()
        if not identifies:
            return None

        # This matcher exists for notices whose text we cannot read. When the text is
        # readable and simply does not name the molecule, the portal's hit is not
        # enough on its own: TED's index also matches a molecule mentioned as a
        # laboratory analyte, which is how the Shimadzu LC-MS/MS tender resurfaced.
        if _has_readable_subject(notice):
            return None

        return Match(
            molecule=molecule,
            method=DetectionMethod.SOURCE_FULL_TEXT,
            variant=query,
            confidence=0.85,
        )


# TED's generic OJ headline for pre-eForms notices. It is the same string for every
# pharmaceutical tender, so its presence means the response told us nothing about the
# notice's actual subject.
_GENERIC_HEADLINE: Final[re.Pattern[str]] = re.compile(
    r"^norway\b.*\b(pharmaceutical|medical|laboratory)", re.IGNORECASE
)


def _has_readable_subject(notice: SourceNotice) -> bool:
    """Whether the notice tells us what it is actually for.

    A descriptive title ("2632a Everolimus", "76746 LC-MS/MS analysis platform") is
    readable; TED's generic OJ headline is not.
    """
    title = (notice.title or "").strip()
    if not title:
        return False
    return not _GENERIC_HEADLINE.match(normalise_for_search(title))
