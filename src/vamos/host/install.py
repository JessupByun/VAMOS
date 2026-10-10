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


def locations(platform: str = sys.platform) -> tuple[Path, list[Path]]:
    """
    Where the launcher goes, and the folders Chrome (and Chromium) read native messaging
    hosts from. Windows registers hosts in the registry instead, which this does not do yet.
    """
    home = Path.home()
    if platform == "darwin":
        # Real Chrome on macOS will not start a host whose executable sits in a
        # privacy-protected folder such as ~/Desktop, so the launcher lives outside the repo.
        support = home / "Library/Application Support"
        return support / "VAMOS", [support / "Google/Chrome/NativeMessagingHosts"]
    if platform.startswith("linux"):
        config = home / ".config"
        return home / ".local/share/vamos", [
            config / "google-chrome/NativeMessagingHosts",
            config / "chromium/NativeMessagingHosts",
        ]
    raise SystemExit(
        "[VAMOS] vamos install supports macOS and Linux. On Windows, Chrome finds native hosts "
        "through the registry, which this installer does not write yet."
    )


def install(
    launcher_dir: Path,
    hosts_dir: Path,
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
