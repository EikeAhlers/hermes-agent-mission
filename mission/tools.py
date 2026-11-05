"""Tool handlers.

Hermes dispatches plugin tools as ``handler(args: dict, **context)`` and requires a
**string** result, so every entry point here takes a dict and returns JSON text.
"""

from __future__ import annotations

import json

from . import server, store


def _fmt(n: float) -> str:
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return str(int(n))


def _open(port: int = server.DEFAULT_PORT, open_browser: bool = True) -> dict:
    try:
        info = server.start(port=port, open_browser=open_browser)
    except OSError:
        # another profile already holds that port — take a free one instead of failing
        info = server.start(port=0, open_browser=open_browser)
    snap = store.snapshot(hours=24)
    t = snap["totals"]
    lines = [
        f"Mission Control: {info['url']}",
        f"last 24h · {t['sessions']} sessions · {_fmt(t['tokens'])} tokens · ${t['cost']:.2f} · "
        f"{t['tools']} tool messages · {t['running']} running",
    ]
    live = [s for s in snap["sessions"] if s["live"]]
    for s in live[:6]:
        lines.append(f"  running · {s['cluster']} · {s['title'][:60]} · {_fmt(s['tokens'])} tok")
    if not live:
        lines.append("  nothing running right now")
    return {
        "ok": True,
        "url": info["url"],
        "already_running": info["already_running"],
        "totals": t,
        "running": [{"id": s["id"], "title": s["title"], "cluster": s["cluster"]} for s in live],
        "summary": "\n".join(lines),
    }


def _snapshot(hours: int = 24, top: int = 5) -> dict:
    snap = store.snapshot(hours=hours)
    t = snap["totals"]
    burners = sorted(snap["sessions"], key=lambda s: -s["tokens"])[:top]
    lines = [
        f"{snap['profile']} · last {hours}h",
        f"{t['sessions']} sessions · {_fmt(t['tokens'])} tokens · ${t['cost']:.2f} · {t['tools']} tool messages · {t['running']} running",
        "clusters: " + ", ".join(f"{c['label']} ({c['sessions']})" for c in snap["clusters"][:8]),
        "top token users:",
    ]
    for s in burners:
        state = "running" if s["live"] else "done"
        lines.append(f"  {_fmt(s['tokens']):>7} tok · {s['tools']:>3} tools · {state} · {s['title'][:58]}")
    if snap["cron"]:
        lines.append("cron: " + ", ".join(f"{c['name']}{'' if c['enabled'] else ' (off)'}" for c in snap["cron"][:8]))
    return {"ok": True, "summary": "\n".join(lines), "totals": t, "clusters": snap["clusters"][:8]}


def _search(query: str, limit: int = 20) -> dict:
    hits = store.search(query, limit=limit)
    if not hits:
        return {"ok": True, "hits": [], "summary": f'no message contains "{query}"'}
    lines = [f'{len(hits)} hits for "{query}"']
    for h in hits[:10]:
        snippet = " ".join((h.get("snippet") or "").split())[:110]
        lines.append(f"  {(h.get('title') or '')[:40]:<40} | {h['id']} | {snippet}")
    return {"ok": True, "hits": hits, "summary": "\n".join(lines)}


def _waste() -> dict:
    rows = store.waste()
    if not rows:
        return {"ok": True, "sessions": [], "summary": "no expensive low-action sessions in the last 7 days"}
    lines = ["most tokens per tool call (last 7d):"]
    for r in rows:
        lines.append(f"  {_fmt(r['tokens']):>7} tok / {r['tools']:>2} tools · {(r.get('title') or '')[:56]}")
    return {"ok": True, "sessions": rows, "summary": "\n".join(lines)}


def _as_text(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


# --- dispatched entry points (dict in, string out) ---------------------------

def open_deck(args: dict | None = None, **_context) -> str:
    args = args or {}
    return _as_text(_open(int(args.get("port", server.DEFAULT_PORT)),
                          bool(args.get("open_browser", True))))


def snapshot(args: dict | None = None, **_context) -> str:
    args = args or {}
    return _as_text(_snapshot(int(args.get("hours", 24)), int(args.get("top", 5))))


def search(args: dict | None = None, **_context) -> str:
    args = args or {}
    return _as_text(_search(str(args.get("query", "")), int(args.get("limit", 20))))


def waste(args: dict | None = None, **_context) -> str:
    return _as_text(_waste())
