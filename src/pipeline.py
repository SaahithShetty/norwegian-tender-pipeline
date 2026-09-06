"""End-to-end run: discover, retrieve, match, group, write.

Discovery deliberately uses several routes, because the brief's central point is
that one route does not find everything:

1. **name search** in both Norwegian and English spellings, derived by rule
2. **ATC code search**, which finds notices that cite the substance by classification
   instead of naming it
3. **CPV sweep** over pharmaceutical procurement, which surfaces tenders whose title
   never mentions a molecule
4. **therapeutic-area lookup**, for molecules bought inside bundled framework
   agreements rather than in a tender of their own

Each route is reported separately at the end, so it is visible which route actually
contributed and which returned nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .config import CPV_PHARMACEUTICAL, MOLECULES, OUTPUT_DIR, Molecule
from .dedup import (
    Procurement,
    cross_source_pairs,
    deduplicate_notices,
    group_procurements,
)
from .http import HttpClient
from .matching.engine import MatchingEngine
from .models import SourceNotice, TenderRow
from .naming import normalise_for_search, norwegian_variants
from .output import build_row, coverage_report, write_csv
from .sources.doffin import DoffinSource
from .sources.ted import TedSource

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunReport:
    """What each discovery route contributed, for the README and for auditing."""

    notices_by_route: dict[str, int] = field(default_factory=dict)
    rows_by_molecule: dict[str, int] = field(default_factory=dict)
    rows_by_method: dict[str, int] = field(default_factory=dict)
    molecules_without_rows: list[str] = field(default_factory=list)
    cross_source_duplicates: list[tuple[str, list[str]]] = field(default_factory=list)
    procurements: int = 0
    notices: int = 0


class Pipeline:
    """Runs every discovery route over every molecule and produces the CSV."""

    def __init__(
        self,
        client: HttpClient | None = None,
        molecules: Sequence[Molecule] = MOLECULES,
    ) -> None:
        self._client = client or HttpClient()
        self._molecules = tuple(molecules)
        self._doffin = DoffinSource(self._client)
        self._ted = TedSource(self._client)
        self._engine = MatchingEngine(molecules=self._molecules)

    # ------------------------------------------------------------- discovery --

    def _discover(self) -> tuple[list[SourceNotice], dict[str, int]]:
        collected: list[SourceNotice] = []
        counts: dict[str, int] = defaultdict(int)

        for molecule in self._molecules:
            # Route 1: name, in every spelling the transliteration rules produce.
            for variant in sorted(norwegian_variants(molecule.inn_en)):
                for source in (self._doffin, self._ted):
                    found = list(source.search_by_term(variant))
                    counts[f"name:{source.name}"] += len(found)
                    collected.extend(found)

            # Route 2: ATC code. Doffin does not index these in free text but TED
            # does, so this route is expected to contribute unevenly - which is
            # itself worth reporting.
            for source in (self._doffin, self._ted):
                found = list(source.search_by_term(molecule.atc_code))
                counts[f"atc:{source.name}"] += len(found)
                collected.extend(found)

            # Route 3: therapeutic area, for molecules procured inside bundles.
            if molecule.therapeutic_area:
                for source in (self._doffin, self._ted):
                    found = list(source.search_by_term(molecule.therapeutic_area))
                    counts[f"area:{source.name}"] += len(found)
                    collected.extend(found)

        # Route 4: classification sweep, independent of any molecule name.
        for source in (self._doffin, self._ted):
            found = list(source.search_by_cpv(CPV_PHARMACEUTICAL, limit=150))
            counts[f"cpv:{source.name}"] += len(found)
            collected.extend(found)

        return collected, dict(counts)

    # ------------------------------------------------------------------- run --

    def run(self, output_path: Path | None = None) -> tuple[list[TenderRow], RunReport]:
        raw_notices, route_counts = self._discover()
        notices = deduplicate_notices(raw_notices)
        logger.info("retrieved %d notices (%d after dedup)", len(raw_notices), len(notices))

        rows: list[TenderRow] = []
        matched_notices: list[SourceNotice] = []

        for notice in notices:
            # Matching is attempted twice, because the two stages see different data.
            # Search results carry no CPV codes; those arrive only with notice detail.
            # A therapeutic-area match requires a pharmaceutical CPV to corroborate a
            # weak signal, so it can only succeed after enrichment. Matching cheaply
            # first means we do not spend a detail request on every notice in a CPV
            # sweep, only on the ones that already look relevant.
            matches = self._engine.match_notice(notice)
            candidate = bool(matches) or self._may_match_after_enrichment(notice)
            if not candidate:
                continue

            enriched = self._enrich(notice)
            matches = self._engine.match_notice(enriched)
            if not matches:
                continue
            matched_notices.append(enriched)

            for match in matches:
                rows.append(build_row(enriched, match))

        rows = self._drop_redundant_bundle_rows(rows)
        rows.sort(key=lambda r: (r.productMolecule, r.publicationDate or "", r.noticeId))

        # Counts are taken from the rows that survived filtering, so the report
        # describes the file that was written rather than an intermediate state.
        rows_by_molecule = defaultdict(int)
        rows_by_method = defaultdict(int)
        for row in rows:
            rows_by_molecule[row.productMolecule] += 1
            rows_by_method[row.detectionMethod] += 1

        procurements = group_procurements(matched_notices)
        report = RunReport(
            notices_by_route=route_counts,
            rows_by_molecule=dict(rows_by_molecule),
            rows_by_method=dict(rows_by_method),
            molecules_without_rows=[
                m.inn_en for m in self._molecules if not rows_by_molecule.get(m.inn_en)
            ],
            cross_source_duplicates=cross_source_pairs(procurements),
            procurements=len(procurements),
            notices=len(notices),
        )

        write_csv(rows, output_path or (OUTPUT_DIR / "output.csv"))
        return rows, report

    @staticmethod
    def _drop_redundant_bundle_rows(rows: list[TenderRow]) -> list[TenderRow]:
        """Keep therapeutic-area inferences only where nothing better was found.

        A bundle row says "this molecule is plausibly inside this framework
        agreement". That is valuable for a molecule with no tender of its own, and
        pure noise for one that was found by name: lenalidomide has its own LIS
        tenders, so also listing it against every oncology framework would bury the
        confirmed evidence under speculation.
        """
        confirmed = {row.productMolecule for row in rows if row.moleculeDetected}
        kept = [
            row
            for row in rows
            if row.moleculeDetected or row.productMolecule not in confirmed
        ]
        dropped = len(rows) - len(kept)
        if dropped:
            logger.info(
                "dropped %d speculative bundle rows for molecules confirmed by name",
                dropped,
            )
        return kept

    def _may_match_after_enrichment(self, notice: SourceNotice) -> bool:
        """Whether fetching notice detail could turn this into a match.

        Only therapeutic-area matching depends on enriched data, so this is limited
        to notices naming an area we care about. Without the guard, a CPV sweep would
        trigger a detail request for every pharmaceutical notice in the database.
        """
        haystack = normalise_for_search(notice.title or "")
        return any(
            molecule.therapeutic_area
            and normalise_for_search(molecule.therapeutic_area) in haystack
            for molecule in self._molecules
        )

    def _enrich(self, notice: SourceNotice) -> SourceNotice:
        source = self._doffin if notice.source_name == self._doffin.name else self._ted
        return source.enrich(notice)


def summarise_run(rows: Sequence[TenderRow], report: RunReport) -> str:
    """A short human-readable run summary, printed at the end of a run."""
    lines = [
        f"notices retrieved : {report.notices}",
        f"procurements      : {report.procurements}",
        f"rows written      : {len(rows)}",
        "",
        "rows by molecule:",
    ]
    for molecule, count in sorted(report.rows_by_molecule.items()):
        lines.append(f"  {molecule:<14} {count}")
    if report.molecules_without_rows:
        lines.append(f"  (no rows: {', '.join(report.molecules_without_rows)})")

    lines.extend(["", "rows by detection method:"])
    for method, count in sorted(report.rows_by_method.items()):
        lines.append(f"  {method:<26} {count}")

    lines.extend(["", "notices found per discovery route:"])
    for route, count in sorted(report.notices_by_route.items()):
        lines.append(f"  {route:<16} {count}")

    if report.cross_source_duplicates:
        lines.extend(["", "same procurement in both portals:"])
        for key, ids in report.cross_source_duplicates:
            lines.append(f"  {key:<20} {', '.join(ids)}")

    coverage = coverage_report(rows)
    empty = [c for c, n in coverage["filled_by_column"].items() if n == 0]
    if empty:
        lines.extend(["", f"columns with no data: {', '.join(empty)}"])

    return "\n".join(lines)
