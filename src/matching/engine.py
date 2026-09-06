"""Runs every matching strategy over a notice and resolves the result.

Strategies are tried in descending order of evidential strength and the strongest
hit wins, so a notice that cites an ATC code is attributed on that basis rather than
on an incidental mention of a name.

The engine also rejects hits that are textually correct but commercially wrong. A
tender for laboratory instruments can legitimately mention a molecule as an analyte;
counting it as a pharmaceutical tender would put a false row in the output, which the
brief treats as a defect.
"""

from __future__ import annotations

import logging
from typing import Final, Iterable, Sequence

from ..config import MOLECULES, Molecule
from ..models import DetectionMethod, SourceNotice
from ..naming import normalise_for_search
from .base import Match, Matcher, searchable_text
from .by_code import AtcCodeMatcher, TherapeuticBundleMatcher
from .by_name import BrandNameMatcher, NameMatcher

logger = logging.getLogger(__name__)

# Strongest evidence first. ATC identifies the substance; a name is specific but can
# appear incidentally; a brand is reused; a therapeutic area is only an inference.
DEFAULT_MATCHERS: Final[tuple[Matcher, ...]] = (
    AtcCodeMatcher(),
    NameMatcher(),
    BrandNameMatcher(),
    TherapeuticBundleMatcher(),
)

# Wording that indicates the notice buys equipment or analysis services that merely
# reference a substance, rather than buying the substance itself. Observed in real
# data: an everolimus hit awarded to a laboratory-instrument supplier.
_NON_PHARMA_SIGNALS: Final[tuple[str, ...]] = (
    "analyseplattform",
    "analyseinstrument",
    "massespektrometer",
    "kromatograf",
    "laboratorieutstyr",
    "reagens",
    "kalibrator",
    "referansestoff",
    "laboratory",
    "instrument",
)


class MatchingEngine:
    """Applies matching strategies and filters out non-pharmaceutical hits."""

    def __init__(
        self,
        matchers: Sequence[Matcher] = DEFAULT_MATCHERS,
        molecules: Sequence[Molecule] = MOLECULES,
    ) -> None:
        self._matchers = tuple(matchers)
        self._molecules = tuple(molecules)

    def match_notice(self, notice: SourceNotice) -> list[Match]:
        """Return one best match per molecule found in this notice."""
        if self._is_non_pharmaceutical(notice):
            logger.debug(
                "rejecting %s as non-pharmaceutical: %s", notice.notice_id, notice.title
            )
            return []

        matches: list[Match] = []
        for molecule in self._molecules:
            best = self._best_match(notice, molecule)
            if best is not None:
                matches.append(best)
        return matches

    def _best_match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        for matcher in self._matchers:
            match = matcher.match(notice, molecule)
            if match is not None:
                return match
        return None

    @staticmethod
    def _is_non_pharmaceutical(notice: SourceNotice) -> bool:
        """Reject notices that reference a substance but do not purchase it.

        Deliberately conservative: it only fires when the notice carries an explicit
        equipment or laboratory signal, so a genuine drug tender is not discarded.
        """
        haystack = normalise_for_search(searchable_text(notice))
        if not haystack:
            return False
        return any(signal in haystack for signal in _NON_PHARMA_SIGNALS)


def detected_flag(match: Match) -> bool:
    """Whether the molecule was explicitly found, as opposed to inferred.

    A therapeutic-area bundle match is an inference about where a molecule is likely
    procured, not an observation of it, so it is reported as not detected.
    """
    return match.method is not DetectionMethod.THERAPEUTIC_BUNDLE


def summarise(matches: Iterable[Match]) -> dict[str, int]:
    """Count matches by detection method, for run reporting."""
    counts: dict[str, int] = {}
    for match in matches:
        counts[match.method.value] = counts.get(match.method.value, 0) + 1
    return counts
