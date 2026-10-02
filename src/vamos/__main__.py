import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="vamos", description="Poker assistant for PokerNow")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="register the backend with Chrome (run once)")
    # Chrome passes the calling extension's origin as an argument; it is not needed.
    host = commands.add_parser("host", help="run as Chrome's native messaging host")
    host.add_argument("chrome_args", nargs="*")
    args = parser.parse_args()

    if args.command == "install":
        from vamos.host.install import install

        print(f"[VAMOS] registered native host: {install()}")
    elif args.command == "host":
        from vamos.host.server import main as host_main

        host_main()


if __name__ == "__main__":
    main()
