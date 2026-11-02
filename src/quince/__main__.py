"""Allow running Quince with ``python -m quince``."""

import sys

from quince.cli import main

sys.exit(main())
