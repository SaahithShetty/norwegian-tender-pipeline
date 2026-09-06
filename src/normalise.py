"""Value normalisation: numbers, dates and text conventions.

Norwegian formatting differs from English in ways that silently corrupt data if
ignored: "1 234 567,89" uses a comma decimal separator and a space (often a
non-breaking space) as the thousands separator. Parsing that with float() either
raises or, worse, truncates.

Text is never transliterated here. The brief requires ae/oe/aa to survive the
pipeline intact, so folding happens only inside matching, never on output.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Final

logger = logging.getLogger(__name__)

# Space variants used as thousands separators: ASCII, non-breaking, narrow no-break.
_SPACES: Final[str] = "    "
_CURRENCY_NOISE: Final[re.Pattern[str]] = re.compile(
    rf"[{_SPACES}]|NOK|kr\.?|,-|MNOK", re.IGNORECASE
)
# "ca. 320 millioner kroner", "3 200 MNOK" - scale words that multiply the figure.
_SCALE_WORDS: Final[tuple[tuple[re.Pattern[str], float], ...]] = (
    (re.compile(r"\bmillioner?\b|\bmill\.?\b|\bMNOK\b", re.IGNORECASE), 1_000_000),
    (re.compile(r"\bmilliarder?\b|\bmrd\.?\b", re.IGNORECASE), 1_000_000_000),
)

# No trailing \b: Doffin returns timestamps such as "2021-06-29T10:00:00Z", where the
# date is followed immediately by "T". Requiring a word boundary there silently
# returned None for every timestamped field.
_ISO_DATE: Final[re.Pattern[str]] = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_NORWEGIAN_DATE: Final[re.Pattern[str]] = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def parse_number(value: Any) -> float | None:
    """Parse a Norwegian-formatted number.

    Returns None rather than 0.0 for unparseable input: a missing value must stay
    missing, because a zero would read as a real price of nothing.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    multiplier = 1.0
    for pattern, scale in _SCALE_WORDS:
        if pattern.search(text):
            multiplier = scale
            break

    cleaned = _CURRENCY_NOISE.sub("", text)
    # Keep only the numeric core, so surrounding words like "ca." are discarded.
    match = re.search(r"-?\d+(?:[.,]\d+)*", cleaned)
    if not match:
        return None
    number = match.group(0)

    # Decide which separator is decimal. Norwegian writes "1 234,56", but Doffin's
    # eForm renders amounts in the English convention ("120,000,000"), so a comma is
    # not always a decimal point and assuming it is silently discards those values.
    #
    # A single group of exactly three digits after one comma - "1,500" - is genuinely
    # ambiguous: 1500 in English, 1.5 in Norwegian. Guessing wrong is a 1000x error on
    # a price, so it is refused rather than resolved by coin flip. (No such value
    # appears in the current sources; both write prices unambiguously.) Two or more
    # groups, "1,234,567", can only be thousands separators, so those are safe.
    if re.fullmatch(r"-?\d{1,3},\d{3}", number):
        logger.warning(
            "refusing ambiguous number %r: could be %s or %s depending on locale",
            text,
            number.replace(",", ""),
            number.replace(",", "."),
        )
        return None
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3}){2,}", number):
        number = number.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", number):
        number = number.replace(".", "")
    elif "," in number:
        number = number.replace(".", "").replace(",", ".")

    try:
        return float(number) * multiplier
    except ValueError:
        return None


def parse_date(value: Any) -> str | None:
    """Return an ISO YYYY-MM-DD string, or None when no date is present."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    iso = _ISO_DATE.search(text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"

    norwegian = _NORWEGIAN_DATE.search(text)
    if norwegian:
        day, month, year = norwegian.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    return None


def clean_text(value: Any) -> str | None:
    """Collapse whitespace while preserving Norwegian characters exactly."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def extract_strength(text: str | None) -> str | None:
    """Pull a dose strength such as "5 mg" or "0,5 mg/ml" out of a product string.

    Intended for pack-level rows parsed out of tender annexes, where strings look
    like "Lenalidomid 5 mg kapsler, 21 stk".

    **Deliberately not applied to notice titles.** Norwegian tender titles carry
    codes, not product descriptions, and running this over them produces false
    positives: the real title "2407 g og j onkologi ikke patenterte legemidler"
    yields a strength of "2407 g", which is a tender number, not a dose. Since the
    annexes are not reachable without a supplier account, this currently has no live
    caller - it is kept, with this warning, for when they are.
    """
    if not text:
        return None
    match = re.search(
        r"\b\d+(?:[.,]\d+)?\s*(?:mg|mikrogram|µg|ug|g|ml|IE|IU)(?:\s*/\s*\w+)?\b",
        text,
        re.IGNORECASE,
    )
    return clean_text(match.group(0)) if match else None


def extract_pack_size(text: str | None) -> str | None:
    """Pull a pack count such as "56 stk" or "x 30" out of a product string.

    Same caveat as `extract_strength`: annex product strings only, never titles.
    """
    if not text:
        return None
    match = re.search(r"\b(\d+)\s*(?:stk|stykk|tabletter|kapsler)\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\bx\s*(\d+)\b", text, re.IGNORECASE)
    return match.group(1) if match else None
