"""Run the submitted test suite against the self-contained source directory."""

import os
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))
os.chdir(SOURCE_ROOT)
