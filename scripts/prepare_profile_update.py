"""
Prepare an update of the opponent profiles for an agent session to write, and install its
result: the saved games no profile has absorbed yet, and each player's exact counts and hand
records from them, in a work folder (data/profile-work/). The side panel's Update profiles
button does the same with the AI model writing (src/vamos/profile_update.py); here an agent
session on a subscription plan does the writing, for example the update-profiles skill in
.claude/. Nothing here calls an AI model.

Run: uv run python scripts/prepare_profile_update.py
       [--match ACCOUNT=PROFILE ...] [--exclude GAME REASON]
     uv run python scripts/prepare_profile_update.py --create NAME --accounts ID[,ID]
     uv run python scripts/prepare_profile_update.py --install WORK_FOLDER
"""

import argparse
import sys
from pathlib import Path

from vamos.config import DATA_DIR, PROFILES_DIR
from vamos.identity import IdentityMap
from vamos.profile_update import (
    InstallError,
    Paths,
    hands_by_account,
    install,
    prepare,
    read_registry,
    register_new_games,
    write_json,
)

PATHS = Paths(DATA_DIR, PROFILES_DIR)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", action="append", default=[], metavar="ACCOUNT=PROFILE")
    parser.add_argument(
        "--exclude", nargs=2, action="append", default=[], metavar=("GAME", "REASON")
    )
    parser.add_argument("--create", metavar="NAME")
    parser.add_argument("--accounts", default="")
    parser.add_argument("--install", type=Path, metavar="WORK_FOLDER")
    args = parser.parse_args()
    if args.install:
        try:
            installed = install(PATHS, args.install)
        except InstallError as error:
            sys.exit(f"{error}; nothing installed")
        print(f"Installed: {', '.join(installed)}")
        return
    registry = read_registry(PATHS)
    identity = IdentityMap(DATA_DIR / "identity.json")
    for pair in args.match:
        account, _, profile = pair.partition("=")
        identity.assign(account.strip(), profile.strip() or None)
    for game, reason in args.exclude:
        registry["excluded"][game] = reason
    added = register_new_games(PATHS, registry)
    write_json(PATHS.registry, registry)
    if added:
        print(f"New sessions: {', '.join(added)}")
    create = None
    if args.create:
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
        if not accounts:
            sys.exit("--create needs --accounts")
        # The account with the most hands is the profile's player_id; the others are matched.
        counts = hands_by_account(PATHS, accounts)
        main_account = max(accounts, key=lambda a: counts[a])
        name = args.create.strip().lower()
        for account in accounts:
            if account != main_account:
                identity.assign(account, name)
        print(f"Creating {name}: player_id {main_account}; hands {counts}")
        create = (name, accounts, main_account)
    prepared = prepare(PATHS, registry, create)
    if prepared is None:
        print("No new hands for any profile: the profiles are up to date.")
        return
    print(f"Work folder: {prepared.work}")
    for entry in prepared.players:
        print(f"- {entry['key']}: {entry['sessions']}")
    if prepared.unknown:
        print("Accounts no profile claims (ask who they were):")
        for pid, seen in prepared.unknown.items():
            print(f"- {pid}: {', '.join(seen['names'])}; hands {seen['hands']}")


if __name__ == "__main__":
    main()
