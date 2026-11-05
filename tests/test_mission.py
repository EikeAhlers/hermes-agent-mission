"""Tests — no Hermes run, no network beyond loopback, no real profile data."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SCHEMA = """
create table sessions (
    id text primary key, title text, source text, model text,
    input_tokens integer, output_tokens integer, cache_read_tokens integer,
    tool_call_count integer, message_count integer, estimated_cost_usd real,
    started_at real, ended_at real, last_activity_at real,
    last_activity_description text, cwd text, parent_session_id text,
    archived integer, profile_name text
);
create table messages (
    id integer primary key autoincrement, session_id text, role text, content text,
    tool_call_id text, tool_calls text, tool_name text, timestamp real
);
"""


@pytest.fixture()
def profile(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    (home / "cron").mkdir(parents=True)
    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    now = time.time()
    rows = [
        ("s1", "wind · Stand 25.10.2026", "local", "m1", 1000, 200, 0, 4, 10, 0.5, now - 900, now - 60, now - 60, "", r"C:\p\21-Wind", None, 0, "default"),
        ("s2", "finance · Stand 25.10.2026", "local", "m1", 5000, 800, 0, 7, 20, 0.9, now - 300, None, now - 5, "receiving stream response", r"C:\p\03-oracle", None, 0, "default"),
        ("s3", "child of wind", "subagent", "m2", 100, 50, 0, 1, 2, 0.0, now - 200, now - 100, now - 100, "", r"C:\p\21-Wind", "s1", 0, "default"),
        ("s4", "old thing", "local", "m1", 10, 10, 0, 1, 2, 0.0, now - 90000, now - 89000, now - 89000, "", "", None, 0, "default"),
    ]
    conn.executemany("insert into sessions values (%s)" % ",".join("?" * 18), rows)
    conn.executemany(
        "insert into messages (session_id, role, content, tool_name, timestamp) values (?,?,?,?,?)",
        [
            ("s2", "tool", '{"output": "trades=3"}', "terminal", now - 30),
            ("s2", "assistant", "Alles gesetzt.", None, now - 20),
            ("s1", "tool", "ok", "read_file", now - 100),
        ],
    )
    conn.commit()
    conn.close()
    (home / "cron" / "jobs.json").write_text(
        json.dumps({"jobs": [{"name": "gmail-health", "schedule": "0 8 * * *", "paused": False}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home
    from mission import server

    server.stop()


def test_cluster_key_rules():
    from mission.store import cluster_key

    assert cluster_key("wind · Stand 25.10.2026", "local") == "wind"
    assert cluster_key("Bot Chat", "local") == "bot"
    assert cluster_key("child of wind", "subagent") == "subagents"
    assert cluster_key(None, "cron") == "cron"
    assert cluster_key("was", "local") == "local"


def test_snapshot_shape(profile):
    from mission.store import snapshot

    snap = snapshot(hours=24)
    assert snap["totals"]["sessions"] == 3, "the 25h-old session must fall out of the window"
    assert snap["totals"]["tokens"] == 1000 + 200 + 5000 + 800 + 100 + 50
    assert snap["totals"]["running"] == 1
    assert [c["label"] for c in snap["clusters"]][0] == "finance"
    live = [s for s in snap["sessions"] if s["live"]]
    assert [s["id"] for s in live] == ["s2"]
    assert snap["cron"][0]["name"] == "gmail-health"
    assert len(snap["timeline"]) == 24


def test_subagent_follows_parent_cluster(profile):
    from mission.store import snapshot

    snap = snapshot(hours=24)
    child = [s for s in snap["sessions"] if s["id"] == "s3"][0]
    assert child["cluster"] == "wind", "a subagent belongs to the project of its parent"


def test_detail_and_search(profile):
    from mission import store

    detail = store.session_detail("s2")
    assert detail["title"].startswith("finance")
    assert detail["messages"][-1]["content"] == "Alles gesetzt."
    assert detail["tools"][0]["name"] == "terminal"
    assert store.session_detail("nope")["error"] == "not found"

    hits = store.search("trades")
    assert len(hits) == 1 and hits[0]["id"] == "s2"
    assert store.search("a") == []


def test_http_api(profile):
    from mission import server

    info = server.start(port=0, open_browser=False)
    # port=0 is not supported by the singleton path; bind a concrete free port instead
    if not info.get("ok"):
        pytest.skip("server could not start")
    url = info["url"]
    with urllib.request.urlopen(url + "api/snapshot", timeout=10) as res:
        snap = json.loads(res.read())
    assert snap["totals"]["sessions"] == 3
    with urllib.request.urlopen(url + "api/session/s2", timeout=10) as res:
        detail = json.loads(res.read())
    assert detail["id"] == "s2"
    with urllib.request.urlopen(url + "api/search?q=trades", timeout=10) as res:
        hits = json.loads(res.read())["hits"]
    assert len(hits) == 1
    server.stop()


def test_tools_contract(profile):
    """Hermes calls handler(args) and requires a JSON string back."""
    from mission import tools

    snap_text = tools.snapshot({"hours": 24})
    assert isinstance(snap_text, str)
    snap = json.loads(snap_text)
    assert "sessions" in snap["summary"] and "running" in snap["summary"]

    found = json.loads(tools.search({"query": "trades"}))
    assert found["hits"] and "1 hits" in found["summary"]
    assert json.loads(tools.search({"query": "zzz-nothing"}))["hits"] == []

    opened = json.loads(tools.open_deck({"port": 0, "open_browser": False}))
    assert opened["ok"] is True and opened["url"].startswith("http://127.0.0.1:")

    assert json.loads(tools.waste({}))["ok"] is True
    assert tools.snapshot({}) == tools.snapshot({"hours": 24})
