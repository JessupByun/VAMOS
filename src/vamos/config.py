import os
from importlib.metadata import version
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Overridable so end-to-end runs never touch the real local state.
DATA_DIR = Path(os.environ.get("VAMOS_DATA_DIR", REPO_ROOT / "data"))
PROFILES_DIR = REPO_ROOT / "profiles"

VERSION = version("vamos")
