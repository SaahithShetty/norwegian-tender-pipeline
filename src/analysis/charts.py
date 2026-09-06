"""Visualisations for the bid decision.

Each chart answers one question a tender manager would actually ask before deciding
whether to bid, rather than displaying whatever the data happens to contain.

A deliberate constraint runs through all of them: where a value is absent, the chart
says so instead of drawing a zero. Norwegian award values are frequently withheld by
law, and plotting "not disclosed" as 0 would invent a finding.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Final, Sequence

import matplotlib

matplotlib.use("Agg")  # no display in a pipeline run
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

# One restrained palette, reused across charts so the set reads as one report.
_INK: Final[str] = "#1c2733"
_MUTED: Final[str] = "#8b98a6"
_ACCENT: Final[str] = "#2f6f8f"
_WARN: Final[str] = "#b4553f"
_GRID: Final[str] = "#dfe4ea"
_CONFIRMED: Final[str] = "#2f6f8f"
_INFERRED: Final[str] = "#c3ccd6"

MNOK: Final[float] = 1_000_000.0


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _style(ax: plt.Axes, title: str, subtitle: str | None = None) -> None:
    ax.set_title(title, loc="left", fontsize=13, color=_INK, fontweight="bold", pad=18)
    if subtitle:
        ax.text(
            0.0,
            1.02,
            subtitle,
            transform=ax.transAxes,
            fontsize=9,
            color=_MUTED,
            va="bottom",
        )
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.grid(axis="x", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    logger.info("wrote %s", path)
    return path


def _as_float(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


# ------------------------------------------------------------------ charts --


def chart_value_by_molecule(rows: Sequence[dict[str, str]], out: Path) -> Path:
    """Where the money is - and how much of it is directly addressable.

    The distinction that matters commercially: a molecule with its own tender can be
    bid on directly, while one bought inside a therapeutic bundle cannot be won
    without bidding the whole bundle.
    """
    best: dict[str, tuple[float, bool]] = {}
    for row in rows:
        value = _as_float(row["estimatedValue"])
        if value is None:
            continue
        molecule = row["productMolecule"]
        confirmed = row["moleculeDetected"] == "true"
        current = best.get(molecule)
        if current is None or value > current[0]:
            best[molecule] = (value, confirmed)

    if not best:
        raise ValueError("no values available to chart")

    ordered = sorted(best.items(), key=lambda kv: kv[1][0])
    labels = [m for m, _ in ordered]
    values = [v / MNOK for _, (v, _) in ordered]
    colours = [_CONFIRMED if c else _INFERRED for _, (_, c) in ordered]

    fig, ax = plt.subplots(figsize=(9, 4.6))
    bars = ax.barh(labels, values, color=colours, height=0.6)
    for bar, (_, (value, confirmed)) in zip(bars, ordered):
        ax.text(
            bar.get_width() * 1.01,
            bar.get_y() + bar.get_height() / 2,
            f"{value / MNOK:,.0f} MNOK" + ("" if confirmed else "  (bundle)"),
            va="center",
            fontsize=9,
            color=_INK if confirmed else _MUTED,
        )
    ax.set_xscale("log")
    ax.set_xlabel("Estimated contract value, MNOK per year (log scale)", fontsize=9,
                  color=_MUTED)
    _style(
        ax,
        "Where the money is - and whether you can bid on it directly",
        "Solid = molecule has its own tender. Grey = only reachable inside a "
        "bundled framework agreement.",
    )
    return _save(fig, out)


def chart_price_disclosure(rows: Sequence[dict[str, str]], out: Path) -> Path:
    """How much of the market is priced in public.

    Answers a question that decides how much of a bid can be modelled from public
    data at all: award values are frequently withheld, so a bidder cannot see what
    the incumbent charged.
    """
    stages = ["prior-information", "competition", "award", "cancelled"]
    with_value = {stage: 0 for stage in stages}
    without_value = {stage: 0 for stage in stages}

    for row in rows:
        stage = row["status"]
        if stage not in with_value:
            continue
        has_value = bool(row["estimatedValue"] or row["awardedValue"])
        (with_value if has_value else without_value)[stage] += 1

    fig, ax = plt.subplots(figsize=(9, 4.4))
    disclosed = [with_value[s] for s in stages]
    withheld = [without_value[s] for s in stages]
    ax.barh(stages, disclosed, color=_ACCENT, height=0.6, label="value published")
    ax.barh(
        stages,
        withheld,
        left=disclosed,
        color=_INFERRED,
        height=0.6,
        label="no value published",
    )
    for index, stage in enumerate(stages):
        total = with_value[stage] + without_value[stage]
        if total:
            ax.text(total * 1.02, index, f"{with_value[stage]}/{total}",
                    va="center", fontsize=9, color=_MUTED)
    ax.set_xlabel("Notices", fontsize=9, color=_MUTED)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    _style(
        ax,
        "Price disclosure is not uniform across the notice lifecycle",
        "Award notices are where a bidder would look for the incumbent's price - "
        "and where it is least often published.",
    )
    return _save(fig, out)


def chart_detection_method(rows: Sequence[dict[str, str]], out: Path) -> Path:
    """What a name-only pipeline would have missed.

    Directly relevant to method: rows found by ATC code or by therapeutic area would
    not exist if the pipeline searched names alone.
    """
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["detectionMethod"]] += 1

    ordered = sorted(counts.items(), key=lambda kv: kv[1])
    labels = [k.replace("-", " ") for k, _ in ordered]
    values = [v for _, v in ordered]
    colours = [
        _WARN if key.startswith(("atc", "therapeutic")) else _ACCENT
        for key, _ in ordered
    ]

    fig, ax = plt.subplots(figsize=(9, 4.0))
    bars = ax.barh(labels, values, color=colours, height=0.6)
    for bar, value in zip(bars, values):
        ax.text(bar.get_width() + 0.4, bar.get_y() + bar.get_height() / 2,
                str(value), va="center", fontsize=9, color=_INK)
    name_only = sum(v for k, v in counts.items() if k.startswith(("name", "brand")))
    total = sum(counts.values())
    ax.set_xlabel("Rows", fontsize=9, color=_MUTED)
    _style(
        ax,
        "Name matching alone would have found less than a third of the rows",
        f"{name_only} of {total} rows came from name matching; the rest required "
        "classification codes or therapeutic-area reasoning (highlighted).",
    )
    return _save(fig, out)


def chart_lifecycle_timeline(rows: Sequence[dict[str, str]], out: Path) -> Path:
    """When each molecule was last tendered - i.e. when to expect the next round.

    Contract timing is the practical question: a framework signed last year is not
    biddable now, and one approaching renewal is.
    """
    points: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for row in rows:
        raw = row["publicationDate"]
        if not raw:
            continue
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            continue
        points[row["productMolecule"]].append((parsed, row["status"]))

    if not points:
        raise ValueError("no dated rows to chart")

    molecules = sorted(points, key=lambda m: min(d for d, _ in points[m]))
    marker_by_stage = {
        "prior-information": ("o", _MUTED),
        "competition": ("o", _ACCENT),
        "award": ("s", _CONFIRMED),
        "cancelled": ("X", _WARN),
    }

    fig, ax = plt.subplots(figsize=(9.5, 4.4))
    for index, molecule in enumerate(molecules):
        for when, stage in points[molecule]:
            marker, colour = marker_by_stage.get(stage, ("o", _MUTED))
            # Real dates, not strings: passing ISO strings makes matplotlib treat
            # them as unordered categories, which silently scrambles the axis.
            ax.scatter(when, index, marker=marker, color=colour, s=60, zorder=3,
                       alpha=0.85)
    ax.set_yticks(range(len(molecules)), molecules)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    handles = [
        plt.Line2D([], [], marker=m, color=c, linestyle="", label=s.replace("-", " "))
        for s, (m, c) in marker_by_stage.items()
    ]
    ax.legend(handles=handles, frameon=False, fontsize=8, ncol=4, loc="upper left",
              bbox_to_anchor=(0, 1.18))
    _style(
        ax,
        "Tender activity over time, by molecule",
        "A cancelled competition followed by a re-tender is a second chance to bid.",
    )
    return _save(fig, out)


def build_all(csv_path: Path, out_dir: Path) -> list[Path]:
    """Render every chart, skipping any that the data cannot support."""
    rows = load_rows(csv_path)
    builders = (
        ("value-by-molecule.png", chart_value_by_molecule),
        ("price-disclosure.png", chart_price_disclosure),
        ("detection-method.png", chart_detection_method),
        ("tender-timeline.png", chart_lifecycle_timeline),
    )

    written: list[Path] = []
    for filename, builder in builders:
        try:
            written.append(builder(rows, out_dir / filename))
        except Exception as exc:  # noqa: BLE001 - a chart must not break a run
            logger.warning("skipped %s: %s", filename, exc)
    return written
