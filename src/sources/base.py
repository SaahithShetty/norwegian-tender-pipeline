"""The contract every portal adapter implements.

A source knows how to *find* and *retrieve* notices from one portal. It knows
nothing about molecule matching, deduplication or the CSV: that keeps adding a
second country's portal to a single new file implementing this protocol.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from ..models import SourceNotice


@runtime_checkable
class NoticeSource(Protocol):
    """A portal that can be searched for procurement notices."""

    name: str

    def search_by_term(self, term: str) -> Iterable[SourceNotice]:
        """Find notices matching a free-text term (a molecule name or code)."""
        ...

    def search_by_cpv(self, cpv_code: str, *, limit: int) -> Iterable[SourceNotice]:
        """Find notices by classification code, independent of any name."""
        ...

    def enrich(self, notice: SourceNotice) -> SourceNotice:
        """Fetch per-notice detail that the search result does not carry.

        Implementations should return the notice unchanged rather than raise when
        detail is unavailable, so one bad record never aborts a run.
        """
        ...
