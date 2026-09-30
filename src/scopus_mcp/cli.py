"""Command-line entry point: start the server, or explain what is missing."""
import sys


def main() -> None:
    try:
        # Importing the server builds the Scopus client, which needs the key.
        from .server import start
    except ValueError as exc:
        print(f"citation-network-mcp: {exc}", file=sys.stderr)
        print("See https://github.com/michalhron/citation-network-mcp#install",
              file=sys.stderr)
        raise SystemExit(1)
    start()
