"""Tests for Norwegian number, date and text normalisation."""

from __future__ import annotations

import pytest

from src.normalise import (
    clean_text,
    extract_pack_size,
    extract_strength,
    parse_date,
    parse_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234 567,89", 1_234_567.89),   # space thousands, comma decimal
        ("320 000 000", 320_000_000.0),
        ("1.234.567", 1_234_567.0),        # dot as thousands separator
        ("12,5", 12.5),
        ("kr 1 500,50", 1_500.50),
        ("ca. 320 millioner kroner", 320_000_000.0),
        ("3 200 MNOK", 3_200_000_000.0),
        (14671946, 14_671_946.0),
    ],
)
def test_parse_number(raw: object, expected: float) -> None:
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "n/a", None, "ingen verdi"])
def test_unparseable_numbers_are_none_not_zero(raw: object) -> None:
    """A missing price must stay missing; 0.0 would read as a real price of nothing."""
    assert parse_number(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2021-06-29", "2021-06-29"),
        ("29.06.2021", "2021-06-29"),                 # Norwegian dotted format
        ("2021-06-29T10:00:00Z", "2021-06-29"),       # Doffin timestamp
        ("2021-06-29T12:00:00+02:00", "2021-06-29"),
        ("2021-01-29+01:00", "2021-01-29"),           # TED offset format
        ("", None),
        (None, None),
    ],
)
def test_parse_date(raw: object, expected: str | None) -> None:
    assert parse_date(raw) == expected


def test_norwegian_characters_survive_cleaning() -> None:
    """ae/oe/aa must reach the CSV intact; folding belongs to matching only."""
    assert clean_text("  SYKEHUSINNKJØP   HF  ") == "SYKEHUSINNKJØP HF"
    assert clean_text("Vadsø") == "Vadsø"


@pytest.mark.parametrize(
    ("text", "strength", "pack"),
    [
        ("Lenalidomid 5 mg kapsler, 21 stk", "5 mg", "21"),
        ("Everolimus 0,5 mg tabletter x 60", "0,5 mg", "60"),
        ("Paliperidon 100 mg/ml", "100 mg/ml", None),
        ("Ingen dose oppgitt", None, None),
    ],
)
def test_strength_and_pack_extraction(
    text: str, strength: str | None, pack: str | None
) -> None:
    assert extract_strength(text) == strength
    assert extract_pack_size(text) == pack


def test_strength_extraction_must_not_run_on_notice_titles() -> None:
    """Documents why these are not wired into the CSV.

    This is a real Doffin title. "2407 g" is a tender code, not a 2407-gram dose, so
    applying strength extraction to titles would write a fabricated value into the
    output. The extractors are for annex product strings only.
    """
    title = "2407 g og j onkologi ikke patenterte legemidler"
    assert extract_strength(title) == "2407 g"  # plausible-looking, and wrong
