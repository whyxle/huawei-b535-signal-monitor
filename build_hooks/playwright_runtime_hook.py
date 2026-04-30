import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    bundled_browsers = Path(sys._MEIPASS) / "ms-playwright"
    if bundled_browsers.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))
