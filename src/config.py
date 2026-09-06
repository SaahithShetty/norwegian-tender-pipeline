"""Static configuration: target molecules, classification codes, and runtime settings.

Everything a future maintainer would need to add a sixth molecule lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
CACHE_DIR: Final[Path] = PROJECT_ROOT / "data" / "cache"
OUTPUT_DIR: Final[Path] = PROJECT_ROOT / "output"

# Polite crawling. The brief asks for 1-2s rate limiting and a meaningful User-Agent.
REQUEST_DELAY_SECONDS: Final[float] = 1.5
REQUEST_TIMEOUT_SECONDS: Final[int] = 45
MAX_RETRIES: Final[int] = 3
USER_AGENT: Final[str] = (
    "RGS-tender-research/1.0 (take-home exercise; contact: shettysaahith123@gmail.com)"
)

# CPV 33600000 = "Farmasoytiske produkter" (pharmaceutical products).
# Used as a classification-based discovery route that does not depend on the
# molecule being named anywhere in the notice text.
CPV_PHARMACEUTICAL: Final[str] = "33600000"
CPV_MEDICAL_EQUIPMENT: Final[str] = "33000000"


@dataclass(frozen=True)
class Molecule:
    """A target molecule and the identifiers it can be discovered by.

    `atc_code` is the WHO ATC classification. Norwegian notices sometimes cite the
    ATC code instead of naming the substance, so it is a first-class search key,
    not decoration.
    """

    inn_en: str
    atc_code: str
    brand_names: tuple[str, ...] = field(default_factory=tuple)
    # Therapeutic area used by Sykehusinnkjop when the molecule is procured inside a
    # bundled framework agreement rather than a single-molecule tender.
    therapeutic_area: str | None = None


# The five target molecules. Norwegian spellings are NOT listed here on purpose:
# they are derived by rule in naming.py, because the brief asks for the pattern
# rather than five hard-coded strings.
MOLECULES: Final[tuple[Molecule, ...]] = (
    Molecule("Axitinib", "L01EK01", ("Inlyta",), therapeutic_area="Onkologi"),
    Molecule("Everolimus", "L01EG02", ("Afinitor", "Votubia", "Certican")),
    Molecule("Lenalidomide", "L04AX04", ("Revlimid",), therapeutic_area="Onkologi"),
    Molecule("Anagrelide", "L01XX35", ("Xagrid",)),
    Molecule("Paliperidone", "N05AX13", ("Invega", "Trevicta", "Xeplion")),
)

MOLECULES_BY_NAME: Final[dict[str, Molecule]] = {m.inn_en.lower(): m for m in MOLECULES}
