import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="vamos", description="Poker assistant for PokerNow")
    parser.add_subparsers(dest="command", required=True)
    parser.parse_args()


if __name__ == "__main__":
    main()
