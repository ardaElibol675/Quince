"""The route coverage audit: no conversion a backend can do is silently missing."""

from quince.core import coverage, formats
from quince.core.registry import discover
from quince.core.routes import RouteTable

ROUTES = discover().routes


def _describe(pairs) -> str:
    return ", ".join(f"{formats.get(s).label} → {formats.get(t).label}" for s, t in pairs)


def test_no_unexplained_gaps():
    gaps = coverage.gaps(ROUTES)
    assert not gaps, (
        f"Conversions a backend can do but the wheel does not offer: {_describe(gaps)}. "
        "Add a route, or an entry with its reason to quince.core.coverage.EXCLUSIONS "
        "(see scripts/audit_routes.py)."
    )


def test_no_route_is_excluded():
    contradictions = coverage.excluded_routes(ROUTES)
    assert not contradictions, f"Routes that EXCLUSIONS leave out: {_describe(contradictions)}"


def test_every_exclusion_is_still_needed():
    stale = coverage.unused_exclusions(ROUTES)
    assert not stale, [e.reason for e in stale]


def test_every_exclusion_has_a_reason():
    for exclusion in coverage.EXCLUSIONS:
        assert exclusion.sources and exclusion.targets
        assert len(exclusion.reason) > 20 and exclusion.reason.endswith(".")


def test_a_missing_route_is_found():
    """Chained conversions count: TXT → DOCX (LibreOffice) → MD (pandoc)."""
    without = RouteTable(r for r in ROUTES if (r.source, r.target) != ("txt", "md"))
    assert ("txt", "md") in coverage.candidates(without)
    assert coverage.gaps(without) == [("txt", "md")]


def test_one_step_capabilities_use_known_formats():
    for capability in coverage.CAPABILITIES:
        for fmt in (*capability.reads, *capability.writes):
            formats.get(fmt)
    assert ("csv", "md") in coverage.one_step()  # pandoc's CSV reader
    assert ("pdf", "md") in coverage.one_step()


def test_ids_expand_categories():
    assert coverage.ids(formats.Category.SUBTITLE, "txt") == ("srt", "vtt", "txt")
    assert coverage.but(("a", "b", "c"), "b") == ("a", "c")
