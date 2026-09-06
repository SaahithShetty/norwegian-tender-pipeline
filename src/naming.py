"""Norwegian <-> English INN name handling.

Norwegian drug nomenclature differs from English systematically, not arbitrarily.
Norwegian follows the Nordic/Latin-derived INN convention, so the mapping is a small
set of orthographic rules rather than a lookup table of five strings:

    axitinib     -> aksitinib     (x -> ks)
    lenalidomide -> lenalidomid   (-ide -> -id, silent trailing e dropped)
    paliperidone -> paliperidon   (-one -> -on)
    ciclosporin  -> ciklosporin   (c -> k before a/o/u/consonant)
    cephalexin   -> kefaleksin    (ph -> f, c -> k, x -> ks)

Encoding the rules means a sixth molecule works without a code change, and it makes
the assumption explicit and testable. `norwegian_variants()` deliberately returns a
set of candidates rather than one string: the rules are a strong heuristic, not a
guarantee, so we search on all plausible forms and let the source decide.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# Suffix rules, applied longest-first so that -ide is tried before -de.
_SUFFIX_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("ide", "id"),      # lenalidomide -> lenalidomid
    ("one", "on"),      # paliperidone -> paliperidon
    ("ine", "in"),      # cytarabine   -> cytarabin
    ("ate", "at"),      # methotrexate -> metotreksat
    ("ole", "ol"),      # omeprazole   -> omeprazol
    ("ase", "ase"),     # unchanged, listed for clarity
)

# Within-word substitutions. Order matters: ph -> f before c -> k.
_INNER_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("ph", "f"),        # ciprofloxacin -> ciprofloksacin
    ("th", "t"),        # methotrexate  -> metotreksat
    ("x", "ks"),        # axitinib      -> aksitinib
    ("cc", "kk"),
    ("qu", "kv"),
)

# Norwegian resolves Latin "c" by sound, not by a single substitution:
#   hard c (before a/o/u/consonant) -> k   ciclosporin -> ciklosporin
#   soft c (before e)               -> s   docetaxel   -> dosetaksel
# Soft c before i/y is left alone: Norwegian keeps e.g. "cytarabin", "ciklosporin".
_C_SOFT_TO_S: Final[re.Pattern[str]] = re.compile(r"c(?=e)")
_C_HARD_TO_K: Final[re.Pattern[str]] = re.compile(r"c(?![eiy])")


def to_norwegian(inn_en: str) -> str:
    """Transliterate an English INN into its most likely Norwegian spelling."""
    name = inn_en.strip().lower()
    for src, dst in _INNER_RULES:
        name = name.replace(src, dst)
    name = _C_SOFT_TO_S.sub("s", name)
    name = _C_HARD_TO_K.sub("k", name)
    for src, dst in _SUFFIX_RULES:
        if name.endswith(src):
            name = name[: -len(src)] + dst
            break
    return name


def norwegian_variants(inn_en: str) -> set[str]:
    """All plausible spellings to search on, English and Norwegian.

    Includes the stem (name minus its final syllable) so that inflected or
    compounded forms such as "lenalidomidkapsler" still match.
    """
    english = inn_en.strip().lower()
    norwegian = to_norwegian(english)
    variants = {english, norwegian}

    # A conservative stem: drop a trailing -id/-in/-on/-ol so declined forms match.
    for suffix in ("id", "in", "on", "ol"):
        if norwegian.endswith(suffix) and len(norwegian) > len(suffix) + 4:
            variants.add(norwegian[: -len(suffix)])
            break

    return {v for v in variants if v}


# ae, oe and aa are distinct letters in the Norwegian alphabet, not accented vowels,
# so Unicode decomposition leaves them untouched and they must be mapped explicitly.
# Without this, "SYKEHUSINNKJOEP" never matches "sykehusinnkjop" and buyer matching
# fails silently.
_NORWEGIAN_LETTERS: Final[dict[int, str]] = str.maketrans(
    {"æ": "ae", "ø": "o", "å": "a", "Æ": "ae", "Ø": "o", "Å": "a"}
)


def normalise_for_search(text: str) -> str:
    """Casefold and fold Norwegian letters for spelling-insensitive comparison.

    Only ever used for *matching*. Values written to the CSV keep their original
    form, because the brief requires ae/oe/aa to survive the pipeline intact.
    """
    folded = text.casefold().translate(_NORWEGIAN_LETTERS)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
