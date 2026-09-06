"""Match a molecule by its name, in either Norwegian or English spelling.

Norwegian and English forms of the same molecule occur across records of the same
tender, so both are searched and the method records which one actually hit. That
distinction is not cosmetic: it is the evidence that a cross-language duplicate
exists at all.
"""

from __future__ import annotations

import re
from typing import Final

from ..config import Molecule
from ..models import DetectionMethod, SourceNotice
from ..naming import normalise_for_search, norwegian_variants, to_norwegian
from .base import Match, searchable_text

# Norwegian compounds freely ("lenalidomidkapsler"), so a variant may be followed by
# more word characters. It must still start at a word boundary, otherwise short stems
# would match unrelated substrings.
_BOUNDARY: Final[str] = r"\b{}\w*"


class NameMatcher:
    """Detects a molecule named in a notice's text."""

    method = DetectionMethod.NAME_NORWEGIAN

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        haystack = normalise_for_search(searchable_text(notice))
        if not haystack:
            return None

        norwegian = to_norwegian(molecule.inn_en)
        english = molecule.inn_en.casefold()

        # Longest variants first so "lenalidomide" is preferred over the "lenalidom"
        # stem, giving the most specific evidence rather than the loosest.
        for variant in sorted(norwegian_variants(molecule.inn_en), key=len, reverse=True):
            pattern = _BOUNDARY.format(re.escape(normalise_for_search(variant)))
            found = re.search(pattern, haystack)
            if not found:
                continue

            actual = self._as_written(searchable_text(notice), found.group(0))
            if normalise_for_search(variant) == normalise_for_search(english):
                method = DetectionMethod.NAME_ENGLISH
            elif normalise_for_search(variant) == normalise_for_search(norwegian):
                method = DetectionMethod.NAME_NORWEGIAN
            else:
                # A stem match is real but less specific; label it by the spelling
                # convention it most resembles.
                method = DetectionMethod.NAME_NORWEGIAN

            return Match(
                molecule=molecule,
                method=method,
                variant=actual,
                confidence=0.95 if len(variant) >= len(norwegian) else 0.75,
            )
        return None

    @staticmethod
    def _as_written(original: str, normalised_hit: str) -> str:
        """Recover the term as the source spelled it, including any diacritics.

        Matching happens on folded text, but the CSV must carry the original form.
        """
        pattern = re.compile(re.escape(normalised_hit), re.IGNORECASE)
        for token in re.findall(r"\w+", original):
            if normalise_for_search(token).startswith(normalised_hit):
                return token
        match = pattern.search(original)
        return match.group(0) if match else normalised_hit


class BrandNameMatcher:
    """Detects a molecule by a registered brand name.

    Kept separate from the INN matcher because a brand hit is weaker evidence: brands
    are reused across formulations and markets.
    """

    method = DetectionMethod.BRAND_NAME

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        if not molecule.brand_names:
            return None
        haystack = normalise_for_search(searchable_text(notice))
        for brand in molecule.brand_names:
            pattern = _BOUNDARY.format(re.escape(normalise_for_search(brand)))
            if re.search(pattern, haystack):
                return Match(
                    molecule=molecule,
                    method=DetectionMethod.BRAND_NAME,
                    variant=brand,
                    confidence=0.8,
                )
        return None
