"""Read-only view of the profile: what Hermes is doing, right now and over time.

Nothing here writes to the profile. Every number the UI shows comes from
``state.db`` (sessions, messages) plus the cron job list.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

SESSION_COLS = (
    "id, title, source, model, input_tokens, output_tokens, cache_read_tokens, "
    "tool_call_count, message_count, estimated_cost_usd, started_at, ended_at, "
    "last_activity_at, last_activity_description, cwd, parent_session_id, archived, profile_name"
)

LIVE_WINDOW_S = 90  # a session without ended_at that touched the db recently is "running"


def home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "hermes"
    return Path.home() / ".hermes"


def db_path() -> Path:
    return home() / "state.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path().as_posix()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


STOPWORDS = {
    "the", "a", "an", "der", "die", "das", "ein", "eine", "und", "and", "or", "oder",
    "zu", "zum", "zur", "mit", "for", "von", "im", "in", "on", "of", "bitte", "mal",
    "was", "wie", "warum", "kannst", "kann", "du", "ich", "wir", "mal",
    "act", "richte", "mach", "bau", "hilf", "help", "please", "bitte", "noch", "schon",
}


def cluster_key(title: str | None, source: str | None) -> str:
    """Group sessions by topic, not by directory — the cwd is almost always the home dir.

    Rule: take the phrase before a separator the agent itself puts in titles
    ("finance · Stand ..."), else the first meaningful word, else the source.
    Generic on purpose: no project names are hardcoded.
    """
    if source == "subagent":
        return "subagents"
    raw = (title or "").strip()
    if not raw:
        return source or "chats"
    for sep in (" · ", " — ", " - ", ": ", " – "):
        if sep in raw:
            head = raw.split(sep, 1)[0].strip()
            if 1 <= len(head.split()) <= 4 and head:
                return head[:28].lower()
    words = [w.strip(".,!?()[]\"'`").lower() for w in raw.split()]
    for word in words:
        if len(word) >= 3 and word not in STOPWORDS and not word.isdigit():
            return word[:28]
    return source or "chats"


def _cluster_colour(label: str) -> str:
    palette = [
        "#7dd3fc", "#a78bfa", "#f0abfc", "#5eead4", "#fcd34d",
        "#fda4af", "#86efac", "#93c5fd", "#f9a8d4", "#c4b5fd",
    ]
    h = int(hashlib.sha256(label.encode()).hexdigest()[:6], 16)
    return palette[h % len(palette)]


def snapshot(hours: int = 24, limit: int = 260) -> dict:
    now = time.time()
    since = now - hours * 3600
    with _conn() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"select {SESSION_COLS} from sessions where archived = 0 "
                "and coalesce(last_activity_at, started_at) >= ? "
                "order by coalesce(last_activity_at, started_at) desc limit ?",
                (since, limit),
            )
        ]
        running = [
            dict(r)
            for r in conn.execute(
                f"select {SESSION_COLS} from sessions where archived = 0 and ended_at is null "
                "and coalesce(last_activity_at, started_at) >= ? order by last_activity_at desc",
                (now - 6 * 3600,),
            )
        ]
        tool_rows = conn.execute(
            "select s.id as sid, m.tool_name as name, count(*) as n from messages m "
            "join sessions s on s.id = m.session_id "
            "where m.tool_name is not null and coalesce(s.last_activity_at, s.started_at) >= ? "
            "group by s.id, m.tool_name",
            (since,),
        ).fetchall()
        hour_rows = conn.execute(
            "select cast((started_at - ?) / 3600 as integer) as bucket, "
            "sum(input_tokens + output_tokens) as tokens, count(*) as n "
            "from sessions where started_at >= ? group by bucket order by bucket",
            (since, since),
        ).fetchall()
        totals = conn.execute(
            "select count(*) as sessions, sum(input_tokens + output_tokens) as tokens, "
            "sum(estimated_cost_usd) as cost, sum(tool_call_count) as tools "
            "from sessions where coalesce(last_activity_at, started_at) >= ?",
            (since,),
        ).fetchone()

    parent_ids = sorted({r["parent_session_id"] for r in rows if r.get("parent_session_id")})
    parent_titles: dict[str, str] = {}
    if parent_ids:
        with _conn() as conn:
            marks = ",".join("?" * len(parent_ids))
            for pr in conn.execute(f"select id, title, source from sessions where id in ({marks})", parent_ids):
                parent_titles[pr["id"]] = cluster_key(pr["title"], pr["source"])

    tools_by_session: dict[str, dict[str, int]] = {}
    for row in tool_rows:
        tools_by_session.setdefault(row["sid"], {})[row["name"]] = row["n"]

    clusters: dict[str, dict] = {}
    sessions = []
    for row in rows:
        parent = row.get("parent_session_id") or ""
        label = parent_titles.get(parent) or cluster_key(row.get("title"), row.get("source"))
        cluster = clusters.setdefault(
            label, {"label": label, "colour": _cluster_colour(label), "sessions": 0, "tokens": 0}
        )
        tokens = int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
        last = row.get("last_activity_at") or row.get("started_at") or now
        is_live = row.get("ended_at") is None and (now - last) < LIVE_WINDOW_S
        cluster["sessions"] += 1
        cluster["tokens"] += tokens
        sessions.append(
            {
                "id": row["id"],
                "title": (row.get("title") or "untitled")[:120],
                "source": row.get("source") or "",
                "model": row.get("model") or "",
                "tokens": tokens,
                "tokens_in": int(row.get("input_tokens") or 0),
                "tokens_out": int(row.get("output_tokens") or 0),
                "cost": float(row.get("estimated_cost_usd") or 0.0),
                "tools": int(row.get("tool_call_count") or 0),
                "messages": int(row.get("message_count") or 0),
                "started_at": float(row.get("started_at") or 0),
                "last": float(last),
                "ended": row.get("ended_at") is not None,
                "live": is_live,
                "parent": row.get("parent_session_id") or "",
                "cluster": label,
                "tool_mix": tools_by_session.get(row["id"], {}),
            }
        )

    buckets = {int(r["bucket"]) if r["bucket"] is not None else 0: r for r in hour_rows}
    timeline = [
        {
            "t": int(since + (i + 0.5) * 3600),
            "tokens": int(buckets[i]["tokens"] or 0) if i in buckets else 0,
            "sessions": int(buckets[i]["n"] or 0) if i in buckets else 0,
        }
        for i in range(int(hours))
    ]

    live_ids = {s["id"] for s in sessions if s["live"]}
    tool_messages = sum(sum(mix.values()) for mix in tools_by_session.values())
    return {
        "now": now,
        "hours": hours,
        "totals": {
            "sessions": int(totals["sessions"] or 0) if totals else 0,
            "tokens": int(totals["tokens"] or 0) if totals else 0,
            "cost": float(totals["cost"] or 0.0) if totals else 0.0,
            "tools": tool_messages,
            "running": len(live_ids),
        },
        "clusters": sorted(clusters.values(), key=lambda c: -c["tokens"]),
        "sessions": sessions,
        "timeline": timeline,
        "cron": cron_jobs(),
        "profile": home().name,
    }


def cron_jobs() -> list[dict]:
    path = home() / "cron" / "jobs.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for job in data.get("jobs", []):
        out.append(
            {
                "name": job.get("name") or job.get("id", ""),
                "schedule": job.get("schedule") or job.get("cron") or "",
                "enabled": not job.get("paused", False),
                "next_run": job.get("next_run_at") or job.get("next_run") or "",
            }
        )
    return out[:24]


def session_detail(session_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(f"select {SESSION_COLS} from sessions where id = ?", (session_id,)).fetchone()
        if row is None:
            return {"error": "not found"}
        messages = conn.execute(
            "select role, tool_name, substr(coalesce(content, ''), 1, 1200) as content, timestamp "
            "from messages where session_id = ? order by id desc limit 30",
            (session_id,),
        ).fetchall()
        first = conn.execute(
            "select content from messages where session_id = ? and role = 'user' "
            "and content is not null and length(trim(content)) > 12 "
            "order by timestamp asc limit 1",
            (session_id,),
        ).fetchone()
        tools = conn.execute(
            "select tool_name as name, count(*) as n from messages "
            "where session_id = ? and tool_name is not null group by tool_name order by n desc limit 12",
            (session_id,),
        ).fetchall()
    data = dict(row)
    data["task"] = (first["content"][:800] if first else "")
    data["tools"] = [dict(t) for t in tools]
    data["messages"] = [dict(m) for m in reversed(messages)]
    return data


def search(query: str, limit: int = 40) -> list[dict]:
    needle = f"%{query.strip()}%"
    if len(query.strip()) < 2:
        return []
    with _conn() as conn:
        rows = conn.execute(
            "select m.session_id as id, m.role, m.tool_name, substr(m.content, 1, 260) as snippet, "
            "s.title, coalesce(s.last_activity_at, s.started_at) as last "
            "from messages m join sessions s on s.id = m.session_id "
            "where m.content like ? order by coalesce(s.last_activity_at, s.started_at) desc limit ?",
            (needle, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def waste() -> list[dict]:
    """Sessions that spent a lot and produced little: high tokens per tool call."""
    since = time.time() - 7 * 24 * 3600
    with _conn() as conn:
        rows = conn.execute(
            "select id, title, input_tokens + output_tokens as tokens, tool_call_count as tools, "
            "estimated_cost_usd as cost, coalesce(last_activity_at, started_at) as last "
            "from sessions where started_at >= ? and (input_tokens + output_tokens) > 20000 "
            "order by (input_tokens + output_tokens) / max(tool_call_count, 1) desc limit 10",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def events(since: int = 0, limit: int = 40) -> list[dict]:
    """Newest messages across every session — the live feed of what the agent is doing."""
    with _conn() as conn:
        rows = conn.execute(
            "select m.id, m.session_id, s.title as title, m.role, m.tool_name, "
            "substr(coalesce(m.content, ''), 1, 240) as snippet, m.timestamp "
            "from messages m left join sessions s on s.id = m.session_id "
            "where m.id > ? order by m.id desc limit ?",
            (int(since), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def newest_since(started_at: float, window: float = 1800.0) -> str:
    """The session a freshly spawned run created (earliest start inside the window)."""
    with _conn() as conn:
        row = conn.execute(
            "select id from sessions where started_at >= ? and started_at <= ? "
            "order by started_at asc limit 1",
            (float(started_at) - 2, float(started_at) + window),
        ).fetchone()
    return row["id"] if row else ""


def transcript(session_id: str, after_id: int = 0, limit: int = 300) -> dict:
    """The real conversation of one session — what the chat panel renders."""
    with _conn() as conn:
        rows = conn.execute(
            "select id, role, tool_name, content, timestamp, tool_calls "
            "from messages where session_id = ? and id > ? order by id asc limit ?",
            (session_id, int(after_id), int(limit)),
        ).fetchall()
        head = conn.execute("select title, started_at from sessions where id = ?", (session_id,)).fetchone()
    out = []
    for row in rows:
        item = {
            "id": row["id"],
            "role": row["role"] or "",
            "tool": row["tool_name"] or "",
            "text": (row["content"] or "")[:12000],
            "ts": row["timestamp"],
        }
        if row["tool_calls"]:
            try:
                calls = json.loads(row["tool_calls"])
                item["calls"] = [c.get("function", {}).get("name", "") for c in calls if isinstance(c, dict)]
            except (ValueError, TypeError):
                item["calls"] = []
        out.append(item)
    return {
        "session": session_id,
        "title": (head["title"] if head else "") or "",
        "started_at": (head["started_at"] if head else 0),
        "messages": out,
    }


def project(label: str, hours: float = 24.0) -> dict:
    """Everything that happened inside one project (cluster) — for the project view."""
    import time as _time

    since = _time.time() - float(hours) * 3600
    with _conn() as conn:
        rows = conn.execute(
            "select id, title, source, model, "
            "(coalesce(input_tokens,0) + coalesce(output_tokens,0) + coalesce(cache_read_tokens,0) "
            " + coalesce(cache_write_tokens,0)) as token_count, "
            "estimated_cost_usd as cost, "
            "tool_call_count as tools, message_count as msgs, started_at, "
            "coalesce(last_activity_at, started_at) as last, ended_at "
            "from sessions where coalesce(last_activity_at, started_at) >= ? "
            "order by last desc",
            (since,),
        ).fetchall()
        keep = [r for r in rows if cluster_key(r["title"], r["source"]) == label]
        ids = [r["id"] for r in keep]
        tools: list[dict] = []
        events: list[dict] = []
        if ids:
            marks = ",".join("?" * len(ids))
            tools = [
                dict(r)
                for r in conn.execute(
                    "select coalesce(tool_name,'?') as name, count(*) as calls, "
                    "coalesce(sum(token_count),0) as tokens from messages "
                    f"where session_id in ({marks}) and tool_name is not null "
                    "group by tool_name order by tokens desc",
                    ids,
                ).fetchall()
            ]
            events = [
                dict(r)
                for r in conn.execute(
                    "select m.id, m.session_id, m.role, m.tool_name, "
                    "substr(coalesce(m.content,''),1,240) as snippet, m.timestamp "
                    f"from messages m where m.session_id in ({marks}) "
                    "order by m.id desc limit 40",
                    ids,
                ).fetchall()
            ]
    buckets: dict[str, int] = {}
    for r in keep:
        key = _time.strftime("%H", _time.localtime(r["started_at"] or 0))
        buckets[key] = buckets.get(key, 0) + (r["token_count"] or 0)
    now = _time.localtime()
    this_hour = now.tm_hour
    series = []
    for off in range(int(hours) - 1, -1, -1):
        h = (this_hour - off) % 24
        series.append({"hour": f"{h:02d}", "tokens": buckets.get(f"{h:02d}", 0)})
    return {
        "label": label,
        "hours": hours,
        "tokens": sum(r["token_count"] or 0 for r in keep),
        "cost": sum(float(r["cost"] or 0) for r in keep),
        "tools_total": sum(r["tools"] or 0 for r in keep),
        "sessions": [
            {
                "id": r["id"],
                "title": r["title"] or "(ohne Titel)",
                "tokens": r["token_count"] or 0,
                "cost": float(r["cost"] or 0),
                "tools": r["tools"] or 0,
                "last": r["last"],
                "model": r["model"] or "",
                "live": bool(r["last"] and not r["ended_at"] and (_time.time() - r["last"]) < 180),
            }
            for r in keep
        ],
        "tools": tools,
        "events": events,
        "series": series,
    }
