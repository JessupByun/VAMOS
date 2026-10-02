"""
Fetch HoldemResources' heads-up push/fold Nash equilibrium data into tests/corpus/hrc/
(gitignored), the reference the preflop push/fold solver is checked against.

Source: https://www.holdemresources.net/hune (raw data, 1-200bb in 0.05bb steps).
Run: uv run python scripts/fetch_hrc_pushfold.py
"""

import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.holdemresources.net/hrdata/holdemresources_hu_equilibrium.zip"
DEST = Path(__file__).resolve().parents[1] / "tests" / "corpus" / "hrc"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read()
    DEST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(DEST)
        names = archive.namelist()
    print(f"[VAMOS] hrc: {len(data)} bytes, {len(names)} files: {', '.join(names)}")


if __name__ == "__main__":
    main()
