from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from cua_sys.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
