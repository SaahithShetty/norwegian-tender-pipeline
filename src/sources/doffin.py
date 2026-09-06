"""Doffin — the Norwegian national procurement database.

Access notes (these were derived by inspecting the site's JS bundle, since the
search UI is a client-rendered SPA that returns a 1.3 KB shell to a plain GET):

* the public web client calls two undocumented but unauthenticated JSON endpoints,
  configured in the bundle as VITE_APP_SEARCH_API_URL / VITE_APP_API_URL
* search is a POST with a JSON body, not a GET with query parameters
* the page parameter is `page`, not `pageNumber`
* CPV filtering must go inside `facets.cpvCodesId.checkedItems`. A top-level
  `cpvCodes` key is accepted and silently ignored, returning the entire database
  (~158 000 notices) with a 200 — so an unvalidated filter fails open, not closed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final, Iterator

from ..http import HttpClient
from ..models import NoticeLifecycle, SourceNotice
from ..normalise import parse_number

logger = logging.getLogger(__name__)

SEARCH_URL: Final[str] = "https://api.doffin.no/webclient/api/v2/search-api/search"
NOTICE_URL: Final[str] = "https://api.doffin.no/webclient/api/v2/notices-api/notices"
PUBLIC_NOTICE_URL: Final[str] = "https://doffin.no/notices"

_FACET_KEYS: Final[tuple[str, ...]] = (
    "cpvCodesId",
    "cpvCodesLabel",
    "type",
    "status",
    "contractNature",
    "procurementStrategicLabels",
    "location",
    "buyer",
    "winner",
)

# Doffin's notice type vocabulary mapped onto procurement lifecycle stages.
# Values verified against live responses rather than guessed: Doffin labels a prior
# information notice ADVISORY_NOTICE/PLANNING ("veiledende kunngjoring"), and an
# intended direct award INTENTION_ANNOUNCEMENT ("intensjonskunngjoring").
_LIFECYCLE_BY_TYPE: Final[dict[str, NoticeLifecycle]] = {
    "ADVISORY_NOTICE": NoticeLifecycle.PRIOR_INFORMATION,
    "PLANNING": NoticeLifecycle.PRIOR_INFORMATION,
    "ANNOUNCEMENT_OF_COMPETITION": NoticeLifecycle.COMPETITION,
    "COMPETITION": NoticeLifecycle.COMPETITION,
    "RESULT": NoticeLifecycle.AWARD,
    "CANCELLED_OR_MISSING_CONCLUSION_OF_CONTRACT": NoticeLifecycle.CANCELLED,
    "INTENTION_ANNOUNCEMENT": NoticeLifecycle.DIRECT_AWARD_INTENT,
}

# "LIS 2234", "LIS 2207b" - Sykehusinnkjop's internal tender numbering, which is the
# most reliable way to recognise notices belonging to one procurement.
_LIS_REF: Final[re.Pattern[str]] = re.compile(r"\bLIS\s*(\d{4}[a-z]?)\b", re.IGNORECASE)


class DoffinSource:
    """Adapter over the Doffin public web-client API."""

    name = "doffin"

    def __init__(self, client: HttpClient, *, page_size: int = 50) -> None:
        self._client = client
        self._page_size = page_size

    # ------------------------------------------------------------- discovery --

    def _build_body(
        self, search_string: str, *, page: int, cpv_codes: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        facets: dict[str, Any] = {key: {"checkedItems": []} for key in _FACET_KEYS}
        facets["cpvCodesId"] = {"checkedItems": list(cpv_codes)}
        facets["publicationDate"] = {"from": None, "to": None}
        return {
            "numHitsPerPage": self._page_size,
            "page": page,
            "searchString": search_string,
            "sortBy": "RELEVANCE",
            "facets": facets,
        }

    def _paged_search(
        self, search_string: str, *, cpv_codes: tuple[str, ...] = (), limit: int
    ) -> Iterator[dict[str, Any]]:
        seen = 0
        page = 1
        while seen < limit:
            body = self._build_body(search_string, page=page, cpv_codes=cpv_codes)
            payload = self._client.post_json(SEARCH_URL, body)
            hits = payload.get("hits") or []
            if not hits:
                return
            for hit in hits:
                yield hit
                seen += 1
                if seen >= limit:
                    return
            total = payload.get("numHitsTotal") or 0
            if page * self._page_size >= total:
                return
            page += 1

    def search_by_term(self, term: str, *, limit: int = 50) -> list[SourceNotice]:
        logger.info("doffin: searching %r", term)
        return [self._to_notice(hit) for hit in self._paged_search(term, limit=limit)]

    def search_by_cpv(self, cpv_code: str, *, limit: int = 200) -> list[SourceNotice]:
        """Classification-based discovery, independent of the molecule being named."""
        logger.info("doffin: searching CPV %s", cpv_code)
        return [
            self._to_notice(hit)
            for hit in self._paged_search("", cpv_codes=(cpv_code,), limit=limit)
        ]

    # ------------------------------------------------------------ conversion --

    def _to_notice(self, hit: dict[str, Any]) -> SourceNotice:
        notice_id = str(hit.get("id") or "")
        title = (hit.get("heading") or "").strip()
        buyers = hit.get("buyer") or []
        buyer = (buyers[0].get("name") if buyers else None) or None

        all_types = tuple(hit.get("allTypes") or ())
        lifecycle = self._lifecycle(hit.get("type"), all_types)

        return SourceNotice(
            notice_id=notice_id,
            title=title,
            source_url=f"{PUBLIC_NOTICE_URL}/{notice_id}",
            source_name=self.name,
            buyer=buyer,
            description=(hit.get("description") or None),
            tender_ref=self._lis_reference(title),
            notice_type=hit.get("type"),
            lifecycle=lifecycle,
            status=hit.get("status"),
            publication_date=hit.get("publicationDate"),
            estimated_value=_as_float(hit.get("estimatedValue")),
            currency="NOK",
            procurement_key=self._lis_reference(title),
            raw=hit,
        )

    @staticmethod
    def _lifecycle(
        primary_type: str | None, all_types: tuple[str, ...]
    ) -> NoticeLifecycle:
        """Resolve lifecycle stage.

        A notice can carry several types at once; a cancellation is the most
        specific outcome and therefore wins over a generic RESULT.
        """
        candidates = [primary_type, *all_types]
        for candidate in candidates:
            if candidate and _LIFECYCLE_BY_TYPE.get(candidate) is NoticeLifecycle.CANCELLED:
                return NoticeLifecycle.CANCELLED
        for candidate in candidates:
            mapped = _LIFECYCLE_BY_TYPE.get(candidate or "")
            if mapped is not None:
                return mapped
        return NoticeLifecycle.OTHER

    @staticmethod
    def _lis_reference(title: str) -> str | None:
        match = _LIS_REF.search(title or "")
        return f"LIS {match.group(1).upper()}" if match else None

    # -------------------------------------------------------------- enrichment --

    @staticmethod
    def _value_from_description(description: str | None) -> float | None:
        """Recover a contract value stated in prose rather than in a value field.

        Doffin's structured estimatedValue is usually null for these tenders, but the
        description states the size in words, e.g.

            "Det totale avtaleomfang er anslatt i maksimal AIP til
             ca. 320 millioner kroner per ar"

        This is the buyer's own published figure, not an inference, so reading it is
        extraction rather than estimation. Only the phrase introduced by "anslatt"
        (estimated) or "avtaleomfang" (contract scope) is read, so unrelated numbers
        elsewhere in the text are ignored.
        """
        if not description:
            return None
        # The window cannot exclude "." because the figure is usually preceded by
        # "ca." (circa); it excludes sentence ends by requiring no intervening
        # digits, so the match stays anchored to the first amount after the cue word.
        match = re.search(
            r"(?:avtaleomfang|anslått|anslatt|verdi)\D{0,120}?"
            r"(\d[\d\s.,]*?)\s*(millioner?|mill\.?|MNOK|milliarder?|mrd\.?)",
            description,
            re.IGNORECASE,
        )
        if not match:
            return None
        return parse_number(f"{match.group(1)} {match.group(2)}")

    def enrich(self, notice: SourceNotice) -> SourceNotice:
        """Fetch notice detail: internal reference, CPV codes, procedure, documents."""
        try:
            detail = self._client.get_json(f"{NOTICE_URL}/{notice.notice_id}")
        except Exception as exc:  # noqa: BLE001 - one bad notice must not stop the run
            logger.warning("doffin: detail unavailable for %s: %s", notice.notice_id, exc)
            return notice

        eform_values = _flatten_eform(detail.get("eform") or [])
        notice.cpv_codes = tuple(detail.get("allCpvCodes") or ())
        notice.documents_url = detail.get("competitionDocsUrl")
        notice.procedure_type = eform_values.get("Type of procedure")
        notice.description = detail.get("description") or notice.description
        if notice.estimated_value is None:
            notice.estimated_value = self._value_from_description(notice.description)
        notice.contract_start = _first_date(eform_values, ("Start date", "Duration start date"))
        internal_ref = eform_values.get("Internal identifier")
        if internal_ref:
            notice.tender_ref = internal_ref.strip() or notice.tender_ref

        # Prefer the document permalink as the procurement key: notices belonging to
        # one purchase share it even when their titles differ across languages.
        if notice.documents_url:
            notice.procurement_key = notice.documents_url

        notice.raw = {**notice.raw, "detail": detail}
        return notice


def _flatten_eform(sections: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse the nested eForm section tree into {label: value}.

    The tree is deeply nested and label-keyed; a flat map is enough for the handful
    of fields the CSV needs, and keeps the caller free of tree walking.
    """
    out: dict[str, str] = {}

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes or []:
            label = node.get("label")
            value = node.get("value")
            if label and isinstance(value, str) and value.strip():
                out.setdefault(label, value.strip())
            children = node.get("sections")
            if children:
                walk(children)

    walk(sections)
    return out


def _first_date(values: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if values.get(key):
            return values[key]
    return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
