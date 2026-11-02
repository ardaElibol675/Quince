import os
import sys
from pathlib import Path

import pytest

# Automated tests never need a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(scope="session")
def make_fixtures():
    """The scripts/make_fixtures.py module."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import make_fixtures
    finally:
        sys.path.remove(str(SCRIPTS))
    return make_fixtures
