#!/usr/bin/env python3
"""Entry point: run the extraction pipeline and write output/output.csv."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from typing import Final

from src.analysis.charts import build_all
from src.config import ANNEX_DIR, OUTPUT_DIR
from src.http import HttpClient
from src.parsing.fetch import BrowserUnavailable, fetch_annexes
from src.pipeline import Pipeline, summarise_run

ANNEX_TENDER_URLS: Final[tuple[str, ...]] = (
    # LIS 2207 Onkologi - the bundled oncology framework whose annex carries
    # axitinib's pack-level detail. Others can be added; each is a tender document
    # page whose Prisskjema will be downloaded.
    "https://www.mercell.com/en/tender/176511702/lis-2207-onkologi-tender.aspx",
)


def _fetch_annexes() -> None:
    """Download tender annexes interactively, then carry on regardless of outcome.

    A failed fetch is not fatal: the pipeline simply runs without pack-level detail,
    which is the same path a reviewer without a browser takes.
    """
    for url in ANNEX_TENDER_URLS:
        try:
            fetched = fetch_annexes(url, ANNEX_DIR)
        except BrowserUnavailable as exc:
            print(f"\n  Skipping annex fetch: {exc}\n")
            return
        except Exception as exc:  # noqa: BLE001 - never let a fetch end the run
            logging.getLogger(__name__).warning("annex fetch failed for %s: %s", url, exc)
            continue
        if fetched:
            print(f"  fetched {len(fetched)} annex file(s) into {ANNEX_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "output.csv",
        help="where to write the CSV (default: output/output.csv)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass the local response cache and re-fetch from the sources",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="log every request"
    )
    parser.add_argument(
        "--no-charts", action="store_true", help="skip rendering the visualisations"
    )
    parser.add_argument(
        "--fetch-annexes",
        action="store_true",
        help=(
            "open a browser to download tender annexes before running. Requires "
            "agent-browser and a person to clear the site's verification prompt; "
            "without it the pipeline uses whatever is already in data/manual/."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)-22s %(message)s",
    )
    # Third-party request logging is noisy and adds nothing here.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.fetch_annexes:
        _fetch_annexes()

    client = HttpClient(use_cache=not args.no_cache)
    rows, report = Pipeline(client).run(args.output)

    print()
    print(summarise_run(rows, report))
    print()
    print(f"CSV written to {args.output}")

    if not args.no_charts:
        charts = build_all(args.output, args.output.parent / "charts")
        print(f"charts written to {args.output.parent / 'charts'} ({len(charts)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
