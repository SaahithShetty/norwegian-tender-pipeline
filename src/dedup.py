"""Recognising that several notices describe one procurement.

The same purchase appears repeatedly: as a prior information notice, a contract
notice, an award or a cancellation, and again on TED in another language under a
different identifier. Treating each as a separate tender would inflate every count
and every value in the analysis.

Notices are therefore grouped into procurements, and each group keeps its own
lifecycle. Rows are *not* collapsed: one row per notice is still emitted, because a
cancellation and its re-tender are genuinely different events. What the grouping adds
is a shared `procurement_key`, so the analysis can count purchases rather than
paperwork, and a resolved status per group.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final, Iterable, Sequence

from .models import NoticeLifecycle, SourceNotice
from .naming import normalise_for_search

logger = logging.getLogger(__name__)

# Sykehusinnkjop numbers its pharmaceutical tenders in two styles, both seen live:
#   "LIS 2234", "LIS 2234b"   - the older prefixed form (Doffin titles)
#   "2632a", "2507gj-1"       - the bare form used in newer eForms titles
# The trailing letters are significant: LIS 2234 and LIS 2234b are a tender and its
# re-run, which are related but not the same procurement, so they are kept distinct.
_LIS_REF: Final[re.Pattern[str]] = re.compile(r"\bLIS\s*(\d{4}[a-z]?)\b", re.IGNORECASE)
_BARE_TENDER_REF: Final[re.Pattern[str]] = re.compile(
    r"\b(\d{4}[a-z]{0,2}(?:-\d)?)\s+(?=[A-Za-zÆØÅæøå])"
)

# Ordering used to decide a group's overall outcome. Later stages win, except that a
# cancellation is terminal for the notice that carries it.
_STAGE_RANK: Final[dict[NoticeLifecycle, int]] = {
    NoticeLifecycle.OTHER: 0,
    NoticeLifecycle.PRIOR_INFORMATION: 1,
    NoticeLifecycle.COMPETITION: 2,
    NoticeLifecycle.DIRECT_AWARD_INTENT: 3,
    NoticeLifecycle.AWARD: 4,
    NoticeLifecycle.CANCELLED: 5,
}


@dataclass(slots=True)
class Procurement:
    """One purchase, and every notice published about it."""

    key: str
    notices: list[SourceNotice] = field(default_factory=list)

    @property
    def stage(self) -> NoticeLifecycle:
        """The furthest stage this procurement reached."""
        return max(
            (n.lifecycle for n in self.notices),
            key=lambda s: _STAGE_RANK.get(s, 0),
            default=NoticeLifecycle.OTHER,
        )

    @property
    def was_cancelled(self) -> bool:
        return any(n.lifecycle is NoticeLifecycle.CANCELLED for n in self.notices)

    @property
    def estimated_value(self) -> float | None:
        """The estimated value, taken from whichever notice actually published one.

        Values are not summed: notices about one purchase restate the same figure,
        so adding them would multiply the tender's size by its paperwork.
        """
        values = [n.estimated_value for n in self.notices if n.estimated_value]
        return max(values) if values else None

    @property
    def awarded_value(self) -> float | None:
        values = [n.awarded_value for n in self.notices if n.awarded_value]
        return max(values) if values else None

    @property
    def sources(self) -> set[str]:
        return {n.source_name for n in self.notices}


def procurement_key(notice: SourceNotice) -> str:
    """A stable identifier for the purchase a notice belongs to.

    Preference order reflects how reliable each signal is:

    1. the buyer's own tender reference (LIS number) - survives translation and
       appears in both portals, so it links a Norwegian Doffin record to its English
       TED counterpart
    2. the tender document permalink - shared by notices of one competition
    3. the notice id - a singleton group, used when nothing better exists
    """
    # The tender number may sit in the title (Doffin) or in the buyer's internal
    # reference (TED), so both are searched before falling back.
    for candidate in (notice.title, notice.tender_ref, notice.description):
        lis = _LIS_REF.search(candidate or "")
        if lis:
            return f"lis:{lis.group(1).lower()}"

    # Newer eForms titles carry the bare code, e.g. "2632a Everolimus". Only the
    # title is searched here: the bare pattern is loose enough that running it over
    # free-text descriptions would match incidental numbers.
    bare = _BARE_TENDER_REF.search(notice.title or "")
    if bare:
        return f"lis:{bare.group(1).lower()}"

    if notice.documents_url:
        return f"docs:{notice.documents_url}"

    # Fall back to a normalised title, which catches repeated notices that carry no
    # LIS number but share wording.
    if notice.title:
        slug = re.sub(r"[^a-z0-9]+", "-", normalise_for_search(notice.title)).strip("-")
        if slug:
            return f"title:{slug[:60]}"

    return f"notice:{notice.notice_id}"


def group_procurements(notices: Iterable[SourceNotice]) -> list[Procurement]:
    """Group notices into the procurements they describe."""
    groups: dict[str, Procurement] = {}
    for notice in notices:
        # Always recompute rather than trusting whatever an adapter happened to set:
        # keys must share one format across portals, otherwise a Doffin record and
        # its TED twin land in separate groups and the duplicate goes unnoticed.
        key = procurement_key(notice)
        notice.procurement_key = key
        groups.setdefault(key, Procurement(key=key)).notices.append(notice)

    ordered = sorted(groups.values(), key=lambda p: p.key)
    logger.info(
        "grouped %d notices into %d procurements",
        sum(len(p.notices) for p in ordered),
        len(ordered),
    )
    return ordered


def deduplicate_notices(notices: Sequence[SourceNotice]) -> list[SourceNotice]:
    """Drop exact re-fetches of the same notice.

    Only identical (source, notice id) pairs are removed. Cross-portal duplicates are
    deliberately kept: a Doffin record and its TED twin carry different fields, and
    the fact that both exist is itself a finding.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[SourceNotice] = []
    for notice in notices:
        identity = (notice.source_name, notice.notice_id)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(notice)

    dropped = len(notices) - len(unique)
    if dropped:
        logger.info("dropped %d duplicate notice fetches", dropped)
    return unique


def cross_source_pairs(
    procurements: Sequence[Procurement],
) -> list[tuple[str, list[str]]]:
    """Procurements published in more than one portal, for reporting.

    These are the cross-language duplicates the brief warns about; surfacing them is
    more useful than silently merging them away.
    """
    pairs: list[tuple[str, list[str]]] = []
    for procurement in procurements:
        if len(procurement.sources) > 1:
            ids = [f"{n.source_name}:{n.notice_id}" for n in procurement.notices]
            pairs.append((procurement.key, sorted(ids)))
    return pairs


def by_molecule(
    procurements: Sequence[Procurement],
) -> dict[str, list[Procurement]]:
    """Index procurements by the molecules matched within them (analysis helper)."""
    index: dict[str, list[Procurement]] = defaultdict(list)
    for procurement in procurements:
        molecules = {
            n.raw.get("_molecule")
            for n in procurement.notices
            if n.raw.get("_molecule")
        }
        for molecule in molecules:
            index[str(molecule)].append(procurement)
    return dict(index)
