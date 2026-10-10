"""
Fetch the parser regression corpus into tests/corpus/ (gitignored).

Source: https://github.com/Jaaaayden/pokernow-tracker, MIT License. Its `pnt/logs` exports
(hero hands redacted except at showdown) go to `jaaaayden/logs/` and its unredacted
`tests/fixtures` copies to `jaaaayden/unredacted/`, with the license alongside.
Run: uv run python scripts/fetch_corpus.py
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Jaaaayden/pokernow-tracker"
# Pinned, so the corpus checks' exact counts never move with that repository (its main as
# of 2026-10-02).
REF = "5377d872a751188a0a9149d92a76edc9917c31a5"
FOLDERS = {"pnt/logs": "logs", "tests/fixtures": "unredacted"}
DEST = Path(__file__).resolve().parents[1] / "tests" / "corpus" / "jaaaayden"


def get(url: str) -> bytes:
    """
    The file at `url`, tried up to three times. GitHub's API allows 60 requests an hour per
    IP without a token, and CI runners share IPs; CI passes its own GITHUB_TOKEN.
    """
    headers = {}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise
            print(f"[VAMOS] {url}: {error}; trying again")
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "LICENSE").write_bytes(get(f"https://raw.githubusercontent.com/{REPO}/{REF}/LICENSE"))
    for folder, target in FOLDERS.items():
        listing = json.loads(
            get(f"https://api.github.com/repos/{REPO}/contents/{folder}?ref={REF}")
        )
        out = DEST / target
        out.mkdir(exist_ok=True)
        for entry in listing:
            if not entry["name"].endswith(".csv"):
                continue
            data = get(f"https://raw.githubusercontent.com/{REPO}/{REF}/{folder}/{entry['name']}")
            # The folder also holds an ID-to-alias table; keep only PokerNow exports.
            if data.startswith(b"entry,at,order"):
                (out / entry["name"]).write_bytes(data)
        print(f"[VAMOS] {target}: {len(list(out.glob('*.csv')))} logs")


if __name__ == "__main__":
    main()
