"""TED — Tenders Electronic Daily, the EU-level publication.

Norwegian above-threshold procurement is published both on Doffin and on TED, so TED
serves two purposes here: it corroborates Doffin, and it carries structured monetary
fields that Doffin's search payload does not expose.

Access notes:

* `POST https://api.ted.europa.eu/v3/notices/search`, no API key
* expert query syntax: `FT~"term"`, `buyer-country="NOR"`, joined with AND
* the `fields` parameter is strictly validated; sending an unknown name returns a 400
  whose body enumerates all ~1830 valid names. `_SAFE_FIELDS` below was validated that
  way rather than guessed (`received-tender-count`, for example, does not exist).
* many values arrive language-keyed (`{"eng": [...]}`) or list-wrapped, so every read
  goes through `_scalar`.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from ..http import HttpClient
from ..models import NoticeLifecycle, SourceNotice

logger = logging.getLogger(__name__)

SEARCH_URL: Final[str] = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_HTML: Final[str] = "https://ted.europa.eu/en/notice"

# Verified against the API's own field enumeration.
_SAFE_FIELDS: Final[tuple[str, ...]] = (
    "publication-number",
    "notice-title",
    "buyer-name",
    "publication-date",
    "notice-type",
    "procedure-type",
    "total-value",
    "total-value-cur",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "winner-name",
    "winner-decision-date",
    "contract-duration-start-date-lot",
    "classification-cpv",
)

# TED notice-type vocabulary -> lifecycle stage.
# `veat` is a voluntary ex-ante transparency notice: an intended direct award, which
# matters commercially because it signals a purchase that never reached competition.
_LIFECYCLE_BY_TYPE: Final[dict[str, NoticeLifecycle]] = {
    "pin-only": NoticeLifecycle.PRIOR_INFORMATION,
    "pin-buyer": NoticeLifecycle.PRIOR_INFORMATION,
    "pin-rtl": NoticeLifecycle.PRIOR_INFORMATION,
    "cn-standard": NoticeLifecycle.COMPETITION,
    "cn-social": NoticeLifecycle.COMPETITION,
    "cn-desg": NoticeLifecycle.COMPETITION,
    "can-standard": NoticeLifecycle.AWARD,
    "can-social": NoticeLifecycle.AWARD,
    "can-desg": NoticeLifecycle.AWARD,
    "veat": NoticeLifecycle.DIRECT_AWARD_INTENT,
}


class TedSource:
    """Adapter over the TED v3 search API, scoped to Norwegian buyers."""

    name = "ted"

    def __init__(
        self, client: HttpClient, *, country: str = "NOR", page_size: int = 50
    ) -> None:
        self._client = client
        self._country = country
        self._page_size = page_size

    # ------------------------------------------------------------- discovery --

    def _search(self, expert_query: str, *, limit: int) -> list[dict[str, Any]]:
        payload = {
            "query": expert_query,
            "limit": min(limit, self._page_size),
            "page": 1,
            "fields": list(_SAFE_FIELDS),
        }
        try:
            result = self._client.post_json(SEARCH_URL, payload)
        except Exception as exc:  # noqa: BLE001 - a failed query must not stop the run
            logger.warning("ted: query %r failed: %s", expert_query, exc)
            return []
        return result.get("notices") or []

    def search_by_term(self, term: str, *, limit: int = 50) -> list[SourceNotice]:
        """Full-text search scoped to Norwegian buyers.

        Also used for ATC codes: TED indexes classification codes in notice full
        text, which Doffin's free-text search does not.
        """
        query = f'FT~"{term}" AND buyer-country="{self._country}"'
        logger.info("ted: searching %r", term)
        return [self._to_notice(n) for n in self._search(query, limit=limit)]

    def search_by_cpv(self, cpv_code: str, *, limit: int = 200) -> list[SourceNotice]:
        query = f'buyer-country="{self._country}" AND classification-cpv="{cpv_code}"'
        logger.info("ted: searching CPV %s", cpv_code)
        return [self._to_notice(n) for n in self._search(query, limit=limit)]

    def enrich(self, notice: SourceNotice) -> SourceNotice:
        """TED search already returns every field we use, so there is nothing to add."""
        return notice

    # ------------------------------------------------------------ conversion --

    def _to_notice(self, hit: dict[str, Any]) -> SourceNotice:
        publication_number = str(_scalar(hit.get("publication-number")) or "")
        notice_type = _scalar(hit.get("notice-type"))

        # A contract notice states the tender's estimated value; an award notice
        # states what was actually awarded. Assigning both from `total-value` would
        # conflate an expectation with an outcome.
        lifecycle = _LIFECYCLE_BY_TYPE.get(notice_type or "", NoticeLifecycle.OTHER)
        total_value = _as_float(_scalar(hit.get("total-value")))
        estimated = _as_float(_scalar(hit.get("estimated-value-lot"))) or total_value
        awarded = total_value if lifecycle is NoticeLifecycle.AWARD else None
        if lifecycle is NoticeLifecycle.AWARD:
            estimated = None

        return SourceNotice(
            notice_id=publication_number,
            title=str(_scalar(hit.get("notice-title")) or "").strip(),
            source_url=f"{NOTICE_HTML}/{publication_number}",
            source_name=self.name,
            buyer=_scalar(hit.get("buyer-name")),
            notice_type=notice_type,
            lifecycle=lifecycle,
            publication_date=_iso_date(_scalar(hit.get("publication-date"))),
            contract_start=_iso_date(
                _scalar(hit.get("contract-duration-start-date-lot"))
            ),
            procedure_type=_scalar(hit.get("procedure-type")),
            estimated_value=estimated,
            awarded_value=awarded,
            awarded_supplier=_scalar(hit.get("winner-name")),
            currency=_scalar(hit.get("total-value-cur"))
            or _scalar(hit.get("estimated-value-cur-lot")),
            cpv_codes=tuple(_as_tuple(hit.get("classification-cpv"))),
            raw=hit,
        )


def _scalar(value: Any) -> Any:
    """Unwrap TED's language-keyed dicts and single-element lists."""
    if isinstance(value, dict):
        for key in ("eng", "nor", "MUL"):
            if key in value:
                value = value[key]
                break
        else:
            value = next(iter(value.values()), None)
    if isinstance(value, list):
        value = value[0] if value else None
    return value


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, dict):
        value = next(iter(value.values()), None)
    if isinstance(value, list):
        return tuple(str(v) for v in value if v)
    return (str(value),)


def _iso_date(value: Any) -> str | None:
    """Trim TED's timezone-suffixed timestamps to a plain ISO date."""
    if not value:
        return None
    return str(value)[:10]


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
