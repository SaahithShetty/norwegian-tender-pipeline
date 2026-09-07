"""Tests for molecule matching.

Cases are drawn from real notices retrieved during development, including the
false positive that motivated the non-pharmaceutical filter.
"""

from __future__ import annotations

from src.config import MOLECULES_BY_NAME
from src.matching.engine import MatchingEngine, detected_flag
from src.models import DetectionMethod, SourceNotice

AXITINIB = MOLECULES_BY_NAME["axitinib"]
EVEROLIMUS = MOLECULES_BY_NAME["everolimus"]
LENALIDOMIDE = MOLECULES_BY_NAME["lenalidomide"]
PALIPERIDONE = MOLECULES_BY_NAME["paliperidone"]


def notice(title: str, **kwargs: object) -> SourceNotice:
    return SourceNotice(
        notice_id=kwargs.pop("notice_id", "test-1"),  # type: ignore[arg-type]
        title=title,
        source_url="https://example.test/1",
        source_name="test",
        **kwargs,  # type: ignore[arg-type]
    )


def test_matches_norwegian_spelling_and_records_it_as_written() -> None:
    """Doffin publishes 'Lenalidomid'; the CSV must show that, not our English form."""
    engine = MatchingEngine(molecules=[LENALIDOMIDE])
    (match,) = engine.match_notice(notice("LIS 2234 Lenalidomid"))
    assert match.molecule is LENALIDOMIDE
    assert match.method is DetectionMethod.NAME_NORWEGIAN
    assert match.variant == "Lenalidomid"


def test_matches_english_spelling_from_ted() -> None:
    """TED publishes the same tender in English - the cross-language duplicate."""
    engine = MatchingEngine(molecules=[LENALIDOMIDE])
    (match,) = engine.match_notice(notice("LIS 2234 Lenalidomide"))
    assert match.method is DetectionMethod.NAME_ENGLISH


def test_matches_by_atc_code_when_molecule_is_never_named() -> None:
    """Norwegian notices sometimes cite the classification instead of the substance."""
    engine = MatchingEngine(molecules=[PALIPERIDONE])
    (match,) = engine.match_notice(notice("Tilleggsavtale N05AX13 depotinjeksjon"))
    assert match.method is DetectionMethod.ATC_CODE
    assert match.variant == "N05AX13"


def test_atc_code_outranks_a_name_mention() -> None:
    """A code is stronger evidence than a name, so it should win."""
    engine = MatchingEngine(molecules=[PALIPERIDONE])
    (match,) = engine.match_notice(notice("Paliperidon N05AX13"))
    assert match.method is DetectionMethod.ATC_CODE


def test_rejects_laboratory_tender_that_merely_mentions_a_molecule() -> None:
    """Real case: an everolimus hit awarded to a laboratory-instrument supplier.

    The molecule appears as an analyte, not as something being bought.
    """
    engine = MatchingEngine(molecules=[EVEROLIMUS])
    assert engine.match_notice(notice("76746 LC-MS/MS analyseplattform everolimus")) == []


def test_keeps_genuine_pharmaceutical_tender() -> None:
    engine = MatchingEngine(molecules=[EVEROLIMUS])
    assert engine.match_notice(notice("2632a Everolimus og Mykofenolsyre")) != []


def test_therapeutic_bundle_needs_pharmaceutical_classification() -> None:
    """An oncology word alone is too weak without a pharma CPV to corroborate it."""
    engine = MatchingEngine(molecules=[AXITINIB])
    assert engine.match_notice(notice("LIS 2207 Onkologi")) == []
    assert engine.match_notice(
        notice("LIS 2207 Onkologi", cpv_codes=("33600000",))
    ) != []


def test_bundle_match_is_reported_as_not_detected() -> None:
    """A bundle hit is an inference about where a molecule is bought, not proof."""
    engine = MatchingEngine(molecules=[AXITINIB])
    (match,) = engine.match_notice(notice("LIS 2207 Onkologi", cpv_codes=("33600000",)))
    assert match.method is DetectionMethod.THERAPEUTIC_BUNDLE
    assert detected_flag(match) is False


def test_named_match_is_reported_as_detected() -> None:
    engine = MatchingEngine(molecules=[LENALIDOMIDE])
    (match,) = engine.match_notice(notice("LIS 2234 Lenalidomid"))
    assert detected_flag(match) is True


def test_recovers_notices_whose_text_the_api_does_not_expose() -> None:
    """TED returns a generic OJ headline for pre-eForms notices.

    The molecule is in the notice document - TED's own full-text index matched it -
    but no field in the API response carries it. Dropping these lost real Norwegian
    paliperidone tenders worth 7.4M and 14.7M NOK.
    """
    engine = MatchingEngine(molecules=[PALIPERIDONE])
    generic = notice("Norway-Vadsø: Pharmaceutical products")
    assert engine.match_notice(generic) == []  # nothing to match on its own

    generic.matched_query = "paliperidon"
    (match,) = engine.match_notice(generic)
    assert match.method is DetectionMethod.SOURCE_FULL_TEXT
    assert match.variant == "paliperidon"  # the exact term, so the claim is auditable


def test_full_text_evidence_does_not_resurrect_a_rejected_false_positive() -> None:
    """A readable title that simply is not about the molecule stays rejected.

    TED's index matches a molecule named as a laboratory analyte too, so trusting the
    search alone would have put the Shimadzu LC-MS/MS tender back into the output.
    """
    engine = MatchingEngine(molecules=[EVEROLIMUS])
    lab = notice("76746 LC-MS/MS analysis platform")
    lab.matched_query = "everolimus"
    assert engine.match_notice(lab) == []


def test_full_text_evidence_never_overrides_a_direct_match() -> None:
    """When the notice names the molecule, that is the stronger evidence."""
    engine = MatchingEngine(molecules=[LENALIDOMIDE])
    named = notice("LIS 2234 Lenalidomid")
    named.matched_query = "lenalidomid"
    (match,) = engine.match_notice(named)
    assert match.method is DetectionMethod.NAME_NORWEGIAN


def test_full_text_evidence_requires_a_molecule_specific_query() -> None:
    """A CPV or therapeutic-area sweep says nothing about which substance applies."""
    engine = MatchingEngine(molecules=[PALIPERIDONE])
    generic = notice("Norway-Vadsø: Pharmaceutical products")
    generic.matched_query = "33600000"
    assert engine.match_notice(generic) == []
