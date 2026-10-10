import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="vamos", description="Poker assistant for PokerNow")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="register the backend with Chrome (run once)")
    install.add_argument(
        "--record",
        action="store_true",
        help="save raw log pages to data/recordings (test tables only; reinstall to turn off)",
    )
    verify = commands.add_parser(
        "verify", help="compare the hands VAMOS built live with PokerNow's exported log"
    )
    verify.add_argument("game_id", help="the game ID from the table URL")
    verify.add_argument("export", help="the CSV from PokerNow's Download Full Log")
    # Chrome passes the calling extension's origin as an argument; it is not needed.
    host = commands.add_parser("host", help="run as Chrome's native messaging host")
    host.add_argument("chrome_args", nargs="*")
    args = parser.parse_args()

    if args.command == "install":
        from vamos.host.install import install, locations

        launcher_dir, hosts_dirs = locations()
        for hosts_dir in hosts_dirs:
            manifest = install(launcher_dir, hosts_dir, record=args.record)
            print(f"[VAMOS] registered native host: {manifest}")
        print(f"[VAMOS] recording {'ON' if args.record else 'off'}; reload the PokerNow tab")
    elif args.command == "verify":
        from pathlib import Path

        from vamos.config import DATA_DIR
        from vamos.verify import verify

        report = verify(
            DATA_DIR / "recordings" / args.game_id / "log_pages.jsonl", Path(args.export)
        )
        print(report.text())
        raise SystemExit(0 if report.ok else 1)
    elif args.command == "host":
        from vamos.host.server import main as host_main

        host_main()


if __name__ == "__main__":
    main()
