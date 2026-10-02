"""
`vamos install`: register the native messaging host with Chrome so that opening a PokerNow
table launches the backend. Re-run it if the repository folder moves.
"""

import json
import sys
from pathlib import Path

HOST_NAME = "com.vamos.host"
# Pinned by the public key in extension/manifest.config.ts.
EXTENSION_ID = "hflgneiimkcoebdkfiblggbfklelebdn"

CHROME_HOSTS_DIR = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
# Real Chrome on macOS will not start a host whose executable sits in a privacy-protected
# folder such as ~/Desktop, so the launcher lives outside the repository.
LAUNCHER_DIR = Path.home() / "Library/Application Support/VAMOS"


def install(
    launcher_dir: Path = LAUNCHER_DIR,
    hosts_dir: Path = CHROME_HOSTS_DIR,
    python: str = sys.executable,
    record: bool = False,
) -> Path:
    # Chrome launches `path` directly with no arguments of ours, so a launcher script pins
    # the project's interpreter (and, for test-table sessions, turns recording on).
    launcher = launcher_dir / "vamos-host"
    launcher_dir.mkdir(parents=True, exist_ok=True)
    env = "VAMOS_RECORD=1 " if record else ""
    launcher.write_text(f'#!/bin/sh\n{env}exec "{python}" -m vamos host "$@"\n')
    launcher.chmod(0o755)

    manifest = {
        "name": HOST_NAME,
        "description": "VAMOS backend",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }
    hosts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = hosts_dir / f"{HOST_NAME}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path
