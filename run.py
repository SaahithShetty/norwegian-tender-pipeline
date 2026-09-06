#!/usr/bin/env python3
"""Entry point: run the extraction pipeline and write output/output.csv."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import OUTPUT_DIR
from src.http import HttpClient
from src.pipeline import Pipeline, summarise_run


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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)-22s %(message)s",
    )
    # Third-party request logging is noisy and adds nothing here.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    client = HttpClient(use_cache=not args.no_cache)
    rows, report = Pipeline(client).run(args.output)

    print()
    print(summarise_run(rows, report))
    print()
    print(f"CSV written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
