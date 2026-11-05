"""Audit the conversion routes against what the backends can do.

Usage: python scripts/audit_routes.py

Prints every conversion the backends could do (in one step or chained) that is neither a
route nor left out on purpose (``quince.core.coverage.EXCLUSIONS``). The exit code is 1 when
there are gaps, when a route is also excluded, or when an exclusion is no longer needed.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from quince.core import coverage, formats
from quince.core.registry import discover


def _label(format_id: str) -> str:
    return formats.get(format_id).label


def main() -> int:
    table = discover().routes
    candidates = coverage.candidates(table)
    routes = {(r.source, r.target) for r in table}
    excluded = [p for p in candidates - routes if coverage.excluded_by(p)]
    gaps = coverage.gaps(table)
    print(f"{len(coverage.one_step())} one-step backend conversions, {len(candidates)} reachable")
    print(f"{len(routes)} routes, {len(excluded)} left out on purpose, {len(gaps)} gaps")

    by_source: dict[str, list[str]] = defaultdict(list)
    for source, target in gaps:
        by_source[source].append(target)
    if gaps:
        print("\nGaps (add a route, or an exclusion with its reason):")
        for source, targets in by_source.items():
            print(f"  {_label(source):>12} → {', '.join(_label(t) for t in targets)}")
    if contradictions := coverage.excluded_routes(table):
        print("\nRoutes that an exclusion leaves out (remove one of them):")
        for source, target in contradictions:
            print(f"  {_label(source)} → {_label(target)}")
    if stale := coverage.unused_exclusions(table):
        print("\nExclusions that cover nothing any more:")
        for exclusion in stale:
            print(f"  {exclusion.reason}")
    ok = not (gaps or contradictions or stale)
    print("\nOK: no unexplained gaps." if ok else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
