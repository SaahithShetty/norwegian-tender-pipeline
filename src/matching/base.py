"""Molecule matching: deciding whether a notice concerns a target molecule.

The brief is explicit that name matching alone will systematically miss part of the
answer, so matching is a list of independent strategies rather than one function.
Each strategy reports how it matched, which is recorded per row so a reviewer can
audit the evidence and so coverage can be reported per method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Molecule
from ..models import DetectionMethod, SourceNotice


@dataclass(frozen=True, slots=True)
class Match:
    """Evidence that a notice concerns a molecule.

    `variant` is the exact string as it appeared in the source, not our normalised
    form: the brief asks for the matched term as written, which is what makes the
    Norwegian/English spelling split visible in the output.
    """

    molecule: Molecule
    method: DetectionMethod
    variant: str | None
    confidence: float


@runtime_checkable
class Matcher(Protocol):
    """One way of tying a notice to a molecule."""

    method: DetectionMethod

    def match(self, notice: SourceNotice, molecule: Molecule) -> Match | None:
        """Return evidence, or None when this strategy finds nothing."""
        ...


def searchable_text(notice: SourceNotice) -> str:
    """The text fields a name or code could plausibly appear in."""
    return " ".join(
        part for part in (notice.title, notice.description, notice.tender_ref) if part
    )
