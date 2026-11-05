"""mission plugin entry point — four tools, one loopback view, no core imports."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schemas  # noqa: E402
from mission import server, tools  # noqa: E402

TOOLSET = "mission"
_started = {"done": False}


def _autostart(**_kwargs):
    """Bring the deck up on the first session so the URL is simply always live."""
    if _started["done"]:
        return
    _started["done"] = True
    try:
        server.start(port=server.DEFAULT_PORT, open_browser=False)
    except OSError:
        pass  # port taken (another profile) — mission_open retries on demand


def register(ctx):
    for name, schema, handler in (
        ("mission_open", schemas.OPEN, tools.open_deck),
        ("mission_snapshot", schemas.SNAPSHOT, tools.snapshot),
        ("mission_search", schemas.SEARCH, tools.search),
        ("mission_waste", schemas.WASTE, tools.waste),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler)

    _autostart()  # plugins load on every entry point, so the deck is simply always up
