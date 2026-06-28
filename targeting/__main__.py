"""``python -m targeting ...`` -> the targeting CLI (approve / expand / deep / show)."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
