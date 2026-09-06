"""Tests for the English -> Norwegian INN transliteration rules.

The brief asks for the *pattern* behind Norwegian nomenclature rather than five
hard-coded strings, so these tests check two separate things:

1. the five target molecules resolve correctly (regression safety), and
2. the rules generalise to molecules the pipeline was never written for, which is
   the only real evidence that a rule was implemented rather than a lookup table.
"""

from __future__ import annotations

import pytest

from src.naming import norwegian_variants, normalise_for_search, to_norwegian


@pytest.mark.parametrize(
    ("english", "norwegian"),
    [
        ("Axitinib", "aksitinib"),        # x -> ks
        ("Everolimus", "everolimus"),     # unchanged
        ("Lenalidomide", "lenalidomid"),  # -ide -> -id
        ("Anagrelide", "anagrelid"),      # -ide -> -id
        ("Paliperidone", "paliperidon"),  # -one -> -on
    ],
)
def test_target_molecules(english: str, norwegian: str) -> None:
    assert to_norwegian(english) == norwegian


@pytest.mark.parametrize(
    ("english", "norwegian"),
    [
        ("Ciclosporin", "ciklosporin"),   # c -> k before a consonant
        ("Docetaxel", "dosetaksel"),      # c -> s before e, x -> ks
        ("Cytarabine", "cytarabin"),      # -ine -> -in, c kept before y
        ("Omeprazole", "omeprazol"),      # -ole -> -ol
        ("Methotrexate", "metotreksat"),  # th -> t, x -> ks, -ate -> -at
    ],
)
def test_rules_generalise_beyond_the_five(english: str, norwegian: str) -> None:
    """Molecules outside the target set — proves this is a rule, not a table."""
    assert to_norwegian(english) == norwegian


def test_variants_include_both_spellings() -> None:
    variants = norwegian_variants("Axitinib")
    assert {"axitinib", "aksitinib"} <= variants


def test_variants_include_stem_for_compounds() -> None:
    """Norwegian compounds words, e.g. 'lenalidomidkapsler' (lenalidomide capsules)."""
    assert "lenalidom" in norwegian_variants("Lenalidomide")


def test_normalise_strips_diacritics_for_matching_only() -> None:
    assert normalise_for_search("SYKEHUSINNKJØP") == "sykehusinnkjop"
    assert normalise_for_search("Ærlig Måte") == "aerlig mate"


def test_norwegian_letters_survive_when_not_normalising() -> None:
    """Folding is for matching only; the original string is never mutated in place."""
    original = "Sykehusinnkjøp HF"
    normalise_for_search(original)
    assert original == "Sykehusinnkjøp HF"
