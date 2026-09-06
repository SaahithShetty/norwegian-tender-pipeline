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
from ..naming import normalise_for_search
from .base import Match, searchable_text

# Axitinib moved from L01EX07 to L01EK01 in the 2021 ATC revision. Notices published
# either side of that change use different codes for the same substance, so both are
# searched.
_RETIRED_ATC: Final[dict[str, tuple[str, ...]]] = {
    "L01EK01": ("L01EX07",),  # axitinib, reclassified in the 2021 revision
    "L01EG02": ("L01XE10",),  # everolimus, reclassified in the 2021 revision
    # Anagrelide (L01XX35), lenalidomide (L04AX04) and paliperidone (N05AX13) have
    # not been reclassified, so they have no predecessor code to search for.
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
