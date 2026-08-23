#!/usr/bin/env python
"""Build the dashboard, serve it locally and open it in a browser.

    ctc dashboard                 # build, serve and open
    ctc dashboard -- --build      # build the HTML only, no server
    ctc dashboard -- --port 9000  # use a specific port
    ctc dashboard -- --no-open    # serve without opening a browser

The server exists only so the claim form can write back to the identity file;
a page opened straight from disk shows everything but cannot record a claim.
It binds to localhost and is not exposed to the network.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ctc_bot  # noqa: E402,F401
from ctc_bot import dashboard, server, store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--build", action="store_true", help="build the HTML and exit")
    parser.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--schedule", action="store_true",
        help="also refresh from the admin console on Wed/Fri mornings",
    )
    args = parser.parse_args()

    if not store.load_all():
        print("No events stored yet. Run `ctc backfill` first.")
        return 1

    if args.build:
        path = dashboard.build()
        print(f"Built {path}  ({path.stat().st_size / 1024:.0f} KB)")
        print("Open it directly, or run `ctc dashboard` to enable the claim form.")
        return 0

    try:
        port = server.find_free_port(args.port)
    except OSError as exc:
        print(exc)
        return 1

    if port != args.port:
        print(f"Port {args.port} is busy; using {port}.")

    server.serve(port, open_browser=not args.no_open, schedule=args.schedule)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
