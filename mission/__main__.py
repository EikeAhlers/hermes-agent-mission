"""Run the deck without Hermes:  python -m mission --port 7337"""

from __future__ import annotations

import argparse
import time

from . import server, store


def main() -> int:
    parser = argparse.ArgumentParser(prog="mission", description="Mission Control for Hermes.")
    parser.add_argument("--port", type=int, default=server.DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--snapshot", action="store_true", help="print a text snapshot and exit")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    if args.snapshot:
        from . import tools
        print(tools.snapshot(hours=args.hours)["summary"])
        return 0

    info = server.start(port=args.port, open_browser=not args.no_browser)
    snap = store.snapshot(hours=24)["totals"]
    print(f"{info['url']}  ·  {snap['sessions']} sessions · {snap['tokens']:,} tokens · "
          f"${snap['cost']:.2f} · {snap['running']} running")
    print("ctrl-c to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
