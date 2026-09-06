"""Tests for procurement grouping across portals and lifecycle stages."""

from __future__ import annotations

from src.dedup import (
    cross_source_pairs,
    deduplicate_notices,
    group_procurements,
    procurement_key,
)
from src.models import NoticeLifecycle, SourceNotice


def notice(
    notice_id: str,
    title: str,
    source: str = "doffin",
    lifecycle: NoticeLifecycle = NoticeLifecycle.COMPETITION,
    **kwargs: object,
) -> SourceNotice:
    return SourceNotice(
        notice_id=notice_id,
        title=title,
        source_url=f"https://example.test/{notice_id}",
        source_name=source,
        lifecycle=lifecycle,
        **kwargs,  # type: ignore[arg-type]
    )


def test_recognises_both_tender_numbering_styles() -> None:
    """Sykehusinnkjop uses 'LIS 2234' on older notices and '2632a' on newer ones."""
    assert procurement_key(notice("1", "LIS 2234 Lenalidomid")) == "lis:2234"
    assert procurement_key(notice("2", "2632a Everolimus")) == "lis:2632a"
    assert procurement_key(notice("3", "2507gj-1 anagrelid")) == "lis:2507gj-1"


def test_retender_is_a_separate_procurement() -> None:
    """LIS 2234 and LIS 2234b are a tender and its re-run, not the same purchase."""
    assert procurement_key(notice("1", "LIS 2234 Lenalidomid")) != procurement_key(
        notice("2", "LIS 2234b Lenalidomid")
    )


def test_groups_the_same_tender_across_portals() -> None:
    """The core cross-language duplicate: Doffin in Norwegian, TED in English."""
    notices = [
        notice("2025-109779", "2632a Everolimus og Mykofenolsyre", source="doffin"),
        notice("404973-2025", "2632a Everolimus and Mykofenolic Acid", source="ted"),
    ]
    groups = group_procurements(notices)
    assert len(groups) == 1
    assert groups[0].sources == {"doffin", "ted"}
    assert cross_source_pairs(groups) != []


def test_lifecycle_resolves_to_the_furthest_stage() -> None:
    notices = [
        notice("a", "LIS 2234 Lenalidomid", lifecycle=NoticeLifecycle.PRIOR_INFORMATION),
        notice("b", "LIS 2234 Lenalidomid", lifecycle=NoticeLifecycle.COMPETITION),
        notice("c", "LIS 2234 Lenalidomid", lifecycle=NoticeLifecycle.CANCELLED),
    ]
    (group,) = group_procurements(notices)
    assert group.was_cancelled is True
    assert group.stage is NoticeLifecycle.CANCELLED


def test_value_is_taken_not_summed_across_notices() -> None:
    """Notices about one purchase restate the same figure; summing would inflate it."""
    notices = [
        notice("a", "LIS 2234 Lenalidomid", estimated_value=320_000_000.0),
        notice("b", "LIS 2234 Lenalidomid", estimated_value=320_000_000.0),
    ]
    (group,) = group_procurements(notices)
    assert group.estimated_value == 320_000_000.0


def test_identical_fetches_are_dropped_but_cross_portal_twins_are_kept() -> None:
    notices = [
        notice("2021-376780", "LIS 2234 Lenalidomid", source="doffin"),
        notice("2021-376780", "LIS 2234 Lenalidomid", source="doffin"),
        notice("300984-2021", "LIS 2234 Lenalidomide", source="ted"),
    ]
    unique = deduplicate_notices(notices)
    assert len(unique) == 2
